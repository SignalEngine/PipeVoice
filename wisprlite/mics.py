"""A microphone picker that recommends, instead of dumping raw PortAudio endpoints.

Pure functions over plain dicts/arrays so the grouping, ranking, and level
maths are all testable without a sound card. Only `list_inputs` touches
`sounddevice`.
"""

from __future__ import annotations

import re

import numpy as np

# Best endpoint first. Anything not listed (rare host APIs) sorts last.
_HOSTAPI_PREFERENCE = ["Windows WASAPI", "Windows WDM-KS", "Windows DirectSound", "MME"]

# Loopback/virtual endpoints: real inputs, but never the recommendation.
_VIRTUAL_MARKERS = [
    "stereo mix", "what u hear", "cable output", "voicemeeter out", "wave out mix",
]


def _normalize_name(name: str) -> str:
    # Truncate the RAW name first, then strip punctuation — MME truncates to
    # 31 raw chars, so cutting our own key at 30 raw chars (one shorter) keeps
    # it a prefix of whatever the other host APIs report for the same device.
    # Stripping symbols before truncating would instead cut at a different
    # point depending on how many symbols fell inside each window.
    return re.sub(r"[^a-z0-9]", "", (name or "")[:30].lower())


def _hostapi_rank(hostapi: str) -> int:
    try:
        return _HOSTAPI_PREFERENCE.index(hostapi)
    except ValueError:
        return len(_HOSTAPI_PREFERENCE)


def _is_virtual(name: str) -> bool:
    lowered = (name or "").lower()
    return any(marker in lowered for marker in _VIRTUAL_MARKERS)


def list_inputs() -> list[dict]:
    """Every raw input endpoint PortAudio reports, undeduplicated."""
    import sounddevice as sd

    hostapis = sd.query_hostapis()
    try:
        default_index = sd.default.device[0]
    except Exception:
        default_index = None

    out = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) <= 0:
            continue
        hostapi_idx = d.get("hostapi")
        hostapi_name = hostapis[hostapi_idx]["name"] if hostapi_idx is not None else ""
        out.append({
            "index": i,
            "name": d.get("name", ""),
            "hostapi": hostapi_name,
            "channels": d.get("max_input_channels", 0),
            "default_samplerate": d.get("default_samplerate", 0.0),
            "is_default": i == default_index,
        })
    return out


def group_inputs(raw: list[dict]) -> list[dict]:
    """One entry per physical mic: the best-hostapi endpoint from each group."""
    groups: dict[str, list[dict]] = {}
    for entry in raw:
        key = _normalize_name(entry.get("name", ""))
        groups.setdefault(key, []).append(entry)

    grouped = []
    for endpoints in groups.values():
        best = min(endpoints, key=lambda e: _hostapi_rank(e.get("hostapi", "")))
        grouped.append({
            "index": best["index"],
            "name": best["name"],
            "hostapi": best.get("hostapi", ""),
            "channels": best.get("channels", 0),
            "default_samplerate": best.get("default_samplerate", 0.0),
            "is_default": any(e.get("is_default") for e in endpoints),
            "is_virtual": _is_virtual(best["name"]),
        })
    return grouped


def recommend(grouped: list[dict]) -> dict | None:
    """The one grouped entry to suggest, or None if nothing qualifies."""
    candidates = [g for g in grouped if not g.get("is_virtual")]
    if not candidates:
        return None
    return min(
        candidates,
        # Deliberately NOT ranked by channel count. A stereo endpoint is not a
        # better microphone than a mono one - webcams and headsets report both -
        # so the count carries no signal and ordering by it just picks wrong.
        key=lambda g: (
            0 if g.get("is_default") else 1,
            -g.get("default_samplerate", 0.0),
            g.get("index", 0),
        ),
    )


# -- "Test my mic" ------------------------------------------------------------

FRAME_MS = 20


def measure(samples: np.ndarray, rate: int) -> dict:
    """Peak/RMS/noise-floor/clipping/SNR over a float32 [-1, 1] buffer."""
    samples = np.asarray(samples, dtype=np.float64).reshape(-1)
    if samples.size == 0:
        return {"peak_dbfs": float("-inf"), "rms_dbfs": float("-inf"),
                "noise_floor_dbfs": float("-inf"), "clipping_pct": 0.0, "snr_db": 0.0}

    def to_dbfs(value: float) -> float:
        return 20.0 * np.log10(value) if value > 0 else float("-inf")

    peak = float(np.abs(samples).max())
    rms = float(np.sqrt(np.mean(samples ** 2)))
    clipping_pct = float(np.mean(np.abs(samples) >= 0.99) * 100.0)

    frame_len = max(1, int(rate * FRAME_MS / 1000))
    frame_count = samples.size // frame_len
    if frame_count > 0:
        frames = samples[: frame_count * frame_len].reshape(frame_count, frame_len)
        frame_rms = np.sqrt(np.mean(frames ** 2, axis=1))
        noise_floor = float(np.percentile(frame_rms, 10))
    else:
        noise_floor = rms

    rms_db = to_dbfs(rms)
    noise_floor_db = to_dbfs(noise_floor)
    if rms_db == float("-inf"):
        snr_db = 0.0          # digital silence: 0/0, not infinite signal
    elif noise_floor_db == float("-inf"):
        snr_db = float("inf")  # real signal, immeasurably quiet floor
    else:
        snr_db = rms_db - noise_floor_db

    return {
        "peak_dbfs": to_dbfs(peak),
        "rms_dbfs": rms_db,
        "noise_floor_dbfs": noise_floor_db,
        "clipping_pct": clipping_pct,
        "snr_db": snr_db,
    }


def verdict(measurement: dict) -> str:
    """First-match-wins verdict string for a `measure()` result."""
    if measurement["clipping_pct"] > 0.1:
        return "Too loud — turn the mic gain down"
    if measurement["rms_dbfs"] < -40:
        return "Nothing heard — is this the right mic?"
    if measurement["rms_dbfs"] < -30:
        return "Too quiet — move closer or raise the gain"
    if measurement["snr_db"] < 15:
        return "Noisy — a lot of background for the level of your voice"
    return "Good"
