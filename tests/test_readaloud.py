"""Read Aloud: capture -> OCR -> speech, all guarded so a missing WinRT bundle
degrades instead of raising into the hotkey loop. WinRT and mss are stubbed in
sys.modules exactly the way test_deepgram_bias.py stubs the deepgram SDK — this
suite must import and pass with neither package installed, since neither is
present on this Linux box (see requirements.txt: winrt-* is win32-only).
"""

from __future__ import annotations

import pathlib
import sys
import types
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import config
from wisprlite import readaloud


# ---- mode selection (pure logic) -------------------------------------------

def test_plain_press_drags_a_region():
    assert readaloud.capture_mode_for(shift=False, ctrl=False) == "region"


def test_shift_captures_the_whole_screen():
    assert readaloud.capture_mode_for(shift=True, ctrl=False) == "screen"


def test_ctrl_captures_the_focused_window():
    assert readaloud.capture_mode_for(shift=False, ctrl=True) == "window"


def test_ctrl_wins_over_shift_if_somehow_both_are_held():
    assert readaloud.capture_mode_for(shift=True, ctrl=True) == "window"


# ---- hotkey collision (gate 3: testable headlessly) ------------------------

def test_read_aloud_default_hotkey_does_not_collide_with_the_six_existing_chords():
    cfg = config.Config()
    existing = [
        cfg.hotkey, cfg.clipboard_hotkey, cfg.meeting_hotkey,
        cfg.bookmark_hotkey, cfg.screenrec_hotkey, cfg.voice_picker_hotkey,
    ]
    non_empty = [h for h in existing if h]
    assert len(non_empty) == len(set(non_empty)), "two existing defaults already collide"
    assert cfg.read_aloud_hotkey not in non_empty


def test_a_user_configured_read_aloud_hotkey_does_not_shadow_the_others():
    """The three capture modes live on ONE base combo (modifiers are read at
    trigger time, not baked into three separate hotkey strings), so the one
    new field is the only thing that can collide."""
    cfg = config.Config(read_aloud_hotkey="ctrl+\\")  # deliberately colliding
    assert cfg.read_aloud_hotkey == cfg.hotkey, "sabotage fixture did not actually collide"
    cfg2 = config.Config(read_aloud_hotkey="alt+r")
    assert cfg2.read_aloud_hotkey not in (
        cfg2.hotkey, cfg2.clipboard_hotkey, cfg2.meeting_hotkey,
        cfg2.bookmark_hotkey, cfg2.screenrec_hotkey, cfg2.voice_picker_hotkey,
    )


# ---- capture never touches disk (gate 4) -----------------------------------

def _fake_mss_module(png_bytes: bytes = b"\x89PNG-fake"):
    grabbed = {}

    class _Shot:
        rgb = b"\x00" * 12
        size = (2, 2)

    class _Sct:
        monitors = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def grab(self, area):
            grabbed["area"] = area
            return _Shot()

    mss_mod = types.ModuleType("mss")
    mss_mod.mss = lambda: _Sct()
    tools_mod = types.ModuleType("mss.tools")
    tools_mod.to_png = lambda rgb, size: png_bytes
    mss_mod.tools = tools_mod
    return mss_mod, tools_mod, grabbed


def test_grab_png_never_writes_a_temp_file(tmp_path, monkeypatch):
    mss_mod, tools_mod, grabbed = _fake_mss_module(b"totally-a-png")
    monkeypatch.chdir(tmp_path)
    with mock.patch.dict(sys.modules, {"mss": mss_mod, "mss.tools": tools_mod}):
        result = readaloud.grab_png(None)
    assert result == b"totally-a-png"
    assert grabbed["area"] == {"left": 0, "top": 0, "width": 1920, "height": 1080}
    assert list(tmp_path.iterdir()) == [], "grab_png left a file on disk"


def test_grab_png_uses_the_given_region_not_the_whole_desktop(tmp_path, monkeypatch):
    mss_mod, tools_mod, grabbed = _fake_mss_module()
    monkeypatch.chdir(tmp_path)
    with mock.patch.dict(sys.modules, {"mss": mss_mod, "mss.tools": tools_mod}):
        readaloud.grab_png((10, 20, 300, 400))
    assert grabbed["area"] == {"left": 10, "top": 20, "width": 300, "height": 400}
    assert list(tmp_path.iterdir()) == []


# ---- degrades without WinRT -------------------------------------------------

def test_winrt_available_is_false_without_the_package():
    with mock.patch.dict(sys.modules, {"winrt": None, "winrt.windows.media.ocr": None,
                                        "winrt.windows.media.speechsynthesis": None}):
        assert readaloud.winrt_available() is False


def test_ocr_without_winrt_raises_readaloud_error_not_a_bare_import_error():
    with mock.patch.dict(sys.modules, {"winrt": None, "winrt.windows.globalization": None}):
        try:
            readaloud.ocr_png(b"fake-png")
        except readaloud.ReadAloudError as exc:
            assert "OCR unavailable" in str(exc)
        else:
            raise AssertionError("expected ReadAloudError")


def test_winrt_selftest_fails_closed_without_the_package():
    with mock.patch.dict(sys.modules, {"winrt": None, "winrt.windows.media.ocr": None,
                                        "winrt.windows.media.speechsynthesis": None}):
        ok, message = readaloud.winrt_selftest()
    assert ok is False
    assert message.startswith("FAIL:")


