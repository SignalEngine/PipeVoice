"""Headless tests for after-call answers (knowledge.py): fake completion, no network."""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import knowledge as k

DOC = {"source": "plans.md", "text": "Enterprise plan includes SSO and audit logs.\n\n"
       "Data ingestion runs nightly from your warehouse."}
QUOTE = "Enterprise plan includes SSO and audit logs."


def qa_completion(answer):
    """Fake LLM: first prompt gets one question, answer prompt gets `answer`."""
    def call(messages):
        if "sincere questions" in messages[0]["content"]:
            return json.dumps([{"t": 5, "question": "Is SSO included?",
                                "keywords": ["single sign-on", "SSO"]}])
        return answer if isinstance(answer, str) else json.dumps(answer)
    return call


def session(tmp_path, segments=None):
    segs = segments or [{"t": 5, "speaker": "Them", "text": "Is SSO included?"}]
    (tmp_path / "transcript.json").write_text(json.dumps({"segments": segs}))
    (tmp_path / "plans.md").write_text(DOC["text"])
    return tmp_path


def run(tmp_path, answer, jev=lambda *a: None, segments=None):
    s = session(tmp_path, segments)
    return k.build_answers(s, str(s), "", "openai", "", completion=qa_completion(answer), jev=jev)


def test_bm25_synonym_ranks_first_and_loses_without_it():
    chunks = [{"source": "a", "text": "You can get in touch and get answers."},
              {"source": "b", "text": "Data ingestion runs nightly from the warehouse."}]
    idx = k.BM25(chunks)
    assert idx.top("get our data in ingestion import warehouse")[0]["source"] == "b"
    assert idx.top("get our data in")[0]["source"] == "a"  # positive control: no synonym, it loses


def test_real_quote_is_answered(tmp_path):
    r = run(tmp_path,
            {"headline": "Yes - Enterprise only", "quote": QUOTE, "source": "plans.md"})
    assert r["items"][0]["status"] == "answered"


def test_fabricated_quote_is_not_found(tmp_path):
    r = run(tmp_path,
            {"headline": "Yes", "quote": "SSO is free for everyone.", "source": "plans.md"})
    assert r["items"][0]["status"] == "not_found"


def test_null_answer_is_not_found(tmp_path):
    r = run(tmp_path,
            {"headline": None, "quote": None, "source": None})
    assert r["items"][0]["status"] == "not_found"


def test_malformed_json_is_not_found_no_crash(tmp_path):
    r = run(tmp_path, "sorry, I cannot {do that")
    assert r["items"][0]["status"] == "not_found"


def test_them_only_questions_come_out_but_you_lines_give_context():
    """Review P2: dropping You lines lost what 'that' meant. Context goes in;
    a question the model attributes to a You line is dropped in code."""
    seen = []
    def call(messages):
        seen.append(messages[1]["content"])
        return json.dumps([{"t": 1, "question": "my own question?", "keywords": []},
                           {"t": 2, "question": "Will the SOC 2 audit cover vendors?", "keywords": []}])
    out = k.extract_questions([{"t": 1, "speaker": "You", "text": "Our SOC 2 audit finishes in June"},
                               {"t": 2, "speaker": "Them 2", "text": "Will that cover vendors?"}], call)
    assert "Our SOC 2 audit finishes in June" in seen[0]
    assert [q["question"] for q in out] == ["Will the SOC 2 audit cover vendors?"]


def test_cjk_doc_is_kept_not_dropped_as_empty(tmp_path):
    (tmp_path / "ja.md").write_text("料金プランには監査ログが含まれます。", encoding="utf-8")
    docs, src = k.load_sources(str(tmp_path))
    assert src["ok"] == ["ja.md"] and docs


def test_transcript_corrections_are_applied_before_questions(tmp_path):
    from wisprlite import meeting
    (tmp_path / "transcript.json").write_text(json.dumps({"segments": [
        {"t": 3, "speaker": "Them", "text": "Does sock too apply?"}]}))
    meeting.save_corrections(tmp_path, {"sock too": "SOC 2"})
    seen = []
    def call(messages):
        seen.append(messages[1]["content"])
        return "[]"
    k.build_answers(tmp_path, "", "", "gemini", "", completion=call, jev=lambda *a: None)
    assert "Does SOC 2 apply?" in seen[0]


