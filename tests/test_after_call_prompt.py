"""The after-call prompt: stop a meeting -> pill -> Transcribe + answer."""

import json
import os
import pathlib
import sys
import time
import types
import wave
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pytest

from uistub import have_display, install_platform_stubs

install_platform_stubs()


# ---- overlay ---------------------------------------------------------------

def test_meeting_done_buttons_are_reachable_through_pill_click():
    from wisprlite.overlay import Overlay

    ov = Overlay.__new__(Overlay)
    for index, (action, _label) in enumerate(Overlay.MEETING_DONE_BUTTONS):
        x1, y1, x2, y2 = ov._done_button_box(index, Overlay.MEETING_DONE_BUTTONS)
        got = ov._pill_click({"name": "meeting_done"}, (x1 + x2) // 2, (y1 + y2) // 2)
        assert got == action
    assert [a for a, _ in Overlay.MEETING_DONE_BUTTONS] == ["md_answer", "md_later"]
    assert ov._pill_click({"name": "meeting_done"}, 0, 0) == ""


# ---- app -------------------------------------------------------------------

def _stopping_app(tmp_path, overlay_on, session_exists=True):
    from wisprlite import config
    from wisprlite.app import App

    session = tmp_path / "meeting-20261017-120000"
    if session_exists:
        session.mkdir()
    app = App.__new__(App)
    app.cfg = config.Config(overlay=overlay_on)
    app._meeting_active = True
    app._meeting_degraded = False
    app._meeting = types.SimpleNamespace(stop=lambda: session, session_dir=session)
    app._stop_pipefocus = lambda: None
    app._set_icon = lambda _s: None
    app.tray = types.SimpleNamespace(update=lambda: None)
    app.overlay = mock.Mock()
    return app, session


def test_stopping_a_meeting_shows_the_after_call_pill(tmp_path):
    app, session = _stopping_app(tmp_path, overlay_on=True)
    app.toggle_meeting()
    app.overlay.show_meeting_done.assert_called_once_with(__import__("wisprlite.app", fromlist=["x"]).AFTER_CALL_PROMPT)
    app.overlay.hide.assert_not_called()
    assert app._last_meeting_dir == session


def test_stopping_a_meeting_with_overlay_off_just_hides(tmp_path):
    app, _ = _stopping_app(tmp_path, overlay_on=False)
    app.toggle_meeting()
    app.overlay.hide.assert_called_once()
    app.overlay.show_meeting_done.assert_not_called()


def test_stopping_with_no_session_dir_just_hides(tmp_path):
    app, _ = _stopping_app(tmp_path, overlay_on=True, session_exists=False)
    app.toggle_meeting()
    app.overlay.hide.assert_called_once()
    app.overlay.show_meeting_done.assert_not_called()


def test_md_answer_opens_meetings_tab_on_that_session(tmp_path):
    from wisprlite.app import App

    app, session = _stopping_app(tmp_path, overlay_on=True)
    app._last_meeting_dir = session
    with mock.patch.object(App, "open_settings") as opened:
        App._screenrec_action(app, "md_answer")
    opened.assert_called_once_with(tab="Meetings", select=str(session), auto="answer")
    app.overlay.hide.assert_called_once()


def test_md_later_only_hides(tmp_path):
    from wisprlite.app import App

    app, session = _stopping_app(tmp_path, overlay_on=True)
    with mock.patch.object(App, "open_settings") as opened:
        App._screenrec_action(app, "md_later")
    opened.assert_not_called()
    app.overlay.hide.assert_called_once()


def test_open_settings_passes_auto_in_the_environment():
    from wisprlite.app import App

    app = App.__new__(App)
    with mock.patch("subprocess.Popen") as popen:
        App.open_settings(app, tab="Meetings", select="/x", auto="answer")
    env = popen.call_args.kwargs["env"]
    assert env["PV_AUTO"] == "answer" and env["PV_SELECT"] == "/x"


# ---- meetings tab ----------------------------------------------------------

def _meeting(base, transcribed):
    folder = base / "meeting-20261017-120000"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({
        "started_at": "2026-10-17T12:00:00", "stopped_at": "2026-10-17T12:05:00",
    }), encoding="utf-8")
    with wave.open(str(folder / "mic.wav"), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16_000)
        out.writeframes(b"\x00\x00" * 800)
    if transcribed:
        (folder / "transcript.json").write_text(json.dumps({"segments": [
            {"start": 0, "end": 2, "speaker": "Them", "text": "What does it cost?"},
        ]}), encoding="utf-8")
    return folder


def _walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)


