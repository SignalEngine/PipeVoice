"""Meetings pane: summary-first tabs (Summary / Transcript / Questions / Stats)."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import knowledge, meetings_tab
from tests.test_after_call_prompt import _open_tab, _walk

SUMMARY = {"bullets": "- Agreed: ship on Friday"}


def _find(root, cls, text, mapped=True):
    return next((w for w in _walk(root)
                 if w.winfo_class() == cls and str(w.cget("text")) == text
                 and (w.winfo_ismapped() or not mapped)), None)


def _pill(root, prefix):
    return next(w for w in _walk(root) if w.winfo_class() == "Button"
                and str(w.cget("text")).startswith(prefix))


def _selected_tab(root):
    return [str(w.cget("text")) for w in _walk(root) if w.winfo_class() == "Button"
            and str(w.cget("fg")) == meetings_tab.ACCENT
            and str(w.cget("text")).split(" ")[0] in ("Summary", "Transcript", "Questions", "Stats")]


def _run(tmp_path, monkeypatch, *, transcribed, summarised, check, **kw):
    monkeypatch.setattr(meetings_tab, "read_summaries",
                        lambda _p: dict(SUMMARY) if summarised else {})
    out = {}

    def drive(r):
        r.update()
        if not _pill(r, "Summary").winfo_ismapped():
            return False
        return check(r, out) is not False

    _open_tab(tmp_path, monkeypatch, transcribed=transcribed, knowledge_dir="",
              auto=kw.pop("auto", False), pump_until=drive, **kw)
    return out


def test_summarised_meeting_opens_on_summary_with_copy_for_ai_visible(tmp_path, monkeypatch):
    def check(r, out):
        r.update()
        out["tab"] = _selected_tab(r)
        out["summary_shown"] = any(w.winfo_class() == "Text" and w.winfo_ismapped()
                                   and "ship on Friday" in w.get("1.0", "end") for w in _walk(r))
        copy = _find(r, "TButton", "Copy for AI")
        out["copy_mapped"] = bool(copy and copy.winfo_ismapped())
        out["copy_in_view"] = bool(copy and copy.winfo_rooty() + copy.winfo_height()
                                   <= r.winfo_rooty() + r.winfo_height())
        out["transcribe_mapped"] = _find(r, "TButton", "Transcribe") is not None
        return out["summary_shown"]

    out = _run(tmp_path, monkeypatch, transcribed=True, summarised=True, check=check)
    assert out["tab"] == ["Summary"]
    assert out["summary_shown"] and out["copy_mapped"] and out["copy_in_view"]
    assert not out["transcribe_mapped"]


def test_untranscribed_meeting_shows_transcribe_and_empty_states(tmp_path, monkeypatch):
    def check(r, out):
        r.update()
        out["tab0"] = _selected_tab(r)
        out["transcribe"] = _find(r, "TButton", "Transcribe") is not None
        for name in ("Summary", "Questions", "Stats"):
            _pill(r, name).invoke()
            r.update()
            out[name] = any(w.winfo_class() == "Label" and w.winfo_ismapped()
                            and str(w.cget("text")) == "Transcribe this meeting first."
                            for w in _walk(r))
        return True

    out = _run(tmp_path, monkeypatch, transcribed=False, summarised=False, check=check)
    assert out["tab0"] == ["Transcript"] and out["transcribe"]
    assert out["Summary"] and out["Questions"] and out["Stats"]


def test_ctrl_3_shows_questions_and_typing_in_search_returns_to_transcript(tmp_path, monkeypatch):
    def check(r, out):
        r.event_generate("<Control-Key-3>")
        r.update()
        out["after_ctrl3"] = _selected_tab(r)
        out["answer_btn"] = _find(r, "TButton", "Answer from my docs") is not None
        _pill(r, "Summary").invoke()
        r.update()
        prev = _find(r, "TButton", "Prev", mapped=False)
        entry = next(w for w in prev.master.winfo_children() if w.winfo_class() == "TEntry")
        out["search_hidden_first"] = not entry.winfo_ismapped()
        entry.insert(0, "cost")
        r.update()
        out["after_search"] = _selected_tab(r)
        return True

    out = _run(tmp_path, monkeypatch, transcribed=True, summarised=True, check=check)
    assert out["after_ctrl3"] == ["Questions"] and out["answer_btn"]
    assert out["search_hidden_first"] and out["after_search"] == ["Transcript"]


def test_more_menu_has_the_moved_actions(tmp_path, monkeypatch):
    import tkinter as tk
    labels = []
    monkeypatch.setattr(tk.Menu, "tk_popup", lambda self, x, y: labels.extend(
        str(self.entrycget(i, "label")) for i in range(self.index("end") + 1)
        if self.type(i) == "command"))

    def check(r, out):
        _find(r, "TButton", "⋯").invoke()
        return bool(labels)

    _run(tmp_path, monkeypatch, transcribed=True, summarised=True, check=check)
    for want in ("Copy transcript", "Polish transcript", "Show raw", "Export all…",
                 "Open folder", "Delete…", "Copy for AI"):
        assert want in labels, (want, labels)
    assert "Transcribe" not in labels          # nothing to transcribe here


def test_more_menu_offers_transcribe_first_when_untranscribed(tmp_path, monkeypatch):
    import tkinter as tk
    labels = []
    monkeypatch.setattr(tk.Menu, "tk_popup", lambda self, x, y: labels.extend(
        str(self.entrycget(i, "label")) for i in range(self.index("end") + 1)
        if self.type(i) == "command"))

    def check(r, out):
        _find(r, "TButton", "⋯").invoke()
        return bool(labels)

    _run(tmp_path, monkeypatch, transcribed=False, summarised=False, check=check)
    assert labels[0] == "Transcribe"


def test_answering_ends_on_the_questions_tab(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(knowledge, "build_answers", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(meetings_tab, "read_summaries", lambda _p: dict(SUMMARY))
    seen = {}

    def drive(r):
        r.update()
        seen["tab"] = _selected_tab(r)
        return bool(calls)

    _open_tab(tmp_path, monkeypatch, transcribed=True, knowledge_dir=str(tmp_path),
              auto=True, pump_until=drive)
    assert calls and seen["tab"] == ["Questions"]
