"""The Wispr-style HUD: a small frameless pill near the bottom of the screen.

Shows a pulsing status dot, a live mic VU meter while listening, and the live
(streaming) transcript as it comes in. Runs its own tkinter mainloop on a
dedicated thread; the app talks to it through a thread-safe queue. If tkinter
is unavailable it silently becomes a no-op.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from typing import Callable, Optional

from .winui import PALETTE

FRAME_MS = 33          # ~30 fps
METER_N = 16           # number of VU bars
METER_BW = 4           # bar width
METER_GAP = 3          # gap between bars
WIN_W, WIN_H = 380, 68
TRANSPARENT = "#010203"  # Windows color key punched out for rounded corners

ACCENT = {
    "listening": PALETTE["accent"],
    "transcribing": PALETTE["amber"],
    "error": PALETTE["error"],
    "done": PALETTE["done"],
    "idle": PALETTE["muted"],
    "picker": PALETTE["picker"],
    "meeting": PALETTE["meeting"],
}


class Overlay:
    def __init__(
        self,
        level_provider: Optional[Callable[[], float]] = None,
        enabled: bool = True,
        meeting_provider: Optional[Callable[[], dict]] = None,
    ) -> None:
        self.level_provider = level_provider or (lambda: 0.0)
        self.meeting_provider = meeting_provider or (lambda: {})
        self.enabled = enabled
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._started = False

    # ---- public, thread-safe API -----------------------------------------
    def start(self) -> None:
        if not self.enabled or self._started:
            return
        self._started = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def show(self, state: str, text: str = "") -> None:
        self._q.put(("show", state, text))

    def set_state(self, state: str, text: Optional[str] = None) -> None:
        self._q.put(("state", state, text))

    def set_text(self, text: str) -> None:
        self._q.put(("text", None, text))

    def hide(self) -> None:
        self._q.put(("hide", None, ""))

    def stop(self) -> None:
        self._q.put(("quit", None, ""))

    def show_picker(self, items: list, title: str = "Pick a voice") -> None:
        self._q.put(("picker", title, list(items or [])))

    def show_meeting(self) -> None:
        self._q.put(("meeting", None, ""))

    # ---- tkinter thread ---------------------------------------------------
    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception:
            return

        root = tk.Tk()
        root.overrideredirect(True)
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        bg = TRANSPARENT
        try:
            root.configure(bg=bg)
            root.attributes("-transparentcolor", bg)  # Windows: rounded pill
        except Exception:
            bg = PALETTE["bg"]
            root.configure(bg=bg)
        try:
            root.attributes("-alpha", 0.96)
        except Exception:
            pass

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x = 24                       # bottom-left corner, out of the way
        y = sh - WIN_H - 60
        root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

        canvas = tk.Canvas(root, width=WIN_W, height=WIN_H, bg=bg, highlightthickness=0)
        canvas.pack()
        root.withdraw()

        st = {
            "name": "idle",
            "text": "",
            "phase": 0.0,
            "hist": [0.0] * METER_N,
            "targets": [0.0] * METER_N,
            "visible": False,
            "hide_at": 0.0,
            "picker_title": "",
            "picker_items": [],
            "h": WIN_H,            # current window height (grows for the picker list)
            "scene": None,
            "items": {},
        }

        def reveal():
            st["visible"] = True
            try:
                root.deiconify()
                root.lift()
                root.attributes("-topmost", True)
            except Exception:
                pass

        def conceal():
            st["visible"] = False
            st["hide_at"] = 0.0
            try:
                root.withdraw()
            except Exception:
                pass

        def resize(h: int) -> None:
            # the picker grows the pill upward from its usual corner so each voice
            # gets its own line; everything else uses the short pill.
            if h == st["h"]:
                return
            st["h"] = h
            try:
                canvas.config(height=h)
                root.geometry(f"{WIN_W}x{h}+{x}+{sh - h - 60}")
            except Exception:
                pass

        def drain() -> bool:
            try:
                while True:
                    kind, state, text = self._q.get_nowait()
                    if kind == "quit":
                        root.quit()
                        return False
                    if kind == "hide":
                        st["name"] = "idle"
                        resize(WIN_H)
                        conceal()
                    elif kind == "show":
                        st["name"] = state or "listening"
                        st["text"] = text or ""
                        st["hide_at"] = 0.0
                        resize(WIN_H)
                        reveal()
                    elif kind == "meeting":
                        st["name"] = "meeting"
                        st["text"] = ""
                        st["hide_at"] = 0.0
                        resize(WIN_H)
                        reveal()
                    elif kind == "state":
                        if state:
                            st["name"] = state
                        if text is not None:
                            st["text"] = text
                        resize(WIN_H)
                        reveal()
                        if state in ("done", "error"):
                            st["hide_at"] = time.time() + (2.2 if state == "error" else 1.4)
                    elif kind == "text":
                        st["text"] = text or ""
                        resize(WIN_H)
                        reveal()
                    elif kind == "picker":
                        st["name"] = "picker"
                        st["picker_title"] = state or "Pick a voice"
                        items = text if isinstance(text, list) else []
                        st["picker_items"] = items
                        st["hide_at"] = 0.0
                        st["scene"] = None
                        resize(44 + 30 * max(1, min(6, len(items))) + 12)
                        reveal()
            except queue.Empty:
                pass
            return True

        def tick():
            if not drain():
                return
            if st["hide_at"] and time.time() >= st["hide_at"]:
                conceal()
            if st["visible"]:
                self._draw(canvas, st)
            root.after(FRAME_MS, tick)

        root.after(FRAME_MS, tick)
        try:
            root.mainloop()
        except Exception:
            pass

    # ---- drawing ----------------------------------------------------------
    def _draw(self, c, st) -> None:
        H = st.get("h", WIN_H)
        accent = ACCENT.get(st["name"], ACCENT["idle"])

        if st["name"] == "picker":
            self._draw_picker(c, st, H, accent)
            return

        st["phase"] += 0.1
        if st["name"] == "listening":
            self._draw_listening(c, st, H, accent)
            return
        if st["name"] == "meeting":
            self._draw_meeting(c, st, H, accent)
            return
        self._draw_status(c, st, H, accent)

    def _base_scene(self, c, st, scene: str, H: int, accent: str) -> dict:
        if st["scene"] == scene:
            items = st["items"]
            c.itemconfigure(items["pill"], outline=accent)
            return items
        c.delete("all")
        st["scene"] = scene
        st["items"] = {
            "pill": self._round_rect(
                c, 3, 3, WIN_W - 3, H - 3, 24,
                fill=PALETTE["bg"], outline=accent, width=2,
            )
        }
        return st["items"]

    def _draw_picker(self, c, st, H: int, accent: str) -> None:
        items = self._base_scene(c, st, "picker", H, accent)
        if "title" in items:
            return
        items["title"] = c.create_text(
            WIN_W // 2, 22, text=st["picker_title"] or "Pick a voice",
            anchor="center", fill=accent, font=("Segoe UI", 9, "bold"),
        )
        for i, name in enumerate(st["picker_items"][:6]):
            yy = 50 + i * 30
            items[f"number_{i}"] = c.create_text(
                24, yy, text=str(i + 1), anchor="w",
                fill=accent, font=("Consolas", 13, "bold"),
            )
            items[f"name_{i}"] = c.create_text(
                48, yy, text=self._fit(name, WIN_W - 64), anchor="w",
                fill=PALETTE["fg"], font=("Segoe UI", 12),
            )

    def _draw_listening(self, c, st, H: int, accent: str) -> None:
        items = self._base_scene(c, st, "listening", H, accent)
        cy = WIN_H // 2
        cx = 28
        if "dot" not in items:
            items["ring"] = c.create_oval(0, 0, 0, 0, fill="", width=2)
            items["dot"] = c.create_oval(0, 0, 0, 0, outline="")
            for i in range(METER_N):
                items[f"bar_{i}"] = c.create_line(
                    0, cy, 0, cy, width=METER_BW, capstyle="round",
                )
            items["text"] = c.create_text(
                176, cy, anchor="w", fill=PALETTE["fg"],
                font=("Segoe UI", 12),
            )
        try:
            level = max(0.0, float(self.level_provider()))
        except Exception:
            level = 0.0
        normalized = min(1.0, level * 7.0)
        targets = st["targets"]
        targets.pop(0)
        targets.append(normalized)
        hist = st["hist"]
        for i, target in enumerate(targets):
            hist[i] += (target - hist[i]) * 0.28
            value = hist[i]
            bx = 50 + i * (METER_BW + METER_GAP)
            half_height = 1.5 + value * 15.5
            c.coords(items[f"bar_{i}"], bx, cy - half_height, bx, cy + half_height)
            c.itemconfigure(
                items[f"bar_{i}"],
                fill=self._blend(PALETTE["muted"], accent, value),
            )
        breath = (math.sin(st["phase"] * 0.55) + 1.0) / 2.0
        ring_r = 10.0 + 2.5 * breath + 4.0 * normalized
        ring_colour = self._blend(PALETTE["bg"], accent, 0.3 + 0.35 * normalized)
        c.coords(items["ring"], cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r)
        c.itemconfigure(items["ring"], outline=ring_colour)
        dot_r = 5.0 + 1.5 * normalized
        c.coords(items["dot"], cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r)
        c.itemconfigure(items["dot"], fill=accent)
        c.itemconfigure(items["text"], text=self._fit(st["text"] or "Listening…", 186))

    def _draw_meeting(self, c, st, H: int, accent: str) -> None:
        items = self._base_scene(c, st, "meeting", H, accent)
        cy = WIN_H // 2
        cx = 28
        if "dot" not in items:
            items["ring"] = c.create_oval(0, 0, 0, 0, fill="", width=2)
            items["dot"] = c.create_oval(0, 0, 0, 0, outline="")
            items["rec"] = c.create_text(
                50, cy, anchor="w", fill=PALETTE["fg"],
                font=("Consolas", 11, "bold"),
            )
            for label, label_x, x1, x2 in (
                ("mic", 145, 174, 221),
                ("desktop", 235, 292, 350),
            ):
                items[f"{label}_label"] = c.create_text(
                    label_x, cy - 9, text=label, anchor="w",
                    fill=PALETTE["muted"], font=("Segoe UI", 8),
                )
                items[f"{label}_track"] = c.create_line(
                    x1, cy + 9, x2, cy + 9, fill=PALETTE["meter_track"],
                    width=4, capstyle="round",
                )
                items[f"{label}_level"] = c.create_line(
                    x1, cy + 9, x1, cy + 9, fill=accent,
                    width=4, capstyle="round",
                )
        try:
            data = self.meeting_provider() or {}
        except Exception:
            data = {}
        levels = data.get("levels") if isinstance(data.get("levels"), dict) else {}
        errors = data.get("errors") if isinstance(data.get("errors"), dict) else {}
        elapsed = data.get("elapsed", 0.0)
        breath = (math.sin(st["phase"]) + 1.0) / 2.0
        dot_colour = self._blend(PALETTE["meeting"], PALETTE["meeting_hi"], breath)
        ring_r = 9.0 + 3.0 * breath
        c.coords(items["ring"], cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r)
        c.itemconfigure(
            items["ring"],
            outline=self._blend(PALETTE["bg"], dot_colour, 0.45),
        )
        dot_r = 5.0 + 1.6 * breath
        c.coords(items["dot"], cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r)
        c.itemconfigure(items["dot"], fill=dot_colour)
        c.itemconfigure(items["rec"], text=f"REC  {self._elapsed_text(elapsed)}")
        for label, x1, x2 in (("mic", 174, 221), ("desktop", 292, 350)):
            dead = bool(errors.get(label))
            normalized = min(1.0, max(0.0, float(levels.get(label, 0.0))) * 7.0)
            if dead:
                c.coords(items[f"{label}_level"], x1, cy + 9, x2, cy + 9)
                colour = PALETTE["error"]
            else:
                end = x1 + max(1.0, (x2 - x1) * normalized)
                c.coords(items[f"{label}_level"], x1, cy + 9, end, cy + 9)
                colour = self._blend(PALETTE["muted"], accent, normalized)
            c.itemconfigure(items[f"{label}_level"], fill=colour)
            c.itemconfigure(
                items[f"{label}_label"],
                fill=PALETTE["error"] if dead else PALETTE["muted"],
            )

    def _draw_status(self, c, st, H: int, accent: str) -> None:
        items = self._base_scene(c, st, "status", H, accent)
        cy = WIN_H // 2
        if "dot" not in items:
            items["dot"] = c.create_oval(22, cy - 6, 34, cy + 6, outline="")
            items["text"] = c.create_text(
                50, cy, anchor="w", fill=PALETTE["fg"],
                font=("Segoe UI", 12),
            )
        c.itemconfigure(items["dot"], fill=accent)
        txt = st["text"]
        if not txt:
            txt = {
                "listening": "Listening…",
                "transcribing": "Transcribing",
                "done": "",
                "error": "Error",
                "idle": "",
            }.get(st["name"], "")
        if st["name"] == "transcribing" and not st["text"]:
            txt = "Transcribing" + "." * (1 + int(st["phase"]) % 3)
        c.itemconfigure(items["text"], text=self._fit(txt, WIN_W - 68))

    @staticmethod
    def _elapsed_text(seconds: object) -> str:
        try:
            total = max(0, int(float(seconds)))
        except (TypeError, ValueError, OverflowError):
            total = 0
        if total >= 3600:
            return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"
        return f"{total // 60:02d}:{total % 60:02d}"

    @staticmethod
    def _blend(start: str, end: str, amount: float) -> str:
        amount = min(1.0, max(0.0, float(amount)))
        rgb = [
            round(int(start[index:index + 2], 16) * (1.0 - amount)
                  + int(end[index:index + 2], 16) * amount)
            for index in (1, 3, 5)
        ]
        return "#" + "".join(f"{component:02x}" for component in rgb)

    @staticmethod
    def _round_rect(c, x1, y1, x2, y2, r, **kw):
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return c.create_polygon(pts, smooth=True, **kw)

    @staticmethod
    def _fit(txt: str, maxw: float) -> str:
        maxchars = max(8, int(maxw / 7.2))
        if len(txt) > maxchars:
            return "…" + txt[-(maxchars - 1):]  # keep the latest words visible
        return txt
