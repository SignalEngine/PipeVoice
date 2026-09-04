"""Peak normalisation: monotonic gain only, capped, never on silence."""

import pathlib
import sys
import wave

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import loudness


def _sine_at(dbfs: float, seconds: float = 0.5, rate: int = 16_000) -> np.ndarray:
    amplitude = loudness.FULL_SCALE * (10.0 ** (dbfs / 20.0))
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    return (np.sin(2 * np.pi * 220 * t) * amplitude).astype("<i2")


def test_a_quiet_sine_is_boosted_towards_the_target():
    quiet = _sine_at(-10.0)  # well inside the 8x/+18dB cap
    boosted = loudness.normalize_peak(quiet)
    assert abs(loudness.peak_dbfs(boosted) - loudness.TARGET_DBFS) <= 0.5


def test_an_already_hot_sine_is_left_untouched():
    hot = _sine_at(-2.0)
    result = loudness.normalize_peak(hot)
    assert np.array_equal(result, hot)


def test_digital_silence_is_left_untouched_no_crash():
    silence = np.zeros(8000, dtype="<i2")
    result = loudness.normalize_peak(silence)
    assert np.array_equal(result, silence)


def test_empty_buffer_is_left_untouched_no_crash():
    empty = np.array([], dtype="<i2")
    result = loudness.normalize_peak(empty)
    assert result.size == 0


def test_a_near_silent_take_is_capped_not_blown_into_a_wall_of_hiss():
    near_silent = _sine_at(-40.0)  # needs +39dB; cap holds it at +18dB
    boosted = loudness.normalize_peak(near_silent)
    gain = loudness.peak_dbfs(boosted) - loudness.peak_dbfs(near_silent)
    assert gain <= 18.1, "gain must never exceed the 8x/+18dB cap"
    assert loudness.peak_dbfs(boosted) < loudness.TARGET_DBFS - 1, (
        "a near-silent take must not reach the same target as a merely quiet one"
    )


def test_normalize_wav_file_rewrites_a_quiet_wav_in_place(tmp_path):
    path = tmp_path / "mic.wav"
    quiet = _sine_at(-10.0)
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
        handle.writeframes(quiet.tobytes())

    loudness.normalize_wav_file(path)

    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
    result = np.frombuffer(raw, dtype="<i2")
    assert abs(loudness.peak_dbfs(result) - loudness.TARGET_DBFS) <= 0.5


def test_normalize_wav_file_never_raises_on_a_missing_file(tmp_path):
    loudness.normalize_wav_file(tmp_path / "nope.wav")
