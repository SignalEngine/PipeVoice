"""Inject transcribed text into the focused window, plus text post-processing."""

from __future__ import annotations

import re
import time

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
    try:
        pattern = re.compile(
            r"\b(" + "|".join(
                re.escape(find)
                for find in sorted(usable, key=len, reverse=True)
            ) + r")\b",
            re.IGNORECASE,
        )
        mapping_ci = {find.casefold(): replacement for find, replacement in usable.items()}
        return pattern.sub(
            lambda match: mapping_ci.get(
                match.group(1).casefold(),
                usable.get(match.group(1), match.group(1)),
            ),
            text,
        )
    except re.error:
        return text


PASTE_TIMINGS = {
    # (sleep before paste, sleep before clipboard-restore, sleep before Enter)
    "fast":   (0.01, 0.08, 0.03),
    "normal": (0.03, 0.15, 0.05),
    "slow":   (0.09, 0.35, 0.12),
}


def type_text(text: str, mode: str = "type", press_enter: bool = False,
              paste_speed: str = "normal") -> None:
    if not text and not press_enter:
        return
    pre_ms, restore_ms, enter_ms = PASTE_TIMINGS.get(paste_speed, PASTE_TIMINGS["normal"])

    if text and mode == "paste":
        try:
            import pyperclip

            try:
                prev = pyperclip.paste()
            except Exception:
                prev = None
            pyperclip.copy(text)
            time.sleep(pre_ms)
            keyboard.send("ctrl+v")
            if prev is not None:
                time.sleep(restore_ms)
                try:
                    pyperclip.copy(prev)
                except Exception:
                    pass
            if press_enter:
                time.sleep(enter_ms)
                keyboard.send("enter")
            return
        except Exception:
            pass  # pyperclip missing or failed -> fall through to typing

    if text:
        keyboard.write(text, delay=0)
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
