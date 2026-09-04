import json
import os
import pathlib
import re
import sys
import tempfile
import threading
import time
import types
import wave
from datetime import datetime as RealDateTime
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import config
from wisprlite import meeting
from wisprlite.meeting import SAMPLE_RATE, MeetingRecorder


class FixedDateTime(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 27, 14, 5, 9)
        return value.replace(tzinfo=tz) if tz is not None else value


class FakeInputStream:
    last_kwargs = None
    close_count = 0

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs
        self.callback = kwargs["callback"]
        self.active = False

    def start(self):
        self.active = True
        self.callback(np.array([[0.25], [-0.25]], dtype=np.float32), 2, None, None)

    def stop(self):
        self.active = False

    def close(self):
        type(self).close_count += 1


class FailingInputStream:
    def __init__(self, **_kwargs):
        raise RuntimeError("fake microphone failure")


class FakeDesktopRecorder:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def record(self, numframes):
        time.sleep(0.005)
        return np.full((numframes, 1), 0.125, dtype=np.float32)


class FakeLoopback:
    id = "speaker-id"
    isloopback = True

    def recorder(self, **_kwargs):
        return FakeDesktopRecorder()


def fake_audio_modules(input_stream=FakeInputStream):
    sounddevice = types.ModuleType("sounddevice")
    sounddevice.InputStream = input_stream
    soundcard = types.ModuleType("soundcard")
    soundcard.default_speaker = lambda: types.SimpleNamespace(id="speaker-id")
    soundcard.all_microphones = lambda include_loopback=False: [FakeLoopback()]
    return {"sounddevice": sounddevice, "soundcard": soundcard}


def app_class():
    sounddevice = types.ModuleType("sounddevice")
    sounddevice.InputStream = FakeInputStream
    keyboard = types.ModuleType("keyboard")
    with patch.dict(
        sys.modules,
        {"sounddevice": sounddevice, "keyboard": keyboard},
    ):
        from wisprlite.app import App
    return App


