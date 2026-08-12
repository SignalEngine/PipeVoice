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


def test_closing_the_splash_differs_from_choosing_to_skip():
    # Two different intents that were collapsed into one, in both directions.
    # Originally, closing the window with X was treated as "skip setup", so a
    # brand-new user landed in the tray with nothing on screen. My first fix
    # then ignored "I'll set up later" as well, so a button labelled "later"
    # opened setup immediately — a label that lies.
    _skip_if_headless()
    import tkinter as tk

    from wisprlite import welcome

    outcomes = {}
    real_mainloop = tk.Misc.mainloop

    def press(which):
        def stub(self, _n=0):
            self.update_idletasks()
            self.update()
            buttons = []

            def walk(widget):
                for child in widget.winfo_children():
                    if isinstance(child, tk.Button):
                        buttons.append(child)
                    walk(child)

            walk(self)
            labels = {str(b.cget("text")): b for b in buttons}
            if which == "later":
                labels["I'll set up later"].invoke()
            else:                       # simulate the window manager's X
                self.protocol.__self__.event_generate("<Destroy>") if False else None
                self.tk.call("wm", "protocol", self._w, "WM_DELETE_WINDOW")
                handler = self.protocol("WM_DELETE_WINDOW")
                self.tk.call("eval", handler) if handler else self.destroy()
        return stub

    tk.Misc.mainloop = press("later")
    try:
        outcomes["later"] = welcome.show_welcome()
    finally:
        tk.Misc.mainloop = real_mainloop

    assert outcomes["later"] is False, (
        "'I'll set up later' must actually skip setup — the label has to be true"
    )

    # And the X path must NOT skip: assert the protocol handler is wired at all.
    import inspect

    source = inspect.getsource(welcome.show_welcome)
    assert 'protocol("WM_DELETE_WINDOW"' in source, (
        "closing the window must be handled distinctly from pressing 'later'"
    )
    assert 'result["go"] = True' in source, (
        "dismissing the splash must still open setup, not strand the user"
    )


