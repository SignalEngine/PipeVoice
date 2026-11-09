"""Meeting names: title.json overlay, auto name after transcription, suggest_title."""

import json
import pathlib
import sys
import types
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from wisprlite import meeting, meetings_tab, summarise
from test_meeting_merge import result, write_session


def cfg():
    return types.SimpleNamespace(
        language="", deepgram_model="nova-3", local_model_size="base.en",
        local_device="cpu", local_compute_type="int8",
        cleanup_provider="ollama", cleanup_model="m", bookmark_phrases="",
    )


def _transcribe(session):
    with patch("wisprlite.engines.transcribe.transcribe_file",
               side_effect=lambda *a, **k: result({"start": 0.0, "text": "hello"})):
        return meeting.transcribe_session(session, cfg(), backend="local")


def test_title_round_trip_trim_cap_and_delete(tmp_path):
    assert meeting.load_title(tmp_path) == ""
    meeting.save_title(tmp_path, "  Chess   ICT \n kickoff ", "user")
    assert meeting.load_title(tmp_path) == "Chess ICT kickoff"
    assert json.loads((tmp_path / "title.json").read_text())["source"] == "user"
    meeting.save_title(tmp_path, "x" * 200, "auto")
    assert len(meeting.load_title(tmp_path)) == 80
    meeting.save_title(tmp_path, "   ", "user")
    assert not (tmp_path / "title.json").exists()
    meeting.save_title(tmp_path, "", "user")   # deleting twice is fine


def test_transcription_writes_an_auto_title(tmp_path):
    write_session(tmp_path)
    with patch("wisprlite.cleanup.provider_ready", return_value=True), \
         patch("wisprlite.summarise.suggest_title", return_value="Acme pricing call"):
        _transcribe(tmp_path)
    assert meeting.load_title(tmp_path) == "Acme pricing call"
    assert json.loads((tmp_path / "title.json").read_text())["source"] == "auto"


def test_transcription_never_overwrites_an_existing_title(tmp_path):
    write_session(tmp_path)
    meeting.save_title(tmp_path, "My own name", "user")
    with patch("wisprlite.cleanup.provider_ready", return_value=True), \
         patch("wisprlite.summarise.suggest_title", return_value="Robot name") as suggest:
        _transcribe(tmp_path)
    assert meeting.load_title(tmp_path) == "My own name"
    suggest.assert_not_called()


def test_a_naming_failure_does_not_fail_the_transcription(tmp_path):
    write_session(tmp_path)
    with patch("wisprlite.cleanup.provider_ready", return_value=True), \
         patch("wisprlite.summarise.suggest_title", side_effect=RuntimeError("boom")):
        transcript = _transcribe(tmp_path)
    assert transcript["segments"]
    assert meeting.load_title(tmp_path) == ""
    assert json.loads((tmp_path / "meta.json").read_text())["status"] == "transcribed"


def test_no_provider_means_no_title_call(tmp_path):
    write_session(tmp_path)
    with patch("wisprlite.cleanup.provider_ready", return_value=False), \
         patch("wisprlite.summarise.suggest_title") as suggest:
        _transcribe(tmp_path)
    suggest.assert_not_called()


SEGS = [{"t": 0, "speaker": "You", "text": "Hi, about the Chess kickoff"}]


def _suggest(reply):
    with patch.object(summarise, "_complete", return_value=reply):
        return summarise.suggest_title(SEGS, "ollama", "m")


def test_suggest_title_strips_quotes_and_full_stop():
    assert _suggest('"Chess ICT kickoff."') == "Chess ICT kickoff"
    assert _suggest("“Acme pricing call”") == "Acme pricing call"


def test_suggest_title_returns_empty_for_garbage():
    assert _suggest("") == ""
    assert _suggest('""') == ""
    assert _suggest(" ".join(["word"] * 40)) == ""
    assert summarise.suggest_title([], "ollama", "m") == ""


def test_suggest_title_sends_a_bounded_transcript():
    seen = {}

    def fake(messages, provider, model):
        seen["user"] = messages[1]["content"]
        return "Title"

    long = [{"t": i, "speaker": "Them", "text": "word " * 50} for i in range(200)]
    with patch.object(summarise, "_complete", side_effect=fake):
        summarise.suggest_title(long, "ollama", "m")
    assert len(seen["user"]) < 3100
    assert "Them: word" in seen["user"]


def _meeting_dir(base):
    path = base / "meeting-20260727-120000"
    path.mkdir()
    (path / "meta.json").write_text(json.dumps({
        "started_at": "2026-07-27T12:00:00+00:00",
        "stopped_at": "2026-07-27T12:10:00+00:00", "duration_seconds": 600,
        "status": "transcribed"}))
    (path / "transcript.json").write_text(json.dumps({
        "text": "", "segments": [{"t": 0, "speaker": "Them", "text": "pricing talk"}]}))
    return path


def test_list_export_and_search_use_the_title(tmp_path):
    path = _meeting_dir(tmp_path)
    assert meetings_tab.list_sessions(tmp_path)[0]["title"] == ""
    date_title = meetings_tab.gather_session_export_data(path)["title"]
    assert "Chess" not in date_title
    meeting.save_title(path, "Chess ICT kickoff", "user")
    assert meetings_tab.list_sessions(tmp_path)[0]["title"] == "Chess ICT kickoff"
    assert meetings_tab.gather_session_export_data(path)["title"] == "Chess ICT kickoff"
    assert "Chess ICT kickoff" in meetings_tab.session_search_text(path)
