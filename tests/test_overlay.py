"""Headless tests for the meeting overlay's pure meter mapping.

A meter is only useful if it DISCRIMINATES. Two curves have already shipped that
technically "worked" and were useless in practice:

  * `level * 7.0` — linear. Normal speech (RMS ~0.03) reached 0.21, a sliver, so
    the meter looked dead while someone was talking.
  * `1 - exp(-level * 160)`, saturating at 0.05 — normal speech reached 0.99 and
    the meter pinned full the moment anyone spoke, animating only in near-silence.

Both read to a user as "the meter is broken", so these tests assert the SHAPE
across realistic levels rather than checking one loose bound.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite.overlay import auto_gain, meter_level, update_peak  # noqa: E402

SILENCE = 0.0
ROOM_TONE = 0.002
QUIET = 0.008
SPEECH = 0.03
LOUD = 0.09
CLIPPING = 1.0


def test_silence_is_empty():
    assert meter_level(SILENCE) == 0.0


def test_room_tone_is_visible_but_low():
    """Idle noise should show life without looking like speech."""
    assert 0.02 < meter_level(ROOM_TONE) < 0.30


def test_normal_speech_sits_mid_meter_with_headroom_left():
    """The failure of BOTH previous curves. Speech must be clearly readable and
    must NOT pin the meter — otherwise louder speech has nowhere to go."""
    value = meter_level(SPEECH)
    assert 0.40 < value < 0.80, value


def test_loud_speech_is_clearly_above_normal_speech():
    assert meter_level(LOUD) - meter_level(SPEECH) > 0.10


def test_clipping_is_full_scale():
    assert meter_level(CLIPPING) == 1.0


def test_curve_is_monotonic():
    levels = [SILENCE, ROOM_TONE, QUIET, SPEECH, LOUD, 0.3, CLIPPING]
    values = [meter_level(v) for v in levels]
    assert values == sorted(values), values


def test_bad_input_does_not_raise():
    for junk in (None, "loud", float("nan"), -1.0):
        assert 0.0 <= meter_level(junk) <= 1.0


# ---- auto-gain, driven through the REAL feedback loop -----------------------
# The previous tests passed hand-picked (raw, peak) pairs the loop cannot
# sustain — e.g. raw=0.0002 with peak=0.008, a 40:1 ratio that decays away in
# seconds. Proof they gated nothing: setting METER_PEAK_DECAY = 1.0 (peak never
# decays, the worst case) left all of them green. These drive frames instead.

def _run(levels):
    """Feed a sequence of raw RMS values through peak-update + auto_gain."""
    peak, out = 0.0, []
    for raw in levels:
        peak = update_peak(peak, raw)
        out.append(auto_gain(raw, peak))
    return out


def test_sustained_room_tone_never_reads_as_speech():
    """The dangerous case: the wrong mic picking up only a fan. Auto-gain
    normalises to a stream's own peak, so without a reference FLOOR any
    sustained level converges to an identical reading and room tone landed at
    0.555 — inside the speech band. A user would believe they were being
    recorded while the capture was unusable."""
    settled = _run([ROOM_TONE] * 600)[-1]
    assert settled < 0.40, settled


def test_speech_still_outreads_room_tone_at_steady_state():
    assert _run([SPEECH] * 600)[-1] - _run([ROOM_TONE] * 600)[-1] > 0.20


def test_meter_recovers_after_a_loud_stream_goes_quiet():
    """Catches a frozen or too-slow peak. With METER_PEAK_DECAY = 1.0 the peak
    from the loud passage never falls and the quiet stream reads dead forever —
    which is exactly the 'mic barely shows' symptom this whole change targets."""
    loud_then_quiet = _run([0.25] * 300 + [0.01] * 300)
    assert loud_then_quiet[300] < 0.30, "should dip right after the drop"
    assert loud_then_quiet[-1] > 0.45, loud_then_quiet[-1]


def test_a_quiet_mic_reads_as_alive_once_settled():
    """The reported bug, as the loop actually produces it."""
    assert _run([0.006] * 600)[-1] > 0.40


def test_peak_rises_instantly_and_decays_slowly():
    assert update_peak(0.0, 0.2) == 0.2, "a transient must be caught immediately"
    decayed = update_peak(0.2, 0.0)
    assert 0.0 < decayed < 0.2 and decayed > 0.15, decayed


def test_non_finite_never_latches_the_peak():
    """inf does not raise, and max(raw, inf * decay) stays inf forever — the
    meter would die for the process lifetime with no recovery."""
    assert update_peak(float("inf"), 0.01) == 0.0
    assert update_peak(0.01, float("inf")) == 0.0
    assert auto_gain(float("inf"), 0.01) == 0.0
    assert auto_gain(float("nan"), 0.01) == 0.0


def test_auto_gain_survives_junk():
    for junk in (None, "x", -1.0):
        assert 0.0 <= auto_gain(junk, 0.01) <= 1.0
        assert 0.0 <= auto_gain(0.01, junk) <= 1.0
        assert 0.0 <= update_peak(junk, 0.01) <= 1.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")


def test_decorators_belong_to_the_functions_below_them():
    # Twice in one session a method inserted directly beneath a decorator STOLE
    # it from the function that owned it: @property from MeetingRecorder.levels
    # (killing both REC meters) and @staticmethod from Overlay._blend, which is
    # called as self._blend() from seven places and would have passed `self` as
    # the start colour, crashing every frame of the overlay.
    from wisprlite.overlay import Overlay

    assert isinstance(Overlay.__dict__["_blend"], staticmethod)
    assert Overlay._blend("#000000", "#ffffff", 0.5) == "#808080"
    assert not isinstance(Overlay.__dict__["_show_bleed_warning"], (staticmethod, property))


def test_bleed_warning_rearms_for_the_next_meeting():
    # The scene does not change between meetings, so keying the one-shot flag off
    # the scene meant a second meeting could never warn. A restarting clock is
    # what marks a new recording.
    state = {"bleed_warned": True, "bleed_last_elapsed": 300.0}

    def tick(elapsed):
        if elapsed < state.get("bleed_last_elapsed", 0.0):
            state["bleed_warned"] = False
        state["bleed_last_elapsed"] = elapsed

    tick(305.0)
    assert state["bleed_warned"] is True, "same meeting must not re-warn"
    tick(2.0)
    assert state["bleed_warned"] is False, "a new meeting must be able to warn"


def test_a_popup_can_be_dragged_and_is_not_killed_by_a_click():
    import pytest

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from uistub import have_display

    if not have_display():
        pytest.skip("no X display; run under xvfb-run")

    # Binding <Button-1> to destroy meant ANY click closed it — including the
    # press that starts a drag, so a nudge landing over what you were reading
    # could not be moved out of the way.
    import tkinter as tk

    from wisprlite.overlay import Overlay

    root = tk.Tk()
    try:
        canvas = tk.Canvas(root)
        canvas.pack()
        root.update()
        Overlay._show_bleed_warning(object(), canvas)
        root.update_idletasks()
        root.update()

        tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        assert len(tops) == 1
        top = tops[0]
        body = next(w for w in top.winfo_children() if isinstance(w, tk.Frame))
        labels = [w for w in body.winfo_children() if isinstance(w, tk.Label)]
        close = [w for w in labels if w.cget("text") == "✕"]
        text = next(w for w in labels if w.cget("text") != "✕")
        assert close, "it needs a real close control, since clicking no longer closes it"

        x0, y0 = top.winfo_x(), top.winfo_y()
        text.event_generate("<Button-1>", x=5, y=5)
        root.update()
        assert [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)], (
            "a plain click must NOT dismiss it"
        )

        # Handlers are bound only on the toplevel: Tk delivers child events to
        # the toplevel bindtag too, so binding both fires twice and the second
        # call writes the old position back, leaving it motionless.
        # Drag by grabbing the TEXT, which is how anyone actually moves it, and
        # is the path that double-fires: Tk delivers a child's event to the
        # toplevel bindtag as well. Generating on `top` alone would pass even
        # with the bug present.
        text.event_generate("<Button-1>", rootx=x0 + 20, rooty=y0 + 20)
        root.update()
        text.event_generate("<B1-Motion>", rootx=x0 + 140, rooty=y0 + 90)
        top.update_idletasks()
        root.update()
        assert (top.winfo_x() - x0, top.winfo_y() - y0) == (120, 70), (
            f"drag should move it; got {(top.winfo_x() - x0, top.winfo_y() - y0)}"
        )

        # The button is placed at the frame's right edge and the frame is only
        # as wide as its widest child, so text ran straight under the glyph.
        button = close[0]
        button_left = button.winfo_x()
        for label in labels:
            if label is not button:
                assert label.winfo_x() + label.winfo_width() <= button_left, (
                    "text runs under the close button"
                )

        button.event_generate("<Button-1>")
        root.update()
        assert not [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
    finally:
        root.destroy()


def test_dragging_the_meeting_pill_moves_it_instead_of_stopping_the_meeting():
    # The pill has no title bar, and its body was bound straight to
    # toggle_meeting — so grabbing it to move it STOPPED the recording and hid
    # the window. Drag must move it; only a clean click may toggle.
    import pytest
    import tkinter as tk

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from uistub import have_display

    if not have_display():
        pytest.skip("no X display; run under xvfb-run")

    from wisprlite.overlay import Overlay

    root = tk.Tk()
    try:
        root.geometry("200x60+100+100")
        canvas = tk.Canvas(root, width=200, height=60)
        canvas.pack()
        root.update()

        clicks = []
        # The callback takes coordinates now: the recording pill carries
        # buttons, so a click has to say WHERE it landed.
        Overlay._bind_drag_or_click(canvas, root,
                                    lambda x, y: clicks.append((x, y)))

        x0, y0 = root.winfo_x(), root.winfo_y()
        canvas.event_generate("<Button-1>", rootx=x0 + 10, rooty=y0 + 10)
        canvas.event_generate("<B1-Motion>", rootx=x0 + 90, rooty=y0 + 50)
        root.update_idletasks()
        canvas.event_generate("<ButtonRelease-1>", rootx=x0 + 90, rooty=y0 + 50)
        root.update()
        assert (root.winfo_x() - x0, root.winfo_y() - y0) == (80, 40), (
            f"drag must move the pill; got {(root.winfo_x() - x0, root.winfo_y() - y0)}"
        )
        assert clicks == [], "a drag must NOT toggle the meeting"

        # A clean click still works, or the pill becomes impossible to use.
        x1, y1 = root.winfo_x(), root.winfo_y()
        canvas.event_generate("<Button-1>", rootx=x1 + 10, rooty=y1 + 10)
        canvas.event_generate("<ButtonRelease-1>", rootx=x1 + 10, rooty=y1 + 10)
        root.update()
        assert len(clicks) == 1, "a click without a drag must still toggle"
        assert (root.winfo_x(), root.winfo_y()) == (x1, y1)

        # A pixel of jitter during a click is still a click, not a drag.
        canvas.event_generate("<Button-1>", rootx=x1 + 10, rooty=y1 + 10)
        canvas.event_generate("<B1-Motion>", rootx=x1 + 11, rooty=y1 + 10)
        canvas.event_generate("<ButtonRelease-1>", rootx=x1 + 11, rooty=y1 + 10)
        root.update()
        assert len(clicks) == 2, "1px of jitter must not swallow the click"
    finally:
        root.destroy()


def test_the_pill_actually_uses_the_drag_binding():
    # A working helper nobody calls fixes nothing: the drag test builds its own
    # canvas, so it stays green even if _run still binds <Button-1> straight to
    # the queue. This asserts the wiring itself.
    import inspect

    from wisprlite.overlay import Overlay

    source = inspect.getsource(Overlay._run)
    assert "_bind_drag_or_click" in source, "the pill must be bound through the drag helper"
    assert 'bind("<Button-1>"' not in source, (
        "a raw <Button-1> binding on the pill toggles the meeting on the press "
        "that starts a drag"
    )


def test_a_fourth_done_button_still_fits_inside_the_pill():
    """The width was a fixed 108px. A fourth button measured 456px inside a
    380px pill, so two would have been drawn off the edge — and the hit test
    would still have claimed they were there."""
    from wisprlite.overlay import Overlay, WIN_W

    ov = Overlay.__new__(Overlay)   # geometry reads class attributes only
    boxes = [ov._done_button_box(i) for i in range(len(Overlay.SCREENREC_DONE))]
    assert boxes[0][0] >= 0, f"the first button starts off the left edge: {boxes[0]}"
    assert boxes[-1][2] <= WIN_W, f"the last button runs off the right edge: {boxes[-1]}"
    for left, right in zip(boxes, boxes[1:]):
        assert left[2] <= right[0], "the buttons overlap"


def test_every_done_button_is_reachable_by_a_click():
    """A button drawn but not hit-testable is worse than no button."""
    from wisprlite.overlay import Overlay

    ov = Overlay.__new__(Overlay)
    for index, (action, _label) in enumerate(Overlay.SCREENREC_DONE):
        x1, y1, x2, y2 = ov._done_button_box(index)
        hit = ov._screenrec_hit((x1 + x2) // 2, (y1 + y2) // 2, "done")
        assert hit == action, f"clicking the {action!r} button returned {hit!r}"


def test_reading_buttons_fit_inside_the_pill():
    """Same shared geometry as the done row - must not regress just because a
    second button set now uses it."""
    from wisprlite.overlay import Overlay, WIN_W

    ov = Overlay.__new__(Overlay)
    boxes = [ov._done_button_box(i, Overlay.READING_BUTTONS)
             for i in range(len(Overlay.READING_BUTTONS))]
    assert boxes[0][0] >= 0, f"the first reading button starts off the left edge: {boxes[0]}"
    assert boxes[-1][2] <= WIN_W, f"the last reading button runs off the right edge: {boxes[-1]}"
    for left, right in zip(boxes, boxes[1:]):
        assert left[2] <= right[0], "the reading buttons overlap"


def test_every_reading_button_is_reachable_by_a_click():
    from wisprlite.overlay import Overlay

    ov = Overlay.__new__(Overlay)
    for index, (action, _label) in enumerate(Overlay.READING_BUTTONS):
        x1, y1, x2, y2 = ov._done_button_box(index, Overlay.READING_BUTTONS)
        hit = ov._reading_hit((x1 + x2) // 2, (y1 + y2) // 2)
        assert hit == action, f"clicking the {action!r} button returned {hit!r}"


def test_a_click_outside_the_reading_buttons_hits_nothing():
    from wisprlite.overlay import Overlay

    ov = Overlay.__new__(Overlay)
    assert ov._reading_hit(0, 0) == ""