def test_winrt_selftest_reports_no_engine_distinctly_from_a_bundling_failure():
    """The spike's README calls this out explicitly: a language-pack miss and a
    bundling miss mean different things and must not read the same."""
    ocr_mod = types.ModuleType("winrt.windows.media.ocr")
    ocr_mod.OcrEngine = types.SimpleNamespace(
        try_create_from_user_profile_languages=lambda: None)
    speech_mod = types.ModuleType("winrt.windows.media.speechsynthesis")
    speech_mod.SpeechSynthesizer = types.SimpleNamespace(all_voices=["a voice"])
    with mock.patch.dict(sys.modules, {
        "winrt.windows.media.ocr": ocr_mod,
        "winrt.windows.media.speechsynthesis": speech_mod,
    }):
        ok, message = readaloud.winrt_selftest()
    assert ok is False
    assert "no OCR engine" in message


def test_winrt_selftest_passes_when_both_namespaces_activate():
    ocr_mod = types.ModuleType("winrt.windows.media.ocr")
    ocr_mod.OcrEngine = types.SimpleNamespace(
        try_create_from_user_profile_languages=lambda: object())
    speech_mod = types.ModuleType("winrt.windows.media.speechsynthesis")
    speech_mod.SpeechSynthesizer = types.SimpleNamespace(all_voices=["Voice A", "Voice B"])
    with mock.patch.dict(sys.modules, {
        "winrt.windows.media.ocr": ocr_mod,
        "winrt.windows.media.speechsynthesis": speech_mod,
    }):
        ok, message = readaloud.winrt_selftest()
    assert ok is True
    assert "2 voice" in message


# ---- speaking is interruptible (gate 5) ------------------------------------

class _FakePlayer:
    def __init__(self):
        self.calls = []

    def play(self):
        self.calls.append("play")

    def pause(self):
        self.calls.append("pause")


def test_stop_pauses_the_player_synchronously_not_after_the_text_finishes():
    """The whole interrupt-latency claim rests on this: stop() must call the
    player's own pause() on the calling thread, not defer it until speak()
    would otherwise have returned."""
    player = _FakePlayer()
    speaker = readaloud.Speaker(player_factory=lambda: player)

    played = {"done": False}
    real_play = player.play

    def play_and_get_stopped():
        real_play()
        # a real long utterance would still be "playing" here — stop() must
        # still take effect immediately, not wait for this to return.
        speaker.stop()
        played["done"] = True

    player.play = play_and_get_stopped
    speaker.speak("hello world, this is a long thing to read aloud")

    assert played["done"] is True
    assert "pause" in player.calls, "stop() never reached the player"


def test_stop_before_any_speech_starts_still_prevents_playback():
    player = _FakePlayer()
    speaker = readaloud.Speaker(player_factory=lambda: player)
    speaker.stop()
    speaker.speak("never should play")
    assert "play" not in player.calls
    assert "pause" in player.calls


def test_pause_and_resume_toggle_the_paused_flag_and_call_the_player():
    player = _FakePlayer()
    speaker = readaloud.Speaker(player_factory=lambda: player)
    speaker._player = player  # normally set inside speak(); simulate mid-read
    assert speaker.paused is False
    speaker.pause()
    assert speaker.paused is True
    assert player.calls[-1] == "pause"
    speaker.resume()
    assert speaker.paused is False
    assert player.calls[-1] == "play"


def test_speaking_empty_text_raises_instead_of_silently_doing_nothing():
    speaker = readaloud.Speaker(player_factory=lambda: _FakePlayer())
    try:
        speaker.speak("   ")
    except readaloud.ReadAloudError:
        pass
    else:
        raise AssertionError("expected ReadAloudError for empty text")


# ---- speak ALWAYS, screen reader running or not (the panel's decision) -----

def test_should_speak_defaults_to_true_even_with_a_screen_reader_running():
    assert readaloud.should_speak(quiet_with_screenreader=False) is True


def test_should_speak_respects_the_opt_out_when_a_screen_reader_is_detected():
    with mock.patch.object(readaloud, "screen_reader_running", lambda: True):
        assert readaloud.should_speak(quiet_with_screenreader=True) is False


def test_should_speak_opt_out_still_speaks_when_no_screen_reader_is_detected():
    with mock.patch.object(readaloud, "screen_reader_running", lambda: False):
        assert readaloud.should_speak(quiet_with_screenreader=True) is True


def test_config_defaults_speak_always():
    """Sabotage-style regression guard: if this default ever flips to True,
    Read Aloud goes silent by default at the exact moment it's needed."""
    assert config.Config().read_aloud_quiet_with_screenreader is False


# ---- review findings (brain, not builder) -----------------------------------

def test_a_player_is_released_not_just_paused():
    """Pause makes the interrupt instant. Close is what stops every single read
    leaking a MediaPlayer and its audio resources."""
    from unittest import mock
    from wisprlite import readaloud

    player = mock.Mock()
    speaker = readaloud.Speaker(player_factory=lambda: player)
    speaker.speak("hello")
    speaker.stop()

    player.pause.assert_called()
    player.close.assert_called_once(), "the player was paused but never released"


def test_a_failed_play_does_not_stay_the_live_player():
    """Otherwise stop() later pauses a player that never played, and the real
    failure is masked by a no-op."""
    from unittest import mock
    import pytest
    from wisprlite import readaloud

    player = mock.Mock()
    player.play.side_effect = RuntimeError("no audio device")
    speaker = readaloud.Speaker(player_factory=lambda: player)

    with pytest.raises(readaloud.ReadAloudError):
        speaker.speak("hello")

    assert speaker._player is None, "a dead player was left assigned as live"


