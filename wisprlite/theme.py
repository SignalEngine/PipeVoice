"""DPI awareness, UI scale and the one type scale for the settings-process windows.

Call ``enable_dpi_awareness()`` before the first ``tk.Tk()`` and ``init(root)`` right
after it. Sizes in the ramp are Fluent "effective pixels"; ``font()`` turns them into
negative (pixel) Tk sizes times the real scale, so text is the same size at any DPI.
"""

from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)

SCALE = 1.0
_inited = False
_families: set[str] | None = None

# role -> (epx, semibold)
RAMP = {
    "caption": (12, False),
    "body": (14, False),
    "body_strong": (14, True),
    "body_large": (18, False),
    "body_large_strong": (18, True),
    "subtitle": (20, True),
    "title": (28, True),
}
_DISPLAY_ROLES = ("subtitle", "title")


def enable_dpi_awareness() -> None:
    """Windows only; no-op elsewhere. Never raises."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception as e:
        log.debug("SetProcessDpiAwareness failed: %s", e)
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception as e:
        log.debug("SetProcessDPIAware failed: %s", e)


def init(root) -> None:
    """Set SCALE from the env override or the real DPI. Idempotent; first call wins."""
    global SCALE, _inited
    if _inited:
        return
    env_set = False
    scale = None
    raw = os.environ.get("PV_UI_SCALE")
    if raw:
        try:
            v = float(raw)
            if 0.5 <= v <= 4:
                scale, env_set = v, True
        except ValueError:
            pass
    if scale is None:
        scale = root.winfo_fpixels("1i") / 96.0
    if not env_set:
        scale = min(max(scale, 1.0), 4.0)
    SCALE = scale
    _inited = True
    root.tk.call("tk", "scaling", SCALE * 96 / 72)


def px(n):
    """Scale a pixel count (or a 2-tuple such as ``pady=(a, b)``)."""
    if isinstance(n, tuple):
        return tuple(round(v * SCALE) for v in n)
    return round(n * SCALE)


def _present() -> set[str]:
    global _families
    if _families is None:
        import tkinter.font as tkfont

        _families = set(tkfont.families())
    return _families


def _first(candidates: list[str]) -> str | None:
    have = _present()
    return next((c for c in candidates if c in have), None)


def font(role: str, underline: bool = False, italic: bool = False) -> tuple:
    size, strong = RAMP[role]
    display = role in _DISPLAY_ROLES
    styles: list[str] = []
    if strong:
        semi = ["Segoe UI Variable Text Semibold", "Segoe UI Semibold"]
        if display:
            semi.insert(0, "Segoe UI Variable Display Semibold")
        family = _first(semi)
        if family is None:
            reg = ["Segoe UI Variable Text", "Segoe UI"]
            if display:
                reg.insert(0, "Segoe UI Variable Display")
            family = _first(reg) or "Segoe UI"
            styles.append("bold")
    else:
        reg = ["Segoe UI Variable Text", "Segoe UI"]
        if display:
            reg.insert(0, "Segoe UI Variable Display")
        family = _first(reg) or "Segoe UI"
    if italic:
        styles.append("italic")
    if underline:
        styles.append("underline")
    return (family, -px(size), *styles)
