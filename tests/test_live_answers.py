"""Live answers: question gate, grounding, dedupe, latest-wins, and the app wiring."""

import json
import pathlib
import sys
import threading
import time
import types
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pytest

from uistub import have_display, install_platform_stubs

install_platform_stubs()

from wisprlite import live_answers

QUOTE = "Single sign-on via SAML is included on the Team plan."


def _docs(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "faq.md").write_text(
        f"# FAQ\n\n{QUOTE} Pricing starts at forty dollars per seat.\n\n"
        "Our office is closed on public holidays.", encoding="utf-8")
    return folder


def _reply(quote=QUOTE):
    return json.dumps({"headline": "SSO is on the Team plan", "quote": quote, "source": "faq.md"})


def _answerer(tmp_path, completion, cards, **kw):
    a = live_answers.LiveAnswerer(str(_docs(tmp_path)), "", None, completion,
                                  lambda *c: cards.append(c), **kw)
    a._build()                       # synchronous index
    return a


def _drain(a, timeout=3):
    """Run the worker inline for whatever is queued."""
    job, a._pending = a._pending, None
    if job:
        a._answer(*job)


# ---- looks_like_question ----------------------------------------------------

def test_looks_like_question():
    assert live_answers.looks_like_question("Do you support SSO")
    assert live_answers.looks_like_question("pricing for teams?")
    assert not live_answers.looks_like_question("you know what I mean")
    assert not live_answers.looks_like_question("why?")


# ---- answerer ---------------------------------------------------------------

def test_you_lines_never_call_completion(tmp_path):
    calls, cards = [], []
    a = _answerer(tmp_path, lambda m: calls.append(m) or _reply(), cards)
    a.on_line("You", "do you support single sign-on SAML?")
    assert a._pending is None
    assert calls == []


def test_low_bm25_score_skips_the_llm(tmp_path):
    calls, cards = [], []
    a = _answerer(tmp_path, lambda m: calls.append(m) or _reply(), cards)
    a.on_line("Them", "did anybody watch the football last night?")
    _drain(a)
    assert calls == [] and cards == []


def test_good_doc_gives_one_card_and_a_repeat_is_suppressed(tmp_path):
    calls, cards = [], []
    a = _answerer(tmp_path, lambda m: calls.append(m) or _reply(), cards,
                  log_path=tmp_path / "live.json")
    a.on_line("Them", "do you support single sign-on SAML?")
    _drain(a)
    assert cards == [("SSO is on the Team plan", QUOTE, "faq.md")]
    a.on_line("Them", "Do you support single sign-on SAML?")
    _drain(a)
    assert len(cards) == 1
    log = json.loads((tmp_path / "live.json").read_text())
    assert [e["shown"] for e in log] == [True, False]


def test_same_quote_for_a_reworded_question_is_not_shown_twice(tmp_path):
    cards = []
    a = _answerer(tmp_path, lambda m: _reply(), cards)
    a.on_line("Them", "do you support single sign-on SAML?")
    _drain(a)
    a.on_line("Them", "is SAML single sign-on included in the plan?")
    _drain(a)
    assert len(cards) == 1


def test_a_quote_not_in_the_doc_gives_no_card(tmp_path):
    cards = []
    a = _answerer(tmp_path, lambda m: _reply("We support every identity provider ever made."), cards)
    a.on_line("Them", "do you support single sign-on SAML?")
    _drain(a)
    assert cards == []


def test_past_the_deadline_is_logged_not_shown(tmp_path):
    cards, now = [], [0.0]
    a = _answerer(tmp_path, lambda m: _reply(), cards, clock=lambda: now[0],
                  deadline=8.0, log_path=tmp_path / "live.json")
    a.on_line("Them", "do you support single sign-on SAML?")
    now[0] = 20.0
    _drain(a)
    assert cards == []
    log = json.loads((tmp_path / "live.json").read_text())
    assert len(log) == 1 and log[0]["shown"] is False and log[0]["quote"] == QUOTE


