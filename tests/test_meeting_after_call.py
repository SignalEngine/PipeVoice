"""Meeting after-call: auto-transcribe, transcript sent with the video."""
import json
import pathlib
import sys
import threading
import types
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import config, meeting, screenrec
from tests.test_meeting import app_class


def _session(tmp_path, with_audio=True):
    d = tmp_path / "meeting-20261109-101500"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"started_at": "2026-11-09T10:15:00+00:00",
                                             "duration_seconds": 60}))
    if with_audio:
        (d / "mic.wav").write_bytes(b"\0" * 200)
    return d


def _app(**cfg_kwargs):
    App = app_class()
    app = App.__new__(App)
    app.cfg = config.Config(**cfg_kwargs)
    app._video_msg_lock = threading.Lock()
    app._meeting_video_sender = None
    app._meeting_auto_transcriber = None
    app._meeting_plans = {}
    said = []
    app._notify = said.append
    return app, said


def _run(app, d):
    app._auto_transcribe(d)
    if app._meeting_auto_transcriber is not None:
        app._meeting_auto_transcriber.join(10)


def _fake_transcribe(calls, fail=None):
    def fake(session_dir, cfg, *a, **k):
        calls.append((pathlib.Path(session_dir), meeting.session_transcribing(session_dir)))
        if fail:
            raise RuntimeError(fail)
        (pathlib.Path(session_dir) / "transcript.json").write_text(json.dumps(
            {"backend": "deepgram", "text": "hello there",
             "segments": [{"start": 0, "end": 1, "text": "hello there", "speaker": "Speaker 1"}]}))
        return {}
    return fake


def _load_cfg(app):
    return patch.object(config.Config, "load", classmethod(lambda cls: app.cfg))


def test_setting_off_does_nothing(tmp_path):
    d = _session(tmp_path)
    app, said = _app(meeting_auto_transcribe=False)
    calls = []
    with patch.object(meeting, "transcribe_session", _fake_transcribe(calls)):
        _run(app, d)
    assert calls == [] and said == []


def test_no_audio_does_nothing(tmp_path):
    d = _session(tmp_path, with_audio=False)
    app, said = _app(meeting_auto_transcribe=True)
    calls = []
    with patch.object(meeting, "transcribe_session", _fake_transcribe(calls)):
        _run(app, d)
    assert calls == [] and said == []


def test_on_transcribes_once_marker_held_then_released_and_announces(tmp_path):
    d = _session(tmp_path)
    app, said = _app(meeting_auto_transcribe=True)
    calls = []
    with patch.object(meeting, "transcribe_session", _fake_transcribe(calls)), _load_cfg(app):
        _run(app, d)
    assert calls == [(d, True)]                      # busy-transcribe marker held during the run
    assert not meeting.session_busy(d)               # and released after
    assert said == ["\u2713 Meeting transcribed: meeting-20261109-101500"]


def test_notify_uses_the_meeting_title_when_there_is_one(tmp_path):
    d = _session(tmp_path)
    meeting.save_title(d, "Acme kickoff", "user")
    app, said = _app(meeting_auto_transcribe=True)
    with patch.object(meeting, "transcribe_session", _fake_transcribe([])), _load_cfg(app):
        _run(app, d)
    assert said == ["\u2713 Meeting transcribed: Acme kickoff"]


def test_failure_notifies_and_releases_marker(tmp_path):
    d = _session(tmp_path)
    app, said = _app(meeting_auto_transcribe=True)
    with patch.object(meeting, "transcribe_session", _fake_transcribe([], fail="no key")), _load_cfg(app):
        _run(app, d)
    assert said == ["Meeting transcription failed: RuntimeError: no key"[:80]]
    assert not meeting.session_busy(d)


def test_send_plan_on_writes_md_and_sends_it(tmp_path):
    d = _session(tmp_path)
    app, said = _app(meeting_auto_transcribe=True)
    app._meeting_plans[str(d)] = (True, "u@h:/in/")
    sent = []

    def fake_send(paths, destination, **_):
        sent.append(([pathlib.Path(p) for p in paths], destination,
                     pathlib.Path(paths[0]).read_text(encoding="utf-8"), meeting.session_busy(d)))
        return True, "sent"

    with patch.object(meeting, "transcribe_session", _fake_transcribe([])), _load_cfg(app), \
            patch.object(screenrec, "send", fake_send):
        _run(app, d)
    md = d / (d.name + "-transcript.md")
    assert md.is_file()
    assert sent[0][0] == [md] and sent[0][1] == "u@h:/in/"
    assert "hello there" in sent[0][2]
    assert sent[0][3] is True                        # session still held while sending
    assert not meeting.session_busy(d)
    assert said[-1] == "\u2713 Meeting transcript sent"


def test_send_failure_keeps_file_and_says_so(tmp_path):
    d = _session(tmp_path)
    app, said = _app(meeting_auto_transcribe=True)
    app._meeting_plans[str(d)] = (True, "u@h:/in/")
    with patch.object(meeting, "transcribe_session", _fake_transcribe([])), _load_cfg(app), \
            patch.object(screenrec, "send", lambda *a, **k: (False, "refused")):
        _run(app, d)
    assert (d / (d.name + "-transcript.md")).is_file()
    assert said[-1] == "Transcript kept locally \u2014 send failed: refused"


def test_send_plan_off_sends_nothing(tmp_path):
    d = _session(tmp_path)
    app, said = _app(meeting_auto_transcribe=True)
    app._meeting_plans[str(d)] = (False, "u@h:/in/")
    with patch.object(meeting, "transcribe_session", _fake_transcribe([])), _load_cfg(app), \
            patch.object(screenrec, "send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent"))):
        _run(app, d)
    assert not (d / (d.name + "-transcript.md")).exists()
    assert said == ["\u2713 Meeting transcribed: meeting-20261109-101500"]


def test_no_stored_plan_falls_back_to_current_config(tmp_path):
    d = _session(tmp_path)
    app, said = _app(meeting_auto_transcribe=True, meeting_video=True, meeting_video_send=True,
                     screenrec_destination="u@h:/in/")
    sent = []
    with patch.object(meeting, "transcribe_session", _fake_transcribe([])), _load_cfg(app), \
            patch.object(screenrec, "send", lambda p, dest, **k: (sent.append(dest), (True, "ok"))[1]):
        _run(app, d)
    assert sent == ["u@h:/in/"]


def test_setting_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.Config.load()
    assert cfg.meeting_auto_transcribe is False
    cfg.meeting_auto_transcribe = True
    cfg.save()
    assert config.Config.load().meeting_auto_transcribe is True


def test_transcript_is_not_sent_when_video_was_off(tmp_path):
    # The send setting is consent for the meeting video; no video, no lone transcript upload.
    d = _session(tmp_path)
    app, said = _app(meeting_auto_transcribe=True, meeting_video=False, meeting_video_send=True,
                     screenrec_destination="u@h:/in/")
    sent = []
    with patch.object(meeting, "transcribe_session", _fake_transcribe([])), _load_cfg(app), \
            patch.object(screenrec, "send", lambda p, dest, **k: (sent.append(dest), (True, "ok"))[1]):
        _run(app, d)
    assert sent == []
