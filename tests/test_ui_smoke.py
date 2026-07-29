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


def test_toggling_all_meetings_search_does_not_raise():
    # The harness built windows but never CLICKED anything, so a feature whose
    # callback raised TypeError on first use shipped: refresh() had no
    # preserve_search parameter and "All meetings" died the moment it was
    # ticked. Building is not exercising.
    _skip_if_headless()
    import tkinter as tk

    from wisprlite import settings

    errors = []
    real_report = tk.Tk.report_callback_exception

    def capture(_self, exc_type, exc_value, _tb):
        errors.append(f"{exc_type.__name__}: {exc_value}")

    tk.Tk.report_callback_exception = capture
    try:
        import os

        os.environ["PV_TAB"] = "Meetings"
        captured = {}
        real_mainloop = tk.Misc.mainloop

        def drive(self, _n=0):
            self.update_idletasks()
            self.update()
            # Find the "All meetings" checkbox and the search entry, then USE them.
            checks, entries = [], []

            def walk(widget):
                for child in widget.winfo_children():
                    if isinstance(child, tk.Checkbutton) and "All meetings" in str(
                        child.cget("text")
                    ):
                        checks.append(child)
                    if child.winfo_class() == "TEntry":
                        entries.append(child)
                    walk(child)

            walk(self)
            captured["found_toggle"] = bool(checks)
            for check in checks:
                check.invoke()          # tick it — this is what raised TypeError
                self.update()
                check.invoke()          # and untick
                self.update()
            self.destroy()

        tk.Misc.mainloop = drive
        try:
            settings.main()
        finally:
            tk.Misc.mainloop = real_mainloop

        assert captured.get("found_toggle"), "the All meetings toggle was not rendered"
        assert not errors, f"toggling cross-meeting search raised: {errors}"
    finally:
        tk.Tk.report_callback_exception = real_report


def test_meetings_tab_opens_with_an_untranscribed_recording():
    # v2.32.0 crashed the settings window on open with
    #   TclError: window "...!frame5" isn't packed
    # whenever a meeting still needed transcribing AND had no bookmarks: the
    # banner was packed relative to the highlights panel, which is pack_forget()
    # in exactly that case. Machine-independent and fully deterministic — it just
    # needs that state, which is why some installs were fine.
    _skip_if_headless()
    import tempfile

    from uistub import make_untranscribed_meeting

    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp) / "meetings"
        base.mkdir(parents=True)
        make_untranscribed_meeting(base)

        from wisprlite import meeting, meetings_tab, settings

        original = meeting.meetings_dir
        meeting.meetings_dir = lambda: base
        meetings_tab.meetings_dir = lambda: base
        try:
            result = build_window(settings.main, tab="Meetings")
            assert result["error"] is None, (
                f"the settings window failed to open: {result['error']}"
            )
        finally:
            meeting.meetings_dir = original
            meetings_tab.meetings_dir = original


def test_first_run_opens_the_guide_maximised():
    # A new install landed on the Settings form, because first_run only changed
    # the window TITLE. Someone who has just installed this needs "how do I use
    # it" first. PV_TAB is the test seam and must still win.
    _skip_if_headless()
    import os
    import tkinter as tk

    from wisprlite import settings

    seen = {}
    real_mainloop = tk.Misc.mainloop

    def capture(self, _n=0):
        self.update_idletasks()
        self.update()
        seen["title"] = self.title()
        seen["zoomed"] = self.state()
        self.destroy()

    os.environ.pop("PV_TAB", None)          # let first_run choose
    tk.Misc.mainloop = capture
    try:
        settings.main(first_run=True)
    finally:
        tk.Misc.mainloop = real_mainloop
        os.environ["PV_TAB"] = "Settings"

    assert seen.get("title") == "Set up Pipevoice"
    # The tab choice is what regressed; assert the source picks Guide on first run.
    import inspect

    source = inspect.getsource(settings.main)
    assert '"Guide" if first_run else "Settings"' in source, (
        "first run must open the Guide, not the settings form"
    )


def test_closing_the_welcome_splash_still_opens_setup():
    # show_welcome() returns False when dismissed, and setup used to be gated on
    # it — so closing the splash dropped the user straight to the tray with
    # nothing on screen, which reads as "it installed and then minimised".
    import inspect

    from wisprlite import app

    source = inspect.getsource(app)
    assert "if welcome.show_welcome():" not in source, (
        "opening the setup window must not depend on how the splash was dismissed"
    )