def test_latest_question_wins_while_the_worker_is_busy(tmp_path):
    gate, asked, cards = threading.Event(), [], []

    def completion(messages):
        asked.append(messages[-1]["content"])
        gate.wait(3)
        return _reply()

    a = _answerer(tmp_path, completion, cards)
    a.start()
    try:
        a.on_line("Them", "do you support single sign-on SAML?")        # worker takes it, blocks
        deadline = time.time() + 3
        while not asked and time.time() < deadline:
            time.sleep(0.01)
        a.on_line("Them", "what does single sign-on cost per seat?")     # queued
        a.on_line("Them", "which plan includes SAML sign-on today?")      # replaces it
        gate.set()
        deadline = time.time() + 3
        while len(asked) < 2 and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)
    finally:
        a.stop()
    assert len(asked) == 2
    assert "which plan includes" in asked[1] and not any("cost per seat" in q for q in asked)


def test_lines_before_the_index_is_ready_are_ignored(tmp_path):
    a = live_answers.LiveAnswerer(str(_docs(tmp_path)), "", None, lambda m: _reply(), lambda *c: None)
    a.on_line("Them", "do you support single sign-on SAML?")
    assert a._pending is None


# ---- app wiring -------------------------------------------------------------

def _app(tmp_path, monkeypatch, **cfg_kw):
    from wisprlite import app as app_mod, config

    monkeypatch.setattr(app_mod, "meetings_dir", lambda: tmp_path)
    a = app_mod.App.__new__(app_mod.App)
    a.cfg = config.Config(engine="deepgram", **cfg_kw)
    a._meeting = types.SimpleNamespace(focus_session=None, session_dir=None)
    a.overlay = mock.Mock()
    return a


def _capture_session(monkeypatch):
    from wisprlite import focus

    made = {}

    class Fake:
        def __init__(self, **kw):
            made.update(kw)
            self.started = True

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(focus, "FocusSession", Fake)
    return made


def test_answers_docs_deepgram_start_a_them_only_session(tmp_path, monkeypatch):
    a = _app(tmp_path, monkeypatch, live_answers=True, pipefocus=False,
             knowledge_dir=str(tmp_path))
    made = _capture_session(monkeypatch)
    a._start_pipefocus()
    assert a._meeting.focus_session is not None
    assert made["them_only"] is True and made["completion"] is None
    assert made["on_line"] == a._on_live_line


def test_pipefocus_alongside_answers_shares_the_one_stream(tmp_path, monkeypatch):
    a = _app(tmp_path, monkeypatch, live_answers=True, pipefocus=True,
             knowledge_dir=str(tmp_path))
    made = _capture_session(monkeypatch)
    a._start_pipefocus()
    assert made["them_only"] is True and made["completion"] is not None


def test_answers_off_and_pipefocus_off_means_no_session(tmp_path, monkeypatch):
    a = _app(tmp_path, monkeypatch, live_answers=False, pipefocus=False,
             knowledge_dir=str(tmp_path))
    made = _capture_session(monkeypatch)
    a._start_pipefocus()
    assert a._meeting.focus_session is None and made == {}


def test_answers_without_any_docs_or_notes_means_no_session(tmp_path, monkeypatch):
    a = _app(tmp_path, monkeypatch, live_answers=True, pipefocus=False)
    made = _capture_session(monkeypatch)
    a._start_pipefocus()
    assert a._meeting.focus_session is None and made == {}


def test_other_engines_never_start_a_session(tmp_path, monkeypatch):
    a = _app(tmp_path, monkeypatch, live_answers=True, knowledge_dir=str(tmp_path))
    a.cfg.engine = "openai"
    made = _capture_session(monkeypatch)
    a._start_pipefocus()
    assert a._meeting.focus_session is None and made == {}


