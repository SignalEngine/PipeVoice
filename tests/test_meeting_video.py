"""Meeting screen video: picture-only capture, the mic+desktop mux, failure
isolation, app send gating, and the Meetings-tab send button."""

import json
import pathlib
import sys
import tempfile
import threading
import time
import types
import wave
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import config, meeting, screenrec
from wisprlite.meeting import SAMPLE_RATE, MeetingRecorder
from tests.test_meeting import app_class, fake_audio_modules, wait_for

av = pytest.importorskip("av", reason="PyAV ships with faster-whisper")


def _write_wav(path, seconds, freq=220):
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    pcm = (np.sin(2 * np.pi * freq * t) * 9000).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        handle.writeframes(pcm.tobytes())


def _synthetic_video(path, seconds=3, fps=4):
    """A real x264 mp4 with ms timestamps, like the grab loop writes."""
    with av.open(str(path), mode="w") as out:
        stream = out.add_stream("libx264", rate=fps)
        stream.time_base = screenrec.VIDEO_TIME_BASE
        stream.codec_context.time_base = screenrec.VIDEO_TIME_BASE
        stream.width, stream.height, stream.pix_fmt = 320, 240, "yuv420p"
        for i in range(seconds * fps):
            frame = np.full((240, 320, 3), (i * 9) % 255, dtype=np.uint8)
            picture = av.VideoFrame.from_ndarray(frame, format="rgb24")
            picture.pts = int(i * 1000 / fps)
            picture.time_base = screenrec.VIDEO_TIME_BASE
            for packet in stream.encode(picture):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)


# -- ScreenRecording(record_audio=False) ----------------------------------------

def test_picture_only_recording_has_one_video_stream_and_writes_no_wav():
    with tempfile.TemporaryDirectory() as tmp:
        rec = screenrec.ScreenRecording((0, 0, 320, 240), pathlib.Path(tmp),
                                        fps=4, record_audio=False)
        # start() with no audio must open no mic thread and no wav.
        with patch.object(rec, "_grab_loop", lambda: None):
            rec.start()
        assert [t.name for t in rec._threads] == ["screenrec-video"]
        assert not rec.audio_path.exists()
        for i in range(8):
            frame = np.full((240, 320, 3), 30 * i, dtype=np.uint8)
            with rec._encode_lock:
                if rec._container is None:
                    rec._open_container(320, 240)
                rec._encode_frame(frame)
            rec.frames_written += 1
        for t in rec._threads:
            t.join(2)
        out = rec.stop()
        assert out is not None and out.exists(), rec.errors
        with av.open(str(out)) as container:
            assert len(container.streams.video) == 1
            assert len(container.streams.audio) == 0
        assert not rec.audio_path.exists()


# -- the mux --------------------------------------------------------------------

def test_mux_gives_one_video_and_one_audio_stream_of_about_3_seconds():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        _synthetic_video(tmp / "v.mp4", seconds=3)
        _write_wav(tmp / "mic.wav", 3.0, 220)
        _write_wav(tmp / "desktop.wav", 3.0, 440)
        offsets = {"video": 100.0, "mic": 100.0, "desktop": 100.0}

        out = meeting.mux_meeting_video(tmp / "v.mp4", tmp / "mic.wav",
                                        tmp / "desktop.wav", offsets, tmp / "meeting.mp4")

        with av.open(str(out)) as container:
            assert len(container.streams.video) == 1
            assert len(container.streams.audio) == 1
            audio = container.streams.audio[0]
            seconds = sum(f.samples for f in container.decode(audio)) / audio.rate
            assert abs(seconds - 3.0) < 1.0, seconds
            assert audio.codec_context.name == "aac"
        # no leftover partial
        assert not list(tmp.glob("*.part*"))


