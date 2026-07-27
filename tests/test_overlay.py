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