def test_next_call_notes_move_into_the_session(tmp_path, monkeypatch):
    session = tmp_path / "meeting-1"
    session.mkdir()
    (tmp_path / "next_call_notes.md").write_text("Call with Acme about SSO", encoding="utf-8")
    a = _app(tmp_path, monkeypatch, live_answers=True, knowledge_dir=str(tmp_path))
    a._meeting.session_dir = session
    a._answers_on = False
    a._start_live_answers()
    assert (session / "call_notes.md").read_text(encoding="utf-8") == "Call with Acme about SSO"
    assert not (tmp_path / "next_call_notes.md").exists()


def test_start_builds_and_stop_stops_the_answerer(tmp_path, monkeypatch):
    session = tmp_path / "meeting-1"
    session.mkdir()
    a = _app(tmp_path, monkeypatch, live_answers=True, knowledge_dir=str(tmp_path))
    a._meeting.session_dir = session
    a._meeting.focus_session = mock.Mock()
    a._answers_on = True
    made = {}

    class FakeAnswerer:
        def __init__(self, *args, **kw):
            made["args"], made["kw"] = args, kw
            made["started"] = False
            made["stopped"] = False
            made["self"] = self

        def start(self):
            made["started"] = True

        def stop(self):
            made["stopped"] = True

    monkeypatch.setattr(live_answers, "LiveAnswerer", FakeAnswerer)
    a._start_live_answers()
    assert made["started"] and a._live_answers is made["self"]
    assert made["kw"]["log_path"] == session / "live_answers.json"
    a._stop_pipefocus()
    assert made["stopped"] and a._live_answers is None


def test_a_card_goes_to_the_overlay(tmp_path, monkeypatch):
    a = _app(tmp_path, monkeypatch)
    a._show_answer_card("h", "q", "s.md")
    a.overlay.show_answer_card.assert_called_once_with("h", "q", "s.md")


# ---- overlay ----------------------------------------------------------------

def test_show_answer_card_queues_the_tuple_and_the_loop_routes_it():
    import inspect
    import queue

    from wisprlite import overlay
    from wisprlite.overlay import Overlay

    ov = Overlay.__new__(Overlay)
    ov._q = queue.Queue()
    ov.show_answer_card("Head", "The quote", "faq.md")
    kind, head, rest = ov._q.get_nowait()
    assert (kind, head) == ("answer_card", "Head")
    assert rest.split(overlay.CARD_SEP) == ["The quote", "faq.md"]
    # routed next to focus_tip in the drain loop
    assert 'kind == "answer_card"' in inspect.getsource(Overlay.start) or \
        'kind == "answer_card"' in inspect.getsource(overlay)
    assert "dismiss_after=20000" in inspect.getsource(Overlay._render_answer_card)
    assert "- 190 - 140" in inspect.getsource(Overlay._render_answer_card)


def test_answer_card_renders_on_a_real_display():
    if not have_display():
        pytest.skip("no X display; run under xvfb-run")
    import tkinter as tk

    from wisprlite.overlay import Overlay

    root = tk.Tk()
    try:
        canvas = tk.Canvas(root)
        Overlay.__new__(Overlay)._render_answer_card(canvas, "Head", "The quote", "faq.md")
        root.update()
        tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        assert len(tops) == 1
        texts = [w.cget("text") for w in _walk(tops[0]) if w.winfo_class() == "Label"]
        assert "Head" in texts and "faq.md" in texts and "“The quote”" in texts
    finally:
        root.destroy()


def _walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)


# ---- Meetings tab: prep panel + settings card -------------------------------

def _prep(root):
    label = next((w for w in _walk(root) if w.winfo_class() == "Label"
                  and str(w.cget("text")) == "Before your next call"), None)
    return label.master if label is not None else None


def _labels(root):
    return [str(w.cget("text")) for w in _walk(root) if w.winfo_class() == "Label"]


def _tab(tmp_path, monkeypatch, *, folder="", pump_until):
    from test_after_call_prompt import _open_tab
    from wisprlite import config

    # CONFIG_PATH is fixed at import time, so APPDATA alone would write the real file.
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    return _open_tab(tmp_path, monkeypatch, transcribed=True, knowledge_dir=folder,
                     auto=False, pump_until=pump_until)


