"""Meetings tab, after-call: Copy for AI, background 'Transcribing…', deferred answers."""
import json
import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import meeting, meetings_tab
from tests.test_after_call_prompt import _open_tab, _walk


def _btn(root, label):
    return next((w for w in _walk(root)
                 if w.winfo_class() == "TButton" and str(w.cget("text")) == label), None)


def _status(root):
    return [str(w.cget("text")) for w in _walk(root) if w.winfo_class() == "Label"]


def _clip(monkeypatch):
    got = []
    monkeypatch.setattr(meetings_tab, "_copy_to_clipboard", lambda _r, text: got.append(text) or True)
    return got


def test_copy_for_ai_puts_title_and_transcript_on_the_clipboard(tmp_path, monkeypatch):
    got = _clip(monkeypatch)
    state = {}

    def drive(r):
        b = _btn(r, "Copy for AI")
        if b is None or str(b.cget("state")) != "normal":
            return False
        if "clicked" not in state:
            meeting.save_title(tmp_path / "meetings" / "meeting-20261017-120000", "Acme kickoff", "user")
            b.invoke()
            state["clicked"] = True
        return bool(got)

    _open_tab(tmp_path, monkeypatch, transcribed=True, knowledge_dir="", auto=False, pump_until=drive)
    assert len(got) == 1
    assert "Acme kickoff" in got[0] and "What does it cost?" in got[0]


def test_copy_for_ai_is_disabled_and_a_forced_click_leaves_the_clipboard_alone(tmp_path, monkeypatch):
    got = _clip(monkeypatch)
    seen = {}

    def drive(r):
        b = _btn(r, "Copy for AI")
        if b is None or not b.winfo_ismapped():
            return False
        seen["initial"] = str(b.cget("state"))
        b.config(state="normal")           # the menu entry is always clickable: same code path
        b.invoke()
        r.update()
        seen["texts"] = _status(r)
        return True

    _open_tab(tmp_path, monkeypatch, transcribed=False, knowledge_dir="", auto=False, pump_until=drive)
    assert seen["initial"] == "disabled"
    assert got == []
    assert "Nothing to copy — this meeting has no transcript yet." in seen["texts"]


def test_row_menu_lists_copy_for_ai_under_export_all(tmp_path, monkeypatch):
    import tkinter as tk

    labels = []
    monkeypatch.setattr(tk.Menu, "tk_popup", lambda self, x, y: labels.extend(
        str(self.entrycget(i, "label")) for i in range(self.index("end") + 1)
        if self.type(i) == "command"))
    state = {}

    def drive(r):
        rows = [w for w in _walk(r) if w.winfo_class() == "Label"
                and str(w.cget("text")) == "17 Oct 12:00" and w.winfo_ismapped()]
        if not rows:
            return False
        if "fired" not in state:
            for w in rows:      # the list row reacts; the detail header does not
                w.event_generate("<Button-3>", x=1, y=1)
            state["fired"] = True
        return bool(labels)

    _open_tab(tmp_path, monkeypatch, transcribed=True, knowledge_dir="", auto=False, pump_until=drive)
    i = labels.index("Export all…")
    assert labels[i + 1] == "Copy for AI"


def test_a_transcribing_marker_shows_on_the_row_and_blocks_transcribe(tmp_path, monkeypatch):
    monkeypatch.setattr(meetings_tab, "session_transcribing", lambda _p: True)
    seen = {}

    def drive(r):
        if any(t == "Transcribing…" for t in _status(r)):
            seen["row"] = True
            seen["transcribe"] = str(_btn(r, "Transcribe").cget("state"))
            return True
        return False

    _open_tab(tmp_path, monkeypatch, transcribed=False, knowledge_dir="", auto=False, pump_until=drive)
    assert seen.get("row") and seen["transcribe"] == "disabled"


def test_auto_answer_waits_for_the_background_transcript_then_answers_once(tmp_path, monkeypatch):
    from wisprlite import knowledge

    calls, transcribed = [], {"on": False}
    monkeypatch.setattr(meetings_tab, "session_transcribing", lambda _p: not transcribed["on"])
    monkeypatch.setattr(meetings_tab, "transcribe_session",
                        lambda *a, **k: pytest.fail("must not start a second transcription"))
    monkeypatch.setattr(knowledge, "build_answers", lambda *a, **k: calls.append(a))
    seen = {}

    def drive(r):
        if any(t.startswith("Transcribing in the background — answers will start")
               for t in _status(r)):
            seen["waiting"] = True
        if seen.get("waiting") and not transcribed["on"]:
            transcribed["on"] = True
            (tmp_path / "meetings" / "meeting-20261017-120000" / "transcript.json").write_text(
                json.dumps({"segments": [{"start": 0, "end": 2, "speaker": "Them", "text": "Price?"}]}),
                encoding="utf-8")
        return bool(calls)

    _open_tab(tmp_path, monkeypatch, transcribed=False, knowledge_dir=str(tmp_path), pump_until=drive)
    time.sleep(0.2)
    assert seen.get("waiting") and len(calls) == 1
