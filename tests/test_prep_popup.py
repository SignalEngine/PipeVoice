"""Paste-only output, answers from chosen documents, and the prep pop-up."""

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

from wisprlite import knowledge, live_answers, typer

QUOTE = "I led a team of six engineers who shipped the billing platform."


# ---- A. paste only ----------------------------------------------------------

def _clip(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "pyperclip", types.SimpleNamespace(
        paste=lambda: "old", copy=lambda t: calls.append(("copy", t))))
    kb = mock.Mock()
    kb.send.side_effect = lambda k: calls.append(("send", k))
    monkeypatch.setattr(typer, "keyboard", kb)
    return calls, kb


def test_type_mode_pastes_and_never_types(monkeypatch):
    calls, kb = _clip(monkeypatch)
    monkeypatch.setattr(typer, "_clipboard_holds_non_text", lambda: False)
    typer.type_text("hi", "type")
    assert ("copy", "hi") in calls and ("send", "ctrl+v") in calls
    assert not kb.write.called
    assert calls[-1] == ("copy", "old")                # text clipboard comes back


def test_an_image_on_the_clipboard_is_pasted_around_then_restored(monkeypatch):
    from PIL import Image, ImageGrab

    calls, kb = _clip(monkeypatch)
    shot = Image.new("RGB", (4, 4), "red")
    monkeypatch.setattr(typer, "_clipboard_holds_non_text", lambda: True)
    monkeypatch.setattr(ImageGrab, "grabclipboard", lambda: shot)
    restored = []
    monkeypatch.setattr(typer, "_restore_clipboard_image", restored.append)
    typer.type_text("hello", "paste")
    assert ("send", "ctrl+v") in calls and not kb.write.called
    assert restored == [shot]
    assert ("copy", "old") not in calls                # no text "restore" over the image


def test_copied_files_are_pasted_over_and_logged(monkeypatch, caplog):
    from PIL import ImageGrab

    calls, kb = _clip(monkeypatch)
    monkeypatch.setattr(typer, "_clipboard_holds_non_text", lambda: True)
    monkeypatch.setattr(ImageGrab, "grabclipboard", lambda: ["C:/a.pdf"])
    with caplog.at_level("INFO", logger="wisprlite"):
        typer.type_text("hello", "paste")
    assert ("send", "ctrl+v") in calls and not kb.write.called
    assert "non-text, non-image contents not restored" in caplog.text


def test_a_failed_paste_raises_so_the_caller_can_save_the_words(monkeypatch):
    calls, kb = _clip(monkeypatch)
    monkeypatch.setattr(typer, "_clipboard_holds_non_text", lambda: False)
    kb.send.side_effect = RuntimeError("no window")
    with pytest.raises(RuntimeError):
        typer.type_text("hi", "paste")
    assert not kb.write.called


def test_a_saved_type_output_loads_as_paste_every_time(tmp_path, monkeypatch):
    from wisprlite import config

    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    path.write_text(json.dumps({"output_mode": "type", "paste_default_migrated": True,
                                "starter_vocab_seeded": True}))
    assert config.Config.load().output_mode == "paste"
    assert json.loads(path.read_text())["output_mode"] == "paste"


def test_a_voice_override_of_type_reads_as_paste_and_set_output_maps_it():
    from wisprlite import app as app_mod, config

    a = app_mod.App.__new__(app_mod.App)
    a.cfg = config.Config(output_mode="paste")
    a._active = {"output_mode": "type"}
    assert a._eff("output_mode") == "paste"
    a.cfg.save = lambda: None
    a.tray = mock.Mock()
    a.set_output("type")
    assert a.cfg.output_mode == "paste"


def _all_texts(widget):
    out = []
    try:
        out.append(str(widget.cget("text")))
    except Exception:
        pass
    for child in widget.winfo_children():
        out += _all_texts(child)
    return out


def test_settings_form_has_no_output_row(monkeypatch):
    if not have_display():
        pytest.skip("no X display; run under xvfb-run")
    import tkinter as tk

    from wisprlite import settings

    seen = []

    def stub_mainloop(self, _n=0):
        self.update_idletasks()
        seen.extend(_all_texts(self))
        self.destroy()

    monkeypatch.setenv("PV_TAB", "Settings")
    monkeypatch.setattr(tk.Misc, "mainloop", stub_mainloop)
    settings.main()
    assert "Mode" in seen                               # the form did build
    assert "Output" not in seen
    assert not any("Type keystrokes" in t for t in seen)
    assert not hasattr(settings, "OUTPUTS")