def wait_for(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("timed out waiting for fake capture")


def test_session_directory_naming():
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        with patch.object(meeting, "datetime", FixedDateTime):
            first = recorder._create_session_dir()
            second = recorder._create_session_dir()
        assert first.name == "meeting-20260727-140509"
        assert second.name == "meeting-20260727-140509-2"
        assert re.fullmatch(r"meeting-\d{8}-\d{6}(?:-\d+)?", second.name)


def test_meta_round_trip_and_elapsed():
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        assert recorder.active is False
        assert recorder.elapsed == 0.0
        with patch.dict(sys.modules, fake_audio_modules()):
            session = recorder.start()
            assert recorder.active is True
            wait_for(
                lambda: all(
                    value is not None for value in recorder._first_blocks.values()
                )
            )
            checkpoint_meta = json.loads(
                (session / "meta.json").read_text(encoding="utf-8")
            )
            assert checkpoint_meta["stopped_at"] is None
            assert recorder.elapsed >= 0.0
            recorder.stop()

        meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
        assert json.loads(json.dumps(meta)) == meta
        assert meta["sample_rate"] == SAMPLE_RATE
        assert meta["channels"] == 1
        assert meta["mic"]["first_block_monotonic"] is not None
        assert meta["desktop"]["first_block_monotonic"] is not None
        assert meta["mic"]["error"] is None
        assert meta["desktop"]["error"] is None
        assert recorder.active is False
        assert recorder.elapsed == 0.0

        for filename in ("mic.wav", "desktop.wav"):
            with wave.open(str(session / filename), "rb") as audio:
                assert audio.getframerate() == SAMPLE_RATE
                assert audio.getnchannels() == 1
                assert audio.getsampwidth() == 2
                assert audio.getnframes() > 0


def test_meeting_records_at_48khz_not_the_16khz_speech_rate():
    assert SAMPLE_RATE == 48_000


def test_mid_recording_checkpoint_patches_wave_header():
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        path = pathlib.Path(tmp) / "mic.wav"
        recorder._waves["mic"] = recorder._open_wave(path)
        block = np.full((1_600, 1), 0.25, dtype=np.float32)

        recorder._write_block("mic", block)
        recorder._write_block("mic", block)
        # The audio callback only appends. The checkpoint thread is what makes
        # the half-written file playable.
        recorder._checkpoint_once()

        with wave.open(str(path), "rb") as audio:
            assert audio.getnframes() == 3_200
        recorder._close_waves()


def test_meta_checkpoint_preserves_transcription_keys():
    with tempfile.TemporaryDirectory() as tmp:
        session = pathlib.Path(tmp) / "meeting-preserve-meta"
        session.mkdir()
        recorder = MeetingRecorder(pathlib.Path(tmp))
        recorder.session_dir = session
        recorder._started_at = "2026-07-27T12:00:00+00:00"
        transcription_meta = {
            "status": "transcribed",
            "transcript_file": "transcript.json",
            "transcription_backend": "deepgram",
            "transcription_error": None,
        }
        (session / "meta.json").write_text(
            json.dumps(transcription_meta),
            encoding="utf-8",
        )

        recorder._write_meta(stopped_at=None, duration=5.0)

        meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
        for key, value in transcription_meta.items():
            assert meta[key] == value
        assert meta["duration_seconds"] == 5.0


def test_late_checkpoint_does_not_overwrite_final_meta():
    with tempfile.TemporaryDirectory() as tmp:
        session = pathlib.Path(tmp) / "meeting-final-meta"
        session.mkdir()
        recorder = MeetingRecorder(pathlib.Path(tmp))
        recorder.session_dir = session
        recorder._started_at = "2026-07-27T12:00:00+00:00"
        stopped_at = "2026-07-27T12:30:00+00:00"
        recorder._write_meta(stopped_at=stopped_at, duration=1800.0)

        recorder._write_meta(stopped_at=None, duration=0.0)

        meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
        assert meta["stopped_at"] == stopped_at
        assert meta["duration_seconds"] == 1800.0


def test_meta_records_the_dropped_block_count():
    """The count is the only on-disk proof the writer kept up.

    A recording whose wav ends short of the clock is ambiguous — device-open
    latency and dropped audio look identical — unless this number is saved.
    """
    with tempfile.TemporaryDirectory() as tmp:
        session = pathlib.Path(tmp) / "meeting-drops"
        session.mkdir()
        recorder = MeetingRecorder(pathlib.Path(tmp))
        recorder.session_dir = session
        recorder._started_at = "2026-07-27T12:00:00+00:00"

        recorder._write_meta(stopped_at=None, duration=1.0)
        assert json.loads((session / "meta.json").read_text(encoding="utf-8"))[
            "dropped_blocks"
        ] == 0

        for _ in range(meeting.PCM_QUEUE_LIMIT + 3):
            recorder._enqueue("mic", np.zeros((1, 1), dtype=np.float32))
        recorder._write_meta(stopped_at="2026-07-27T12:00:02+00:00", duration=2.0)

        meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
        assert meta["dropped_blocks"] == 3


def test_recoverable_overflow_is_not_persisted_as_session_error():
    with tempfile.TemporaryDirectory() as tmp:
        session = pathlib.Path(tmp) / "meeting-overflow"
        session.mkdir()
        recorder = MeetingRecorder(pathlib.Path(tmp))
        recorder.session_dir = session
        recorder._started_at = "2026-07-27T12:00:00+00:00"
        recorder._on_mic_block(np.zeros((1, 1), dtype=np.float32), 1, None, "input overflow")
        recorder._write_meta(stopped_at="2026-07-27T12:00:01+00:00", duration=1.0)

        meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
        assert recorder.errors["mic"] == "RuntimeError: input overflow"
        assert recorder.fatal_errors["mic"] is None
        assert meta["mic"]["error"] is None


def test_retention_uses_recording_time_not_directory_mtime():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        sessions = []
        for index, started_at in enumerate((
            "2026-07-20T12:00:00+00:00",
            "2026-07-21T12:00:00+00:00",
            "2026-07-22T12:00:00+00:00",
        )):
            path = base / f"meeting-{index}"
            path.mkdir()
            (path / "meta.json").write_text(
                json.dumps({"started_at": started_at}), encoding="utf-8"
            )
            sessions.append(path)
        # Simulate transcribing the oldest: its directory is now newest.
        os.utime(sessions[0], (100, 100))
        os.utime(sessions[2], (1, 1))

        recorder = MeetingRecorder(base, retention_sessions=2)
        recorder._prune_sessions()

        assert sessions[0].exists() is False
        assert sessions[1].exists()
        assert sessions[2].exists()


def test_retention_prunes_all_but_newest_sessions():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        for index in range(5):
            session = base / f"meeting-2026072{index + 1}-120000"
            session.mkdir()
            os.utime(session, (index, index))

        recorder = MeetingRecorder(base, retention_sessions=3)
        recorder._prune_sessions()

        assert sorted(path.name for path in base.iterdir()) == [
            "meeting-20260723-120000",
            "meeting-20260724-120000",
            "meeting-20260725-120000",
        ]


def test_retention_reserves_room_for_the_next_session():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        oldest = base / "meeting-20260725-120000"
        newest = base / "meeting-20260726-120000"
        oldest.mkdir()
        newest.mkdir()
        os.utime(oldest, (1, 1))
        os.utime(newest, (2, 2))
        recorder = MeetingRecorder(base, retention_sessions=2)
        recorder._prune_sessions(reserve=1)

        assert oldest.exists() is False
        assert newest.exists()


def test_retention_continues_after_one_session_cannot_be_removed():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        sessions = []
        for index in range(4):
            session = base / f"meeting-2026072{index + 1}-120000"
            session.mkdir()
            os.utime(session, (index, index))
            sessions.append(session)

        original_rmtree = meeting.shutil.rmtree

        def remove_unless_blocked(path):
            if path == sessions[2]:
                raise PermissionError("session is open")
            original_rmtree(path)

        recorder = MeetingRecorder(base, retention_sessions=1)
        with patch.object(meeting.shutil, "rmtree", side_effect=remove_unless_blocked):
            recorder._prune_sessions()

        assert sessions[3].exists()
        assert sessions[2].exists()
        assert sessions[1].exists() is False
        assert sessions[0].exists() is False


def test_default_meetings_dir_is_outside_roaming_profile():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        roaming = root / "Roaming"
        local = root / "Local"
        with patch.object(meeting.sys, "platform", "win32"), patch.dict(
            os.environ,
            {"APPDATA": str(roaming), "LOCALAPPDATA": str(local)},
        ):
            recorder = MeetingRecorder()

        assert recorder.base_dir == local / config.APP_NAME / "meetings"
        assert roaming not in recorder.base_dir.parents


def test_elapsed_uses_monotonic_time():
    recorder = MeetingRecorder(pathlib.Path("unused"))
    recorder._active = True
    recorder._started_monotonic = 10.0
    with patch.object(meeting.time, "monotonic", return_value=12.75):
        assert recorder.elapsed == 2.75


def test_stream_levels_use_fast_attack_slow_release():
    recorder = MeetingRecorder(pathlib.Path("unused"))
    recorder._waves = {
        "mic": types.SimpleNamespace(
            writeframesraw=lambda _pcm: None,
            _patchheader=lambda: None,
            _file=types.SimpleNamespace(flush=lambda: None),
        )
    }
    recorder._last_header_patches["mic"] = time.monotonic()

    recorder._write_block("mic", np.array([0.5, -0.5], dtype=np.float32))
    assert recorder.levels["mic"] == 0.5
    recorder._write_block("mic", np.array([0.1, -0.1], dtype=np.float32))
    assert abs(recorder.levels["mic"] - 0.35) < 1e-6
    recorder._write_block("mic", np.array([0.6, -0.6], dtype=np.float32))
    assert abs(recorder.levels["mic"] - 0.6) < 1e-6


def test_one_stream_failure_keeps_the_other():
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        with patch.dict(sys.modules, fake_audio_modules(FailingInputStream)):
            session = recorder.start()
            wait_for(lambda: recorder._errors["mic"] is not None)
            wait_for(lambda: recorder._first_blocks["desktop"] is not None)
            recorder.stop()

        meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
        assert "fake microphone failure" in meta["mic"]["error"]
        assert meta["desktop"]["error"] is None
        assert meta["desktop"]["first_block_monotonic"] is not None
        with wave.open(str(session / "desktop.wav"), "rb") as audio:
            assert audio.getnframes() > 0


def test_missing_dependency_raises_from_start():
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        modules = fake_audio_modules()
        modules["soundcard"] = None
        with patch.dict(sys.modules, modules):
            try:
                recorder.start()
            except ModuleNotFoundError:
                pass
            else:
                raise AssertionError("start() hid a missing soundcard dependency")
        assert recorder.active is False


def test_post_thread_start_failure_rolls_back_capture():
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp), max_minutes="240")
        with patch.dict(sys.modules, fake_audio_modules()):
            try:
                recorder.start()
            except TypeError:
                pass
            else:
                raise AssertionError("post-thread failure did not escape start()")

        assert recorder.active is False
        assert recorder._waves == {}
        assert not any(thread.is_alive() for thread in recorder._threads)


