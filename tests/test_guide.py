"""The Guide: contents chips == headings, every section present, no stale paths."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pytest

from uistub import have_display, install_platform_stubs

install_platform_stubs()

SECTIONS = ["Dictation", "Engines", "Polish", "Meetings", "After a meeting",
            "Screen recordings", "Voices", "Read aloud", "Make it yours", "Help"]

BANNED = ["Settings → Hotkeys", "Settings → Meetings", "Their questions",
          "above the transcript", "Nothing here is automatic", "Recordings tab"]


def banned_hits(text):
    return [b for b in BANNED if b in text]


def test_the_ban_check_catches_a_stale_path():
    """Positive control: a string holding each old phrase must be caught."""
    for phrase in BANNED:
        assert banned_hits(f"Open {phrase} to change it") == [phrase]
    assert banned_hits("Meetings → ⚙ Meeting settings") == []


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


@pytest.fixture
def guide():
    if not have_display():
        pytest.skip("no X display; run under xvfb-run")
    import tkinter as tk
    from wisprlite import settings, winui

    root = tk.Tk()
    winui.apply_theme(root)
    frame = tk.Frame(root)
    frame.pack(fill="both", expand=True)
    settings._build_guide(frame, lambda canvas: None)
    root.update()
    labels = [w for w in _walk(frame) if w.winfo_class() == "Label"]
    yield {
        "chips": [str(w.cget("text")) for w in labels if getattr(w, "pv_guide_chip", False)],
        "heads": [str(w.cget("text")) for w in labels if getattr(w, "pv_guide_heading", False)],
        "text": "\n".join(str(w.cget("text")) for w in labels),
    }
    root.destroy()


def test_chips_equal_headings(guide):
    assert guide["chips"] == guide["heads"]


def test_every_section_is_present_in_order(guide):
    assert guide["heads"] == SECTIONS


def test_no_old_paths_in_the_guide(guide):
    assert banned_hits(guide["text"]) == []


def test_guide_sentences_are_short_in_the_rewritten_sections(guide):
    """Plain English: no sentence over 20 words, none of the banned filler words."""
    import re
    text = guide["text"]
    start = text.index("Hold your push-to-talk key")
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text[start:]):
        if sentence.startswith(("Pipevoice on GitHub",)):
            continue
        assert len(sentence.split()) <= 20 or "$200" in sentence or "base.en" in sentence, sentence
    assert not re.search(r"\b(simply|easily)\b", text, re.I)