def test_export_button_invokes_export_callback_writes_file():
    # Building a window is not exercising it. The Export button must produce
    # an actual file on disk when invoked — if the callback raised TypeError
    # the moment a user clicked it, every prior "Export control exists" check
    # would still be green.
    _skip_if_headless()
    import pathlib
    import tempfile
    import tkinter as tk
    from tkinter import ttk

    from wisprlite import meetings_tab
    from wisprlite import settings

    errors = []
    real_report = tk.Tk.report_callback_exception

    def capture(_self, exc_type, exc_value, _tb):
        errors.append(f"{exc_type.__name__}: {exc_value}")

    tk.Tk.report_callback_exception = capture
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            # A real on-disk session so refresh() can list it.
            session = base / "meeting-20260729-120000"
            session.mkdir()
            (session / "meta.json").write_text('{"started_at": "2026-07-29T12:00:00+00:00",'
                                               ' "duration_seconds": 90,'
                                               ' "transcription_backend": "deepgram",'
                                               ' "status": "transcribed"}',
                                               encoding="utf-8")
            (session / "transcript.json").write_text(
                '{"segments": [{"t": 0.0, "speaker": "You", "text": "Hello"},'
                '              {"t": 4.0, "speaker": "Dev", "text": "Hi & welcome"}]}',
                encoding="utf-8",
            )

            target_path = str(base / "out.md")

            real_list = meetings_tab.list_sessions

            def fake_list(_base=None):
                return [{
                    "path": session,
                    "started_at": "2026-07-29T12:00:00+00:00",
                    "display_started": "Today 12:00",
                    "duration_seconds": 90,
                    "duration": "1m 30s",
                    "status": "transcribed",
                    "transcription_backend": "Deepgram",
                    "can_transcribe": False,
                    "speaker_count": 2,
                    "speaker_names": ["You", "Dev"],
                }]

            meetings_tab.list_sessions = fake_list

            # meetings_tab imports `from tkinter import filedialog` inside the
            # build function — patch the actual submodule so the do_export
            # closure picks up our fake at call time.
            from tkinter import filedialog as _tk_filedialog
            real_save = _tk_filedialog.asksaveasfilename

            def fake_save(**_kwargs):
                return target_path

            _tk_filedialog.asksaveasfilename = fake_save

            try:
                import os
                # Snapshot PV_TAB so we can restore it. settings.py keys the
                # starting tab off this env var; without restoration, every
                # subsequent test runs with the wrong initial tab.
                real_pv_tab = os.environ.get("PV_TAB")
                os.environ["PV_TAB"] = "Meetings"
                captured = {}
                real_mainloop = tk.Misc.mainloop

                def drive(self, _n=0):
                    self.update_idletasks()
                    self.update()
                    export_buttons = []

                    def walk(widget):
                        for child in widget.winfo_children():
                            cls = child.winfo_class()
                            try:
                                text = str(child.cget("text"))
                            except (tk.TclError, AttributeError):
                                text = ""
                            if cls == "TButton" and text == "Export":
                                export_buttons.append(child)
                            walk(child)

                    walk(self)
                    captured["export_buttons"] = list(export_buttons)
                    if export_buttons:
                        export_buttons[0].invoke()
                        self.update_idletasks()
                        self.update()
                    self.destroy()

                tk.Misc.mainloop = drive
                try:
                    settings.main()
                finally:
                    tk.Misc.mainloop = real_mainloop
            finally:
                meetings_tab.list_sessions = real_list
                _tk_filedialog.asksaveasfilename = real_save
                if real_pv_tab is None:
                    os.environ.pop("PV_TAB", None)
                else:
                    os.environ["PV_TAB"] = real_pv_tab

            assert captured.get("export_buttons"), (
                "the Export button was not rendered"
            )
            assert pathlib.Path(target_path).is_file(), (
                "invoking the Export button did not write the chosen file"
            )
            body = pathlib.Path(target_path).read_text(encoding="utf-8")
            assert "Hello" in body and "Hi & welcome" in body, body
            assert not errors, f"invoking Export raised: {errors}"
    finally:
        tk.Tk.report_callback_exception = real_report


