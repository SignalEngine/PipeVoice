"""The settings tooltips repeated text that was already printed on screen and
covered the row below while doing it. Those are gone; these guard the ones that
remain (voices editor, meetings tab), where the control has no visible
explanation of its own.
"""

import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from uistub import have_display  # noqa: E402


def _skip_if_headless():
    if not have_display():
        pytest.skip("no X display; run under xvfb-run")


def _settle(root, seconds=0.75):
    """Let the show-delay timer actually fire."""
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def _tips(root):
    import tkinter as tk

    found = []

    def walk(w):
        if isinstance(w, tk.Toplevel) and w.winfo_ismapped():
            found.append(w)
        for child in w.winfo_children():
            walk(child)

    walk(root)
    return found


def _fixture():
    import tkinter as tk

    from wisprlite import winui

    root = tk.Tk()
    root.geometry("400x200+80+80")
    a = tk.Label(root, text="A", width=10)
    b = tk.Label(root, text="B", width=10)
    a.pack()
    b.pack()
    root.update()
    winui.tooltip(a, "explains A")
    winui.tooltip(b, "explains B")
    return root, a, b


def test_a_tooltip_waits_before_appearing():
    # Without a delay, sweeping the pointer across a list or scrolling past a
    # control fired a popup for every widget the cursor crossed.
    _skip_if_headless()
    root, a, _b = _fixture()
    try:
        a.event_generate("<Enter>")
        root.update()
        assert _tips(root) == [], "a tooltip must not appear the instant the pointer arrives"
        _settle(root)
        assert len(_tips(root)) == 1, "it must appear once the pointer has settled"
    finally:
        root.destroy()


def test_only_one_tooltip_is_ever_on_screen():
    # Each widget used to own its popup and hide it only on its OWN <Leave>. A
    # missed leave stranded it, so they stacked: three were mapped at once from
    # a single hover, two of them duplicates of each other.
    _skip_if_headless()
    root, a, b = _fixture()
    try:
        a.event_generate("<Enter>")
        _settle(root)
        assert len(_tips(root)) == 1
        b.event_generate("<Enter>")          # no <Leave> for a — the stranding case
        _settle(root)
        assert len(_tips(root)) == 1, "moving on must replace the popup, not add one"
        text = [w for w in _tips(root)[0].winfo_children()[0].winfo_children()]
        assert text[0].cget("text") == "explains B"
    finally:
        root.destroy()


def test_a_tooltip_sits_beside_its_widget_not_over_what_follows():
    # Placed below, it covered the next row — the one you were moving toward.
    _skip_if_headless()
    root, a, b = _fixture()
    try:
        a.event_generate("<Enter>")
        _settle(root)
        tip = _tips(root)[0]

        def box(w):
            x, y = w.winfo_rootx(), w.winfo_rooty()
            return x, y, x + w.winfo_width(), y + w.winfo_height()

        # Sharing vertical extent with the widget below is fine — sitting on top
        # of it is not. Only a full rectangle intersection is an occlusion.
        tx0, ty0, tx1, ty1 = box(tip)
        bx0, by0, bx1, by1 = box(b)
        assert not (tx0 < bx1 and tx1 > bx0 and ty0 < by1 and ty1 > by0), (
            "the tooltip covers the widget below it"
        )
        assert tx0 >= a.winfo_rootx() + a.winfo_width(), "it should sit beside the widget"
    finally:
        root.destroy()


def test_scrolling_dismisses_a_tooltip():
    # James: "when you scroll over it, it kind of gets in the way."
    _skip_if_headless()
    root, a, _b = _fixture()
    try:
        a.event_generate("<Enter>")
        _settle(root)
        assert len(_tips(root)) == 1
        # Windows sends <MouseWheel>; X11 sends button 5. <Button> already
        # catches the X11 form, so testing only that passes even with the
        # <MouseWheel> binding deleted — and Windows is where the users are.
        a.event_generate("<MouseWheel>", delta=-120)
        root.update()
        assert _tips(root) == [], "the Windows wheel must dismiss it"

        a.event_generate("<Enter>")
        _settle(root)
        assert len(_tips(root)) == 1
        a.event_generate("<Button-5>")       # X11 wheel-down
        root.update()
        assert _tips(root) == [], "the X11 wheel must dismiss it too"
    finally:
        root.destroy()


def test_settings_attaches_no_tooltips_at_all():
    # Every settings tooltip duplicated the description printed under its own
    # label, so it could only ever occlude. The inline text is the explanation.
    source = (pathlib.Path(__file__).resolve().parent.parent
              / "wisprlite" / "settings.py").read_text(encoding="utf-8")
    assert "tooltip(" not in source, (
        "a settings tooltip repeats the description already shown inline"
    )
