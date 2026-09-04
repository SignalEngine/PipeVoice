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


def test_settings_live_in_the_tab_they_belong_to():
    """Screen-recording settings under Recordings, meeting settings under Meetings.

    Walking for the text alone is not enough - every tab frame exists in the
    tree whether or not it is the visible one, so "the widget is somewhere"
    passes even when a card is packed into the wrong tab. This checks ANCESTRY:
    the card must share a container with its own tab's intro text, and must not
    share one with the Settings form.
    """
    _skip_if_headless()
    install_platform_stubs()
    import os
    import tkinter as tk
    from wisprlite import settings

    os.environ["PV_TAB"] = "Settings"
    seen: dict[str, object] = {}
    real_mainloop = tk.Misc.mainloop

    def walk(widget):
        for child in widget.winfo_children():
            try:
                text = str(child.cget("text"))
            except Exception:
                text = ""
            if text and text not in seen:
                seen[text] = child
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

    def find(prefix):
        for text, widget in seen.items():
            if text.startswith(prefix):
                return widget
        raise AssertionError(f"no widget whose text starts with {prefix!r}")

    def ancestors(widget):
        chain, node = [], widget
        while node is not None:
            chain.append(node)
            node = getattr(node, "master", None)
        return chain

    settings_form = set(ancestors(find("Min seconds")))          # still in Advanced
    recordings_tab = set(ancestors(find("Press your screen recording hotkey")))
    meetings_tab_frames = set(ancestors(find("Press your meeting hotkey")))

    screenrec_card = set(ancestors(find("Screen recordings")))
    assert screenrec_card & (recordings_tab - settings_form), \
        "the screen-recording card is not inside the Recordings tab"
    assert not (screenrec_card & (settings_form - recordings_tab)), \
        "the screen-recording card is still in the Settings form"

    meeting_card = set(ancestors(find("Meeting hotkey")))
    assert meeting_card & (meetings_tab_frames - settings_form), \
        "the meeting settings are not inside the Meetings tab"
    assert not (meeting_card & (settings_form - meetings_tab_frames)), \
        "the meeting settings are still in the Settings form"

    assert "Recordings" in seen, "the Recordings tab button was never mounted"


def test_the_settings_link_swaps_the_view_and_swaps_it_back():
    """Opening settings must hide the browser, and closing must bring it back.

    Packed ABOVE the list instead, the settings shoved it off the bottom and
    then ran off the bottom themselves — neither usable. And a toggle that only
    goes one way strands the user in a settings panel with no way out.
    """
    _skip_if_headless()
    install_platform_stubs()
    import os
    import tkinter as tk
    from wisprlite import settings

    os.environ["PV_TAB"] = "Recordings"
    outcome: dict[str, bool] = {}
    real_mainloop = tk.Misc.mainloop

    def find_visible_link(widget):
        for child in widget.winfo_children():
            try:
                if str(child.cget("text")).startswith("Settings  ") and child.winfo_ismapped():
                    return child
            except Exception:
                pass
            found = find_visible_link(child)
            if found is not None:
                return found
        return None

    def find_by_text(widget, prefix):
        for child in widget.winfo_children():
            try:
                if str(child.cget("text")).startswith(prefix):
                    return child
            except Exception:
                pass
            found = find_by_text(child, prefix)
            if found is not None:
                return found
        return None

    def stub_mainloop(self, _n=0):
        try:
            self.update_idletasks()
            self.update()
            intro = find_by_text(self, "Press your screen recording hotkey")
            link = find_visible_link(self)
            outcome["found_link"] = link is not None
            outcome["browser_before"] = bool(intro and intro.winfo_ismapped())
            link.event_generate("<Button-1>")
            self.update_idletasks()
            self.update()
            outcome["browser_hidden"] = not intro.winfo_ismapped()
            hotkey = find_by_text(self, "Screen recording hotkey")
            outcome["settings_shown"] = bool(hotkey and hotkey.winfo_ismapped())
            link.event_generate("<Button-1>")
            self.update_idletasks()
            self.update()
            outcome["browser_back"] = intro.winfo_ismapped()
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

    assert outcome.get("found_link"), "no visible Settings link on the Recordings tab"
    assert outcome.get("browser_before"), "the browser should start visible"
    assert outcome.get("browser_hidden"), "opening settings must hide the browser"
    assert outcome.get("settings_shown"), "the settings never became visible"
    assert outcome.get("browser_back"), "closing settings must restore the browser"