def test_tray_menu_has_no_output_item(monkeypatch):
    from wisprlite import tray

    items = []

    class Item:
        def __init__(self, text, action=None, **kw):
            self.text = text
            items.append(text)

    class Menu:
        SEPARATOR = object()

        def __init__(self, *items):
            pass

    fake = types.ModuleType("pystray")
    fake.Menu, fake.MenuItem = Menu, Item
    fake.Icon = lambda *a, **k: mock.Mock()
    monkeypatch.setitem(sys.modules, "pystray", fake)
    t = tray.Tray(types.SimpleNamespace(cfg=types.SimpleNamespace()))
    monkeypatch.setattr(t, "_state_image", lambda s: None)
    t.start()
    assert "Engine" in items and "Mode" in items
    assert "Output" not in items


def test_no_keyboard_write_left_in_the_package():
    root = pathlib.Path(__file__).resolve().parent.parent / "wisprlite"
    assert not [p.name for p in root.rglob("*.py") if "keyboard.write" in p.read_text("utf-8")]


# ---- B. answers from the documents you choose --------------------------------

def _src(name, text):
    return {"source": name, "text": text}


def test_build_context_returns_every_doc_whole_under_the_limit():
    docs = [_src("cv.txt", "Led six engineers."), _src("ad.md", "Fast-moving team.")]
    index = knowledge.BM25(knowledge.chunk_docs(docs))
    ctx = knowledge.build_context("why would this job suit you?", index, docs)
    assert ctx == "[cv.txt]\nLed six engineers.\n\n[ad.md]\nFast-moving team."


def test_over_the_limit_a_second_source_is_not_crowded_out():
    big = "\n\n".join(f"billing platform migration notes number {i} " + "filler " * 100
                      for i in range(40))
    docs = [_src("a.md", big), _src("b.md", "Our unicorn billing story " + "x " * 5)]
    docs[0]["text"] += " ".join(["pad"] * knowledge.FULL_TEXT_WORDS)
    index = knowledge.BM25(knowledge.chunk_docs(docs))
    question = "billing platform migration"
    top = index.top(question)
    assert top and all(h["source"] == "a.md" for h in top)       # A fills the top-k
    ctx = knowledge.build_context(question, index, docs)
    assert "[b.md]" in ctx


def _live_reply(quote=QUOTE, points=("Led six engineers", "Shipped billing", "third")):
    return json.dumps({"headline": "I have led billing work", "points": list(points),
                       "quote": quote, "source": "cv.txt"})


def test_answer_live_returns_trimmed_points_and_a_verified_quote():
    docs = [_src("cv.txt", f"Experience. {QUOTE} Later I moved on.")]
    index = knowledge.BM25(knowledge.chunk_docs(docs))
    seen = []
    ans = knowledge.answer_live("why would this job suit you?", index, docs,
                                lambda m: seen.append(m) or _live_reply(points=("x" * 300, "b", "c")))
    assert ans["headline"] == "I have led billing work" and ans["source"] == "cv.txt"
    assert ans["points"] == ["x" * 120, "b"]
    assert "Experience." in seen[0][1]["content"]               # the CV text reached the model


def test_answer_live_with_a_quote_not_in_the_doc_is_all_none():
    docs = [_src("cv.txt", "I like boats.")]
    index = knowledge.BM25(knowledge.chunk_docs(docs))
    ans = knowledge.answer_live("why you?", index, docs, lambda m: _live_reply())
    assert ans == {"headline": None, "points": None, "quote": None, "source": None}


def test_load_sources_reads_chosen_files(tmp_path):
    cv = tmp_path / "cv.txt"
    cv.write_text("Led six engineers.", encoding="utf-8")
    docs, report = knowledge.load_sources("", "", None, files=[str(cv)])
    assert [d["source"] for d in docs] == ["cv.txt"] and report["ok"] == ["cv.txt"]


# ---- live_answers + prep.json ------------------------------------------------

def _folder(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "cv.txt").write_text(f"Experience. {QUOTE}", encoding="utf-8")
    (folder / "faq.md").write_text("Pricing starts at forty dollars.", encoding="utf-8")
    return folder


