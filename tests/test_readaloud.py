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

def test_plain_press_captures_the_focused_window():
    assert readaloud.capture_mode_for(shift=False, ctrl=False) == "window"


def test_shift_captures_the_whole_screen():
    assert readaloud.capture_mode_for(shift=True, ctrl=False) == "screen"


def test_ctrl_drags_a_region():
    assert readaloud.capture_mode_for(shift=False, ctrl=True) == "region"


def test_ctrl_wins_over_shift_if_somehow_both_are_held():
    assert readaloud.capture_mode_for(shift=True, ctrl=True) == "region"


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