def test_numeric_config_values_are_coerced():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "config.json"
        path.write_text(
            json.dumps(
                {
                    "meeting_max_minutes": "240",
                    "meeting_retention_sessions": "12",
                    "min_seconds": "0.5",
                    "history_size": "75",
                    "deepgram_finish_timeout": "7.5",
                    "mcp_port": "49519",
                    "hands_free_silence_ms": "900",
                }
            ),
            encoding="utf-8",
        )
        with patch.object(config, "CONFIG_PATH", path):
            cfg = config.Config.load()

    assert cfg.meeting_max_minutes == 240
    assert type(cfg.meeting_max_minutes) is int
    assert cfg.meeting_retention_sessions == 12
    assert type(cfg.meeting_retention_sessions) is int
    assert cfg.min_seconds == 0.5
    assert type(cfg.min_seconds) is float
    assert cfg.history_size == 75
    assert cfg.deepgram_finish_timeout == 7.5
    assert cfg.mcp_port == 49519
    assert cfg.hands_free_silence_ms == 900


def test_configured_device_is_passed_to_input_stream():
    with tempfile.TemporaryDirectory() as tmp:
        FakeInputStream.last_kwargs = None
        FakeInputStream.close_count = 0
        recorder = MeetingRecorder(pathlib.Path(tmp), device=7)
        with patch.dict(sys.modules, fake_audio_modules()):
            recorder.start()
            wait_for(lambda: FakeInputStream.last_kwargs is not None)
            recorder.stop()
        assert FakeInputStream.last_kwargs["device"] == 7
        assert FakeInputStream.close_count == 1