def _agent_app(**over):
    """An App shaped just enough to run the agent screen-recording path."""
    import types
    from unittest import mock
    from wisprlite.app import App, ScreenrecUI

    app = App.__new__(App)
    app._screenrec = None
    app._screenrec_selecting = False
    app._screenrec_agent = None
    app._screenrec_agent_result = None
    app._screenrec_ui = ScreenrecUI()
    app._finished_recording = None
    app.paused = False
    app.overlay = mock.Mock()
    app._notify = mock.Mock()
    app._fail = mock.Mock()
    app.cfg = types.SimpleNamespace(
        screenrec_hotkey="ctrl+alt+r",
        screenrec_destination="root@vps:/inbox/",
        screenrec_keep_local=True,
    )
    for k, v in over.items():
        setattr(app, k, v) if not hasattr(app.cfg, k) else setattr(app.cfg, k, v)
    return app


def test_an_agent_call_never_uploads_the_recording():
    """The agent gets the local path. Sending it to a remote host is a second,
    larger act, and the agent asking for a clip did not consent to it."""
    import threading
    from unittest import mock
    from wisprlite.app import App
    from wisprlite import screenrec

    app = _agent_app()
    recording = mock.Mock()
    recording.errors = []
    recording.stop.return_value = pathlib.Path("/out/2026-08-12 10-00-00.mp4")
    recording.stem = "2026-08-12 10-00-00"
    recording.audio_path = pathlib.Path("/out/2026-08-12 10-00-00.wav")
    app._screenrec = recording
    app._screenrec_agent = threading.Event()
    app._transcribe_recording = mock.Mock(return_value=None)

    with mock.patch.object(screenrec, "send") as send, \
         mock.patch.object(App, "_ask_name_in_pill") as ask:
        App._finish_screen_recording(app)

    assert not send.called, "an agent-driven recording must never be uploaded"
    assert not ask.called, "an agent-driven recording must not stop for a name"
    assert app._screenrec_agent_result["status"] == "ok"
    assert app._screenrec_agent_result["uploaded"] is False
    assert app._screenrec_agent_result["video_path"].endswith(".mp4")


def test_an_agent_cannot_record_without_a_way_to_stop_or_while_paused():
    from unittest import mock
    from wisprlite.app import App

    paused = _agent_app()
    paused.paused = True
    assert App.on_agent_record_screen(paused)["status"] == "error"

    no_hotkey = _agent_app(screenrec_hotkey="")
    result = App.on_agent_record_screen(no_hotkey)
    assert result["status"] == "error"
    assert "hotkey" in result["error"], "must say WHY, not just fail"

    busy = _agent_app()
    busy._screenrec = object()
    assert App.on_agent_record_screen(busy)["status"] == "error"


def test_esc_during_an_agent_recording_writes_nothing():
    from unittest import mock
    from wisprlite.app import App

    app = _agent_app()
    app._begin_screen_recording = mock.Mock()   # leaves _screenrec None, as Esc does

    result = App.on_agent_record_screen(app, prompt="show me the bug")

    assert result["status"] == "cancelled"
    app._notify.assert_called_once_with("show me the bug")
    assert app._screenrec_agent is None, "the waiter must not be left dangling"


def test_a_failed_agent_recording_releases_the_caller():
    """Every early return must wake the agent, or it blocks for its full timeout
    with no idea what happened."""
    import threading
    from unittest import mock
    from wisprlite.app import App

    app = _agent_app()
    recording = mock.Mock()
    recording.errors = ["RuntimeError: no frames were captured"]
    recording.stop.return_value = None
    app._screenrec = recording
    waiter = threading.Event()
    app._screenrec_agent = waiter

    App._finish_screen_recording(app)

    assert waiter.is_set(), "the agent must be woken even when the recording failed"
    assert app._screenrec_agent_result["status"] == "error"
    assert "no frames" in app._screenrec_agent_result["error"]


