"""Build every real window and assert it actually renders.

Run headless with:  xvfb-run -a python -m pytest tests/test_ui_smoke.py

Skips cleanly when there is no display, so it never breaks a normal test run.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from uistub import build_window, have_display, install_platform_stubs

install_platform_stubs()

DISPLAY = have_display()
SKIP = "no X display; run under xvfb-run"


def _skip_if_headless():
    if not DISPLAY:
        import pytest

        pytest.skip(SKIP)


def test_every_settings_tab_builds():
    # A decorator stolen from the function below it crashed every overlay frame
    # and was invisible to unit tests. Building the window catches that class of
    # bug in a second.
    _skip_if_headless()
    from wisprlite import settings

    for tab in ("Settings", "Voices", "History", "Meetings", "Guide", "About"):
        result = build_window(settings.main, tab=tab)
        assert result["error"] is None, f"{tab} tab failed to build: {result['error']}"
        assert result["widgets"] > 0, f"{tab} tab built nothing"


def test_meetings_tab_exposes_its_primary_action():
    # The Transcribe control was one grey button among six at the bottom edge,
    # so a fresh recording offered no obvious next step. It is now a green
    # Go.TButton in two places; assert the style exists and is actually green.
    _skip_if_headless()
    import tkinter as tk
    from tkinter import ttk

    from wisprlite import winui

    root = tk.Tk()
    try:
        winui.apply_theme(root)
        style = ttk.Style()
        assert style.lookup("Go.TButton", "background") == winui.PALETTE["good"], (
            "the primary meeting action must stand out"
        )
        # ...and must not be the same colour as Save, or it competes for the eye.
        assert style.lookup("Go.TButton", "background") != style.lookup(
            "Accent.TButton", "background"
        )
    finally:
        root.destroy()


def test_overlay_renders_a_meeting_frame_without_raising():
    # Every overlay frame calls self._blend(); when it lost its @staticmethod
    # the whole overlay would have crashed on the first frame of a meeting.
    _skip_if_headless()
    import tkinter as tk

    from wisprlite.overlay import Overlay

    root = tk.Tk()
    try:
        canvas = tk.Canvas(root)
        canvas.pack()
        root.update()

        # Call _blend the way production does — through an instance. Without its
        # @staticmethod, `self` is passed as the start colour and every frame of
        # a meeting raises. Calling Overlay._blend(...) directly does NOT catch
        # that, which is exactly how the bug slipped through once already.
        instance = Overlay.__new__(Overlay)
        assert instance._blend("#000000", "#ffffff", 0.5) == "#808080", (
            "self._blend must work from an instance, as every overlay frame calls it"
        )

        # The popup is the newest widget path and must never raise.
        Overlay._show_bleed_warning(object(), canvas)
        root.update_idletasks()
        root.update()
        tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        assert len(tops) == 1, "the speaker-bleed warning did not appear"
        assert tops[0].winfo_ismapped(), "the warning was created but never shown"
    finally:
        root.destroy()