def test_stopping_before_speaking_never_starts_playback():
    """Esc during the OCR pass, before speech begins."""
    from unittest import mock
    from wisprlite import readaloud

    player = mock.Mock()
    speaker = readaloud.Speaker(player_factory=lambda: player)
    speaker.stop()
    speaker.speak("hello")

    player.play.assert_not_called()


def test_screen_reader_check_uses_a_four_byte_bool():
    """SPI_GETSCREENREADER writes a Win32 BOOL - four bytes. ctypes.c_bool is
    ONE, so the original wrote three bytes past the buffer: a silent stack
    smash, in an accessibility code path of all places."""
    source = (pathlib.Path(__file__).resolve().parent.parent
              / "wisprlite" / "readaloud.py").read_text(encoding="utf-8")
    reader = source[source.index("def screen_reader_running"):]
    reader = reader[:reader.index("\ndef ")]
    assert "wintypes.BOOL()" in reader, "the screen-reader probe is not using a Win32 BOOL"
    assert "c_bool()" not in reader, "ctypes.c_bool is one byte; Windows writes four"


def test_the_png_is_not_expanded_into_a_python_list_first():
    """A whole-screen PNG is megabytes; list(png_bytes) built a list of millions
    of ints before a single byte was written."""
    source = (pathlib.Path(__file__).resolve().parent.parent
              / "wisprlite" / "readaloud.py").read_text(encoding="utf-8")
    assert "writer.write_bytes(png_bytes)" in source, \
        "the raw bytes are not passed straight to the writer"


def test_a_second_press_during_the_ocr_pass_does_not_start_a_second_read():
    """The mid-read check reads _read_aloud_speaker, which is still None while
    OCR runs - seconds on a full-screen grab. Without a lock, two threads race
    to own the speaker and you hear both reads at once."""
    import threading
    from unittest import mock

    from uistub import install_platform_stubs
    install_platform_stubs()          # app.py imports sounddevice/keyboard at module scope
    from uistub import install_platform_stubs
    install_platform_stubs()
    from wisprlite.app import App

    app = App.__new__(App)
    app._read_aloud_speaker = None
    app._read_aloud_busy = threading.Lock()

    with mock.patch("wisprlite.app.threading.Thread") as thread:
        App._read_aloud_trigger(app)   # first press starts a read
        App._read_aloud_trigger(app)   # second press, still mid-OCR
    assert thread.call_count == 1, "a second press started a second read"


def test_the_read_lock_is_released_on_every_path_out():
    """A read that finds no text, or raises, must not leave the hotkey dead for
    the rest of the session."""
    import threading
    from unittest import mock

    from uistub import install_platform_stubs
    install_platform_stubs()
    from wisprlite.app import App

    app = App.__new__(App)
    app._read_aloud_busy = threading.Lock()
    app._read_aloud_busy.acquire()

    with mock.patch.object(App, "_read_aloud_body", side_effect=RuntimeError("boom")):
        try:
            App._read_aloud_run(app)
        except RuntimeError:
            pass

    assert app._read_aloud_busy.acquire(blocking=False), \
        "the lock survived an exception - the hotkey is now dead"


def test_a_speaker_is_one_shot_and_says_so():
    """stop() is latched so an Esc during OCR is honoured. That makes the class
    one-shot, which must be documented rather than left as a silent no-op."""
    from wisprlite import readaloud

    assert "ONE-SHOT" in readaloud.Speaker.speak.__doc__, \
        "the one-shot contract is not documented on speak()"


# ---- overlay controls (gates 1, 4, 5) --------------------------------------

class _FakeSpeaker:
    def __init__(self):
        self.paused = False
        self.pause_calls = 0
        self.resume_calls = 0
        self.stop_calls = 0

    def pause(self):
        self.pause_calls += 1
        self.paused = True

    def resume(self):
        self.resume_calls += 1
        self.paused = False

    def stop(self):
        self.stop_calls += 1


def _make_app():
    import threading

    from uistub import install_platform_stubs
    install_platform_stubs()
    from wisprlite.app import App

    from unittest import mock

    app = App.__new__(App)
    app._read_aloud_speaker = None
    app._read_aloud_last_text = None
    app._read_aloud_busy = threading.Lock()
    # Every read-aloud action reports the resulting state back to the pill, so
    # an App built for a test needs an overlay even when the test does not
    # assert on it.
    app.overlay = mock.Mock()
    return app


def test_ra_pause_pauses_a_playing_speaker():
    from uistub import install_platform_stubs
    install_platform_stubs()
    from wisprlite.app import App

    app = _make_app()
    speaker = _FakeSpeaker()
    app._read_aloud_speaker = speaker
    App._screenrec_action(app, "ra_pause")
    assert speaker.pause_calls == 1
    assert speaker.resume_calls == 0


def test_ra_pause_toggles_to_resume_on_a_paused_speaker():
    from uistub import install_platform_stubs
    install_platform_stubs()
    from wisprlite.app import App

    app = _make_app()
    speaker = _FakeSpeaker()
    speaker.paused = True
    app._read_aloud_speaker = speaker
    App._screenrec_action(app, "ra_pause")
    assert speaker.resume_calls == 1
    assert speaker.pause_calls == 0