def test_inactive_mic_stream_records_an_error():
    class InactiveInputStream(FakeInputStream):
        def start(self):
            self.active = False

    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        with patch.dict(sys.modules, fake_audio_modules(InactiveInputStream)):
            recorder.start()
            wait_for(lambda: recorder.errors["mic"] is not None)
            recorder.stop()

        assert "became inactive" in recorder.errors["mic"]


def test_mic_callback_status_records_an_error():
    recorder = MeetingRecorder(pathlib.Path("unused"))
    recorder._on_mic_block(
        np.empty((0, 1), dtype=np.float32),
        0,
        None,
        "input overflow",
    )
    assert recorder.errors["mic"] == "RuntimeError: input overflow"
    assert recorder.fatal_errors["mic"] is None


def test_fatal_error_upgrades_an_earlier_recoverable_error():
    recorder = MeetingRecorder(pathlib.Path("unused"))
    recorder._on_mic_block(
        np.empty((0, 1), dtype=np.float32),
        0,
        None,
        "input overflow",
    )
    recorder._record_error("mic", RuntimeError("device lost"))

    assert recorder.errors["mic"] == "RuntimeError: device lost"
    assert recorder.fatal_errors["mic"] == "RuntimeError: device lost"


def test_paused_meeting_can_stop_but_not_start():
    class FakeMeeting:
        def __init__(self):
            self.starts = 0
            self.stops = 0

        def start(self):
            self.starts += 1
            return pathlib.Path("unused")

        def stop(self):
            self.stops += 1

    App = app_class()
    app = App.__new__(App)
    app.paused = True
    app._meeting_active = False
    app._meeting = FakeMeeting()
    app._meeting_errors_reported = set()
    app._set_icon = lambda _state: None
    app._fail = lambda _message: None
    app.tray = types.SimpleNamespace(update=lambda: None)

    app.toggle_meeting()
    assert app._meeting.starts == 0
    assert app._meeting_active is False

    app._meeting_active = True
    app.toggle_meeting()
    assert app._meeting.stops == 1
    assert app._meeting_active is False