def _open_tab(tmp_path, monkeypatch, *, transcribed, knowledge_dir, auto=True,
              pump_until=None):
    """Open Settings on the Meetings tab the way the after-call pill does and
    pump Tk until `pump_until(root)` holds (or 3 s). Returns what was seen."""
    if not have_display():
        pytest.skip("no X display; run under xvfb-run")
    import tkinter as tk

    from wisprlite import config, meeting, meetings_tab, settings

    base = tmp_path / "meetings"
    base.mkdir()
    session = _meeting(base, transcribed)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(meeting, "meetings_dir", lambda: base)
    monkeypatch.setattr(meetings_tab, "meetings_dir", lambda: base)
    cfg = config.Config.load()
    cfg.knowledge_dir = knowledge_dir
    cfg.cleanup_provider = "ollama"
    cfg.save()
    monkeypatch.setattr(meetings_tab, "provider_ready", lambda _p: True)
    monkeypatch.setenv("PV_TAB", "Meetings")
    monkeypatch.setenv("PV_SELECT", str(session))
    if auto:
        monkeypatch.setenv("PV_AUTO", "answer")
    else:
        monkeypatch.delenv("PV_AUTO", raising=False)

    # Tk only accepts root.after from the main thread while a real mainloop
    # runs, and the stubbed loop below is not one - so run the tab's worker
    # threads inline.
    class _Inline:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._t, self._a, self._k = target, args, kwargs or {}

        def start(self):
            self._t(*self._a, **self._k)

    monkeypatch.setattr(meetings_tab, "threading",
                        types.SimpleNamespace(Thread=_Inline))

    seen = {"error": None}

    def drive(self, _n=0):
        try:
            deadline = time.time() + 3
            while time.time() < deadline:
                self.update_idletasks()
                self.update()
                if pump_until and pump_until(self):
                    break
                time.sleep(0.02)
            for _ in range(5):          # let the last pack() finish mapping
                self.update_idletasks()
                self.update()
            seen["root"] = self
            seen["buttons"] = [
                str(w.cget("text")) for w in _walk(self)
                if w.winfo_class() == "TButton" and w.winfo_ismapped()
            ]
            seen["texts"] = [
                str(w.cget("text")) for w in _walk(self)
                if w.winfo_class() == "Label" and w.winfo_ismapped()
            ]
            seen["all_labels"] = [
                (str(w.cget("text"))[:20], w.winfo_ismapped(), w.winfo_width(), w.winfo_reqwidth(), w.master.winfo_width())
                for w in _walk(self) if w.winfo_class() == "Label"
                and "docs" in str(w.cget("text")).lower() and len(str(w.cget("text"))) < 120
            ]
            seen["invoke"] = lambda label: next(
                w for w in _walk(self)
                if w.winfo_class() == "TButton" and str(w.cget("text")) == label
            ).invoke()
            seen["post"] = pump_after(self) if pump_after else None
        finally:
            self.destroy()

    pump_after = None
    real = tk.Misc.mainloop
    tk.Misc.mainloop = drive
    try:
        settings.main()
    finally:
        tk.Misc.mainloop = real
    return seen, session


def test_auto_answer_on_a_transcribed_session_answers_once(tmp_path, monkeypatch):
    from wisprlite import knowledge

    calls = []
    monkeypatch.setattr(knowledge, "build_answers",
                        lambda *a, **k: calls.append(a))
    _open_tab(tmp_path, monkeypatch, transcribed=True, knowledge_dir=str(tmp_path),
              pump_until=lambda _r: bool(calls))
    time.sleep(0.2)
    assert len(calls) == 1


def test_auto_answer_on_an_untranscribed_session_transcribes_first(tmp_path, monkeypatch):
    from wisprlite import knowledge, meetings_tab

    order = []

    def fake_transcribe(path, cfg):
        order.append("transcribe")
        (path / "transcript.json").write_text(json.dumps({"segments": [
            {"start": 0, "end": 2, "speaker": "Them", "text": "Price?"}]}),
            encoding="utf-8")

    monkeypatch.setattr(meetings_tab, "transcribe_session", fake_transcribe)
    monkeypatch.setattr(knowledge, "build_answers",
                        lambda *a, **k: order.append("answers"))
    _open_tab(tmp_path, monkeypatch, transcribed=False, knowledge_dir=str(tmp_path),
              pump_until=lambda _r: "answers" in order)
    assert order == ["transcribe", "answers"]


def test_without_the_auto_flag_nothing_runs(tmp_path, monkeypatch):
    from wisprlite import knowledge

    calls = []
    monkeypatch.setattr(knowledge, "build_answers", lambda *a, **k: calls.append(a))
    _open_tab(tmp_path, monkeypatch, transcribed=True, knowledge_dir=str(tmp_path),
              auto=False, pump_until=lambda _r: False)
    assert calls == []


def test_no_docs_shows_choose_docs_folder(tmp_path, monkeypatch):
    from wisprlite import knowledge

    calls = []
    monkeypatch.setattr(knowledge, "build_answers", lambda *a, **k: calls.append(a))
    seen, _ = _open_tab(tmp_path, monkeypatch, transcribed=True, knowledge_dir="",
                        pump_until=lambda r: any(
                            w.winfo_class() == "TButton"
                            and str(w.cget("text")).startswith("Choose docs folder")
                            and w.winfo_ismapped() for w in _walk(r)))
    assert any(b.startswith("Choose docs folder") for b in seen["buttons"])
    assert any("Pick a docs folder" in t for t in seen["texts"]), seen["all_labels"]
    assert calls == []


def test_choose_docs_folder_saves_the_folder_and_answers(tmp_path, monkeypatch):
    from tkinter import filedialog

    from wisprlite import config, knowledge

    calls = []
    monkeypatch.setattr(knowledge, "build_answers", lambda *a, **k: calls.append(a))
    chosen = tmp_path / "docs"
    chosen.mkdir()
    monkeypatch.setattr(filedialog, "askdirectory", lambda **k: str(chosen))

    def shown(r):
        return any(w.winfo_class() == "TButton"
                   and str(w.cget("text")).startswith("Choose docs folder")
                   and w.winfo_ismapped() for w in _walk(r))

    state = {}

    def drive_click(r):
        if "clicked" not in state and shown(r):
            next(w for w in _walk(r) if w.winfo_class() == "TButton"
                 and str(w.cget("text")).startswith("Choose docs folder")).invoke()
            state["clicked"] = True
        return bool(calls)

    seen, _ = _open_tab(tmp_path, monkeypatch, transcribed=True, knowledge_dir="",
                        pump_until=drive_click)
    assert state.get("clicked")
    assert len(calls) == 1 and calls[0][1] == str(chosen)
    assert config.Config.load().knowledge_dir == str(chosen)
    assert not any(b.startswith("Choose docs folder") for b in seen["buttons"])