def test_mux_places_a_late_starting_source_after_the_video_start():
    """desktop starts 1 s after the first frame -> 1 s of leading silence, same total."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        _synthetic_video(tmp / "v.mp4", seconds=3)
        _write_wav(tmp / "mic.wav", 2.0)
        _write_wav(tmp / "desktop.wav", 2.0)
        out = meeting.mux_meeting_video(
            tmp / "v.mp4", tmp / "mic.wav", tmp / "desktop.wav",
            {"video": 10.0, "mic": 11.0, "desktop": 11.0}, tmp / "meeting.mp4")
        with av.open(str(out)) as container:
            audio = container.streams.audio[0]
            pcm = np.concatenate([f.to_ndarray().reshape(-1) for f in container.decode(audio)])
        head = np.abs(pcm[: SAMPLE_RATE // 2]).max()      # first half second: silence
        body = np.abs(pcm[int(SAMPLE_RATE * 1.3): int(SAMPLE_RATE * 1.8)]).max()
        assert head < 0.02 and body > 0.1, (head, body)  # decoded as float


def test_mux_failure_keeps_the_video_only_file_and_leaves_no_partial():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "v.mp4").write_bytes(b"not a video")
        with pytest.raises(Exception):
            meeting.mux_meeting_video(tmp / "v.mp4", tmp / "m.wav", tmp / "d.wav",
                                      {}, tmp / "meeting.mp4")
        assert (tmp / "v.mp4").exists()
        assert not (tmp / "meeting.mp4").exists()
        assert not list(tmp.glob("*.part*"))


# -- MeetingRecorder ------------------------------------------------------------

def test_video_failure_at_start_never_stops_the_audio_meeting():
    with tempfile.TemporaryDirectory() as tmp:
        rec = MeetingRecorder(pathlib.Path(tmp), video_fps=4)
        with patch.dict(sys.modules, fake_audio_modules()), \
             patch.object(screenrec, "monitor_under_mouse",
                          side_effect=RuntimeError("no monitor")):
            session = rec.start()
            try:
                assert rec.active is True
                assert rec.video_state == "failed"
                meta = {"video": json.loads((session / "video.json").read_text())}
                assert "no monitor" in meta["video"]["error"]
                assert meta["video"]["file"] is None
            finally:
                rec.stop()
        # audio still produced, and the meta kept the video error through stop()
        assert (session / "mic.wav").exists()
        meta = {"video": json.loads((session / "video.json").read_text())}
        assert "no monitor" in meta["video"]["error"]
        assert json.loads((session / "meta.json").read_text())["mic"]["error"] is None


class _FakeVideo:
    """Stands in for a ScreenRecording: stop() returns a prepared clip."""

    def __init__(self, clip, first_frame_at):
        self.clip, self.first_frame_at = clip, first_frame_at
        self.errors, self.video_failed = [], threading.Event()
        self._stop = threading.Event()   # ScreenRecording's grab-loop stop flag

    def stop(self):
        return self.clip


def test_stop_muxes_on_its_own_thread_then_reports_the_finished_file():
    with tempfile.TemporaryDirectory() as tmp:
        got = []
        rec = MeetingRecorder(pathlib.Path(tmp),
                              on_video_ready=lambda p, e, _plan=None: got.append((p, e)))
        with patch.dict(sys.modules, fake_audio_modules()):
            session = rec.start()
            wait_for(lambda: rec._first_blocks["mic"] is not None
                     and rec._first_blocks["desktop"] is not None)
            _synthetic_video(session / meeting.video_only_name(session), seconds=2)
            rec._video = _FakeVideo(session / meeting.video_only_name(session),
                                    min(rec._first_blocks.values()))
            rec.stop()
        wait_for(lambda: got, timeout=15)
        path, error = got[0]
        assert error is None and path == session / meeting.meeting_video_name(session)
        with av.open(str(path)) as container:
            assert len(container.streams.video) == 1
            assert len(container.streams.audio) == 1
        assert not (session / meeting.video_only_name(session)).exists()   # intermediate removed on success
        meta = {"video": json.loads((session / "video.json").read_text())}
        assert meta["video"] == {"file": meeting.meeting_video_name(session), "error": None}
        assert rec.video_mux_pending is False


def test_a_failed_mux_keeps_the_video_only_file_and_reports_the_error():
    with tempfile.TemporaryDirectory() as tmp:
        got = []
        rec = MeetingRecorder(pathlib.Path(tmp),
                              on_video_ready=lambda p, e, _plan=None: got.append((p, e)))
        with patch.dict(sys.modules, fake_audio_modules()):
            session = rec.start()
            (session / meeting.video_only_name(session)).write_bytes(b"garbage")
            rec._video = _FakeVideo(session / meeting.video_only_name(session), 0.0)
            rec.stop()
        wait_for(lambda: got, timeout=15)
        assert got[0][0] is None and got[0][1]
        assert (session / meeting.video_only_name(session)).exists()
        meta = {"video": json.loads((session / "video.json").read_text())}
        assert meta["video"]["file"] is None and meta["video"]["error"]


# -- App ------------------------------------------------------------------------

def _app(**cfg_kwargs):
    App = app_class()
    app = App.__new__(App)
    app.cfg = config.Config(**cfg_kwargs)
    app._video_msg_lock = threading.Lock()
    app._meeting_video_sender = None
    said = []
    # Video messages go to a Windows notification, never the overlay (it holds
    # the after-call card). Any overlay call would raise here.
    app.overlay = types.SimpleNamespace()
    app._notify = said.append
    return app, said


def _clip(tmp):
    path = pathlib.Path(tmp) / "meeting.mp4"
    path.write_bytes(b"x" * 1000)
    return path


def _ready(app, path, error=None, plan=None):
    app._meeting_video_ready(path, error, plan)
    if app._meeting_video_sender is not None:
        app._meeting_video_sender.join(5)


def test_video_is_sent_only_when_send_is_on_and_a_destination_is_set():
    with tempfile.TemporaryDirectory() as tmp:
        clip = _clip(tmp)
        calls = []

        def fake_send(paths, destination, **_):
            calls.append((list(paths), destination))
            return True, "sent 1 file(s)"

        with patch.object(screenrec, "send", fake_send):
            app, said = _app(meeting_video_send=False, screenrec_destination="u@h:/in/")
            _ready(app, clip)
            assert calls == [] and said[-1] == ("\u2713 Meeting video ready")

            app, said = _app(meeting_video_send=True, screenrec_destination="")
            _ready(app, clip)
            assert calls == []
            assert said[-1] == ("Video saved locally. Set a Screen recording "
                                        "destination to send it.")

            app, said = _app(meeting_video_send=True, screenrec_destination="u@h:/in/")
            _ready(app, clip)
            assert calls == [([clip], "u@h:/in/")]
            assert said[-1] == ("\u2713 Meeting video sent")
        assert clip.exists()


def test_a_failed_send_keeps_the_file_and_says_so():
    with tempfile.TemporaryDirectory() as tmp:
        clip = _clip(tmp)
        with patch.object(screenrec, "send", lambda *a, **k: (False, "connection refused")):
            app, said = _app(meeting_video_send=True, screenrec_destination="u@h:/in/")
            _ready(app, clip)
        assert said[-1].startswith("Meeting video kept locally \u2014 send failed: connection")
        assert clip.exists()


def test_a_video_error_says_the_audio_is_fine_and_sends_nothing():
    with patch.object(screenrec, "send",
                      lambda *a, **k: pytest.fail("must not send")):
        app, said = _app(meeting_video_send=True, screenrec_destination="u@h:/in/")
        _ready(app, None, "mux failed")
    assert "audio is fine" in said[-1]


def test_preparing_notice_shows_only_while_the_mux_is_pending():
    app, said = _app()
    app._meeting = types.SimpleNamespace(video_mux_pending=False)
    app._video_preparing_notice()
    assert said == []
    app._meeting = types.SimpleNamespace(video_mux_pending=True)
    app._video_preparing_notice()
    assert said == ["Preparing meeting video\u2026"]


def test_a_config_reload_pushes_the_video_setting_to_the_recorder():
    app, _ = _app()
    app._meeting = types.SimpleNamespace(video_fps=0)
    app._sync_meeting_video(config.Config(meeting_video=True, meeting_video_fps=6))
    assert app._meeting.video_fps == 6
    app._sync_meeting_video(config.Config(meeting_video=False, meeting_video_fps=6))
    assert app._meeting.video_fps == 0


# -- config round trip ------------------------------------------------------------

def test_the_three_video_settings_round_trip_through_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.Config.load()
    assert (cfg.meeting_video, cfg.meeting_video_fps, cfg.meeting_video_send) == (False, 4, False)
    cfg.meeting_video, cfg.meeting_video_fps, cfg.meeting_video_send = True, 7, True
    cfg.save()
    again = config.Config.load()
    assert (again.meeting_video, again.meeting_video_fps, again.meeting_video_send) == (True, 7, True)


# -- Meetings tab -----------------------------------------------------------------

def test_send_video_target_and_the_reason_when_there_is_none(tmp_path):
    from wisprlite.meetings_tab import meeting_video_file

    assert meeting_video_file(tmp_path) == (None, "No video for this meeting")
    (tmp_path / "video.json").write_text(json.dumps({"file": None, "error": "boom"}))
    assert meeting_video_file(tmp_path) == (None, "Video failed: boom")
    leftover = meeting.video_only_name(tmp_path)
    (tmp_path / leftover).write_bytes(b"x")
    # Still being written (meeting or mux in progress): never offered for upload.
    assert meeting_video_file(tmp_path)[0] is None
    # Only after a FAILED mux names it as the leftover picture.
    (tmp_path / "video.json").write_text(json.dumps(
        {"file": None, "error": "boom", "video_only": leftover}))
    assert meeting_video_file(tmp_path)[0] == tmp_path / leftover
    (tmp_path / meeting.meeting_video_name(tmp_path)).write_bytes(b"x")
    assert meeting_video_file(tmp_path)[0] == tmp_path / meeting.meeting_video_name(tmp_path)
    # Two meetings never share an upload name (same remote inbox).
    assert meeting.meeting_video_name("a/meeting-1") != meeting.meeting_video_name("a/meeting-2")


# -- overlay pill state ---------------------------------------------------------

def test_the_pill_state_carries_the_video_status_for_the_screen_marker():
    app, _ = _app()
    rec = MeetingRecorder(pathlib.Path(tempfile.gettempdir()), video_fps=4)
    app._meeting = rec
    assert app._meeting_overlay_state()["video"] == "off"
    rec._video = _FakeVideo(None, 0.0)
    assert app._meeting_overlay_state()["video"] == "recording"
    rec._video.video_failed.set()                      # died mid-meeting
    assert app._meeting_overlay_state()["video"] == "failed"
    rec._video, rec._video_error = None, "boom"        # never started
    assert app._meeting_overlay_state()["video"] == "failed"


def test_the_send_decision_is_taken_when_the_meeting_starts():
    """Changing settings while the video muxes must not redirect or enable the upload."""
    with tempfile.TemporaryDirectory() as tmp:
        clip = _clip(tmp)
        calls = []
        with patch.object(screenrec, "send", lambda p, d, **_: (calls.append(d), (True, "ok"))[1]):
            app, said = _app(meeting_video_send=True, screenrec_destination="new@h:/x/")
            _ready(app, clip, plan=(False, ""))     # what was set at meeting start
            assert calls == []
            _ready(app, clip, plan=(True, "old@h:/in/"))
            assert calls == ["old@h:/in/"]


def test_a_finished_mux_writes_to_its_own_meeting_not_the_next_one(tmp_path):
    rec = MeetingRecorder(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    rec.session_dir = b                               # meeting B already started
    rec._set_video_meta({"file": "meeting.mp4", "error": None}, a)
    assert json.loads((a / "video.json").read_text())["file"] == "meeting.mp4"
    assert not (b / "video.json").exists()


def test_a_crafted_video_only_name_cannot_pick_another_file(tmp_path):
    from wisprlite.meetings_tab import meeting_video_file

    (tmp_path / "secret.txt").write_text("x")
    (tmp_path / "video.json").write_text(json.dumps(
        {"error": "boom", "video_only": "secret.txt"}))
    assert meeting_video_file(tmp_path)[0] is None


def test_retention_never_deletes_a_meeting_whose_video_is_muxing(tmp_path):
    rec = MeetingRecorder(tmp_path, retention_sessions=1)
    old = tmp_path / "meeting-20260101-000000"
    old.mkdir()
    hold = meeting.hold_session(old)            # e.g. Settings uploading it, other process
    rec._prune_sessions(reserve=1)
    assert old.exists()
    meeting.release_session(hold)
    rec._prune_sessions(reserve=1)
    assert not old.exists()


def test_retention_never_deletes_a_meeting_whose_video_is_uploading(tmp_path):
    rec = MeetingRecorder(tmp_path, retention_sessions=1)
    old = tmp_path / "meeting-20260101-000000"
    old.mkdir()
    seen = {}

    def slow_send(paths, destination, **_):
        rec._prune_sessions(reserve=1)          # a new meeting starts mid-upload
        seen["exists"] = pathlib.Path(paths[0]).exists()
        return True, "ok"

    clip = old / meeting.meeting_video_name(old)
    clip.write_bytes(b"x")
    with patch.object(screenrec, "send", slow_send):
        app, _ = _app()
        _ready(app, clip, plan=(True, "u@h:/in/"))
    assert seen["exists"] is True
    assert not meeting.session_busy(old)        # released once the upload ends


def test_filming_stops_the_moment_the_meeting_stops(tmp_path):
    """Normalising audio takes time after stop; the screen must not be filmed then."""
    rec = MeetingRecorder(tmp_path)
    video = _FakeVideo(None, 0.0)
    rec._video = video
    rec._active = True
    rec._threads = []
    calls = []
    rec._finish_writer = lambda: calls.append(video._stop.is_set())
    rec._close_waves = lambda: None
    try:
        rec._stop_recording("test")
    except Exception:
        pass
    assert calls and calls[0] is True


def test_a_crashed_hold_goes_stale(tmp_path):
    import os
    marker = tmp_path / ".busy-crashed"
    marker.touch()
    old = time.time() - 7 * 3600
    os.utime(marker, (old, old))
    assert meeting.session_busy(tmp_path) is False