def test_config_watcher_surfaces_a_late_meeting_error():
    class TwoIntervals:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 2

    class LateFailureMeeting:
        session_dir = pathlib.Path("session")

        def __init__(self):
            self.checks = 0

        @property
        def errors(self):
            self.checks += 1
            desktop = None if self.checks == 1 else "RuntimeError: device lost"
            return {"mic": None, "desktop": desktop}

        @property
        def fatal_errors(self):
            desktop = None if self.checks == 1 else "RuntimeError: device lost"
            return {"mic": None, "desktop": desktop}

    App = app_class()
    app = App.__new__(App)
    app._stop = TwoIntervals()
    app._meeting_active = True
    app._meeting = LateFailureMeeting()
    app._meeting_errors_reported = set()
    failures = []
    app._fail = failures.append

    missing_config = pathlib.Path("config-that-does-not-exist.json")
    with patch.object(config, "CONFIG_PATH", missing_config), patch(
        "wisprlite.app.time.sleep", return_value=None
    ):
        app._watch_config()

    assert app._meeting.checks == 2
    assert failures == ["meeting capture: desktop: RuntimeError: device lost"]
    assert app._meeting_degraded is True


def test_recoverable_meeting_error_does_not_latch_degraded_state():
    class RecoverableFailureMeeting:
        errors = {"mic": "RuntimeError: input overflow", "desktop": None}
        fatal_errors = {"mic": None, "desktop": None}

    App = app_class()
    app = App.__new__(App)
    app._meeting_active = True
    app._meeting_degraded = False
    app._meeting = RecoverableFailureMeeting()
    app._meeting_errors_reported = set()
    failures = []
    app._fail = failures.append

    app._check_meeting_errors()

    assert failures == ["meeting capture: mic: RuntimeError: input overflow"]
    assert app._meeting_degraded is False


def test_agent_hands_free_is_busy_during_meeting():
    App = app_class()
    app = App.__new__(App)
    app._meeting_active = True
    app._busy = threading.Lock()

    assert app._agent_listen_hands_free() == {"status": "busy", "text": ""}
    assert app._busy.locked() is False


def test_agent_push_to_talk_is_busy_during_meeting():
    App = app_class()
    app = App.__new__(App)
    app._meeting_active = True
    app._busy = threading.Lock()
    app._pending_agent_listen = None
    app.cfg = types.SimpleNamespace(mcp_default_mode="push_to_talk")

    assert app.on_agent_listen() == {"status": "busy", "text": ""}