def test_quitting_does_not_open_the_naming_dialog():
    """Quit must still finish the recording, but never wait on a modal.

    ask_name() blocks on wait_window. On the shutdown path nobody is looking at
    a window that is already closing, so the dialog would hold the quit open
    until it was hunted down and dismissed.
    """
    import types
    from unittest import mock
    from wisprlite.app import App, ScreenrecUI
    from wisprlite import screenrec

    app = App.__new__(App)
    app._screenrec_agent = None
    app._screenrec_agent_result = None
    app._screenrec_ui = ScreenrecUI()
    app._finished_recording = None
    recording = types.SimpleNamespace(
        stem="2026-08-12 10-33-25",
        errors=[],
        stop=lambda: pathlib.Path("2026-08-12 10-33-25.mp4"),
        audio_path=pathlib.Path("nope.wav"),
    )
    app._screenrec = recording
    app.overlay = mock.Mock()
    app.cfg = types.SimpleNamespace(screenrec_destination="", screenrec_keep_local=True)
    app._fail = mock.Mock()
    app._transcribe_recording = mock.Mock(return_value=None)

    with mock.patch.object(App, "_ask_name_in_pill") as ask:
        App._finish_screen_recording(app, ask=False)
        assert not ask.called, "shutdown must not wait for a name it cannot be given"

    app._screenrec = recording
    with mock.patch.object(App, "_ask_name_in_pill", return_value="") as ask:
        App._finish_screen_recording(app)
        assert ask.called, "the normal stop path still asks for a name"

    # And prove quit() is the caller that passes it — asserting on the method
    # in isolation would pass just as happily if quit() never set the flag.
    quitting = App.__new__(App)
    quitting._screenrec = recording
    quitting._screenrec_finishing = None
    quitting._stop = mock.Mock()
    quitting._voice_mgrs, quitting._picker_mgr = [], None
    quitting._meeting_active = False
    for name in ("stop_mcp_bridge", "hotkeys", "clip_hotkeys", "meeting_hotkeys",
                 "screenrec_hotkeys", "bookmark_hotkeys", "overlay", "tray"):
        setattr(quitting, name, mock.Mock())
    quitting._finish_screen_recording = mock.Mock()

    App.quit(quitting)

    quitting._finish_screen_recording.assert_called_once_with(ask=False)


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


def test_pressing_stop_twice_does_not_start_a_new_recording():
    """Stop clears _screenrec immediately, then muxes/uploads for seconds while
    the pill is still up. A second press in that window must do nothing, not
    fall through to "nothing is recording" and open a region selector."""
    from unittest import mock
    from wisprlite.app import App

    app = App.__new__(App)
    app._screenrec = object()
    app._fail = mock.Mock()
    app.toggle_screen_recording = mock.Mock()

    App._screenrec_action(app, "stop")
    assert app.toggle_screen_recording.call_count == 1

    app._screenrec = None                     # as the finish path leaves it
    App._screenrec_action(app, "stop")
    assert app.toggle_screen_recording.call_count == 1, \
        "a second Stop started a brand-new recording"

    # Pause and resume on a finished recording are equally no-ops.
    App._screenrec_action(app, "pause")
    App._screenrec_action(app, "resume")
    assert not app._fail.called