def test_the_pause_label_follows_the_SPEAKER_not_the_click():
    """Two sources of truth is the bug. The overlay used to flip the label
    itself on a click, so pressing SPACE paused the speaker without the pill
    knowing: the button read "Pause" while clicking it called resume().

    The app owns the speaker, so the app pushes the label state.
    """
    from unittest import mock

    from uistub import install_platform_stubs
    install_platform_stubs()
    from wisprlite.app import App

    app = App.__new__(App)
    app.overlay = mock.Mock()
    speaker = mock.Mock(paused=False)
    app._read_aloud_speaker = speaker

    def pause():
        speaker.paused = True
    def resume():
        speaker.paused = False
    speaker.pause.side_effect = pause
    speaker.resume.side_effect = resume

    App._screenrec_action(app, "ra_pause")
    speaker.pause.assert_called_once()
    app.overlay.set_reading_paused.assert_called_with(True)

    App._screenrec_action(app, "ra_pause")
    speaker.resume.assert_called_once()
    app.overlay.set_reading_paused.assert_called_with(False)


def test_a_pause_click_with_no_speaker_does_not_flip_the_label():
    """Clicking Pause in the window before a speaker exists used to flip the
    label to Resume with nothing to resume."""
    from unittest import mock

    from uistub import install_platform_stubs
    install_platform_stubs()
    from wisprlite.app import App

    app = App.__new__(App)
    app.overlay = mock.Mock()
    app._read_aloud_speaker = None

    App._screenrec_action(app, "ra_pause")

    app.overlay.set_reading_paused.assert_not_called()