def test_small_docs_answer_a_question_that_shares_no_words_with_the_cv(tmp_path):
    cards = []
    a = live_answers.LiveAnswerer(str(_folder(tmp_path)), "", None, lambda m: _live_reply(),
                                  lambda *c: cards.append(c))
    a._build()
    assert a._small
    a.on_line("Them", "Why would this job suit you?")
    job, a._pending = a._pending, None
    a._answer(*job)
    assert cards and cards[0][1] == ["Led six engineers", "Shipped billing"]
    assert cards[0][3] == "cv.txt"


def test_prep_files_replace_the_folder(tmp_path):
    folder = _folder(tmp_path)
    prep = tmp_path / "prep.json"
    prep.write_text(json.dumps({"title": "t", "notes": "", "files": [str(folder / "cv.txt")],
                                "use_folder": False}))
    ready = []
    a = live_answers.LiveAnswerer(str(folder), "", None, lambda m: "", lambda *c: None,
                                  prep_path=prep, on_ready=ready.append)
    a._build()
    assert [d["source"] for d in a._docs] == ["cv.txt"]
    assert ready == [1]


def test_prep_with_folder_adds_the_folder(tmp_path):
    folder = _folder(tmp_path)
    prep = tmp_path / "prep.json"
    extra = tmp_path / "agenda.md"
    extra.write_text("Agenda: roadmap.", encoding="utf-8")
    prep.write_text(json.dumps({"files": [str(extra)], "use_folder": True}))
    a = live_answers.LiveAnswerer(str(folder), "", None, lambda m: "", lambda *c: None,
                                  prep_path=prep)
    a._build()
    assert sorted(d["source"] for d in a._docs) == ["agenda.md", "cv.txt", "faq.md"]


def test_skip_means_no_card(tmp_path):
    cards = []
    prep = tmp_path / "prep.json"
    prep.write_text(json.dumps({"skip": True}))
    a = live_answers.LiveAnswerer(str(_folder(tmp_path)), "", None, lambda m: _live_reply(),
                                  lambda *c: cards.append(c), prep_path=prep)
    a._build()
    assert not a._ready.is_set()
    a.on_line("Them", "Why would this job suit you?")
    assert a._pending is None and cards == []


def test_no_prep_file_and_stop_ends_the_build_thread(tmp_path):
    a = live_answers.LiveAnswerer(str(_folder(tmp_path)), "", None, lambda m: "",
                                  lambda *c: None, prep_path=tmp_path / "prep.json")
    t = threading.Thread(target=a._build, daemon=True)
    t.start()
    time.sleep(0.2)
    assert t.is_alive() and not a._ready.is_set()
    a.stop()
    t.join(3)
    assert not t.is_alive() and not a._ready.is_set()


def test_a_prep_file_that_never_comes_builds_from_the_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(live_answers, "PREP_WAIT", 0.3)
    a = live_answers.LiveAnswerer(str(_folder(tmp_path)), "", None, lambda m: "",
                                  lambda *c: None, prep_path=tmp_path / "prep.json")
    a._build()
    assert sorted(d["source"] for d in a._docs) == ["cv.txt", "faq.md"]


# ---- C. the pop-up ------------------------------------------------------------

def _pump(root, steps):
    """Run steps (callables) once each, with the Tk loop turning in between."""
    for step in steps:
        for _ in range(5):
            root.update()
        step(root)
    for _ in range(5):
        try:
            root.update()
        except Exception:
            break


def _buttons(root, text):
    from test_live_answers import _walk

    return next(w for w in _walk(root) if w.winfo_class() == "TButton"
                and str(w.cget("text")) == text)


def _run_popup(tmp_path, monkeypatch, steps, folder=""):
    from wisprlite import config, prep_window

    session = tmp_path / "meeting-1"
    session.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config.Config, "load",
                        classmethod(lambda cls: config.Config(knowledge_dir=folder)))
    monkeypatch.setattr("wisprlite.meeting.meetings_dir", lambda: tmp_path)
    prep_window.main(session, pump_until=lambda root: _pump(root, steps))
    return session