def test_the_finished_pill_never_announces_the_scp_destination():
    """It used to sit open reading "sent to root@host:/srv/inbox/" - a string
    the user configured, that never changes, and that they had to dismiss.

    Drives the REAL finish path with a destination configured: asserting on a
    hand-built state object cannot catch the caller putting a host back in.
    """
    from unittest import mock
    from wisprlite.app import App
    from wisprlite import screenrec

    app = _agent_app()
    app._screenrec_agent = None
    recording = mock.Mock()
    recording.errors = []
    recording.stem = "2026-08-12 17-32-18"
    recording.stop.return_value = pathlib.Path("/out/2026-08-12 17-32-18.mp4")
    recording.audio_path = pathlib.Path("/out/2026-08-12 17-32-18.wav")
    app._screenrec = recording
    app._transcribe_recording = mock.Mock(return_value=None)

    with mock.patch.object(App, "_ask_name_in_pill", return_value=""), \
         mock.patch.object(screenrec, "send", return_value=(True, "ok")):
        App._finish_screen_recording(app)

    state = app._screenrec_ui.snapshot()
    assert state["phase"] == "done"
    blob = " ".join(str(v) for v in state.values())
    assert "root@vps" not in blob and ":/inbox/" not in blob, \
        f"the pill is naming a destination again: {blob}"
    assert "sent" in state["title"].lower(), \
        "it must still say the clip went somewhere, just not where"


def test_a_failed_send_does_not_strand_the_pill_on_a_progress_bar():
    """The pill is phase-driven, so any path that does not reach "done" must
    put it away. Left as-is the user watches a sweeping bar for ever."""
    from unittest import mock
    from wisprlite.app import App
    from wisprlite import screenrec

    app = _agent_app()
    app._screenrec_agent = None
    recording = mock.Mock()
    recording.errors = []
    recording.stem = "2026-08-12 17-32-18"
    recording.stop.return_value = pathlib.Path("/out/clip.mp4")
    recording.audio_path = pathlib.Path("/out/clip.wav")
    app._screenrec = recording
    app._transcribe_recording = mock.Mock(return_value=None)

    with mock.patch.object(App, "_ask_name_in_pill", return_value=""), \
         mock.patch.object(screenrec, "send", return_value=(False, "connection refused")):
        App._finish_screen_recording(app)

    assert app._screenrec_ui.snapshot()["phase"] == "recording", \
        "a failed send left the pill mid-flight"
    assert app.overlay.hide.called, "the pill must be put away, not left up"
    assert app._fail.called, "and the failure must still be reported"

    # An exception anywhere in the flow must land the same way.
    app2 = _agent_app()
    app2._screenrec_agent = None
    app2._screenrec = recording
    app2._transcribe_recording = mock.Mock(side_effect=RuntimeError("boom"))
    with mock.patch.object(App, "_ask_name_in_pill", return_value=""):
        App._finish_screen_recording(app2)
    assert app2._screenrec_ui.snapshot()["phase"] == "recording"


def test_a_failed_paste_still_leaves_the_words_in_history_and_on_the_clipboard():
    """James, 2026-08-12: it "records the dictation, polishes it, then does not
    paste into the box" - and the transcript was missing from history too.

    history.record ran AFTER type_text inside a try that has only a finally, so
    a raising keyboard backend skipped it entirely. Words you have already said
    must not be destroyed by a failure to deliver them.
    """
    import types
    from unittest import mock
    from wisprlite.app import App
    from wisprlite import history

    recorded = []
    app = App.__new__(App)
    app.cfg = types.SimpleNamespace(
        history_enabled=True, replacements={}, voice_commands=False,
        paste_speed="normal", min_seconds=0.0, engine="gemini", language="",
    )
    app._clipboard_only = False
    app._active = {}
    app._fg_ctx = {}
    app.overlay = mock.Mock()
    app._fail = mock.Mock()
    app._beep = mock.Mock()
    app._set_icon = mock.Mock()

    with mock.patch.object(history, "record", side_effect=lambda t, k: recorded.append((t, k))), \
         mock.patch("wisprlite.app.type_text", side_effect=RuntimeError("no window")), \
         mock.patch("wisprlite.app.copy_clipboard", return_value=True) as clip:
        App._deliver(app, "hello there", False, "type", False)

    assert recorded == [("hello there", "typed")], \
        f"the transcript was lost when typing failed: {recorded}"
    clip.assert_called_once_with("hello there")
    states = [c.args[0] for c in app.overlay.set_state.call_args_list]
    assert "error" in states, "a failed paste must say so, not look like success"
    assert "done" not in states, \
        "a failed paste must never flash 'done' first — two answers to 'did that " \
        "work?', in the order that reads as yes"