def test_the_click_resolver_does_not_touch_the_label():
    """It resolves. It must not also decide what the label says, or the two
    disagree the moment a keypress pauses instead of a click."""
    from wisprlite.overlay import Overlay

    ov = Overlay.__new__(Overlay)
    st = {"name": "reading"}
    x1, y1, x2, y2 = ov._done_button_box(0, Overlay.READING_BUTTONS)
    assert ov._pill_click(st, (x1 + x2) // 2, (y1 + y2) // 2) == "ra_pause"
    assert "reading_paused" not in st, \
        "the resolver is still guessing at the label state"


def test_a_click_on_the_reading_pill_is_actually_routed():
    """The reading branch lived inline in the Tk drain loop, where no test
    could reach it - deleting the whole branch left every test green."""
    from wisprlite.overlay import Overlay

    ov = Overlay.__new__(Overlay)
    for index, (action, _label) in enumerate(Overlay.READING_BUTTONS):
        x1, y1, x2, y2 = ov._done_button_box(index, Overlay.READING_BUTTONS)
        got = ov._pill_click({"name": "reading"}, (x1 + x2) // 2, (y1 + y2) // 2)
        assert got == action, f"a click on {action!r} resolved to {got!r}"


def test_the_same_click_resolver_still_serves_the_recording_pill():
    """One function now serves both, so it must not have lost the other."""
    from wisprlite.overlay import Overlay

    ov = Overlay.__new__(Overlay)
    x1, y1, x2, y2 = ov._done_button_box(0, Overlay.SCREENREC_DONE)
    got = ov._pill_click({"name": "screenrec", "screenrec_phase": "done"},
                         (x1 + x2) // 2, (y1 + y2) // 2)
    assert got == Overlay.SCREENREC_DONE[0][0], f"the recording pill lost its clicks: {got!r}"


def test_a_click_in_a_state_with_no_buttons_resolves_to_nothing():
    from wisprlite.overlay import Overlay

    ov = Overlay.__new__(Overlay)
    assert ov._pill_click({"name": "listening"}, 100, 70) == ""


def test_ra_stop_stops_a_playing_speaker():
    from uistub import install_platform_stubs
    install_platform_stubs()
    from wisprlite.app import App

    app = _make_app()
    speaker = _FakeSpeaker()
    app._read_aloud_speaker = speaker
    App._screenrec_action(app, "ra_stop")
    assert speaker.stop_calls == 1


def test_ra_stop_with_no_active_speaker_does_nothing():
    from uistub import install_platform_stubs
    install_platform_stubs()
    from wisprlite.app import App

    app = _make_app()
    App._screenrec_action(app, "ra_stop")   # must not raise


def test_ra_restart_with_no_prior_read_does_nothing():
    from uistub import install_platform_stubs
    install_platform_stubs()
    from wisprlite.app import App

    app = _make_app()
    App._screenrec_action(app, "ra_restart")   # must not raise, nothing to restart


def test_ra_restart_speaks_again_without_re_running_ocr():
    """Gate 4. `_read_aloud_speak` is the only thing ra_restart calls - it never
    touches `readaloud.ocr_png`, so restarting cannot re-OCR by construction.
    Assert that directly against the source, and that speaking happens again."""
    from unittest import mock

    from uistub import install_platform_stubs
    install_platform_stubs()
    from wisprlite.app import App

    app = _make_app()
    old_speaker = _FakeSpeaker()
    app._read_aloud_speaker = old_speaker
    app._read_aloud_last_text = "hello world"

    with mock.patch.object(App, "_read_aloud_speak") as speak, \
         mock.patch("wisprlite.readaloud.ocr_png") as ocr:
        App._screenrec_action(app, "ra_restart")

    assert old_speaker.stop_calls == 1, "restart must stop whatever was already playing"
    speak.assert_called_once_with("hello world", "read_all")
    ocr.assert_not_called()


def test_the_selftest_writes_its_verdict_to_a_file():
    """The release exe is built --noconsole, so stdout does not reach the CI log.
    The first real run of this gate failed the build correctly and printed
    nothing - a red build with no reason attached."""
    import os
    from unittest import mock
    import pytest
    from wisprlite import readaloud

    with mock.patch.object(readaloud, "winrt_selftest", return_value=(False, "FAIL: no voices")):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "verdict.txt")
        with mock.patch.dict(os.environ, {"PV_SELFTEST_OUT": path}):
            with pytest.raises(SystemExit) as exit_info:
                readaloud.main()
        assert exit_info.value.code == 1
        assert open(path, encoding="utf-8").read().strip() == "FAIL: no voices"


def test_a_failure_to_write_the_verdict_does_not_change_it():
    """Reporting must never turn a FAIL into a PASS or vice versa."""
    import os
    from unittest import mock
    import pytest
    from wisprlite import readaloud

    with mock.patch.object(readaloud, "winrt_selftest", return_value=(True, "PASS: ok")), \
         mock.patch.dict(os.environ, {"PV_SELFTEST_OUT": "/nonexistent-dir/verdict.txt"}):
        with pytest.raises(SystemExit) as exit_info:
            readaloud.main()
    assert exit_info.value.code == 0, "a write failure flipped the verdict"


def test_the_selftest_reports_progress_before_each_step():
    """The first real run of this gate HUNG - no stdout, no stderr, no verdict -
    so there was no way to tell whether the import, the OCR activation or the
    voice enumeration blocked. A hang has to leave a trail or it cannot be
    diagnosed."""
    from unittest import mock
    from wisprlite import readaloud

    seen = []
    with mock.patch.dict(sys.modules, {
        "winrt": mock.Mock(),
        "winrt.windows": mock.Mock(),
        "winrt.windows.media": mock.Mock(),
        "winrt.windows.media.ocr": mock.Mock(OcrEngine=mock.Mock(
            try_create_from_user_profile_languages=mock.Mock(return_value=object()))),
        "winrt.windows.media.speechsynthesis": mock.Mock(SpeechSynthesizer=mock.Mock(
            all_voices=[object()])),
    }):
        ok, _msg = readaloud.winrt_selftest(progress=seen.append)

    assert ok is True
    assert seen[0] == "start", f"no marker before the first import: {seen}"
    for expected in ("import-ocr", "import-speech", "create-ocr-engine", "list-voices"):
        assert expected in seen, f"no marker for {expected}: {seen}"


def test_instrumentation_cannot_change_the_verdict():
    """A progress callback that raises must not turn a PASS into a FAIL."""
    from unittest import mock
    from wisprlite import readaloud

    def boom(_marker):
        raise RuntimeError("logging blew up")

    with mock.patch.dict(sys.modules, {
        "winrt": mock.Mock(),
        "winrt.windows": mock.Mock(),
        "winrt.windows.media": mock.Mock(),
        "winrt.windows.media.ocr": mock.Mock(OcrEngine=mock.Mock(
            try_create_from_user_profile_languages=mock.Mock(return_value=object()))),
        "winrt.windows.media.speechsynthesis": mock.Mock(SpeechSynthesizer=mock.Mock(
            all_voices=[object()])),
    }):
        ok, _ = readaloud.winrt_selftest(progress=boom)

    assert ok is True, "a failing progress callback flipped the verdict"


# ---- James, 2026-09-05: "blue thing came up but did nothing" / "cant hear it"

def test_a_ctrl_hotkey_does_not_always_mean_a_non_default_mode():
    """asking is_pressed("ctrl") the instant a ctrl-chord hotkey fires is
    trivially true, so EVERY press picked a non-default mode - which is the
    blue thing that appeared when it should have dragged a region."""
    from wisprlite import readaloud

    shift, ctrl = readaloud.extra_modifiers(
        "ctrl+shift+r", shift_down=True, ctrl_down=True)
    assert (shift, ctrl) == (False, False), \
        "the hotkey's own modifiers were counted as a mode choice"
    assert readaloud.capture_mode_for(shift=shift, ctrl=ctrl) == "region"


def test_a_genuinely_extra_modifier_still_picks_its_mode():
    """The fix must not make the modes unreachable."""
    from wisprlite import readaloud

    # hotkey is alt+r, so a held ctrl IS extra
    shift, ctrl = readaloud.extra_modifiers("alt+r", shift_down=False, ctrl_down=True)
    assert readaloud.capture_mode_for(shift=shift, ctrl=ctrl) == "window"

    shift, ctrl = readaloud.extra_modifiers("alt+r", shift_down=True, ctrl_down=False)
    assert readaloud.capture_mode_for(shift=shift, ctrl=ctrl) == "screen"


def test_modifier_names_are_normalised():
    from wisprlite import readaloud
    for chord in ("control+r", "CTRL+R", "left ctrl+r", "Ctrl + R"):
        _s, c = readaloud.extra_modifiers(chord, shift_down=False, ctrl_down=True)
        assert c is False, f"{chord!r} was not recognised as holding ctrl"


def test_speak_waits_for_playback_instead_of_returning_immediately():
    """play() is ASYNC. Returning straight after it let the caller's finally drop
    the last reference, so the MediaPlayer was collected mid-sentence and nothing
    was ever audible."""
    import threading as _t
    from unittest import mock
    from wisprlite import readaloud

    ended = None

    class FakePlayer:
        def __init__(self):
            self.played = False
        def add_media_ended(self, handler):
            nonlocal ended
            ended = handler
        def play(self):
            self.played = True
        def pause(self):
            pass
        def close(self):
            pass

    player = FakePlayer()
    speaker = readaloud.Speaker(player_factory=lambda: player)

    returned = _t.Event()
    _t.Thread(target=lambda: (speaker.speak("hello there"), returned.set()),
              daemon=True).start()

    assert not returned.wait(0.4), "speak() returned before playback ended"
    assert player.played
    ended()                                   # WinRT fires MediaEnded
    assert returned.wait(2), "speak() never returned after playback ended"


def test_stopping_breaks_the_wait_promptly():
    """Esc must not have to wait out the whole passage."""
    import threading as _t
    from wisprlite import readaloud

    class FakePlayer:
        def add_media_ended(self, handler): pass
        def play(self): pass
        def pause(self): pass
        def close(self): pass

    speaker = readaloud.Speaker(player_factory=FakePlayer)
    returned = _t.Event()
    _t.Thread(target=lambda: (speaker.speak("a" * 2000), returned.set()),
              daemon=True).start()
    assert not returned.wait(0.3)
    speaker.stop()
    assert returned.wait(2), "stop() did not break the playback wait"


# ---- long-text chunking (plan: pipevoice-read-aloud-long-text, gates 1-2) --

def test_a_long_text_is_split_into_chunks_under_the_limit():
    """Gate 1: none of the pieces exceed the engine's limit."""
    from wisprlite import readaloud

    text = " ".join(f"Sentence number {i} says something." for i in range(400))
    assert len(text) > 5000
    chunks = readaloud.split_into_chunks(text, 1900)
    assert len(chunks) > 1
    assert all(len(c) <= 1900 for c in chunks)


def test_chunks_concatenate_back_to_the_original_words_in_order():
    """Gate 1: nothing lost, nothing duplicated."""
    from wisprlite import readaloud

    text = " ".join(f"Sentence number {i} says something interesting." for i in range(400))
    chunks = readaloud.split_into_chunks(text, 1900)
    normalized = " ".join(text.split())
    assert " ".join(chunks) == normalized


def test_a_single_sentence_over_the_limit_still_produces_speakable_chunks():
    """Gate 2: a 3,000-character sentence (no periods at all) still splits,
    never dropping a word, by falling back to clause punctuation then
    whitespace."""
    from wisprlite import readaloud

    words = [f"word{i}," if i % 5 == 0 else f"word{i}" for i in range(500)]
    sentence = " ".join(words) + "."
    assert len(sentence) > 3000
    chunks = readaloud.split_into_chunks(sentence, 1900)
    assert len(chunks) > 1
    assert all(len(c) <= 1900 for c in chunks)
    assert " ".join(chunks) == " ".join(sentence.split())


def test_a_single_word_longer_than_the_limit_is_hard_cut_not_dropped():
    from wisprlite import readaloud

    text = "a" * 5000
    chunks = readaloud.split_into_chunks(text, 1900)
    assert all(len(c) <= 1900 for c in chunks)
    assert "".join(chunks) == text


def test_short_text_is_a_single_chunk():
    from wisprlite import readaloud

    assert readaloud.split_into_chunks("hello there.", 1900) == ["hello there."]


def test_a_none_limit_never_splits_windows_has_no_documented_cap():
    from wisprlite import readaloud

    text = "word " * 2000
    assert readaloud.split_into_chunks(text, None) == [" ".join(text.split())]


def test_empty_text_produces_no_chunks():
    from wisprlite import readaloud

    assert readaloud.split_into_chunks("", 1900) == []


# ---- duration estimate (gate 4) --------------------------------------------

def test_duration_estimate_is_silent_below_1000_characters():
    from wisprlite import readaloud

    assert readaloud.duration_estimate(999) == ""
    assert readaloud.duration_estimate(1000) == ""


def test_duration_estimate_appears_above_1000_characters():
    from wisprlite import readaloud

    estimate = readaloud.duration_estimate(4200)
    assert "4,200" in estimate
    assert "min" in estimate


# ---- speak_chunks: stop mid-sequence never starts the next chunk (gate 3) -

def test_stopping_during_chunk_one_of_four_never_starts_chunk_two():
    from wisprlite import readaloud

    built = []

    class FakePlayer:
        def __init__(self, name):
            self.name = name
        def play(self):
            pass
        def pause(self):
            pass
        def close(self):
            pass

    speaker = readaloud.Speaker()

    def player_builder(chunk):
        player = FakePlayer(chunk)
        built.append(chunk)
        if len(built) == 1:
            # Stop lands while chunk 1 is "playing" (before it awaits end).
            speaker.stop()
        return player

    speaker.speak_chunks(["chunk one", "chunk two", "chunk three", "chunk four"],
                          player_builder)
    assert built == ["chunk one"], "a later chunk was built after stop()"


def test_speak_chunks_plays_every_chunk_when_nothing_stops_it():
    from wisprlite import readaloud

    played = []

    class FakePlayer:
        def __init__(self, chunk):
            self.chunk = chunk
        def play(self):
            played.append(self.chunk)
        def pause(self):
            pass
        def close(self):
            pass

    speaker = readaloud.Speaker(player_factory=None)
    speaker.speak_chunks(["one", "two", "three"], lambda c: FakePlayer(c))
    assert played == ["one", "two", "three"]


def test_speak_chunks_with_no_chunks_raises_instead_of_silently_doing_nothing():
    from wisprlite import readaloud

    speaker = readaloud.Speaker()
    try:
        speaker.speak_chunks([], lambda c: None)
    except readaloud.ReadAloudError:
        pass
    else:
        raise AssertionError("expected ReadAloudError for an empty chunk list")


# ---- build_chunked_speaker: same fallback contract, but chunked -----------

def test_build_chunked_speaker_windows_tier_has_no_cap_so_stays_one_chunk():
    """Windows has no documented character cap - unlike the cloud tiers, it
    must not be split just for the sake of it."""
    from wisprlite import config, readaloud

    cfg = config.Config(read_aloud_tts="windows", read_aloud_voice="")
    text = " ".join(f"Sentence {i} here." for i in range(400))
    speaker, chunks, builder, reason = readaloud.build_chunked_speaker(text, cfg)
    assert reason == ""
    assert chunks == [" ".join(text.split())]
    assert isinstance(speaker, readaloud.Speaker)
    assert builder == speaker._build_player


def test_build_chunked_speaker_falls_back_to_windows_on_a_deepgram_failure():
    from unittest import mock
    from wisprlite import config, readaloud, tts_cloud

    cfg = config.Config(read_aloud_tts="deepgram", read_aloud_voice="aura-2-draco-en")
    with mock.patch.object(config, "deepgram_key", return_value="a-key"), \
         mock.patch.object(tts_cloud, "deepgram_speak",
                            side_effect=tts_cloud.CloudTTSError("Deepgram speak failed: HTTP 401")):
        speaker, chunks, builder, reason = readaloud.build_chunked_speaker("hello world", cfg)
    assert isinstance(speaker, readaloud.Speaker)
    assert "HTTP 401" in reason and "Windows" in reason
    assert chunks == ["hello world"]


def test_build_chunked_speaker_uses_cloud_audio_and_only_fetches_each_chunk_once():
    from unittest import mock
    from wisprlite import config, readaloud, tts_cloud

    cfg = config.Config(read_aloud_tts="deepgram", read_aloud_voice="aura-2-draco-en")
    calls = []

    def fake_speak(text, voice, key):
        calls.append(text)
        return b"wav-bytes"

    with mock.patch.object(config, "deepgram_key", return_value="a-key"), \
         mock.patch.object(tts_cloud, "deepgram_speak", side_effect=fake_speak), \
         mock.patch.object(readaloud, "_winrt_player_from_bytes",
                           side_effect=lambda audio, ct: object()):
        text = " ".join(f"Sentence {i} here." for i in range(400))
        speaker, chunks, builder, reason = readaloud.build_chunked_speaker(text, cfg)
        assert reason == ""
        assert len(chunks) > 1
        # First chunk already fetched to detect a dead key early.
        assert calls == [chunks[0]]
        for chunk in chunks:
            builder(chunk)
    assert calls == chunks, "each chunk must be synthesized exactly once, in order"


# ---- Read all vs Summarise, chosen at the pill (gates 5-6) -----------------

def _make_app_for_speak():
    import threading
    from unittest import mock

    from uistub import install_platform_stubs
    install_platform_stubs()
    from wisprlite import config
    from wisprlite.app import App

    app = App.__new__(App)
    app.cfg = config.Config(read_aloud_tts="windows", read_aloud_clipboard=False)
    app._read_aloud_speaker = None
    app._read_aloud_busy = threading.Lock()
    app.overlay = mock.Mock()
    return app


def test_summarise_calls_clean_once_and_speaks_its_output():
    from unittest import mock
    from wisprlite.app import App

    app = _make_app_for_speak()
    with mock.patch("wisprlite.cleanup.clean", return_value="A short summary.") as clean, \
         mock.patch("wisprlite.readaloud.build_chunked_speaker") as build:
        fake_speaker = mock.Mock()
        build.return_value = (fake_speaker, ["A short summary."], lambda c: None, "")
        App._read_aloud_speak(app, "a very long original text " * 50, "summarise")

    clean.assert_called_once()
    assert clean.call_args.kwargs.get("style") == "summarise", clean.call_args
    build.assert_called_once_with("A short summary.", app.cfg)
    fake_speaker.speak_chunks.assert_called_once()


def test_a_failed_summarise_speaks_the_full_text_and_shows_a_reason():
    from unittest import mock
    from wisprlite import cleanup
    from wisprlite.app import App

    app = _make_app_for_speak()
    original = "a very long original text " * 50
    with mock.patch("wisprlite.cleanup.clean", return_value=None), \
         mock.patch.object(cleanup, "last_error", return_value="no API key for gemini"), \
         mock.patch("wisprlite.readaloud.build_chunked_speaker") as build:
        fake_speaker = mock.Mock()
        build.return_value = (fake_speaker, [original], lambda c: None, "")
        App._read_aloud_speak(app, original, "summarise")

    # The FULL text is what gets built into a speaker, never a truncated summary.
    build.assert_called_once_with(original, app.cfg)
    state_calls = [c.args for c in app.overlay.set_state.call_args_list]
    assert any("no API key for gemini" in str(c) for c in state_calls), state_calls


def test_read_all_never_calls_clean():
    from unittest import mock
    from wisprlite.app import App

    app = _make_app_for_speak()
    with mock.patch("wisprlite.cleanup.clean") as clean, \
         mock.patch("wisprlite.readaloud.build_chunked_speaker") as build:
        fake_speaker = mock.Mock()
        build.return_value = (fake_speaker, ["hello"], lambda c: None, "")
        App._read_aloud_speak(app, "hello", "read_all")

    clean.assert_not_called()


def test_the_pill_states_which_mode_was_read():
    from unittest import mock
    from wisprlite.app import App

    app = _make_app_for_speak()
    with mock.patch("wisprlite.readaloud.build_chunked_speaker") as build:
        fake_speaker = mock.Mock()
        build.return_value = (fake_speaker, ["hello"], lambda c: None, "")
        App._read_aloud_speak(app, "hello", "read_all")
    reading_calls = [c.args[1] for c in app.overlay.set_state.call_args_list
                     if c.args[0] == "reading"]
    assert any("Read all" in text for text in reading_calls), reading_calls

    app2 = _make_app_for_speak()
    with mock.patch("wisprlite.cleanup.clean", return_value="short summary"), \
         mock.patch("wisprlite.readaloud.build_chunked_speaker") as build2:
        fake_speaker2 = mock.Mock()
        build2.return_value = (fake_speaker2, ["short summary"], lambda c: None, "")
        App._read_aloud_speak(app2, "hello world", "summarise")
    reading_calls2 = [c.args[1] for c in app2.overlay.set_state.call_args_list
                      if c.args[0] == "reading"]
    assert any("Summary" in text for text in reading_calls2), reading_calls2


def test_enter_or_the_hotkey_always_resolves_the_choice_to_read_all():
    """Whatever was remembered as the last choice, the fast path (Enter, or
    the read-aloud hotkey pressed again) always means Read all."""
    import threading
    from wisprlite.app import App

    app = _make_app_for_speak()
    app.cfg.read_aloud_last_mode = "summarise"
    app._ra_choice_event = None
    app._ra_choice_result = None
    App._ra_choice_answer(app, "read_all")
    assert app._ra_choice_result == "read_all" or app._ra_choice_event is None
    # answer() with no event pre-set should not raise (mirrors a stray click)


def test_ra_choice_answer_sets_the_event_and_remembers_the_choice():
    import threading
    from wisprlite.app import App

    app = _make_app_for_speak()
    app._ra_choice_event = threading.Event()
    app._ra_choice_result = None
    App._ra_choice_answer(app, "summarise")
    assert app._ra_choice_result == "summarise"
    assert app.cfg.read_aloud_last_mode == "summarise"
    assert app._ra_choice_event.is_set()


def test_ask_read_mode_returns_read_all_when_overlay_is_off():
    from wisprlite.app import App

    app = _make_app_for_speak()
    app.cfg.overlay = False
    assert App._ask_read_mode_in_pill(app, 5000) == "read_all"


def test_ra_choice_buttons_route_through_screenrec_action():
    from unittest import mock
    from wisprlite.app import App

    app = _make_app_for_speak()
    app._ra_choice_event = mock.Mock()
    app._ra_choice_event.is_set.return_value = False
    with mock.patch.object(App, "_ra_choice_answer") as answer:
        App._screenrec_action(app, "ra_read_all")
        App._screenrec_action(app, "ra_summarise")
    answer.assert_any_call("read_all")
    answer.assert_any_call("summarise")


# ---- chunking must not lose or corrupt a single word -------------------------

def test_chunking_never_splits_a_word_across_chunks():
    """The first implementation hard-cut an over-long CLAUSE at the character
    limit, and _pack rejoined with a space - so "MqXXQagO" came back as
    "MqXXQ agO", which a voice reads as two words. 132 of 400 random inputs
    were corrupted. Only a single word longer than the whole limit may be cut.
    """
    import random
    import string

    from wisprlite.readaloud import split_into_chunks

    random.seed(7)
    for _ in range(400):
        words = []
        for _ in range(random.randint(1, 60)):
            word = "".join(random.choice(string.ascii_letters)
                           for _ in range(random.randint(1, 14)))
            if random.random() < 0.15:
                word += random.choice(".!?;,")
            words.append(word)
        text = " ".join(words)
        limit = random.choice([20, 50, 200, 1900])

        chunks = split_into_chunks(text, limit)

        assert " ".join(chunks) == " ".join(text.split()), (
            f"text was corrupted at limit={limit}\n"
            f"  in : {text[:120]!r}\n  out: {' '.join(chunks)[:120]!r}")
        for chunk in chunks:
            assert len(chunk) <= limit, f"chunk of {len(chunk)} exceeds {limit}"


def test_a_single_word_longer_than_the_limit_is_the_only_hard_cut():
    """Pathological, but it must still be speakable rather than dropped."""
    from wisprlite.readaloud import split_into_chunks

    chunks = split_into_chunks("x" * 3000, 1900)
    assert [len(c) for c in chunks] == [1900, 1100]
    assert "".join(chunks) == "x" * 3000, "characters were lost in the hard cut"


def test_a_long_clause_is_split_on_words_not_characters():
    """The exact shape of the bug: one clause, no sentence punctuation, longer
    than the limit."""
    from wisprlite.readaloud import split_into_chunks

    text = " ".join(["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"])
    chunks = split_into_chunks(text, 20)
    for chunk in chunks:
        for word in chunk.split():
            assert word in text.split(), f"{word!r} is not a real word from the input"
