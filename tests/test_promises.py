"""Your promises: regex candidates, optional AI filter, saved file, export, never fails a transcription."""

import json
import pathlib
import shutil
import sys
import types
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from wisprlite import export, meeting, promises

KICKOFF = pathlib.Path(__file__).resolve().parent / "fixtures" / "live_calls" / "kickoff"


def _segments():
    return json.loads((KICKOFF / "transcript.json").read_text())["segments"]


def _labels():
    return json.loads((KICKOFF / "labels.json").read_text())


def _in(sentence, labelled):
    """A found sentence belongs to a labelled You line when it is part of that line."""
    return any(sentence in line for line in labelled)


def test_regex_on_the_labelled_call_has_precision_and_recall_of_at_least_80_percent():
    found = promises.find_promises(_segments())
    real = _labels()["promises"]
    tp = [p for p in found if _in(p["text"], real)]
    recalled = [line for line in real if any(p["text"] in line for p in found)]
    precision, recall = len(tp) / len(found), len(recalled) / len(real)
    assert precision >= 0.8, (precision, [p["text"] for p in found if not _in(p["text"], real)])
    assert recall >= 0.8, (recall, [line for line in real if line not in recalled])


def test_due_phrases_are_extracted_for_the_two_with_a_day():
    found = promises.find_promises(_segments())
    labelled = _labels()
    for line, due in labelled["due"].items():
        match = [p for p in found if p["text"] in line]
        assert match and match[0]["due"].lower() == due.lower(), line
    others = [p for p in found if _in(p["text"], labelled["promises"])
              and not _in(p["text"], labelled["due"])]
    assert others and all(p["due"] == "" for p in others)


def test_a_condition_or_an_in_call_action_is_not_a_promise_on_unseen_sentences():
    # Each sentence matches the bare candidate regex, so only the reject rule removes it.
    for sentence in ("Unless legal objects, we will send the draft on Monday.",
                     "When the contract arrives I'll send it over.",
                     "In case it fails we'll call support.",
                     "Let me open the pricing page and check it with you.",
                     "Let me quickly pull up the plan and share it."):
        assert promises._PROMISE.search(sentence), sentence
        assert promises.find_promises([{"t": 1, "speaker": "You", "text": sentence}]) == [], sentence
    assert promises.find_promises([{"t": 1, "speaker": "You", "text": "Let me send you the plan."}])


def test_only_you_lines_count_and_t_is_the_segment_time():
    segs = [{"t": 12.0, "speaker": "Them 1", "text": "I'll send you the contract tomorrow."},
            {"t": 30.5, "speaker": "You", "text": "Fine. I'll send the notes tomorrow. Thanks all."}]
    out = promises.find_promises(segs)
    assert out == [{"t": 30.5, "text": "I'll send the notes tomorrow.", "due": "tomorrow"}]


def test_due_phrase_shapes():
    for sentence, due in [("I'll email it by Monday.", "by Monday"),
                          ("We will send it end of day.", "end of day"),
                          ("I'll follow up next week.", "next week"),
                          ("I'll call you today.", "today"),
                          ("I'll send it on 14 March.", "14 March"),
                          ("I'll send it.", "")]:
        out = promises.find_promises([{"t": 1, "speaker": "You", "text": sentence}])
        assert out and out[0]["due"].lower() == due.lower(), sentence


def test_the_ai_filter_keeps_only_the_returned_indexes():
    cands = promises.find_promises(_segments())
    reply = json.dumps([0, 2])
    kept = promises.ai_filter(cands, lambda messages: reply)
    assert kept == [cands[0], cands[2]]


def test_an_ai_failure_keeps_the_regex_list():
    cands = promises.find_promises(_segments())

    def boom(messages):
        raise RuntimeError("no network")

    assert promises.ai_filter(cands, boom) == cands
    assert promises.ai_filter(cands, lambda m: "not json") == cands
    assert promises.ai_filter(cands, lambda m: "[true]") == cands  # a bool is not an index


def test_build_saves_promises_json_and_uses_the_ai_only_when_the_provider_is_ready(tmp_path):
    cfg = types.SimpleNamespace(cleanup_provider="openai", cleanup_model="m")
    segs = _segments()
    with patch("wisprlite.cleanup.provider_ready", return_value=False):
        out = promises.build_promises(tmp_path, segs, cfg)
    assert out and json.loads((tmp_path / "promises.json").read_text()) == out
    assert promises.load_promises(tmp_path) == out
    called = []
    with patch("wisprlite.cleanup.provider_ready", return_value=True):
        promises.build_promises(tmp_path, segs, cfg, completion=lambda m: called.append(m) or "[0]")
        assert called == []                      # use_ai defaults to off
        promises.build_promises(tmp_path, segs, cfg, completion=lambda m: called.append(m) or "[0]",
                                use_ai=True)
    assert len(called) == 1 and len(promises.load_promises(tmp_path)) == 1


