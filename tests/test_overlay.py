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

from wisprlite.overlay import meter_level  # noqa: E402

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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