def test_popup_start_writes_prep_json_and_appends_the_notes(tmp_path, monkeypatch):
    if not have_display():
        pytest.skip("no X display; run under xvfb-run")
    from test_live_answers import _walk
    from tkinter import filedialog

    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF")
    (tmp_path / "next_call_notes.md").write_text("Backend role", encoding="utf-8")
    monkeypatch.setattr(filedialog, "askopenfilenames", lambda **kw: (str(cv),))
    folder = _folder(tmp_path)

    def fill(root):
        entry = next(w for w in _walk(root) if w.winfo_class() == "TEntry")
        entry.insert(0, "Interview with Sam")
        _buttons(root, "Add file\u2026").invoke()

    def start(root):
        checks = [w for w in _walk(root) if w.winfo_class() == "TCheckbutton"]
        assert checks and "(2 files)" in str(checks[0].cget("text"))
        _buttons(root, "Start answering").invoke()

    session = _run_popup(tmp_path, monkeypatch, [fill, start], folder=str(folder))
    prep = json.loads((session / "prep.json").read_text())
    assert prep["files"] == [str(cv)] and prep["use_folder"] is False
    assert prep["title"] == "Interview with Sam" and prep["notes"] == "Backend role"
    notes = (session / "call_notes.md").read_text(encoding="utf-8")
    assert "Interview with Sam" in notes and "Backend role" in notes
    assert not (tmp_path / "next_call_notes.md").exists()


def test_popup_escape_is_skip(tmp_path, monkeypatch):
    if not have_display():
        pytest.skip("no X display; run under xvfb-run")

    def esc(root):
        root.focus_force()
        root.event_generate("<Escape>")

    session = _run_popup(tmp_path, monkeypatch, [esc])
    assert json.loads((session / "prep.json").read_text()) == {"skip": True}


# ---- app + overlay ------------------------------------------------------------

def _meeting_app(answers_on):
    from wisprlite import app as app_mod

    a = app_mod.App.__new__(app_mod.App)
    a._meeting_active = False
    a.paused = False
    a._meeting_degraded = False
    a._meeting_errors_reported = set()
    a._meeting = types.SimpleNamespace(start=lambda: None, session_dir="/tmp/sess-1",
                                       focus_session=object())
    a._start_pipefocus = lambda: None
    a._start_live_answers = lambda: None
    a._answers_on = answers_on
    a.overlay = mock.Mock()
    a._set_icon = lambda s: None
    a.tray = mock.Mock()
    return a


def test_meeting_start_with_answers_on_spawns_the_prep_popup(monkeypatch):
    popen = mock.Mock()
    monkeypatch.setattr("subprocess.Popen", popen)
    _meeting_app(True).toggle_meeting()
    cmd = popen.call_args[0][0]
    assert cmd[-2:] == ["--prep", "/tmp/sess-1"]


def test_meeting_start_with_answers_off_spawns_nothing(monkeypatch):
    popen = mock.Mock()
    monkeypatch.setattr("subprocess.Popen", popen)
    _meeting_app(False).toggle_meeting()
    assert not popen.called


def test_card_with_points_renders_on_a_real_display():
    if not have_display():
        pytest.skip("no X display; run under xvfb-run")
    import tkinter as tk

    from test_live_answers import _walk
    from wisprlite.overlay import Overlay

    root = tk.Tk()
    try:
        canvas = tk.Canvas(root)
        Overlay.__new__(Overlay)._render_answer_card(
            canvas, "Head", "The quote", "cv.txt", ["Point one", "Point two"])
        root.update()
        tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        assert len(tops) == 1
        texts = [str(w.cget("text")) for w in _walk(tops[0]) if w.winfo_class() == "Label"]
        assert texts[0] == "Head"
        assert "\u2022 Point one" in texts and "\u2022 Point two" in texts
        assert "\u201cThe quote\u201d" in texts and "cv.txt" in texts
    finally:
        root.destroy()


def test_a_failed_image_restore_does_not_fail_the_paste(monkeypatch):
    from PIL import Image, ImageGrab

    calls, kb = _clip(monkeypatch)
    monkeypatch.setattr(typer, "_clipboard_holds_non_text", lambda: True)
    monkeypatch.setattr(ImageGrab, "grabclipboard", lambda: Image.new("RGB", (2, 2)))

    def boom(_image):
        raise OSError("clipboard busy")

    monkeypatch.setattr(typer, "_restore_clipboard_image", boom)
    typer.type_text("hello", "paste")                  # must not raise: the paste landed
    assert ("send", "ctrl+v") in calls