def test_prep_panel_says_no_docs_yet_without_a_folder(tmp_path, monkeypatch):
    got = {}

    def until(root):
        prep = _prep(root)
        if prep is not None:
            got["labels"] = _labels(prep)
            got["buttons"] = [str(w.cget("text")) for w in _walk(prep) if w.winfo_class() == "TButton"]
        return prep is not None

    _tab(tmp_path, monkeypatch, pump_until=until)
    assert "No docs yet" in got["labels"]
    assert "Choose docs folder\u2026" in got["buttons"]


def test_prep_panel_shows_the_folder_and_file_count(tmp_path, monkeypatch):
    docs = _docs(tmp_path)
    (docs / "more.txt").write_text("x", encoding="utf-8")
    (docs / "skip.docx").write_text("x", encoding="utf-8")
    got = {}

    def until(root):
        prep = _prep(root)
        if prep is not None:
            got["labels"] = _labels(prep)
            got["buttons"] = [str(w.cget("text")) for w in _walk(prep) if w.winfo_class() == "TButton"]
        return prep is not None

    _tab(tmp_path, monkeypatch, folder=str(docs), pump_until=until)
    assert f"Docs: {docs} \u00b7 2 files" in got["labels"]
    assert "Change\u2026" in got["buttons"]


def test_ask_saves_the_notes_and_shows_the_headline(tmp_path, monkeypatch):
    from wisprlite import knowledge

    docs = _docs(tmp_path)
    monkeypatch.setattr(knowledge, "_default_completion", lambda p, m: (lambda msgs: ""))
    monkeypatch.setattr(knowledge, "answer_question", lambda q, i, d, c: {
        "headline": "SSO is on the Team plan", "quote": QUOTE, "source": "faq.md"})
    state = {}

    def until(root):
        prep = _prep(root)
        if prep is None:
            return False
        if "asked" not in state:
            notes = next(w for w in _walk(prep) if w.winfo_class() == "Text")
            notes.insert("1.0", "Acme, asking about SSO")
            next(w for w in _walk(prep) if w.winfo_class() == "TEntry").insert(0, "Do you do SSO?")
            next(w for w in _walk(prep) if w.winfo_class() == "TButton"
                 and str(w.cget("text")) == "Ask").invoke()
            state["asked"] = True
        state["labels"] = _labels(prep)
        return any("SSO is on the Team plan" in t for t in state["labels"])

    _tab(tmp_path, monkeypatch, folder=str(docs), pump_until=until)
    assert any("SSO is on the Team plan" in t and QUOTE in t for t in state["labels"])
    saved = (tmp_path / "meetings" / "next_call_notes.md").read_text(encoding="utf-8")
    assert saved == "Acme, asking about SSO"


def test_ask_says_not_in_your_docs_when_nothing_grounds(tmp_path, monkeypatch):
    from wisprlite import knowledge

    monkeypatch.setattr(knowledge, "_default_completion", lambda p, m: (lambda msgs: ""))
    monkeypatch.setattr(knowledge, "answer_question", lambda q, i, d, c: {
        "headline": None, "quote": None, "source": None})
    state = {}

    def until(root):
        prep = _prep(root)
        if prep is None:
            return False
        if "asked" not in state:
            next(w for w in _walk(prep) if w.winfo_class() == "TEntry").insert(0, "Do you do SSO?")
            next(w for w in _walk(prep) if w.winfo_class() == "TButton"
                 and str(w.cget("text")) == "Ask").invoke()
            state["asked"] = True
        state["labels"] = _labels(prep)
        return "Not in your docs" in state["labels"]

    _tab(tmp_path, monkeypatch, folder=str(_docs(tmp_path)), pump_until=until)
    assert "Not in your docs" in state["labels"]


