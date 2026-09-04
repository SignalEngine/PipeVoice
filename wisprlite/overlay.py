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
METER_N = 16           # samples of rolling level history behind the ribbon
RIBBON_SPLINE = 20     # Bezier steps per span; higher = smoother, more work per frame
RIBBON_FLOOR = 0.06    # a sliver of ribbon at silence, so the HUD never looks dead
RIBBON_WOBBLE = 0.75   # depth of the travelling wave, scaled by level
# Per-stream auto-gain for the meeting meters (see _draw_meeting).
METER_PEAK_DECAY = 0.99      # ~2.3s half-life at 30fps: long enough that a pause
                              # between words does not collapse the reference,
                              # short enough that the meter recovers within a few
                              # seconds after a loud passage rather than ~20s.
METER_MIN_REFERENCE = 0.02    # floor the reference: without this ANY sustained
                              # level converges peak->level and every stationary
                              # stream reads an identical 0.666, with room tone
                              # (0.002) landing at 0.555 — inside the speech band.
                              # A fan on the wrong mic would have read as healthy.
METER_REFERENCE_RMS = 0.05    # a stream at its own recent peak reads as loud speech
MEETING_METER_N = 7
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
    "screenrec": PALETTE["meeting"],
}


# Meter range in dBFS. Loudness is logarithmic, so a LINEAR map of RMS is useless:
# the original `level * 7.0` left normal speech (RMS ~0.02-0.05) at a tenth of the
# bar, and the first attempt to fix that saturated at 0.05 — pinning the meter full
# the moment anyone spoke and only animating during near-silence. Both read to a user
# as "the meter is broken". These bounds put room tone near the bottom, conversational
# speech around the middle, and leave real headroom before clipping.
METER_DB_FLOOR = -62.0
METER_DB_CEIL = -8.0


def meter_level(level: float) -> float:
    """Map an RMS capture level to 0..1 for the meter, on a dB scale."""
    try:
        level = max(0.0, float(level))
    except (TypeError, ValueError, OverflowError):
        level = 0.0
    if level <= 0.0:
        return 0.0
    db = 20.0 * math.log10(level)
    span = METER_DB_CEIL - METER_DB_FLOOR
    return min(1.0, max(0.0, (db - METER_DB_FLOOR) / span))


def update_peak(peak: float, raw: float) -> float:
    """Advance a stream's decaying peak reference by one frame."""
    try:
        peak, raw = float(peak), float(raw)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not (math.isfinite(peak) and math.isfinite(raw)):
        return 0.0
    return max(max(0.0, raw), max(0.0, peak) * METER_PEAK_DECAY)


def auto_gain(raw: float, peak: float) -> float:
    """Meter value for one stream, judged against that stream's OWN recent peak.

    Desktop loopback carries system audio (a podcast sits around RMS 0.1-0.3)
    while a microphone carries a voice at ~0.01-0.03 — about ten times quieter.
    On one shared absolute scale the mic meter barely moves while desktop looks
    healthy, which is what a user reported. These meters exist to answer "is this
    stream alive?", so each is scaled to its own range instead.
    """
    try:
        raw = float(raw)
        peak = float(peak)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    # inf does not raise, and max(raw, inf * decay) == inf forever — the meter
    # would die for the process lifetime with no recovery.
    if not (math.isfinite(raw) and math.isfinite(peak)):
        return 0.0
    raw, peak = max(0.0, raw), max(0.0, peak)
    reference = max(peak, METER_MIN_REFERENCE)
    return meter_level(raw / reference * METER_REFERENCE_RMS)


