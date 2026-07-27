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
from wisprlite.meeting import MeetingRecorder


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
        assert meta["sample_rate"] == 16_000
        assert meta["channels"] == 1
        assert meta["mic"]["first_block_monotonic"] is not None
        assert meta["desktop"]["first_block_monotonic"] is not None
        assert meta["mic"]["error"] is None
        assert meta["desktop"]["error"] is None
        assert recorder.active is False
        assert recorder.elapsed == 0.0

        for filename in ("mic.wav", "desktop.wav"):
            with wave.open(str(session / filename), "rb") as audio:
                assert audio.getframerate() == 16_000
                assert audio.getnchannels() == 1
                assert audio.getsampwidth() == 2
                assert audio.getnframes() > 0


def test_mid_recording_checkpoint_patches_wave_header():
    with tempfile.TemporaryDirectory() as tmp:
        recorder = MeetingRecorder(pathlib.Path(tmp))
        path = pathlib.Path(tmp) / "mic.wav"
        recorder._waves["mic"] = recorder._open_wave(path)
        block = np.full((1_600, 1), 0.25, dtype=np.float32)

        with patch.object(meeting.time, "monotonic", side_effect=(10.0, 16.0)):
            recorder._write_block("mic", block)
            recorder._write_block("mic", block)

        with wave.open(str(path), "rb") as audio:
            assert audio.getnframes() == 3_200
        recorder._close_waves()


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


if __name__ == "__main__":
    test_session_directory_naming()
    test_meta_round_trip_and_elapsed()
    test_mid_recording_checkpoint_patches_wave_header()
    test_retention_prunes_all_but_newest_sessions()
    test_retention_reserves_room_for_the_next_session()
    test_default_meetings_dir_is_outside_roaming_profile()
    test_elapsed_uses_monotonic_time()
    test_one_stream_failure_keeps_the_other()
    test_missing_dependency_raises_from_start()
    test_post_thread_start_failure_rolls_back_capture()
    test_numeric_config_values_are_coerced()
    test_configured_device_is_passed_to_input_stream()
    test_inactive_mic_stream_records_an_error()
    test_mic_callback_status_records_an_error()
    test_paused_meeting_can_stop_but_not_start()
    test_config_watcher_surfaces_a_late_meeting_error()
    test_agent_hands_free_is_busy_during_meeting()
    test_agent_push_to_talk_is_busy_during_meeting()
    test_degraded_meeting_icon_persists()
    test_live_thread_blocks_a_second_session()
    test_session_limit_stops_and_records_reason()
    print("OK")