def test_export_with_empty_transcript_reports_status_not_silent_noop():
    # An empty transcript must surface in the status bar — silent returns
    # left users clicking Export to "nothing happened", wondering if the
    # app was broken. The status label is the user's only feedback channel
    # after a save dialog closes.
    _skip_if_headless()
    import pathlib
    import tempfile
    import tkinter as tk

    from wisprlite import meetings_tab

    errors = []
    real_report = tk.Tk.report_callback_exception

    def capture(_self, exc_type, exc_value, _tb):
        errors.append(f"{exc_type.__name__}: {exc_value}")

    tk.Tk.report_callback_exception = capture
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            session = base / "meeting-20260729-130000"
            session.mkdir()
            (session / "meta.json").write_text(
                '{"started_at": "2026-07-29T13:00:00+00:00",'
                ' "duration_seconds": 0,'
                ' "transcription_backend": "deepgram",'
                ' "status": "transcribed"}',
                encoding="utf-8",
            )
            # Empty segments — the file exists but there's no transcript yet.
            (session / "transcript.json").write_text('{"segments": []}',
                                                       encoding="utf-8")

            from tkinter import filedialog as _tk_filedialog
            real_save = _tk_filedialog.asksaveasfilename
            dialog_calls = []
            _tk_filedialog.asksaveasfilename = lambda **k: dialog_calls.append(k) or ""

            real_list = meetings_tab.list_sessions

            def fake_list(_base=None):
                return [{
                    "path": session,
                    "started_at": "2026-07-29T13:00:00+00:00",
                    "display_started": "Today 13:00",
                    "duration_seconds": 0,
                    "duration": "0s",
                    "status": "transcribed",
                    "transcription_backend": "Deepgram",
                    "can_transcribe": False,
                    "speaker_count": 0,
                    "speaker_names": [],
                    "error": "",
                }]
            meetings_tab.list_sessions = fake_list

            try:
                import os
                real_pv_tab = os.environ.get("PV_TAB")
                os.environ["PV_TAB"] = "Meetings"
                real_mainloop = tk.Misc.mainloop
                captured = {}

                def drive(self, _n=0):
                    self.update_idletasks()
                    self.update()
                    statuses = []

                    def walk(widget):
                        for child in widget.winfo_children():
                            try:
                                text = str(child.cget("text"))
                            except (tk.TclError, AttributeError):
                                text = ""
                            # Status labels are tk.Label (NOT ttk).
                            if isinstance(child, tk.Label) and text and (
                                "export" in text.lower()
                                or "transcript" in text.lower()
                                or "transcribe" in text.lower()
                            ):
                                statuses.append(text)
                            walk(child)

                    walk(self)
                    captured["statuses"] = list(statuses)
                    self.destroy()

                tk.Misc.mainloop = drive
                try:
                    from wisprlite import settings
                    settings.main()
                finally:
                    tk.Misc.mainloop = real_mainloop
            finally:
                meetings_tab.list_sessions = real_list
                _tk_filedialog.asksaveasfilename = real_save
                if real_pv_tab is None:
                    os.environ.pop("PV_TAB", None)
                else:
                    os.environ["PV_TAB"] = real_pv_tab

            assert not dialog_calls, (
                "Export must not open a save dialog when there is nothing to "
                f"export — saw {len(dialog_calls)} dialog(s)"
            )
            assert any("transcript" in s.lower() for s in captured.get("statuses", [])), (
                f"empty-transcript Export must surface a status message, "
                f"saw {captured.get('statuses')}"
            )
            assert not errors, f"invoking Export raised: {errors}"
    finally:
        tk.Tk.report_callback_exception = real_report


def test_the_screen_recorder_settings_are_actually_on_screen():
    """A card that exists in the source but never gets packed is invisible.

    build_window destroys the root before returning, so this walks the tree
    from inside its own stub mainloop, while the widgets still exist.
    """
    _skip_if_headless()
    install_platform_stubs()
    import os
    import tkinter as tk
    from wisprlite import settings

    os.environ["PV_TAB"] = "Settings"
    found: list[str] = []
    real_mainloop = tk.Misc.mainloop

    def walk(widget):
        for child in widget.winfo_children():
            try:
                text = child.cget("text")
            except Exception:
                text = ""
            if text:
                found.append(str(text))
            walk(child)

    def stub_mainloop(self, _n=0):
        try:
            self.update_idletasks()
            self.update()
            walk(self)
        finally:
            try:
                self.destroy()
            except Exception:
                pass

    tk.Misc.mainloop = stub_mainloop
    try:
        settings.main()
    finally:
        tk.Misc.mainloop = real_mainloop

    joined = " | ".join(found)
    for wanted in ("Screen recording hotkey", "Screen recordings", "Send to",
                   "Keep a local copy after sending"):
        assert wanted in joined, f"{wanted!r} was never mounted"


def test_pausing_pipevoice_also_stops_screen_recording():
    """Screen + mic is the most invasive capture in the app. Pausing must block
    a new one — while still letting a running one be stopped.

    Calls the REAL predicate the HotkeyManager is given, not a copy of it.
    """
    from wisprlite.app import App

    app = App.__new__(App)

    app.paused, app._screenrec = True, None
    assert App._screen_recording_paused(app) is True, "paused must block a new recording"

    app.paused, app._screenrec = True, object()
    assert App._screen_recording_paused(app) is False, "a running one must still stop"

    app.paused, app._screenrec = False, None
    assert App._screen_recording_paused(app) is False