def test_the_settings_link_says_which_state_it_is_in():
    """It read "Settings" whether the panel was open or shut, so the screen gave
    no clue which state you were in. It must say what pressing it will DO."""
    _skip_if_headless()
    install_platform_stubs()
    import os
    import tkinter as tk
    from wisprlite import settings

    os.environ["PV_TAB"] = "Recordings"
    seen = {}
    real_mainloop = tk.Misc.mainloop

    def find(widget, prefix):
        for child in widget.winfo_children():
            try:
                if str(child.cget("text")).startswith(prefix) and child.winfo_ismapped():
                    return child
            except Exception:
                pass
            found = find(child, prefix)
            if found is not None:
                return found
        return None

    def stub_mainloop(self, _n=0):
        try:
            self.update_idletasks(); self.update()
            # "Settings" alone also matches the TAB header, which is a
            # different widget entirely. The gear is the link.
            link = find(self, "Settings  \u2699")
            seen["shut"] = str(link.cget("text"))
            link.event_generate("<Button-1>")
            self.update_idletasks(); self.update()
            seen["open"] = str(link.cget("text"))
            # Pressing the tab header must be a way out of the settings.
            tab = find(self, "Recordings")
            tab.event_generate("<Button-1>")
            self.update_idletasks(); self.update()
            seen["after_tab"] = str(link.cget("text"))
            intro = find(self, "Press your screen recording hotkey")
            seen["browser_back"] = bool(intro and intro.winfo_ismapped())
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

    assert seen["shut"] != seen["open"], \
        f"the link reads {seen['shut']!r} in both states — no indication at all"
    assert "Close" in seen["open"], seen["open"]
    assert seen["after_tab"] == seen["shut"], \
        "clicking the tab header must close the settings and reset the link"
    assert seen["browser_back"], "the tab header must bring the browser back"


def test_the_local_fallback_loads_the_model_once_not_every_utterance():
    """Reported as "cloud issues, reverting to local, taking a lot longer".

    _fallback built a fresh LocalEngine per failure, and constructing one loads
    the entire Whisper model. A bad key meant paying that load on every single
    utterance. It must go through the engine cache like every other path.
    """
    import types
    from unittest import mock
    from wisprlite.app import App

    builds = []
    app = App.__new__(App)
    app.cfg = types.SimpleNamespace(
        engine="gemini", local_model_size="base.en", local_device="auto",
        local_compute_type="int8", language="en-US",
    )
    app._active = {}
    app._engines = {}
    app.overlay = mock.Mock()
    app._fail = mock.Mock()

    class FakeLocal:
        def __init__(self):
            builds.append(1)
        def start_session(self, on_partial=None):
            return types.SimpleNamespace(finish=lambda audio: "words")

    app._build_engine = lambda name=None: (FakeLocal() if name == "local"
                                           else pytest.fail("wrong engine: " + str(name)))

    for _ in range(5):
        assert App._fallback(app, object(), RuntimeError("cloud down")) == "words"

    assert len(builds) == 1, \
        f"the model was loaded {len(builds)} times for 5 utterances"
    assert not app._fail.called


def _np_zeros(n):
    import numpy as np
    return np.zeros(n, dtype="float32")


def test_a_press_too_short_to_transcribe_closes_the_engine_socket():
    """A tap under min_seconds opened a Deepgram websocket on key-down and then
    walked away from it. Ten seconds later the server killed it with a 1011 and
    two ERROR lines - 59 of them in one of James's logs, 5% of all dictations,
    every one a live connection held open for nothing.
    """
    import types
    from unittest import mock
    from wisprlite.app import App

    app = App.__new__(App)
    app.cfg = types.SimpleNamespace(min_seconds=0.35)
    app._session = mock.Mock()
    app.overlay = mock.Mock()
    app._set_icon = mock.Mock()
    app._release = mock.Mock()
    app._active = {}
    app._fg_ctx = {}

    session = app._session
    App._finish(app, _np_zeros(16000), 0.1)

    session.cancel.assert_called_once_with()