def test_degraded_meeting_icon_persists():
    states = []
    App = app_class()
    app = App.__new__(App)
    app._meeting_active = True
    app._meeting_degraded = True
    app.tray = types.SimpleNamespace(set_state=states.append)

    app._set_icon("idle")

    assert states == ["meeting_degraded"]


def test_live_thread_blocks_a_second_session():
    entered = threading.Event()
    release = threading.Event()

    class StalledDesktopRecorder(FakeDesktopRecorder):
        def record(self, numframes):
            entered.set()
            release.wait()
            return np.full((numframes, 1), 0.125, dtype=np.float32)

    class StalledLoopback(FakeLoopback):
        def recorder(self, **_kwargs):
            return StalledDesktopRecorder()

    modules = fake_audio_modules()
    modules["soundcard"].all_microphones = (
        lambda include_loopback=False: [StalledLoopback()]
    )

    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        with patch.dict(sys.modules, modules), patch.object(
            meeting, "CAPTURE_JOIN_TIMEOUT", 0.01
        ):
            first_session = recorder.start()
            wait_for(entered.is_set)
            recorder.stop()
            assert "did not stop" in recorder.errors["desktop"]
            assert recorder.fatal_errors["desktop"] is None
            try:
                recorder.start()
            except RuntimeError as exc:
                assert "still stopping" in str(exc)
            else:
                raise AssertionError("second session started with a live capture thread")

            release.set()
            wait_for(lambda: not any(thread.is_alive() for thread in recorder._threads))
            second_session = recorder.start()
            assert second_session != first_session
            recorder.stop()


def test_session_limit_stops_and_records_reason():
    reasons = []
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(
            pathlib.Path(tmp),
            max_minutes=0.0005,
            on_auto_stop=reasons.append,
        )
        with patch.dict(sys.modules, fake_audio_modules()):
            session = recorder.start()
            wait_for(lambda: not recorder.active)
            wait_for(lambda: bool(reasons))

        meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
        assert "maximum session length reached" in meta["stop_reason"]
        assert reasons == [meta["stop_reason"]]


def test_speaker_map_round_trip_and_clearing_preserves_transcript_bytes():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp)
        raw = b'{"segments":[{"speaker":"Them 1","text":"hello"}]}'
        (path / "transcript.json").write_bytes(raw)
        meeting.save_speaker_map(path, {"Them 1": "Sarah", "Them 2": ""})
        assert meeting.load_speaker_map(path) == {"Them 1": "Sarah"}
        assert meeting.render_transcript(
            json.loads(raw)["segments"], speaker_map={"Them 1": "Sarah"}
        ) == "Sarah: hello"
        meeting.save_speaker_map(path, {})
        assert meeting.load_speaker_map(path) == {}
        assert (path / "transcript.json").read_bytes() == raw


def test_loudest_speaker_window_uses_highest_rms_region():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "desktop.wav"
        frames = (np.zeros(64_000, dtype=np.int16))
        frames[32_000:64_000] = 20_000
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16_000)
            audio.writeframes(frames.tobytes())
        result = meeting.find_loudest_speaker_window(
            path,
            [{"speaker": "Them 1", "t": 0}, {"speaker": "Them 1", "t": 1},
             {"speaker": "Them 1", "t": 2}],
            "Them 1",
        )
        assert result == (2.0, 2.0)


def test_loudest_speaker_window_converts_merged_time_to_desktop_time():
    """Merged transcript t includes the desktop stream's 1.4s start shift."""
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "desktop.wav"
        frames = np.zeros(96_000, dtype=np.int16)
        frames[64_000:96_000] = 20_000
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16_000)
            audio.writeframes(frames.tobytes())
        result = meeting.find_loudest_speaker_window(
            path,
            [{"speaker": "Them", "t": 5.4}],
            "Them",
            stream_shift=1.4,
        )
        assert result == (4.0, 2.0)


