"""Mic grouping, ranking, and the pure "test my mic" maths — no sound card needed."""

import math
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import mics


def _endpoint(index, name, hostapi, channels=1, rate=48000.0, is_default=False):
    return {
        "index": index, "name": name, "hostapi": hostapi,
        "channels": channels, "default_samplerate": rate, "is_default": is_default,
    }


# One physical mic (a USB headset) enumerated once per host API, the way
# PortAudio does on Windows — worst (MME) sorted first, as the plan warns.
_FOUR_HOST_APIS = [
    _endpoint(0, "Blue Yeti", "MME"),
    _endpoint(1, "Blue Yeti", "Windows DirectSound"),
    _endpoint(2, "Blue Yeti", "Windows WDM-KS"),
    _endpoint(3, "Blue Yeti", "Windows WASAPI", is_default=True),
]


def test_group_inputs_collapses_the_same_mic_across_host_apis_to_one_entry():
    grouped = mics.group_inputs(_FOUR_HOST_APIS)
    assert len(grouped) == 1
    assert grouped[0]["hostapi"] == "Windows WASAPI"
    assert grouped[0]["index"] == 3


def test_group_inputs_without_the_preference_order_would_pick_mme_first():
    # Sabotage check: if the host-API preference is removed and grouping just
    # takes the first endpoint seen, MME — the worst of the four — wins.
    naive_choice = _FOUR_HOST_APIS[0]
    assert naive_choice["hostapi"] == "MME"
    grouped = mics.group_inputs(_FOUR_HOST_APIS)
    assert grouped[0]["hostapi"] != "MME"


def test_group_inputs_keeps_distinct_physical_mics_separate():
    raw = _FOUR_HOST_APIS + [_endpoint(4, "USB Microphone", "Windows WASAPI")]
    grouped = mics.group_inputs(raw)
    assert len(grouped) == 2


def test_group_inputs_truncates_mme_names_to_the_same_key_as_the_full_name():
    # MME truncates device names to 31 chars; the plan's grouping key truncates
    # to 30 so both variants of the same device fall in the same bucket.
    long_name = "Microphone (Realtek High Definition Audio)"
    raw = [
        _endpoint(0, long_name[:31], "MME"),
        _endpoint(1, long_name, "Windows WASAPI"),
    ]
    grouped = mics.group_inputs(raw)
    assert len(grouped) == 1


def test_recommend_prefers_the_default_device():
    grouped = [
        {"index": 0, "name": "A", "hostapi": "Windows WASAPI", "channels": 2,
         "default_samplerate": 48000.0, "is_default": False, "is_virtual": False},
        {"index": 1, "name": "B", "hostapi": "Windows WASAPI", "channels": 1,
         "default_samplerate": 48000.0, "is_default": True, "is_virtual": False},
    ]
    assert mics.recommend(grouped)["name"] == "B"


def test_recommend_never_picks_a_virtual_or_loopback_endpoint():
    grouped = [
        {"index": 0, "name": "Stereo Mix", "hostapi": "MME", "channels": 2,
         "default_samplerate": 48000.0, "is_default": True, "is_virtual": True},
        {"index": 1, "name": "Real Mic", "hostapi": "Windows WASAPI", "channels": 1,
         "default_samplerate": 48000.0, "is_default": False, "is_virtual": False},
    ]
    assert mics.recommend(grouped)["name"] == "Real Mic"


def test_recommend_returns_none_on_an_empty_list_rather_than_raising():
    assert mics.recommend([]) is None


def test_recommend_returns_none_when_only_virtual_devices_exist():
    grouped = [{"index": 0, "name": "Stereo Mix", "hostapi": "MME", "channels": 2,
                "default_samplerate": 48000.0, "is_default": True, "is_virtual": True}]
    assert mics.recommend(grouped) is None


def test_virtual_markers_are_matched_case_insensitively():
    grouped = mics.group_inputs([_endpoint(0, "CABLE Output (VB-Audio)", "Windows WASAPI")])
    assert grouped[0]["is_virtual"] is True


def test_nvidia_broadcast_is_not_excluded_as_virtual():
    grouped = mics.group_inputs([_endpoint(0, "NVIDIA Broadcast", "Windows WASAPI")])
    assert grouped[0]["is_virtual"] is False


# -- measure() / verdict() ----------------------------------------------------

def _sine(dbfs, seconds=0.5, rate=16_000):
    amplitude = 10.0 ** (dbfs / 20.0)
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    return (np.sin(2 * np.pi * 220 * t) * amplitude).astype(np.float32)


def test_verdict_too_loud_on_clipping():
    m = {"clipping_pct": 1.0, "rms_dbfs": -10, "snr_db": 30}
    assert mics.verdict(m) == "Too loud — turn the mic gain down"


def test_verdict_nothing_heard_on_very_low_rms():
    m = {"clipping_pct": 0.0, "rms_dbfs": -50, "snr_db": 30}
    assert mics.verdict(m) == "Nothing heard — is this the right mic?"


def test_verdict_too_quiet():
    m = {"clipping_pct": 0.0, "rms_dbfs": -35, "snr_db": 30}
    assert mics.verdict(m) == "Too quiet — move closer or raise the gain"


def test_verdict_noisy_on_low_snr():
    m = {"clipping_pct": 0.0, "rms_dbfs": -10, "snr_db": 5}
    assert mics.verdict(m) == "Noisy — a lot of background for the level of your voice"


def test_verdict_good():
    m = {"clipping_pct": 0.0, "rms_dbfs": -10, "snr_db": 30}
    assert mics.verdict(m) == "Good"


def test_measure_reports_clipping_percentage():
    samples = np.full(1000, 0.995, dtype=np.float32)
    m = mics.measure(samples, 16_000)
    assert m["clipping_pct"] == 100.0


def test_measure_on_silence_does_not_raise():
    m = mics.measure(np.zeros(1000, dtype=np.float32), 16_000)
    assert m["clipping_pct"] == 0.0
    assert math.isinf(m["peak_dbfs"])


def test_measure_on_empty_array_does_not_raise():
    m = mics.measure(np.array([], dtype=np.float32), 16_000)
    assert m["clipping_pct"] == 0.0


def test_silence_reports_no_signal_to_noise_not_infinite():
    """rms and noise floor are both -inf for digital silence: 0/0, undefined.
    Reporting inf made a dead mic look like it had immaculate SNR."""
    m = mics.measure(np.zeros(16_000, dtype=np.float32), 16_000)
    assert m["snr_db"] == 0.0
    assert mics.verdict(m) == "Nothing heard — is this the right mic?"


def test_a_stereo_endpoint_does_not_outrank_a_mono_one():
    """Channel count says nothing about microphone quality — webcams and
    headsets report both — so it must not decide the recommendation."""
    grouped = [
        {"index": 3, "name": "Yeti Mono", "hostapi": "Windows WASAPI", "channels": 1,
         "default_samplerate": 48000.0, "is_default": False, "is_virtual": False},
        {"index": 7, "name": "Webcam Stereo", "hostapi": "Windows WASAPI", "channels": 2,
         "default_samplerate": 48000.0, "is_default": False, "is_virtual": False},
    ]
    assert mics.recommend(grouped)["index"] == 3, \
        "the stereo endpoint won purely on channel count"