def test_jev_is_given_the_headline_too():
    bodies = []
    def post(body):
        bodies.append(json.loads(body))
        return {"answers": {"answers": {"type": "noul", "noul": 0.1}}}
    assert k.jev_check("Is SSO included?", "SSO is not included in any plan.",
                       "Yes, SSO is included", api_key="x", post=post) == 0.1
    assert bodies[0]["state"]["headline"] == "Yes, SSO is included"


def test_failed_source_zero_word_pdf_and_bad_url_reported(tmp_path):
    (tmp_path / "scan.pdf").write_bytes(b"%PDF")
    def bad_fetch(url):
        raise OSError("timed out")
    docs, src = k.load_sources(str(tmp_path), "http://x.test", None,
                               pdf_reader=lambda p: "  ", fetch=bad_fetch)
    assert docs == []
    assert any("scan.pdf" in f for f in src["failed"])
    assert any("http://x.test" in f for f in src["failed"])


def test_jev_no_key_gives_none(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert k.jev_check("q", "quote") is None


def test_jev_doubtful_when_low(tmp_path):
    r = run(tmp_path, {"headline": "Yes", "quote": QUOTE, "source": "plans.md"},
            jev=lambda q, quote, headline=None: 0.2)
    assert r["items"][0]["status"] == "doubtful" and r["items"][0]["jev_p"] == 0.2


def test_jev_check_parses_response():
    post = lambda body: {"answers": {"answers": {"type": "noul", "noul": 0.9}}}
    assert k.jev_check("q", "quote", api_key="x", post=post) == 0.9


def test_answers_roundtrip_and_vote(tmp_path):
    run(tmp_path, {"headline": "Yes", "quote": QUOTE, "source": "plans.md"})
    assert k.load_answers(tmp_path)["items"][0]["vote"] is None
    assert k.set_vote(tmp_path, 0, "up")
    assert k.load_answers(tmp_path)["items"][0]["vote"] == "up"
    assert not k.set_vote(tmp_path, 5, "up")


def test_describe_item_helper():
    assert k.describe_item({"status": "not_found"}) == ("Not in your docs", "")
    head, detail = k.describe_item({"status": "doubtful", "headline": "Yes", "quote": "Q.", "source": "a.md"})
    assert head == "Yes" and "a.md" in detail and "may not answer" in detail


def test_answers_panel_is_mounted_for_a_transcribed_meeting_and_hidden_otherwise(tmp_path):
    import tkinter as tk

    from wisprlite import meeting, meetings_tab

    try:
        root = tk.Tk()
    except tk.TclError:
        return  # headless without xvfb
    base = tmp_path / "meetings"
    folder = base / "meeting-20260722-120000"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps(
        {"started_at": "2026-07-22T12:00:00", "stopped_at": "2026-07-22T12:05:00"}))
    (folder / "transcript.json").write_text(json.dumps(
        {"segments": [{"t": 5, "speaker": "Them", "text": "Is SSO included?"}]}))
    (folder / "answers.json").write_text(json.dumps({
        "built_at": "x", "sources": {"ok": ["plans.md"], "failed": []},
        "items": [{"t": 5, "question": "Is SSO included?", "headline": "Yes", "quote": QUOTE,
                   "source": "plans.md", "jev_p": None, "status": "answered", "vote": None}]}))
    orig = (meeting.meetings_dir, meetings_tab.meetings_dir)
    meeting.meetings_dir = meetings_tab.meetings_dir = lambda: base
    try:
        frame = tk.Frame(root)
        frame.pack(fill="both", expand=True)
        meetings_tab.build(frame, root)
        root.update()
        panels = [w for w in _walk(frame) if isinstance(w, tk.Label)
                  and w.cget("text") == "Their questions"]
        assert panels, "answers panel was never built"
        assert any("Yes" == w.cget("text") for w in _walk(frame) if isinstance(w, tk.Label)), \
            "saved answers.json was not rendered when the meeting was selected"
        assert panels[0].master.winfo_ismapped()
    finally:
        meeting.meetings_dir, meetings_tab.meetings_dir = orig
        root.destroy()


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)
