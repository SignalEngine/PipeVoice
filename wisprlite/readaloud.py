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
import re
import threading
import time
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
    keyboard.

    Dragging a region is the default: James, the actual user, asked plainly to
    "select what I want to be read aloud... where we just highlight what we
    want", overriding an earlier design decision that defaulted to the focused
    window on the theory that some hypothetical user has no mouse.
    """
    if ctrl:
        return "window"
    if shift:
        return "screen"
    return "region"


def extra_modifiers(hotkey: str, *, shift_down: bool, ctrl_down: bool) -> tuple[bool, bool]:
    """The modifiers held BEYOND the ones the hotkey itself requires.

    Asking `keyboard.is_pressed("ctrl")` the instant a hotkey fires is useless
    when the hotkey IS a chord containing ctrl: it is trivially true, so every
    press picked region mode and opened the selector. Subtract what the chord
    already holds, and only genuinely extra modifiers choose a mode.
    """
    # Substring, not exact match: the keyboard library yields "ctrl",
    # "left ctrl", "right ctrl", "control" and people type "Ctrl". An alias
    # table has to be complete to be correct; a substring test does not.
    chord = (hotkey or "").lower()
    holds_ctrl = "ctrl" in chord or "control" in chord
    holds_shift = "shift" in chord
    return (shift_down and not holds_shift,
            ctrl_down and not holds_ctrl)


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


def winrt_selftest(progress: Optional[Callable[[str], None]] = None) -> tuple[bool, str]:
    """Activate both WinRT namespaces used here from a FROZEN build and report
    PASS/FAIL. This is spike 1, made permanent as a CI gate (`--winrt-selftest`):
    a broken PyInstaller bundle then fails the build instead of shipping.

    `progress` is called with a marker before EACH step. The first real run of
    this gate did not crash - it HUNG, with no stdout, no stderr and no verdict,
    so there was no way to tell whether the import, the OCR activation or the
    voice enumeration was the thing that blocked. A hang has to leave a trail or
    it is unfalsifiable.
    """
    def step(name: str) -> None:
        if progress is not None:
            try:
                progress(name)
            except Exception:
                pass    # instrumentation must never change the verdict

    step("start")
    try:
        step("import-ocr")
        from winrt.windows.media.ocr import OcrEngine
        step("import-speech")
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer
    except Exception as exc:
        return False, f"FAIL: winrt import: {type(exc).__name__}: {exc}"

    try:
        step("create-ocr-engine")
        engine = OcrEngine.try_create_from_user_profile_languages()
        step("list-voices")
        voices = list(SpeechSynthesizer.all_voices)
        step("done")
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


# ---- chunking (long text vs. an engine's per-request character cap) -------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_SPLIT = re.compile(r"(?<=[,;:])\s+")


def duration_estimate(char_count: int) -> str:
    """A short warning line for a long read - `chars/5/150` minutes at ~150
    words/minute, 5 characters/word. Empty at/under ~1,000 characters so a
    short read stays quiet."""
    if char_count <= 1000:
        return ""
    minutes = max(1, round(char_count / 5 / 150))
    return f"Reading {char_count:,} characters — about {minutes} min"


def _pack(pieces: list[str], limit: int) -> list[str]:
    """Greedily pack already-limit-sized pieces into chunks under `limit`,
    joined with a single space - the shared last step for both the sentence
    pass and the clause/word fallback."""
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}".strip() if current else piece
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def _split_long_piece(text: str, limit: int) -> list[str]:
    """One sentence longer than the limit still has to be spoken: fall back to
    clause punctuation, then whitespace, then a hard cut. Never drops a chunk."""
    safe: list[str] = []
    for part in _CLAUSE_SPLIT.split(text):
        if len(part) <= limit:
            safe.append(part)
            continue
        # A clause over the limit is still many WORDS. Hard-cutting it here put
        # a break mid-word, and _pack then rejoined with a space: "MqXXQagO"
        # came back as "MqXXQ agO", which the voice reads as two words. Go down
        # to words first.
        for word in part.split(" "):
            if len(word) <= limit:
                safe.append(word)
            else:
                # A SINGLE word longer than the limit. Only here is a hard cut
                # unavoidable, and only here can the text not round-trip.
                safe.extend(word[i:i + limit] for i in range(0, len(word), limit))
    return _pack(safe, limit)


def split_into_chunks(text: str, limit: Optional[int]) -> list[str]:
    """Split `text` into pieces each at most `limit` characters, breaking on
    sentence boundaries first so nothing is cut mid-word. `limit` of None (the
    Windows voice has no documented cap) returns the whole text as one chunk.

    Concatenating the result with " ".join(...) reproduces the original words
    in order - nothing is dropped or duplicated, only regrouped."""
    text = " ".join((text or "").split())
    if not text:
        return []
    if not limit or len(text) <= limit:
        return [text]
    sentences = _SENTENCE_SPLIT.split(text)
    safe: list[str] = []
    for sentence in sentences:
        if len(sentence) <= limit:
            safe.append(sentence)
        else:
            safe.extend(_split_long_piece(sentence, limit))
    return _pack(safe, limit)


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
            self._await_end(player, text)
        except Exception:
            # Do not leave a dead player as the live one - stop() would then
            # think something is speaking and pause a player that never played.
            with self._lock:
                if self._player is player:
                    self._player = None
            self._stop_player(player)
            raise

    def speak_chunks(self, chunks: list[str], player_builder: Callable[[str], object]) -> None:
        """Speak an ordered sequence of already-chunked pieces back to back
        through this ONE Speaker, so Stop/Pause govern the whole read rather
        than one fragment. `player_builder(chunk)` is called lazily, one piece
        at a time - a stop between pieces breaks the loop before the next one
        is built (or fetched from a cloud engine), so stopping never starts
        the next chunk."""
        chunks = [c for c in (chunks or []) if c]
        if not chunks:
            raise ReadAloudError("nothing to read")
        for chunk in chunks:
            with self._lock:
                if self._stopped:
                    return
            try:
                player = player_builder(chunk)
            except ReadAloudError:
                raise
            except Exception as exc:
                raise ReadAloudError(f"speech unavailable: {exc}") from exc
            with self._lock:
                if self._stopped:
                    self._stop_player(player)
                    return
                self._player = player
            try:
                self._play(player)
                self._await_end(player, chunk)
            except Exception:
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
        # _stopped is set INSIDE the lock, which is what _await_end polls, so a
        # stop breaks the wait within one 100ms tick.
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

    def _await_end(self, player, text: str) -> None:
        """Block until playback ends, is stopped, or a generous cap passes.

        play() is ASYNCHRONOUS. Returning straight after it meant the caller's
        finally dropped the last reference to the Speaker and its MediaPlayer,
        the player was garbage-collected mid-sentence, and nothing was ever
        audible. It also killed the Esc/Space watcher, which loops on that same
        reference.

        The cap is generous and derived from the text: it exists so a WinRT
        event that never arrives cannot wedge the hotkey for ever, not as the
        normal path.
        """
        done = threading.Event()
        for attach in ("add_media_ended", "add_MediaEnded"):
            adder = getattr(player, attach, None)
            if adder is None:
                continue
            try:
                adder(lambda *_a: done.set())
                break
            except Exception as exc:
                log.info("read-aloud: could not attach the end handler: %s", exc)

        # ~14 chars/second is slow speech; +5s of slack, capped at 10 minutes.
        budget = min(600.0, 5.0 + len(text) / 14.0)
        deadline = time.monotonic() + budget
        while not done.wait(0.1):
            if time.monotonic() > deadline:
                log.info("read-aloud: playback cap reached after %.0fs", budget)
                return
            with self._lock:
                if self._stopped or self._player is not player:
                    return


# ---- tiered voice selection (Windows / Deepgram / ElevenLabs) ---------------

def _winrt_player_from_bytes(audio_bytes: bytes, content_type: str):
    """A winrt MediaPlayer sourced from already-synthesized bytes (a cloud TTS
    response) instead of SpeechSynthesizer. Reuses the same interruptible
    player Speaker already knows how to stop/pause/release, rather than a
    second playback path for cloud voices."""
    import asyncio

    from winrt.windows.media.core import MediaSource
    from winrt.windows.media.playback import MediaPlayer
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    async def build():
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream.get_output_stream_at(0))
        try:
            writer.write_bytes(audio_bytes)
        except TypeError:
            writer.write_bytes(list(audio_bytes))
        await writer.store_async()
        await writer.flush_async()
        try:
            stream.seek(0)
        except AttributeError:
            stream.position = 0
        player = MediaPlayer()
        player.source = MediaSource.create_from_stream(stream, content_type)
        return player

    return asyncio.run(build())


def build_speaker(text: str, cfg) -> tuple["Speaker", str]:
    """Build the Speaker for one read, honouring `cfg.read_aloud_tts`.

    Windows stays the default: offline, no key, no cost, and the privacy
    promise is the product — a cloud voice must always be an explicit choice.
    Every cloud path degrades to the Windows voice on ANY failure (a dead key,
    no network, a 402): the second item in the returned tuple is empty on
    success or a reason to surface when it degraded — Read Aloud must never go
    silent about why it fell back.
    """
    tier = (getattr(cfg, "read_aloud_tts", "") or "windows").strip().lower()
    rate = getattr(cfg, "read_aloud_rate", 1.0)
    if tier == "windows":
        return Speaker(voice=getattr(cfg, "read_aloud_voice", ""), rate=rate), ""

    from . import config as _config
    from . import tts_cloud

    try:
        if tier == "deepgram":
            audio = tts_cloud.deepgram_speak(
                text,
                getattr(cfg, "read_aloud_voice", "") or tts_cloud.DEFAULT_DEEPGRAM_VOICE,
                # A key on the cfg wins over the saved one, so Settings can
                # preview a key that has been TYPED but not saved yet - which
                # is exactly the moment someone wants to hear whether it works.
                getattr(cfg, "read_aloud_deepgram_key", "") or _config.deepgram_key())
            content_type = "audio/wav"
        elif tier == "elevenlabs":
            audio = tts_cloud.elevenlabs_speak(
                text,
                getattr(cfg, "read_aloud_elevenlabs_voice_id", ""),
                getattr(cfg, "read_aloud_elevenlabs_key", "") or _config.elevenlabs_key())
            content_type = "audio/mpeg"
        else:
            # An unrecognised tier used to hand back a Windows speaker and no
            # reason, so a setting that had silently drifted looked like normal
            # operation. Say which value was not understood.
            return (Speaker(voice="", rate=rate),
                    f"unknown voice engine {tier!r} — using the Windows voice instead")
        # Building the player is INSIDE the try. It was outside, so a cloud
        # call that succeeded and then failed to produce a player raised
        # straight out of here - no fallback, and silence, which is the one
        # thing this path must never do.
        player = _winrt_player_from_bytes(audio, content_type)
    except tts_cloud.CloudTTSError as exc:
        return (Speaker(voice="", rate=rate),
                f"{str(exc)} — using the Windows voice instead")
    except Exception as exc:
        log.exception("read-aloud: cloud voice failed, falling back")
        return (Speaker(voice="", rate=rate),
                f"{type(exc).__name__} — using the Windows voice instead")

    return Speaker(rate=rate, player_factory=lambda: player), ""


def build_chunked_speaker(text: str, cfg) -> tuple["Speaker", list[str], Callable[[str], object], str]:
    """The chunked sibling of `build_speaker`: same tier dispatch and the same
    fallback-to-Windows-on-any-failure contract, but for a full read instead of
    the Settings preview's one fixed sentence. Returns the Speaker, its
    sentence-safe chunks, a per-chunk player builder (called lazily by
    `Speaker.speak_chunks`), and a fallback reason (empty on success).
    """
    tier = (getattr(cfg, "read_aloud_tts", "") or "windows").strip().lower()
    rate = getattr(cfg, "read_aloud_rate", 1.0)

    if tier == "windows":
        speaker = Speaker(voice=getattr(cfg, "read_aloud_voice", ""), rate=rate)
        return speaker, split_into_chunks(text, None), speaker._build_player, ""

    from . import config as _config
    from . import tts_cloud

    if tier == "deepgram":
        limit = tts_cloud.DEEPGRAM_MAX_CHARS
    elif tier == "elevenlabs":
        limit = tts_cloud.ELEVENLABS_MAX_CHARS
    else:
        speaker = Speaker(voice="", rate=rate)
        return (speaker, split_into_chunks(text, None), speaker._build_player,
                f"unknown voice engine {tier!r} — using the Windows voice instead")

    def synth(chunk: str) -> tuple[bytes, str]:
        if tier == "deepgram":
            return tts_cloud.deepgram_speak(
                chunk,
                getattr(cfg, "read_aloud_voice", "") or tts_cloud.DEFAULT_DEEPGRAM_VOICE,
                getattr(cfg, "read_aloud_deepgram_key", "") or _config.deepgram_key()), "audio/wav"
        return tts_cloud.elevenlabs_speak(
            chunk,
            getattr(cfg, "read_aloud_elevenlabs_voice_id", ""),
            getattr(cfg, "read_aloud_elevenlabs_key", "") or _config.elevenlabs_key()), "audio/mpeg"

    chunks = split_into_chunks(text, limit)
    try:
        # Synthesize the FIRST chunk now, same as build_speaker, so a dead key
        # or no network is caught before anything plays rather than mid-read.
        first_audio, content_type = synth(chunks[0])
        first_player = _winrt_player_from_bytes(first_audio, content_type)
    except tts_cloud.CloudTTSError as exc:
        speaker = Speaker(voice="", rate=rate)
        return (speaker, split_into_chunks(text, None), speaker._build_player,
                f"{exc} — using the Windows voice instead")
    except Exception as exc:
        log.exception("read-aloud: cloud voice failed, falling back")
        speaker = Speaker(voice="", rate=rate)
        return (speaker, split_into_chunks(text, None), speaker._build_player,
                f"{type(exc).__name__} — using the Windows voice instead")

    served = {"player": first_player}

    def player_builder(chunk: str):
        # speak_chunks calls this once per chunk, in order - the first call is
        # always chunks[0], already fetched above to detect a dead key early.
        if served["player"] is not None:
            player, served["player"] = served["player"], None
            return player
        audio, content_type = synth(chunk)
        return _winrt_player_from_bytes(audio, content_type)

    return Speaker(rate=rate), chunks, player_builder, ""


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
    this is spike 1, made permanent.

    The result is also written to a FILE, because the release exe is built
    `--noconsole`: the first real run of this gate failed the build correctly
    and printed nothing, so nobody could tell whether it was a missing module,
    a dead activation, or simply no voices on the runner. A gate that fails
    without saying why is half a gate.
    """
    import os
    import sys

    out = os.environ.get("PV_SELFTEST_OUT")
    handle = None
    if out:
        try:
            handle = open(out, "w", encoding="utf-8", buffering=1)
        except Exception as exc:
            print(f"(could not open {out}: {exc})")

    def progress(marker: str) -> None:
        # Flushed per line: on a HANG the file is the only evidence there is,
        # and a buffered write would be lost when the process is killed.
        if handle is not None:
            handle.write(f"step: {marker}\n")
            handle.flush()
            os.fsync(handle.fileno())

    ok, message = winrt_selftest(progress=progress)
    print(message)
    if handle is not None:
        try:
            handle.write(message + "\n")
            handle.flush()
            handle.close()
        except Exception as exc:      # never let reporting change the verdict
            print(f"(could not write {out}: {exc})")
    sys.exit(0 if ok else 1)


def keep_last_text(text: str, folder) -> None:
    """Write what OCR produced to read-aloud-last.txt beside the log.

    When a read comes out as nonsense there is no way to tell whether OCR
    misread the screen or the voice mangled good text. The file answers that;
    the log line gives the size without the content.
    """
    try:
        from pathlib import Path

        Path(folder).mkdir(parents=True, exist_ok=True)
        (Path(folder) / "read-aloud-last.txt").write_text(text, encoding="utf-8")
        log.info("read-aloud: OCR produced %d chars, %d words", len(text), len(text.split()))
    except Exception as exc:
        log.info("read-aloud: could not keep the last text: %s", exc)
