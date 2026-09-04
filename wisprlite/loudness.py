"""Peak normalisation for recorded audio, not dictation.

Monotonic gain only: a quiet take is boosted towards a usable level, a take
that is already hot is left alone, and gain is capped so a near-silent take
is not blown up into a wall of hiss. No compression, no EQ, no noise
suppression — see the plan this implements for why.
"""

from __future__ import annotations

import wave

import numpy as np

TARGET_DBFS = -1.0
THRESHOLD_DBFS = -3.0
MAX_GAIN = 8.0  # +18 dB
FULL_SCALE = 32767.0


def peak_dbfs(samples: np.ndarray) -> float:
    """dBFS of the loudest sample. -inf for silence."""
    if samples.size == 0:
        return float("-inf")
    peak = float(np.abs(samples).max())
    if peak <= 0:
        return float("-inf")
    return 20.0 * np.log10(peak / FULL_SCALE)


def normalize_peak(
    samples: np.ndarray,
    *,
    target_dbfs: float = TARGET_DBFS,
    threshold_dbfs: float = THRESHOLD_DBFS,
    max_gain: float = MAX_GAIN,
) -> np.ndarray:
    """Boost a quiet int16 PCM buffer towards `target_dbfs`, gain-capped.

    Already-hot audio (peak >= threshold_dbfs) and digital silence both come
    back unchanged.
    """
    if samples.size == 0:
        return samples
    peak = float(np.abs(samples).max())
    if peak <= 0:
        return samples
    current_dbfs = 20.0 * np.log10(peak / FULL_SCALE)
    if current_dbfs >= threshold_dbfs:
        return samples
    target_peak = FULL_SCALE * (10.0 ** (target_dbfs / 20.0))
    gain = min(target_peak / peak, max_gain)
    if gain <= 1.0:
        return samples
    boosted = np.clip(samples.astype(np.float64) * gain, -32768, 32767)
    return boosted.astype(samples.dtype if samples.dtype.kind == "i" else "<i2")


def normalize_wav_file(path) -> None:
    """Rewrite a mono int16 wav in place at its normalised peak.

    Best-effort: a missing/corrupt/empty file is left untouched rather than
    raising, since this runs after the recording is already safely on disk.
    """
    try:
        with wave.open(str(path), "rb") as handle:
            params = handle.getparams()
            raw = handle.readframes(handle.getnframes())
        if params.sampwidth != 2:
            return  # only int16 PCM is supported; leave anything else alone
        samples = np.frombuffer(raw, dtype="<i2")
        normalized = normalize_peak(samples)
        if normalized is samples:
            return
        with wave.open(str(path), "wb") as handle:
            handle.setparams(params)
            handle.writeframes(normalized.tobytes())
    except Exception:
        pass