def test_mic_only_live_session_refreshes_heartbeat_and_stays_live():
    from wisprlite import meetings_tab

    with tempfile.TemporaryDirectory() as tmp:
        session = pathlib.Path(tmp) / "meeting-mic-only"
        session.mkdir()
        recorder = MeetingRecorder(pathlib.Path(tmp))
        recorder.session_dir = session
        recorder._started_at = "2026-07-27T12:00:00+00:00"
        recorder._active = True
        recorder._waves["mic"] = recorder._open_wave(session / "mic.wav")
        recorder._last_header_patches["mic"] = 0.0
        recorder._write_meta(stopped_at=None, duration=20.0)
        stale = time.time() - 120.0
        os.utime(session / "meta.json", (stale, stale))

        recorder._write_block("mic", np.full((1_600, 1), 0.25, dtype=np.float32))
        recorder._checkpoint_once()

        listed = meetings_tab.list_sessions(pathlib.Path(tmp))
        assert listed[0]["status"] == "recording"
        assert json.loads((session / "meta.json").read_text())["stopped_at"] is None
        recorder._close_waves()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("OK")


def test_the_audio_callback_does_not_block_on_a_held_wave_lock():
    """The P1 a reviewer caught: moving the disk I/O off the callback is not
    enough while the callback still WAITS on the lock the slow work holds.

    Both locks are taken by another thread here, exactly as the checkpoint's
    seek+flush would hold them, and the callback must still return at once.
    """
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        recorder._waves["mic"] = recorder._open_wave(pathlib.Path(tmp) / "mic.wav")
        block = np.full((800, 1), 0.25, dtype=np.float32)

        # The locks are held by ANOTHER thread that lets go shortly. A callback
        # that waits on them finishes late instead of deadlocking, so a
        # regression shows up as a failure rather than a hung test run.
        holding = threading.Event()
        release = threading.Event()

        def hold_the_locks():
            with recorder._wave_locks["mic"], recorder._state_lock:
                holding.set()
                release.wait(2.0)

        holder = threading.Thread(target=hold_the_locks, daemon=True)
        holder.start()
        assert holding.wait(2.0)

        # Drive the REAL PortAudio callback, not _enqueue — otherwise putting
        # the direct write back into _on_mic_block leaves this test green.
        started = time.monotonic()
        for _ in range(20):
            recorder._on_mic_block(block, 800, None, None)
        elapsed = time.monotonic() - started

        release.set()
        holder.join(timeout=2.0)

        assert elapsed < 0.25, f"callback blocked for {elapsed:.3f}s while locks were held"
        recorder._close_waves()


def test_the_callback_copies_the_buffer_it_is_handed():
    """sounddevice reuses one buffer, so queueing a view records later audio."""
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        path = pathlib.Path(tmp) / "mic.wav"
        recorder._waves["mic"] = recorder._open_wave(path)

        reused = np.full((800, 1), 0.5, dtype=np.float32)
        recorder._enqueue("mic", reused)
        reused[:] = -0.5          # PortAudio refilling its buffer
        recorder._drain_queue()
        recorder._close_waves()

        with wave.open(str(path), "rb") as audio:
            first = np.frombuffer(audio.readframes(1), dtype="<i2")[0]
        assert first > 0, f"queued a live view, not a copy (got {first})"


def test_the_tail_of_a_recording_is_written_before_the_files_close():
    """Everything still queued at stop is audio the user spoke."""
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        path = pathlib.Path(tmp) / "mic.wav"
        recorder._waves["mic"] = recorder._open_wave(path)

        block = np.full((800, 1), 0.25, dtype=np.float32)
        for _ in range(10):
            recorder._enqueue("mic", block)
        recorder._drain_queue()
        recorder._close_waves()

        with wave.open(str(path), "rb") as audio:
            assert audio.getnframes() == 8_000