def test_a_short_press_with_no_session_does_not_explode():
    """The early return also runs when start_session failed, so _session is None."""
    import types
    from unittest import mock
    from wisprlite.app import App

    app = App.__new__(App)
    app.cfg = types.SimpleNamespace(min_seconds=0.35)
    app._session = None
    app.overlay = mock.Mock()
    app._set_icon = mock.Mock()
    app._release = mock.Mock()
    app._active = {}
    app._fg_ctx = {}

    App._finish(app, _np_zeros(16000), 0.1)   # must not raise

    app.overlay.hide.assert_called_once_with()


def test_a_silent_polish_failure_is_shown_instead_of_hidden():
    """Flow mode ON, provider reachable, nothing came back - a dead model id, a
    revoked key, a quota wall. cleanup.clean() returns None and the raw text was
    delivered with only a log line, so the words just got worse with no way to
    know why. One log carried 235 of these across three months.
    """
    import types
    from unittest import mock
    from wisprlite.app import App
    from wisprlite import cleanup

    app = App.__new__(App)
    app.cfg = types.SimpleNamespace(
        cleanup_provider="gemini", cleanup_model="gemini-2.5-flash",
        language="", speech_notes="",
    )
    app._eff = lambda key: {"ai_cleanup": True}.get(key, "")
    app.overlay = mock.Mock()

    with mock.patch.object(cleanup, "provider_ready", return_value=True), \
         mock.patch.object(cleanup, "clean", return_value=None), \
         mock.patch.object(cleanup, "last_error", return_value="model no longer available"):
        out = App._polish(app, "raw words")

    assert out == "raw words", "the raw text must still be delivered"
    assert app._polish_error == "model no longer available", \
        "the failure must be recorded, not swallowed"


def test_a_successful_polish_leaves_no_warning_behind():
    """The flag is per-utterance. One bad polish must not mark every later one."""
    import types
    from unittest import mock
    from wisprlite.app import App
    from wisprlite import cleanup

    app = App.__new__(App)
    app.cfg = types.SimpleNamespace(
        cleanup_provider="gemini", cleanup_model="m", language="", speech_notes="",
    )
    app._eff = lambda key: {"ai_cleanup": True}.get(key, "")
    app.overlay = mock.Mock()
    app._polish_error = "stale failure from last time"

    with mock.patch.object(cleanup, "provider_ready", return_value=True), \
         mock.patch.object(cleanup, "clean", return_value="Raw words."):
        out = App._polish(app, "raw words")

    assert out == "Raw words."
    assert app._polish_error is None, "a good polish must clear the previous warning"


def test_the_polish_warning_never_overwrites_a_delivery_failure():
    """Two things went wrong at once: the polish failed AND the paste failed.
    The overlay has one line, and 'couldn't type there' is the news that costs
    the user words. It must be the message left on screen."""
    import types
    from unittest import mock
    from wisprlite.app import App
    from wisprlite import history

    app = App.__new__(App)
    app.cfg = types.SimpleNamespace(
        history_enabled=True, replacements={}, paste_speed="normal",
    )
    app.overlay = mock.Mock()
    app._fail = mock.Mock()

    with mock.patch.object(history, "record"), \
         mock.patch("wisprlite.app.type_text", side_effect=RuntimeError("no window")), \
         mock.patch("wisprlite.app.copy_clipboard", return_value=True):
        ok = App._deliver(app, "hello", False, "type", False)

    assert ok is False, \
        "a failed paste must report failure so the polish warning stays off the screen"


def test_the_reason_is_pulled_out_of_the_provider_json_blob():
    """Providers return a three-line JSON envelope; the overlay has one line."""
    from wisprlite.cleanup import _short_reason

    exc = Exception(
        "Error code: 404 - [{'error': {'code': 404, 'message': 'This model "
        "models/gemini-2.5-flash is no longer available.', 'status': 'NOT_FOUND'}}]"
    )
    assert _short_reason(exc) == "This model models/gemini-2.5-flash is no longer available"
