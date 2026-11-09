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
        cleanup_provider="ollama", cleanup_model="m", bookmark_phrases="", meeting_auto_name=True,
    )


def _transcribe(session, config=None):
    with patch("wisprlite.engines.transcribe.transcribe_file",
               side_effect=lambda *a, **k: result({"start": 0.0, "text": "hello"})):
        return meeting.transcribe_session(session, config or cfg(), backend="local")


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

    def fake(messages, provider, model, timeout=None):
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


def test_auto_name_off_never_sends_the_transcript(tmp_path):
    write_session(tmp_path)
    off = cfg()
    off.meeting_auto_name = False       # any provider, including local ollama
    with patch("wisprlite.cleanup.provider_ready", return_value=True), \
         patch("wisprlite.summarise.suggest_title", return_value="X") as suggest:
        _transcribe(tmp_path, off)
    suggest.assert_not_called()
    assert meeting.load_title(tmp_path) == ""


def test_auto_name_on_names_the_meeting_even_with_ai_cleanup_off(tmp_path):
    write_session(tmp_path)
    on = cfg()
    on.ai_cleanup, on.cleanup_provider = False, "gemini"
    with patch("wisprlite.cleanup.provider_ready", return_value=True), \
         patch("wisprlite.summarise.suggest_title", return_value="Named") as suggest:
        _transcribe(tmp_path, on)
    suggest.assert_called_once()
    assert meeting.load_title(tmp_path) == "Named"


def test_save_title_if_empty_never_overwrites(tmp_path):
    assert meeting.save_title_if_empty(tmp_path, " First ", "auto") is True
    assert meeting.load_title(tmp_path) == "First"
    assert meeting.save_title_if_empty(tmp_path, "Second", "auto") is False
    assert meeting.load_title(tmp_path) == "First"
    assert meeting.save_title_if_empty(tmp_path, "   ", "auto") is False


def test_a_title_saved_during_the_model_call_wins_in_auto_naming(tmp_path):
    write_session(tmp_path)

    def slow(*_a, **_k):
        meeting.save_title(tmp_path, "Typed meanwhile", "user")
        return "Robot"

    with patch("wisprlite.cleanup.provider_ready", return_value=True), \
         patch("wisprlite.summarise.suggest_title", side_effect=slow):
        _transcribe(tmp_path)
    assert meeting.load_title(tmp_path) == "Typed meanwhile"


def test_meeting_auto_name_setting_round_trips(tmp_path, monkeypatch):
    from wisprlite import config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg_ = config.Config.load()
    assert cfg_.meeting_auto_name is True
    cfg_.meeting_auto_name = False
    cfg_.save()
    assert config.Config.load().meeting_auto_name is False


def test_suggest_title_has_a_20_second_timeout():
    seen = {}

    def fake_chat(messages, provider, model, **kwargs):
        seen.update(kwargs)
        return "Title"

    with patch.object(summarise, "chat_completion", side_effect=fake_chat):
        summarise.suggest_title(SEGS, "ollama", "m")
    assert seen["timeout"] == 20


def test_search_finds_a_named_meeting_with_no_transcript(tmp_path):
    path = tmp_path / "meeting-20260727-130000"
    path.mkdir()
    (path / "meta.json").write_text(json.dumps({"started_at": "2026-07-27T13:00:00+00:00"}))
    meeting.save_title(path, "Chess ICT kickoff", "user")
    hits = meetings_tab.search_sessions("chess ict", [{"path": path}])
    assert str(path) in hits
    assert meetings_tab.search_sessions("nothing", [{"path": path}]) == {}


def test_signature_changes_when_a_meeting_is_renamed(tmp_path):
    path = _meeting_dir(tmp_path)
    before = meetings_tab.meetings_signature(tmp_path)
    meeting.save_title(path, "Chess ICT kickoff", "user")
    after = meetings_tab.meetings_signature(tmp_path)
    assert after != before
    meeting.save_title(path, "Chess ICT kickoff 2", "user")
    assert meetings_tab.meetings_signature(tmp_path) != after