class Overlay:
    def __init__(
        self,
        level_provider: Optional[Callable[[], float]] = None,
        enabled: bool = True,
        meeting_provider: Optional[Callable[[], dict]] = None,
        on_meeting_click: Optional[Callable[[], None]] = None,
        screenrec_provider: Optional[Callable[[], dict]] = None,
        on_screenrec_action: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.level_provider = level_provider or (lambda: 0.0)
        self.meeting_provider = meeting_provider or (lambda: {})
        self.on_meeting_click = on_meeting_click
        # {"elapsed": float, "paused": bool} — polled each frame so the pill's
        # clock and its button states come from the recorder, never from a copy
        # of them that can drift out of step with it.
        self.screenrec_provider = screenrec_provider or (lambda: {})
        self.on_screenrec_action = on_screenrec_action
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

    def show_screenrec(self) -> None:
        """Show the recording pill. Its clock and buttons poll the recorder."""
        self._q.put(("screenrec", None, ""))

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
        self._bind_drag_or_click(
            canvas, root, lambda x, y: self._q.put(("click", x, y)))
        canvas.bind("<Enter>", lambda _event: self._q.put(("hover", True, "")))
        canvas.bind("<Leave>", lambda _event: self._q.put(("hover", False, "")))
        root.withdraw()

        st = {
            "name": "idle",
            "text": "",
            "phase": 0.0,
            "hist": [0.0] * METER_N,
            "targets": [0.0] * METER_N,
            "meeting_hist": {
                "mic": [0.0] * MEETING_METER_N,
                "desktop": [0.0] * MEETING_METER_N,
            },
            "meeting_peak": {"mic": 0.0, "desktop": 0.0},
            "hover": False,
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

        def fire(action: str, value: str = "") -> None:
            """Hand a pill action to the app, off the Tk thread."""
            handler = self.on_screenrec_action
            if not handler:
                return
            threading.Thread(target=handler, args=(action, value), daemon=True).start()

        def drain() -> bool:
            try:
                while True:
                    kind, state, text = self._q.get_nowait()
                    if kind == "quit":
                        root.quit()
                        return False
                    if kind == "focus_tip":
                        # state = tip, text = because. Rendered on THIS thread,
                        # which is the only one allowed to touch Tk.
                        self._render_focus_tip(canvas, state, text)
                    if kind == "click":
                        if st["name"] == "meeting" and self.on_meeting_click:
                            callback = self.on_meeting_click
                            threading.Thread(target=callback, daemon=True).start()
                        elif st["name"] == "screenrec" and self.on_screenrec_action:
                            action = self._screenrec_hit(
                                state, text, st.get("screenrec_phase", "recording"))
                            if action:
                                fire(action)
                    elif kind == "hover":
                        st["hover"] = bool(state)
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
                    elif kind == "screenrec_submit":
                        # Save/Skip from the in-pill name field. The typed text
                        # travels with the action, so the app never has to reach
                        # into a widget owned by another thread.
                        typed = ""
                        entry = (st.get("items") or {}).get("entry")
                        if entry is not None and state == "save":
                            try:
                                typed = entry.get()
                            except Exception:
                                typed = ""
                        fire(state or "skip", typed)
                    elif kind == "screenrec":
                        st["name"] = "screenrec"
                        st["text"] = ""
                        st["hide_at"] = 0.0
                        st["scene"] = None
                        st["screenrec_phase"] = "recording"
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
            if st["name"] == "screenrec":
                # The finished-clip phase needs a second row for its buttons, and
                # the phase is driven by the app rather than by a queue event, so
                # the height has to follow it here.
                try:
                    phase = str((self.screenrec_provider() or {}).get("phase")
                                or "recording")
                except Exception:
                    phase = "recording"
                resize(self.SCREENREC_H.get(phase, WIN_H))
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
        if st["name"] == "screenrec":
            self._draw_screenrec(c, st, H, accent)
            return
        self._draw_status(c, st, H, accent)

    def _base_scene(self, c, st, scene: str, H: int, accent: str) -> dict:
        if st["scene"] == scene:
            items = st["items"]
            c.itemconfigure(items["pill"], outline=accent)
            return items
        c.delete("all")
        if scene == "meeting":
            # Reset the auto-gain references when a meeting scene opens. They
            # only decay inside _draw_meeting, so BETWEEN meetings they are
            # frozen, not decaying: after recording a podcast (peak ~0.25), the
            # first ~20s of the next, quieter meeting showed a near-dead meter —
            # precisely the symptom auto-gain was added to fix.
            st["meeting_peak"] = {"mic": 0.0, "desktop": 0.0}
            st["bleed_warned"] = False
            st["meeting_hist"] = {
                "mic": [0.0] * MEETING_METER_N,
                "desktop": [0.0] * MEETING_METER_N,
            }
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
            # Three spline-smoothed polygons, back to front: two trailing echoes
            # then the live ribbon. Created in draw order — Tk stacks by creation.
            # 16 discrete rectangles could never look like audio; create_polygon's
            # Bezier smoothing is what makes this read as a waveform at all.
            for layer in ("echo2", "echo1", "wave"):
                items[layer] = c.create_polygon(
                    0, cy, 0, cy, 0, cy, smooth=True, splinesteps=RIBBON_SPLINE,
                    outline="",
                )
            items["text"] = c.create_text(
                176, cy, anchor="w", fill=PALETTE["fg"],
                font=("Segoe UI", 12),
            )
        try:
            level = max(0.0, float(self.level_provider()))
        except Exception:
            level = 0.0
        normalized = meter_level(level)
        # Smooth the INCOMING value once, then push it. The previous version
        # lerped every history slot toward a shifting buffer, so the shift and
        # the smoothing blurred each other and the ribbon flattened into a
        # featureless lens — the time variation that makes it look like audio
        # was being averaged away.
        targets = st["targets"]
        smoothed = targets[-1] + (normalized - targets[-1]) * 0.45
        targets.pop(0)
        targets.append(smoothed)
        hist = st["hist"] = list(targets)
        peak = max(hist) if hist else 0.0
        for layer, lag, amp, colour in (
            ("echo2", 4, 13.0, self._blend(PALETTE["card"], accent, 0.22)),
            ("echo1", 2, 14.5, self._blend(PALETTE["card"], accent, 0.45)),
            ("wave", 0, 16.0, self._blend(PALETTE["muted"], accent, peak)),
        ):
            c.coords(items[layer], *self._ribbon_points(
                hist, lag, 50, 120.0, cy, amp, st["phase"] - lag * 0.24))
            c.itemconfigure(items[layer], fill=colour)
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
                    width=1,
                )
                for index in range(MEETING_METER_N):
                    items[f"{label}_bar_{index}"] = c.create_line(
                        0, cy + 9, 0, cy + 9, width=3, capstyle="round",
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
        c.itemconfigure(
            items["rec"],
            text="Click to stop" if st.get("hover") else f"REC  {self._elapsed_text(elapsed)}",
        )

        # Warn ONCE, during the call. Told afterwards, the user can only regret
        # it; told now, headphones still save this recording.
        # A second meeting must be able to warn again. The scene does not change
        # between meetings, so detect a fresh recording by its clock restarting.
        if elapsed < st.get("bleed_last_elapsed", 0.0):
            st["bleed_warned"] = False
        st["bleed_last_elapsed"] = elapsed

        bleed = data.get("bleed")
        try:
            suspected = bool(bleed() if callable(bleed) else bleed)
        except Exception:
            suspected = False
        if suspected and not st.get("bleed_warned"):
            st["bleed_warned"] = True
            self._show_bleed_warning(c)
        for label, x1, x2 in (("mic", 174, 221), ("desktop", 292, 350)):
            dead = bool(errors.get(label))
            try:
                raw_level = float(levels.get(label, 0.0))
            except (TypeError, ValueError, OverflowError):
                raw_level = 0.0
            # Per-stream auto-gain. Desktop loopback carries system audio (a
            # podcast sits around RMS 0.1-0.3) while the mic carries a voice at
            # ~0.01-0.03 — roughly ten times quieter. On one shared absolute
            # scale the mic meter barely moves while desktop looks fine, which
            # is exactly what a user reported. These meters answer "is this
            # stream ALIVE?", so each is judged against its OWN recent peak:
            # a reference that rises instantly and decays slowly.
            peaks = st["meeting_peak"]
            peaks[label] = update_peak(peaks[label], raw_level)
            normalized = auto_gain(raw_level, peaks[label])
            history = st["meeting_hist"][label]
            history.pop(0)
            history.append(normalized)
            if dead:
                colour = PALETTE["error"]
            else:
                colour = self._blend(PALETTE["muted"], accent, normalized)
            gap = (x2 - x1 - MEETING_METER_N * 3) / max(1, MEETING_METER_N - 1)
            for index, value in enumerate(history):
                bar_x = x1 + index * (3 + gap)
                half_height = 1.0 if dead else 1.0 + value * 7.0
                item = items[f"{label}_bar_{index}"]
                c.coords(item, bar_x, cy + 9 - half_height, bar_x, cy + 9 + half_height)
                c.itemconfigure(item, fill=colour)
            c.itemconfigure(
                items[f"{label}_label"],
                fill=PALETTE["error"] if dead else PALETTE["muted"],
            )
    # Button geometry for the recording pill, in canvas coordinates. One table,
    # used to DRAW and to HIT-TEST, so a button can never sit somewhere other
    # than where the click for it is caught.
    # Button geometry, in canvas coordinates. One table drives DRAWING and
    # HIT-TESTING, so a button can never sit somewhere other than where the
    # click for it is caught.
    SCREENREC_BUTTONS = (
        ("resume", WIN_W - 122),
        ("pause", WIN_W - 84),
        ("stop", WIN_W - 46),
    )
    SCREENREC_BUTTON_R = 15
    # The finished-recording row: three named actions, because glyphs alone do
    # not say "open PipeVoice".
    SCREENREC_DONE = (("play", "Play"), ("send", "Send"),
                      ("copy", "Copy path"), ("open", "Open"))
    DONE_BTN_H, DONE_BTN_Y = 30, 62
    DONE_GAP, DONE_EDGE = 8, 8
    SCREENREC_H = {"recording": WIN_H, "naming": 88, "working": WIN_H, "done": 100}

    @classmethod
    def _done_button_w(cls):
        # Derived from the count, never a constant. A fourth button at the old
        # fixed 108px measured 456px inside a 380px pill, so two of them would
        # have been drawn off the edge - and the hit test would still have
        # claimed they were there.
        count = len(cls.SCREENREC_DONE)
        gaps = (count - 1) * cls.DONE_GAP
        return max(48, (WIN_W - 2 * cls.DONE_EDGE - gaps) // count)

    def _done_button_box(self, index: int):
        width = self._done_button_w()
        count = len(self.SCREENREC_DONE)
        total = count * width + (count - 1) * self.DONE_GAP
        x1 = (WIN_W - total) // 2 + index * (width + self.DONE_GAP)
        return x1, self.DONE_BTN_Y, x1 + width, self.DONE_BTN_Y + self.DONE_BTN_H

    def _screenrec_hit(self, x, y, phase: str = "recording") -> str:
        """Which control a click at (x, y) landed on, or "" for the pill body."""
        if phase == "done":
            for index, (action, _label) in enumerate(self.SCREENREC_DONE):
                x1, y1, x2, y2 = self._done_button_box(index)
                if x1 <= x <= x2 and y1 <= y <= y2:
                    return action
            return ""
        if phase == "working":
            return ""                 # nothing to press while it is finishing
        cy = 56 if phase == "naming" else WIN_H // 2
        live = ("save", "skip") if phase == "naming" else \
               tuple(a for a, _cx in self.SCREENREC_BUTTONS)
        slots = self.SCREENREC_BUTTONS[-len(live):] if phase == "naming" \
            else self.SCREENREC_BUTTONS
        for action, (_name, cx) in zip(live, slots):
            if (x - cx) ** 2 + (y - cy) ** 2 <= (self.SCREENREC_BUTTON_R + 3) ** 2:
                return action
        return ""

    def _draw_screenrec(self, c, st, H: int, accent: str) -> None:
        """One pill for the whole recording, from REC dot to finished clip.

        It used to be a grey box reading "recording - press the hotkey again to
        stop", then a SEPARATE dialog to name the file, then the same box stuck
        open announcing an scp destination nobody needed to read. Four windows
        for one action. Now it is four phases of one pill.
        """
        info = {}
        try:
            info = self.screenrec_provider() or {}
        except Exception:
            info = {}
        phase = str(info.get("phase") or "recording")
        st["screenrec_phase"] = phase
        items = self._base_scene(c, st, f"screenrec:{phase}", H, accent)
        cy = WIN_H // 2

        if phase == "recording":
            self._draw_screenrec_recording(c, st, items, info, cy)
        elif phase == "naming":
            self._draw_screenrec_naming(c, st, items, info, cy)
        elif phase == "working":
            self._draw_screenrec_working(c, st, items, info, cy)
        else:
            self._draw_screenrec_done(c, st, items, info, cy)

    def _rec_dot(self, c, items, cy, *, breathing: bool, phase: float):
        if "dot" not in items:
            items["dot"] = c.create_oval(0, 0, 0, 0, outline="")
        breath = (math.sin(phase) + 1.0) / 2.0 if breathing else 0.0
        colour = self._blend(PALETTE["meeting"], PALETTE["meeting_hi"], breath) \
            if breathing else PALETTE["muted"]
        r = 6.0 + (2.0 * breath if breathing else 0.0)
        c.coords(items["dot"], 24 - r, cy - r, 24 + r, cy + r)
        c.itemconfigure(items["dot"], fill=colour)

    def _circle_buttons(self, c, items, cy, spec):
        """Draw the circular controls. `spec` maps slot -> (glyph, enabled)."""
        for action, cx in self.SCREENREC_BUTTONS:
            if action not in spec:
                continue
            glyph, live = spec[action]
            r = self.SCREENREC_BUTTON_R
            if f"{action}_bg" not in items:
                items[f"{action}_bg"] = c.create_oval(
                    cx - r, cy - r, cx + r, cy + r, fill=PALETTE["card"],
                    outline="", width=1)
                items[f"{action}_icon"] = c.create_text(
                    cx, cy, text="", fill=PALETTE["fg"], font=("Segoe UI", 11))
            c.itemconfigure(items[f"{action}_icon"], text=glyph,
                            fill=PALETTE["fg"] if live else PALETTE["div"])
            # A disabled button keeps a faint ring. Filled flat with the pill's
            # own background it disappeared, reading as "a button is missing".
            c.itemconfigure(items[f"{action}_bg"],
                            fill=PALETTE["card"] if live else PALETTE["bg"],
                            outline="" if live else PALETTE["div"])

    def _draw_screenrec_recording(self, c, st, items, info, cy) -> None:
        paused = bool(info.get("paused"))
        if "clock" not in items:
            items["clock"] = c.create_text(44, cy, anchor="w", fill=PALETTE["fg"],
                                           font=("Segoe UI Semibold", 15))
            items["label"] = c.create_text(44, cy + 15, anchor="w",
                                           fill=PALETTE["muted"], font=("Segoe UI", 8))
        # A paused recording must not keep breathing: a pulsing red dot is the
        # universal "still rolling", and showing one while paused is a lie.
        self._rec_dot(c, items, cy, breathing=not paused, phase=st["phase"])
        c.itemconfigure(items["clock"], text=self._elapsed_text(info.get("elapsed", 0.0)),
                        fill=PALETTE["muted"] if paused else PALETTE["fg"])
        c.itemconfigure(items["label"], text="Paused" if paused else "Recording")
        self._circle_buttons(c, items, cy, {
            "resume": ("\u25b6", paused),
            "pause": ("\u23f8", not paused),
            "stop": ("\u23f9", True),
        })

    def _draw_screenrec_naming(self, c, st, items, info, cy) -> None:
        """Name it here, in the pill. A second window for one short field was
        one window too many."""
        if "entry" not in items:
            # A bare text box appearing the moment you stop recording does not
            # say what it wants. One line of caption fixes that.
            items["caption"] = c.create_text(
                24, 22, anchor="w", fill=PALETTE["muted"], font=("Segoe UI", 9),
                text="Name this recording \u2014 Enter to save, Esc to skip")
            entry = self._name_entry(c)
            items["entry"] = entry
            items["entry_window"] = c.create_window(
                22, 56, anchor="w", window=entry,
                width=WIN_W - 22 - 96, height=30)
            entry.delete(0, "end")
            entry.insert(0, str(info.get("name") or ""))
            entry.focus_set()
            entry.icursor("end")
        self._circle_buttons(c, items, 56, {
            "pause": ("\u2713", True),      # save
            "stop": ("\u2715", True),       # skip
        })

    def _draw_screenrec_working(self, c, st, items, info, cy) -> None:
        """A moving bar and a line about what it is doing, because "it takes a
        few seconds" with nothing moving reads as a hang."""
        if "work_text" not in items:
            items["work_text"] = c.create_text(
                24, cy - 9, anchor="w", fill=PALETTE["fg"], font=("Segoe UI", 11))
            items["work_track"] = self._round_rect(
                c, 24, cy + 9, WIN_W - 24, cy + 15, 3,
                fill=PALETTE["meter_track"], outline="")
            items["work_bar"] = self._round_rect(
                c, 24, cy + 9, 120, cy + 15, 3, fill=PALETTE["meeting"], outline="")
        c.itemconfigure(items["work_text"],
                        text=str(info.get("status") or "Finishing up\u2026"))
        # Indeterminate: nothing here knows how long x264 or an upload will take,
        # and a fake percentage that stalls at 90% is worse than an honest sweep.
        span = WIN_W - 48
        width = span * 0.32
        travel = (span - width)
        pos = (math.sin(st["phase"] * 0.6) + 1.0) / 2.0
        x1 = 24 + travel * pos
        c.coords(items["work_bar"], *self._round_rect_points(x1, cy + 9, x1 + width, cy + 15, 3))

    def _draw_screenrec_done(self, c, st, items, info, cy) -> None:
        if "done_title" not in items:
            items["done_title"] = c.create_text(
                24, 26, anchor="w", fill=PALETTE["fg"], font=("Segoe UI Semibold", 12))
            items["done_sub"] = c.create_text(
                24, 44, anchor="w", fill=PALETTE["muted"], font=("Segoe UI", 8))
            for index, (action, label) in enumerate(self.SCREENREC_DONE):
                x1, y1, x2, y2 = self._done_button_box(index)
                items[f"done_{action}_bg"] = self._round_rect(
                    c, x1, y1, x2, y2, 8, fill=PALETTE["card"], outline="")
                items[f"done_{action}_text"] = c.create_text(
                    (x1 + x2) // 2, (y1 + y2) // 2, text=label,
                    fill=PALETTE["fg"], font=("Segoe UI", 9))
        c.itemconfigure(items["done_title"], text=str(info.get("title") or "Saved"))
        c.itemconfigure(items["done_sub"], text=str(info.get("subtitle") or ""))
        # Play leads: after watching a recording finish, playing it back is what
        # you do next nine times out of ten.
        c.itemconfigure(items["done_play_bg"], fill=PALETTE["meeting"])
        c.itemconfigure(items["done_play_text"], fill="#1a0c0d")

    @staticmethod
    def _round_rect_points(x1, y1, x2, y2, r):
        return [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]

    def _name_entry(self, c):
        import tkinter as tk

        entry = tk.Entry(
            c, bg=PALETTE["card"], fg=PALETTE["fg"], relief="flat",
            insertbackground=PALETTE["fg"], font=("Segoe UI", 11),
            highlightthickness=1, highlightbackground=PALETTE["div"],
            highlightcolor=PALETTE["meeting"],
        )
        entry.bind("<Return>", lambda _e: self._q.put(("screenrec_submit", "save", "")))
        entry.bind("<Escape>", lambda _e: self._q.put(("screenrec_submit", "skip", "")))
        return entry

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
    def _ribbon_points(hist, lag: int, x0: float, span: float, cy: float,
                       amp: float, phase: float = 0.0) -> list:
        """Mirrored outline for one smoothed ribbon layer.

        This is a VISUALISER, not an oscilloscope. Plotting the level history
        literally gives a featureless lens: 16 samples of a slow-moving envelope,
        mirrored and spline-smoothed, has no detail left to see. So the amplitude
        comes from real audio and the *shape* comes from a travelling wave — the
        same thing every music visualiser does, and what makes it read as sound
        rather than as a progress bar.

        `lag` reads further back through the history so a layer trails the live
        one; that, plus the phase offset, is the entire echo effect.
        """
        count = len(hist)
        if count < 2:
            return [x0, cy, x0 + span, cy, x0 + span, cy]
        step = span / (count - 1)
        top, bottom = [], []
        for i in range(count):
            level = max(RIBBON_FLOOR, hist[max(0, i - lag)])
            # Taper to the ends, or the mirrored shape is a flat-topped lozenge.
            taper = math.sin(math.pi * i / (count - 1)) ** 0.55
            # Travelling wave: quiet audio barely ripples, loud audio undulates.
            wobble = 1.0 - RIBBON_WOBBLE * level * (
                1.0 - math.sin(phase * 3.4 + i * 0.62)
            ) / 2.0
            x = x0 + i * step
            half = level * amp * taper * wobble
            top.extend((x, cy - half))
            bottom[:0] = (x, cy + half)
        return top + bottom

    @staticmethod
    def _bind_drag_or_click(widget, window, on_click, *, threshold: int = 3) -> None:
        """Drag `window` by `widget`; only a click that did NOT drag fires on_click.

        The overlay pill has no title bar, so the only way to move it is to drag
        its body — and its body was wired straight to "toggle the meeting". Every
        attempt to reposition it stopped the recording and hid the window, which
        reads exactly like the pill closing the instant you touch it.

        The threshold keeps a normal click a click: a few pixels of mouse jitter
        between press and release must not be read as a drag, or the pill would
        become impossible to click instead of impossible to move.
        """
        state = {"x": 0, "y": 0, "moved": False}

        def press(event) -> None:
            state["x"], state["y"] = event.x_root, event.y_root
            state["moved"] = False

        def drag(event) -> None:
            dx = event.x_root - state["x"]
            dy = event.y_root - state["y"]
            if not state["moved"] and abs(dx) + abs(dy) < threshold:
                return
            state["moved"] = True
            state["x"], state["y"] = event.x_root, event.y_root
            window.geometry(f"+{window.winfo_x() + dx}+{window.winfo_y() + dy}")

        def release(event) -> None:
            if not state["moved"]:
                # Coordinates matter now: the recording pill carries buttons, so
                # WHERE the click landed decides what it means.
                on_click(event.x, event.y)

        widget.bind("<Button-1>", press)
        widget.bind("<B1-Motion>", drag)
        widget.bind("<ButtonRelease-1>", release)

    @staticmethod
    def _make_movable(top, frame, *, dismiss_after: int) -> None:
        """Let a popup be dragged, and give it a real close button.

        Binding <Button-1> to destroy meant ANY click killed it — including the
        press that starts a drag, so it could never be moved out of the way. A
        nudge that lands over the thing you are reading has to be movable, not
        merely dismissible.

        Dragging also cancels the auto-dismiss: if you are moving it somewhere,
        you want to keep it.
        """
        import tkinter as tk

        state = {"x": 0, "y": 0, "timer": top.after(dismiss_after, top.destroy)}

        def press(event) -> None:
            state["x"], state["y"] = event.x_root, event.y_root

        def drag(event) -> None:
            if state["timer"] is not None:
                top.after_cancel(state["timer"])
                state["timer"] = None
            dx = event.x_root - state["x"]
            dy = event.y_root - state["y"]
            state["x"], state["y"] = event.x_root, event.y_root
            top.geometry(f"+{top.winfo_x() + dx}+{top.winfo_y() + dy}")

        # Reserve room for the button before creating it. It is placed at the
        # frame's right edge, and the frame is only as wide as its widest child,
        # so without this the longest line of text always runs under the glyph —
        # on a wrapped PipeFocus tip the two overlapped exactly.
        for child in frame.winfo_children():
            child.pack_configure(padx=(0, 20))

        close = tk.Label(frame, text="\u2715", bg=frame.cget("bg"),
                         fg=PALETTE["muted"], cursor="hand2",
                         font=("Segoe UI", 10, "bold"))
        close.place(relx=1.0, rely=0.0, anchor="ne")
        close.bind("<Button-1>", lambda _event: top.destroy())
        close.bind("<Enter>", lambda _e: close.config(fg=PALETTE["fg"]))
        close.bind("<Leave>", lambda _e: close.config(fg=PALETTE["muted"]))

        # Bind ONLY the toplevel. Tk delivers a child's event to the toplevel's
        # bindtag as well, so binding both fires every handler TWICE — and the
        # second drag call computes dx=0 from an already-updated origin and an
        # un-processed winfo_x(), writing the old position straight back. The
        # popup stayed exactly where it was.
        top.bind("<Button-1>", press, add="+")
        top.bind("<B1-Motion>", drag, add="+")
        for widget in (top, frame, *frame.winfo_children()):
            if widget is not close:
                widget.configure(cursor="fleur")

    def _render_focus_tip(self, canvas, tip: str, because: str) -> None:
        """A small, self-dismissing nudge. Never modal, never over the call."""
        try:
            import tkinter as tk

            root = canvas.winfo_toplevel()
            top = tk.Toplevel(root)
            top.overrideredirect(True)
            top.attributes("-topmost", True)
            top.configure(bg=PALETTE["accent"])
            frame = tk.Frame(top, bg=PALETTE["card"], padx=16, pady=12)
            frame.pack(fill="both", expand=True, padx=2, pady=2)
            tk.Label(frame, text=tip, bg=PALETTE["card"], fg=PALETTE["fg"],
                     font=("Segoe UI", 10, "bold"), wraplength=380,
                     justify="left").pack(anchor="w")
            if because:
                tk.Label(frame, text=f"\u201c{because}\u201d", bg=PALETTE["card"],
                         fg=PALETTE["muted"], font=("Segoe UI", 9, "italic"),
                         wraplength=380, justify="left").pack(anchor="w", pady=(4, 0))
            top.update_idletasks()
            width, height = top.winfo_reqwidth(), top.winfo_reqheight()
            x = max(0, top.winfo_screenwidth() - width - 40)
            y = max(0, top.winfo_screenheight() - height - 190)
            top.geometry(f"+{x}+{y}")
            self._make_movable(top, frame, dismiss_after=14000)
        except Exception:
            pass          # a nudge must never disturb the meeting it comments on

    def show_focus_tip(self, tip: str, because: str = "") -> None:
        """Queue a PipeFocus nudge for display on the overlay's own thread.

        Called from the analysis thread, so it must NOT touch Tk here — the
        overlay owns its root on another thread entirely.
        """
        try:
            self._q.put(("focus_tip", str(tip or ""), str(because or "")))
        except Exception:
            pass

    def _show_bleed_warning(self, canvas) -> None:
        """A small, self-dismissing note that the mic is hearing the speakers."""
        try:
            import tkinter as tk

            # Derived from the canvas rather than stored: the Tk root is a local
            # inside the overlay's run loop and belongs to its thread.
            root = canvas.winfo_toplevel()
            if root is None:
                return
            top = tk.Toplevel(root)
            top.overrideredirect(True)
            top.attributes("-topmost", True)
            top.configure(bg=PALETTE["amber"])
            frame = tk.Frame(top, bg=PALETTE["card"], padx=16, pady=12)
            frame.pack(fill="both", expand=True, padx=2, pady=2)
            tk.Label(frame, text="Your microphone is picking up the call",
                     bg=PALETTE["card"], fg=PALETTE["fg"],
                     font=("Segoe UI", 10, "bold")).pack(anchor="w")
            tk.Label(frame,
                     text="Everything they say will appear twice in the transcript.\n"
                          "Put headphones on now and the rest of this recording is clean.",
                     bg=PALETTE["card"], fg=PALETTE["muted"], justify="left",
                     font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))
            top.update_idletasks()
            width, height = top.winfo_reqwidth(), top.winfo_reqheight()
            x = max(0, (top.winfo_screenwidth() - width) // 2)
            y = max(0, top.winfo_screenheight() - height - 150)
            top.geometry(f"+{x}+{y}")
            # Never modal and never sticky: this must not sit over a live call.
            Overlay._make_movable(top, frame, dismiss_after=12000)
        except Exception:
            pass          # a warning must never disturb the recording itself

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
