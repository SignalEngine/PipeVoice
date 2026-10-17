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


def run(tmp_path, answer, jev=lambda q, quote: None, segments=None):
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


def test_them_only_reach_question_prompt():
    seen = []
    def call(messages):
        seen.append(messages[1]["content"])
        return "[]"
    k.extract_questions([{"t": 1, "speaker": "You", "text": "my secret pitch"},
                         {"t": 2, "speaker": "Them 2", "text": "what does it cost?"}], call)
    assert "my secret pitch" not in seen[0] and "what does it cost?" in seen[0]


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
            jev=lambda q, quote: 0.2)
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
