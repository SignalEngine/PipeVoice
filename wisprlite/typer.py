"""Inject transcribed text into the focused window, plus text post-processing."""

from __future__ import annotations

import logging
import re
import time

log = logging.getLogger("wisprlite")

try:
    import keyboard
except ImportError:  # Keep pure post-processing usable on headless test machines.
    keyboard = None


def apply_replacements(text: str, mapping: dict) -> str:
    """Apply user word-fixes (case-insensitive, word-boundary aware)."""
    if not text or not mapping:
        return text
    usable = {str(find): str(repl) for find, repl in mapping.items() if str(find)}
    if not usable:
        return text
    # One capture group per key, so the group INDEX identifies which key matched.
    # Do not map the matched text back through casefold(): re.IGNORECASE uses
    # simple case folding while str.casefold() uses full folding, and they
    # disagree (Turkish dotted/dotless i), which both raised KeyError and, once
    # guarded, silently skipped replacements the old code performed.
    #
    # sorted() is stable, so longest-first ordering preserves insertion order on
    # ties, and the regex takes the leftmost alternative — meaning ambiguous
    # case-variant keys resolve FIRST-wins, as the original sequential re.sub did.
    keys = sorted(usable, key=len, reverse=True)
    try:
        pattern = re.compile(
            r"\b(?:" + "|".join(f"({re.escape(find)})" for find in keys) + r")\b",
            re.IGNORECASE,
        )
    except (re.error, AssertionError, OverflowError):
        # AssertionError/OverflowError: too many groups for the regex engine.
        return text

    def _resolve(match: re.Match) -> str:
        for index, find in enumerate(keys, start=1):
            if match.group(index) is not None:
                return usable[find]
        return match.group(0)

    try:
        return pattern.sub(_resolve, text)
    except re.error:
        return text


PASTE_TIMINGS = {
    # (sleep before paste, sleep before clipboard-restore, sleep before Enter)
    "fast":   (0.01, 0.08, 0.03),
    "normal": (0.03, 0.15, 0.05),
    "slow":   (0.09, 0.35, 0.12),
}


def _clipboard_holds_non_text() -> bool:
    """True when the clipboard holds something pyperclip cannot put back.

    pyperclip saves and restores TEXT only. A copied screenshot or files would
    be replaced by the dictation, so `type_text` handles those itself.
    Safe = empty, or has a text form we restore.
    """
    try:
        import ctypes
        u32 = ctypes.windll.user32
        if u32.CountClipboardFormats() == 0:
            return False
        return not u32.IsClipboardFormatAvailable(13)   # CF_UNICODETEXT
    except Exception:
        return False   # not Windows / no clipboard API: nothing pyperclip would lose


def _grab_clipboard_image():
    """The image on the clipboard as a PIL image, else None (files, other formats)."""
    try:
        from PIL import ImageGrab

        grabbed = ImageGrab.grabclipboard()
    except Exception:
        return None
    return grabbed if hasattr(grabbed, "save") else None


def _restore_clipboard_image(image) -> None:
    """Put a PIL image back on the clipboard as CF_DIB (Windows)."""
    import ctypes
    import io
    from ctypes import wintypes

    buf = io.BytesIO()
    image.convert("RGB").save(buf, "BMP")
    dib = buf.getvalue()[14:]                      # BMP file header is not part of a DIB
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = wintypes.HANDLE
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [wintypes.HANDLE]
    k32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    handle = k32.GlobalAlloc(0x0002, len(dib))     # GMEM_MOVEABLE
    ptr = k32.GlobalLock(handle)
    ctypes.memmove(ptr, dib, len(dib))
    k32.GlobalUnlock(handle)
    if not u32.OpenClipboard(None):
        raise OSError("could not open the clipboard to restore the image")
    try:
        u32.EmptyClipboard()
        u32.SetClipboardData(8, handle)            # CF_DIB; the clipboard owns the handle now
    finally:
        u32.CloseClipboard()


def type_text(text: str, mode: str = "paste", press_enter: bool = False,
              paste_speed: str = "normal") -> None:
    """Paste `text` at the cursor (copy + Ctrl+V). Never types keystrokes.

    `mode` is kept for callers; "clipboard" is handled by the caller before this.
    Raises when copy/paste fails, so the caller can save the words elsewhere.
    """
    if not text and not press_enter:
        return
    pre_ms, restore_ms, enter_ms = PASTE_TIMINGS.get(paste_speed, PASTE_TIMINGS["normal"])

    if text:
        import pyperclip

        image, prev = None, None
        if _clipboard_holds_non_text():
            image = _grab_clipboard_image()
            if image is None:
                # ponytail: copied files are not restored; CF_HDROP if anyone misses it
                log.info("clipboard: non-text, non-image contents not restored")
        else:
            try:
                prev = pyperclip.paste()
            except Exception:
                prev = None
        pyperclip.copy(text)
        time.sleep(pre_ms)
        keyboard.send("ctrl+v")
        if image is not None:
            time.sleep(restore_ms)
            _restore_clipboard_image(image)
        elif prev is not None:
            time.sleep(restore_ms)
            try:
                pyperclip.copy(prev)
            except Exception:
                pass
    if press_enter:
        time.sleep(enter_ms)
        keyboard.send("enter")


def copy_clipboard(text: str) -> bool:
    """Copy text to the clipboard without typing or pasting it. True on success."""
    if not text:
        return False
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:
        return False