def test_empty_notes_delete_the_next_call_file(tmp_path, monkeypatch):
    note = tmp_path / "meetings" / "next_call_notes.md"
    state = {}

    def until(root):
        prep = _prep(root)
        if prep is None:
            return False
        if "asked" not in state:
            note.write_text("old", encoding="utf-8")       # file exists, box is empty
            next(w for w in _walk(prep) if w.winfo_class() == "TEntry").insert(0, "Do you do SSO?")
            next(w for w in _walk(prep) if w.winfo_class() == "TButton"
                 and str(w.cget("text")) == "Ask").invoke()
            state["asked"] = True
        return True

    _tab(tmp_path, monkeypatch, pump_until=until)
    assert not note.exists()


def test_settings_has_your_docs_first_and_the_checkbox_round_trips(tmp_path, monkeypatch):
    from wisprlite import config

    state = {}

    def until(root):
        docs = next((w for w in _walk(root) if w.winfo_class() == "Label"
                     and str(w.cget("text")) == "Your docs"), None)
        if docs is None:
            return False
        sibs = docs.master.master.pack_slaves()      # the cards in the settings panel
        titles = [next((c for c in wrap.winfo_children() if c.winfo_class() == "Label"), None)
                  for wrap in sibs]
        state["order"] = [str(t.cget("text")) for t in titles if t is not None]
        boxes = [w for w in _walk(root) if w.winfo_class() == "TCheckbutton"
                 and str(w.cget("text")).startswith("Show answers during the call")]
        state["box"] = bool(boxes)
        save = next((b for b in _walk(root) if b.winfo_class() == "TButton"
                     and str(b.cget("text")) == "Save"), None)
        if boxes and save is not None and "flipped" not in state:
            boxes[0].invoke()                 # default on -> off
            save.invoke()
            state["flipped"] = True
        return "flipped" in state

    _tab(tmp_path, monkeypatch, pump_until=until)
    assert state["order"].index("Your docs") < state["order"].index("Meetings")
    assert state["box"]
    assert config.Config.load().live_answers is False


def test_a_question_split_over_two_lines_is_joined(tmp_path):
    asked, cards, now = [], [], [0.0]
    a = _answerer(tmp_path, lambda m: asked.append(m[-1]["content"]) or _reply(), cards,
                  clock=lambda: now[0])
    a.on_line("Them", "so tell me")                     # not a question on its own
    now[0] = 1.5
    a.on_line("Them", "about single sign-on SAML?")
    assert a._pending[0] == "so tell me about single sign-on SAML?"
    now[0] = 30.0
    a.on_line("Them", "and then the pricing per seat")  # too late to join, not a question
    assert a._pending[0] == "so tell me about single sign-on SAML?"


def test_saving_settings_keeps_a_folder_picked_in_the_meetings_tab(tmp_path, monkeypatch):
    # Choose docs folder… in the Meetings tab writes config directly while the
    # Settings form still holds the old (empty) value. Save must not revert it.
    from wisprlite import config

    state = {}

    def until(root):
        save = next((b for b in _walk(root) if b.winfo_class() == "TButton"
                     and str(b.cget("text")) == "Save"), None)
        if save is None:
            return False
        if "saved" not in state:
            cfg = config.Config.load()
            cfg.knowledge_dir = str(tmp_path / "picked")
            cfg.save()
            save.invoke()
            state["saved"] = True
        return True

    _tab(tmp_path, monkeypatch, pump_until=until)
    assert config.Config.load().knowledge_dir == str(tmp_path / "picked")


def test_a_one_file_docs_folder_still_gets_cards(tmp_path):
    # BM25 scores shrink with fewer chunks: a fixed score cut-off scored this
    # perfect question at 0.99 against one short FAQ and never showed a card.
    folder = tmp_path / "one"
    folder.mkdir()
    (folder / "faq.md").write_text(f"{QUOTE} The Starter plan uses email login only.",
                                   encoding="utf-8")
    cards = []
    a = live_answers.LiveAnswerer(str(folder), "", None, lambda m: _reply(),
                                  lambda *c: cards.append(c))
    a._build()
    a.on_line("Them", "so does it do single sign-on?")
    _drain(a)
    assert len(cards) == 1