def test_a_full_queue_drops_a_block_rather_than_stalling_the_callback():
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        recorder._waves["mic"] = recorder._open_wave(pathlib.Path(tmp) / "mic.wav")
        block = np.full((800, 1), 0.25, dtype=np.float32)

        for _ in range(meeting.PCM_QUEUE_LIMIT + 5):
            recorder._enqueue("mic", block)

        assert recorder.dropped_blocks == 5
        recorder._drain_queue()
        recorder._close_waves()


def test_the_checkpoint_still_makes_a_half_written_recording_playable():
    """Moving the patch must not cost the crash-safety it was there for."""
    with tempfile.TemporaryDirectory() as tmp:
        session = pathlib.Path(tmp) / "meeting-crash"
        session.mkdir()
        recorder = MeetingRecorder(pathlib.Path(tmp))
        recorder.session_dir = session
        recorder._started_at = "2026-08-11T12:00:00+00:00"
        recorder._active = True
        path = session / "mic.wav"
        recorder._waves["mic"] = recorder._open_wave(path)

        block = np.full((1_600, 1), 0.25, dtype=np.float32)
        for _ in range(5):
            recorder._write_block("mic", block)
        recorder._checkpoint_once()

        # Read it WITHOUT closing — this is the crashed-process case.
        with wave.open(str(path), "rb") as audio:
            assert audio.getnframes() == 8_000
        recorder._close_waves()


def test_a_checkpoint_before_any_audio_does_not_record_an_error():
    """The checkpoint thread starts with the capture, so it gets there first."""
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        recorder._waves["mic"] = recorder._open_wave(pathlib.Path(tmp) / "mic.wav")

        recorder._checkpoint_once()

        assert recorder.errors["mic"] is None, recorder.errors["mic"]
        recorder._close_waves()


def test_a_crash_in_the_first_seconds_still_leaves_a_playable_file():
    """The header used to be patched by the first write. Keep that property:
    waiting for the 5s checkpoint would leave real PCM behind a 0-frame header."""
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        path = pathlib.Path(tmp) / "mic.wav"
        recorder._waves["mic"] = recorder._open_wave(path)

        recorder._enqueue("mic", np.full((800, 1), 0.25, dtype=np.float32))
        recorder._drain_queue()
        # No _checkpoint_once() — this is the process dying two seconds in.

        with wave.open(str(path), "rb") as audio:
            assert audio.getnframes() == 800


def test_a_stalled_writer_is_not_drained_underneath():
    """Two threads writing one WAV interleaves the audio. If the writer will
    not stop, leave the tail to it rather than racing it."""
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        recorder._waves["mic"] = recorder._open_wave(pathlib.Path(tmp) / "mic.wav")
        recorder._enqueue("mic", np.full((800, 1), 0.25, dtype=np.float32))

        stuck = threading.Event()
        recorder._writer_thread = threading.Thread(
            target=stuck.wait, kwargs={"timeout": 30}, daemon=True)
        recorder._writer_thread.start()

        with patch.object(meeting, "CAPTURE_JOIN_TIMEOUT", 0.01):
            recorder._finish_writer()

        assert recorder._pcm_queue.qsize() == 1, "drained while the writer was alive"
        assert "did not stop" in (recorder.errors["mic"] or "")
        stuck.set()
        recorder._close_waves()


def test_a_writer_that_finished_hands_over_its_tail():
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        path = pathlib.Path(tmp) / "mic.wav"
        recorder._waves["mic"] = recorder._open_wave(path)
        recorder._enqueue("mic", np.full((800, 1), 0.25, dtype=np.float32))

        recorder._writer_thread = threading.Thread(target=lambda: None, daemon=True)
        recorder._writer_thread.start()
        recorder._finish_writer()

        assert recorder._pcm_queue.qsize() == 0
        recorder._close_waves()
        with wave.open(str(path), "rb") as audio:
            assert audio.getnframes() == 800