def test_no_promises_removes_a_stale_file(tmp_path):
    (tmp_path / "promises.json").write_text("[]")
    cfg = types.SimpleNamespace(cleanup_provider="openai", cleanup_model="m")
    with patch("wisprlite.cleanup.provider_ready", return_value=False):
        assert promises.build_promises(tmp_path, [{"t": 1, "speaker": "You", "text": "hello"}], cfg) == []
    assert promises.load_promises(tmp_path) == []


def test_a_crash_in_promises_never_fails_the_transcription(tmp_path):
    from test_meeting_names import _transcribe
    from test_meeting_merge import write_session

    write_session(tmp_path)
    with patch("wisprlite.promises.build_promises", side_effect=RuntimeError("boom")):
        transcript = _transcribe(tmp_path)
    assert transcript["segments"]
    assert json.loads((tmp_path / "meta.json").read_text())["status"] == "transcribed"


def test_ai_sees_your_lines_only_for_deepgram_or_when_the_box_is_ticked(tmp_path):
    for backend, ticked, expected in (("deepgram", False, True), ("local", False, False),
                                      ("local", True, True)):
        cfg = types.SimpleNamespace(cleanup_provider="openai", cleanup_model="m",
                                    meeting_auto_name=ticked)
        with patch("wisprlite.promises.build_promises") as build:
            meeting._auto_promises(tmp_path, [], cfg, backend)
        assert build.call_args.kwargs["use_ai"] is expected, (backend, ticked)


def test_transcription_writes_promises_json(tmp_path):
    from test_meeting_names import _transcribe
    from test_meeting_merge import write_session

    write_session(tmp_path)
    with patch("wisprlite.promises.build_promises") as build:
        _transcribe(tmp_path)
    assert build.call_count == 1


def test_export_has_a_your_promises_section_only_when_there_are_any():
    session = {"title": "T", "transcript": "x",
               "promises": [{"t": 75, "text": "I'll send the notes.", "due": "tomorrow"},
                            {"t": 130, "text": "We will call them.", "due": ""}]}
    md = export.build_markdown(session)
    assert "## Your promises" in md
    assert "[1:15] I'll send the notes. (tomorrow)" in md
    assert "[2:10] We will call them." in md and "We will call them. (" not in md
    assert "Your promises" not in export.build_markdown({"title": "T", "transcript": "x"})
    assert "promises" in export.REQUIRED_KEYS


# ---- Summary tab block -------------------------------------------------------

def _tab_with(tmp_path, monkeypatch, items, step):
    import pytest as _pytest
    from uistub import have_display, install_platform_stubs

    install_platform_stubs()
    if not have_display():
        _pytest.skip("no X display; run under xvfb-run")
    import tests.test_after_call_prompt as tacp

    real = tacp._meeting

    def with_promises(base, transcribed):
        folder = real(base, transcribed)
        seg = {"start": 3, "end": 6, "speaker": "You", "text": "I'll send the notes tomorrow."}
        data = json.loads((folder / "transcript.json").read_text())
        data["segments"].append(seg)
        (folder / "transcript.json").write_text(json.dumps(data))
        if items:
            (folder / "promises.json").write_text(json.dumps(items))
        return folder

    monkeypatch.setattr(tacp, "_meeting", with_promises)
    out = {}

    def until(root):
        pill = next((w for w in tacp._walk(root) if w.winfo_class() == "Button"
                     and str(w.cget("text")) == "Summary"), None)
        if pill is None:
            return False
        if "done" not in out:
            pill.invoke()
            root.update()
            out["before"] = [str(w.cget("text")) for w in tacp._walk(root)
                             if w.winfo_class() == "Label" and w.winfo_ismapped()]
            step(root, out, tacp._walk)
            out["done"] = True
        return True

    tacp._open_tab(tmp_path, monkeypatch, transcribed=True, knowledge_dir="", auto=False,
                   pump_until=until)
    return out


def test_summary_tab_lists_the_promises_and_a_row_jumps_to_the_transcript(tmp_path, monkeypatch):
    items = [{"t": 75, "text": "I'll send the notes tomorrow.", "due": "tomorrow"}]

    def step(root, out, walk):
        row = next(w for w in walk(root) if w.winfo_class() == "Label"
                   and str(w.cget("text")).startswith("1:15"))
        out["row"] = str(row.cget("text"))
        row.event_generate("<Button-1>")
        root.update()
        texts = [w for w in walk(root) if w.winfo_class() == "Text" and w.winfo_ismapped()]
        out["transcript_shown"] = any("I'll send the notes" in w.get("1.0", "end") for w in texts)
        out["row_hidden"] = not row.winfo_ismapped()

    out = _tab_with(tmp_path, monkeypatch, items, step)
    assert "Your promises" in out["before"]
    assert out["row"] == "1:15   I'll send the notes tomorrow.  ·  tomorrow"
    assert out["transcript_shown"] and out["row_hidden"]


def test_summary_tab_hides_the_promises_block_when_there_are_none(tmp_path, monkeypatch):
    out = _tab_with(tmp_path, monkeypatch, [], lambda root, out, walk: None)
    assert "Your promises" not in out["before"]
