"""Read Aloud — capture a region of the screen, OCR it, speak it.

For people who struggle to read, and for accessibility generally. OCR and speech
both come from WinRT (`Windows.Media.Ocr` / `Windows.Media.SpeechSynthesis`):
offline, no key, no extra download, and the modern natural voices SAPI's legacy
ones do not reach.

Everything here degrades: no WinRT, no OCR engine for the profile's languages,
no voices, or any WinRT failure returns a message through the normal error path
instead of raising into the hotkey loop. Read Aloud is additive and must never
be able to break dictation, which is why every import is lazy (see CLAUDE.md's
"Lazy, fault-tolerant imports" rule) and every winrt call is wrapped.

Speak ALWAYS, even with a screen reader running — the OCR hotkey is triggered
*because* the screen reader cannot read that region (an image, a canvas, a
scanned PDF). Going silent there defeats the feature at the exact moment it is
needed. `Config.read_aloud_quiet_with_screenreader` is an opt-out, default OFF.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from typing import Callable, Optional

log = logging.getLogger("wisprlite")


class ReadAloudError(Exception):
    """A degraded-but-handled failure: no engine, no bundle, bad frame, etc."""


# ---- capture -----------------------------------------------------------------

def focused_window_rect() -> Optional[tuple[int, int, int, int]]:
    """(left, top, width, height) of the foreground window, or None.

    ctypes straight to user32 rather than adding pywin32/pygetwindow for one
    struct's worth of API.
    """
    try:
        import ctypes.wintypes as wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        if right <= left or bottom <= top:
            return None
        return (left, top, right - left, bottom - top)
    except Exception as exc:
        log.info("read-aloud: could not read the focused window rect: %s", exc)
        return None


def capture_mode_for(*, shift: bool, ctrl: bool) -> str:
    """Which of the three modes a hotkey press means, by which modifiers were
    ALSO held. Pure function so the mode-selection logic is testable without a
    keyboard: focused window is the default because it needs no mouse."""
    if ctrl:
        return "region"
    if shift:
        return "screen"
    return "window"


def grab_png(region: Optional[tuple[int, int, int, int]]) -> bytes:
    """PNG bytes of a screen region, or the whole virtual desktop if None.

    In memory only — screen contents can contain anything, so this must never
    touch disk (mss.tools.to_png builds the PNG in memory; no temp file).
    """
    import mss
    import mss.tools

    with mss.mss() as sct:
        area = sct.monitors[0] if region is None else {
            "left": region[0], "top": region[1],
            "width": region[2], "height": region[3],
        }
        shot = sct.grab(area)
        return mss.tools.to_png(shot.rgb, shot.size)


# ---- WinRT availability --------------------------------------------------------

def winrt_available() -> bool:
    try:
        import winrt.windows.media.ocr  # noqa: F401
        import winrt.windows.media.speechsynthesis  # noqa: F401
        return True
    except Exception:
        return False


def winrt_selftest() -> tuple[bool, str]:
    """Activate both WinRT namespaces used here from a FROZEN build and report
    PASS/FAIL. This is spike 1, made permanent as a CI gate (`--winrt-selftest`):
    a broken PyInstaller bundle then fails the build instead of shipping."""
    try:
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer
    except Exception as exc:
        return False, f"FAIL: winrt import: {type(exc).__name__}: {exc}"
    try:
        engine = OcrEngine.try_create_from_user_profile_languages()
        voices = list(SpeechSynthesizer.all_voices)
    except Exception as exc:
        return False, f"FAIL: winrt activation: {type(exc).__name__}: {exc}"
    if engine is None:
        return False, "FAIL: no OCR engine for this profile's languages"
    if not voices:
        return False, "FAIL: no speech voices installed"
    return True, f"PASS: ocr engine activated, {len(voices)} voice(s)"


# ---- OCR -----------------------------------------------------------------------

def ocr_png(png_bytes: bytes, *, language: str = "") -> str:
    """Recognize text in a PNG via Windows.Media.Ocr. Raises ReadAloudError,
    never a raw winrt/asyncio exception, so callers have one thing to catch."""
    try:
        import asyncio

        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    except Exception as exc:
        raise ReadAloudError(f"OCR unavailable: {exc}") from exc

    async def run() -> str:
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream.get_output_stream_at(0))
        # bytes straight through where the projection accepts it. A whole-screen
        # PNG is megabytes, and list(png_bytes) built a Python list of millions
        # of ints before anything was written.
        try:
            writer.write_bytes(png_bytes)
        except TypeError:
            writer.write_bytes(list(png_bytes))
        await writer.store_async()
        await writer.flush_async()
        try:
            stream.seek(0)
        except AttributeError:
            stream.position = 0    # projection versions differ on which exists
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = (
            OcrEngine.try_create_from_language(Language(language)) if language
            else OcrEngine.try_create_from_user_profile_languages()
        )
        if engine is None:
            raise ReadAloudError("no OCR engine for this profile's languages")
        result = await engine.recognize_async(bitmap)
        return result.text

    try:
        text = asyncio.run(run())
    except ReadAloudError:
        raise
    except Exception as exc:
        raise ReadAloudError(f"OCR failed: {exc}") from exc
    return " ".join((text or "").split())


# ---- speech ----------------------------------------------------------------

class Speaker:
    """Speaks text via Windows.Media.SpeechSynthesis, interruptible.

    ``player_factory`` is injected so the state machine (stop/pause/resume) is
    testable on Linux with a fake player: real code passes None and gets a
    winrt MediaPlayer; tests pass a fake that records calls.

    Stopping calls the player's own pause/stop SYNCHRONOUSLY, on the calling
    thread — the ~200ms interrupt requirement holds because pausing playback is
    near-instant regardless of how long the remaining text is, unlike a design
    that waits for the current sentence to finish before checking a flag.
    """

    def __init__(self, *, voice: str = "", rate: float = 1.0,
                 player_factory: Optional[Callable[[], object]] = None):
        self.voice = voice
        self.rate = max(0.5, min(2.0, float(rate or 1.0)))
        self._player_factory = player_factory
        self._lock = threading.Lock()
        self._player = None
        self._stopped = False
        self.paused = False

    def speak(self, text: str) -> None:
        """Speak once. A Speaker is ONE-SHOT by design.

        `stop()` is latched, so that pressing Esc DURING the OCR pass - before
        speech has started - still prevents it speaking. Resetting the latch
        here would silently discard that Esc. The cost is that a stopped Speaker
        cannot be reused, so say that loudly rather than no-op'ing: a "repeat
        last" button built on a reused Speaker would otherwise just do nothing.
        The app builds a fresh Speaker per read.
        """
        text = " ".join((text or "").split())
        if not text:
            raise ReadAloudError("nothing to read")
        try:
            player = self._build_player(text)
        except ReadAloudError:
            raise
        except Exception as exc:
            raise ReadAloudError(f"speech unavailable: {exc}") from exc
        with self._lock:
            if self._stopped:
                # Stopped before playback began - an Esc during OCR. Honour it.
                self._stop_player(player)
                return
            self._player = player
        try:
            self._play(player)
        except Exception:
            # Do not leave a dead player as the live one - stop() would then
            # think something is speaking and pause a player that never played.
            with self._lock:
                if self._player is player:
                    self._player = None
            self._stop_player(player)
            raise

    def pause(self) -> None:
        with self._lock:
            player = self._player
        self.paused = True
        if player is not None:
            try:
                player.pause()
            except Exception as exc:
                log.info("read-aloud: pause failed: %s", exc)

    def resume(self) -> None:
        with self._lock:
            player = self._player
        self.paused = False
        if player is not None:
            try:
                player.play()
            except Exception as exc:
                log.info("read-aloud: resume failed: %s", exc)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            player, self._player = self._player, None
        self._stop_player(player)

    @staticmethod
    def _stop_player(player) -> None:
        """Pause FIRST, then release. Pause is what makes the interrupt feel
        instant; close is what stops every read leaking a MediaPlayer and its
        audio resources, which pausing alone never did."""
        if player is None:
            return
        try:
            player.pause()
        except Exception as exc:
            log.info("read-aloud: stop failed: %s", exc)
        for method in ("close", "Close"):
            closer = getattr(player, method, None)
            if closer is None:
                continue
            try:
                closer()
            except Exception as exc:
                log.info("read-aloud: could not release the player: %s", exc)
            break

    def _build_player(self, text: str):
        if self._player_factory is not None:
            return self._player_factory()

        from winrt.windows.media.core import MediaSource
        from winrt.windows.media.playback import MediaPlayer
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer

        import asyncio

        async def synth():
            synthesizer = SpeechSynthesizer()
            if self.voice:
                for v in SpeechSynthesizer.all_voices:
                    if v.display_name == self.voice or v.id == self.voice:
                        synthesizer.voice = v
                        break
            try:
                synthesizer.options.speaking_rate = self.rate
            except Exception:
                pass
            return await synthesizer.synthesize_text_to_stream_async(text)

        stream = asyncio.run(synth())
        player = MediaPlayer()
        player.source = MediaSource.create_from_stream(stream, stream.content_type)
        return player

    @staticmethod
    def _play(player) -> None:
        try:
            player.play()
        except Exception as exc:
            raise ReadAloudError(f"playback failed: {exc}") from exc


# ---- screen reader detection (informational only — never gates speaking) -----

def screen_reader_running() -> bool:
    """Best-effort SPI_GETSCREENREADER check. Unreliable by design (Microsoft
    documents it as not set consistently), so this only feeds the OPT-OUT
    setting — Read Aloud speaks regardless unless the user has turned that on."""
    try:
        import ctypes.wintypes as wintypes

        SPI_GETSCREENREADER = 0x0046
        # wintypes.BOOL, NOT ctypes.c_bool. Win32 BOOL is a 4-byte int; c_bool
        # is ONE byte, so SystemParametersInfoW would write three bytes past
        # the buffer. A silent stack smash in an accessibility code path.
        value = wintypes.BOOL()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETSCREENREADER, 0, ctypes.byref(value), 0)
        return bool(ok) and bool(value.value)
    except Exception:
        return False


def should_speak(*, quiet_with_screenreader: bool) -> bool:
    """Speak always, unless the user opted into staying quiet AND a screen
    reader is (as best as can be told) actually running."""
    if not quiet_with_screenreader:
        return True
    return not screen_reader_running()


def main() -> None:
    """`--winrt-selftest` entry point: one PASS/FAIL line, exit non-zero on
    failure. CI runs this against the BUILT EXE and fails the build on it —
    this is spike 1, made permanent."""
    import sys

    ok, message = winrt_selftest()
    print(message)
    sys.exit(0 if ok else 1)
