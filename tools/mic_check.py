"""Answer one question: does this machine deliver microphone audio to Pipevoice?

Run it when the acoustic-bookmark Test shows nothing. It opens the mic exactly
the way the app does, counts the audio blocks that actually arrive, and prints
what it heard — so "no microphone showing" becomes a specific, fixable fact.

    .\\.venv\\Scripts\\python.exe tools\\mic_check.py

Nothing is recorded or written to disk.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def scan(sd, selected) -> int:
    """Open every input device in turn and report which ones deliver real audio.

    Windows exposes the same physical microphone several times, once per host
    API (MME, DirectSound, WASAPI). They are not equivalent: one can hand back
    a silent stream while another works perfectly. Rather than reason about
    which, measure all of them.
    """
    import numpy as np

    print("Scanning every input device — KEEP TALKING while this runs.")
    print("-" * 72)
    print(f"  {'idx':>3}  {'peak':>8}  {'host API':<12} name")
    results = []
    for index, info in enumerate(sd.query_devices()):
        if info.get("max_input_channels", 0) <= 0:
            continue
        try:
            api = sd.query_hostapis(info["hostapi"])["name"]
        except Exception:
            api = "?"
        peak = 0.0
        try:
            with sd.InputStream(samplerate=16_000, channels=1, dtype="float32",
                                blocksize=800, device=index) as stream:
                for _ in range(15):                      # ~1.2s of audio
                    block, _overflowed = stream.read(800)
                    value = float(np.max(np.abs(block))) if block.size else 0.0
                    peak = max(peak, value)
            mark = "  <- currently selected" if str(selected) == str(index) else ""
            state = "SILENT" if peak < 0.002 else "audio"
            print(f"  {index:>3}  {peak:>8.4f}  {api:<12} {info['name'][:34]} [{state}]{mark}")
            results.append((peak, index, info["name"], api))
        except Exception as exc:
            print(f"  {index:>3}  {'--':>8}  {api:<12} {info['name'][:34]} "
                  f"({type(exc).__name__})")
    print("-" * 72)
    working = sorted((r for r in results if r[0] >= 0.002), reverse=True)
    if not working:
        print("FAIL  no input device produced audio.")
        print()
        print("      If the exclusive-capable APIs (WASAPI, WDM-KS) all raised")
        print("      PortAudioError while the shared ones (MME, DirectSound) were")
        print("      merely SILENT, that is the signature of another process")
        print("      already holding the microphone: exclusive APIs refuse, shared")
        print("      APIs hand back silence.")
        print()
        print("      1. Quit Pipevoice completely from the tray and run this again.")
        print("         The settings window is a SEPARATE process from the app, so")
        print("         while the app holds the mic, the Test dialog cannot read it.")
        print("      2. Close Teams / Zoom / Discord / any browser tab in a call.")
        print("      3. Only then suspect Windows: Settings > Privacy & security >")
        print("         Microphone, and any Studio Effects / voice-isolation.")
        print()
        print("      Note: talk continuously for the WHOLE scan. Devices that error")
        print("      fail instantly, so the run can finish faster than you expect.")
        return 2
    peak, index, name, api = working[0]
    print(f"PASS  loudest working device is [{index}] {name} ({api}), peak {peak:.4f}")
    print(f"      Set Settings > Audio > Microphone to [{index}] and retest.")
    return 0


def main() -> int:
    try:
        import sounddevice as sd
    except Exception as exc:
        print(f"FAIL  sounddevice will not import: {type(exc).__name__}: {exc}")
        print("      The app cannot capture any audio at all in this environment.")
        return 2

    from wisprlite import config
    from wisprlite.snap import SnapDetector

    cfg = config.Config.load()
    device = config.device_arg(cfg)

    print("Input devices visible to Pipevoice")
    print("-" * 60)
    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = None
    for index, info in enumerate(sd.query_devices()):
        if info.get("max_input_channels", 0) > 0:
            marks = []
            if index == default_in:
                marks.append("system default")
            if str(device) == str(index):
                marks.append("SELECTED IN PIPEVOICE")
            suffix = f"   <- {', '.join(marks)}" if marks else ""
            print(f"  [{index}] {info['name']}{suffix}")

    print()
    print(f"config.device      = {cfg.device!r}")
    print(f"device passed to sd= {device!r}"
          f"{'  (system default)' if device is None else ''}")
    print()

    state = {"blocks": 0, "peak": 0.0, "hits": 0, "error": ""}
    detector = SnapDetector(16_000, sensitivity=float(cfg.bookmark_sensitivity))

    def callback(block, _frames, _time, status):
        try:
            state["blocks"] += 1
            peak = 0.0
            for value in block:
                sample = float(value[0] if getattr(value, "ndim", 0) else value)
                sample = sample if sample >= 0 else -sample
                if sample > peak:
                    peak = sample
            if peak > state["peak"]:
                state["peak"] = peak
            if detector.feed(block):
                state["hits"] += 1
                print(f"  ** double clap/snap detected at {state['blocks'] * 0.05:.1f}s")
        except Exception as exc:
            state["error"] = f"{type(exc).__name__}: {exc}"

    if "--scan" in sys.argv:
        return scan(sd, device)

    print("Opening the microphone for 8 seconds.")
    print("TALK, then CLAP TWICE, then clap twice again.")
    print("-" * 60)
    try:
        stream = sd.InputStream(samplerate=16_000, channels=1, dtype="float32",
                                blocksize=800, callback=callback, device=device)
        stream.start()
    except Exception as exc:
        print(f"FAIL  could not open the microphone: {type(exc).__name__}: {exc}")
        return 2

    for remaining in range(8, 0, -1):
        print(f"  {remaining}...  blocks={state['blocks']:<5} peak={state['peak']:.4f}")
        time.sleep(1.0)
    stream.stop()
    stream.close()

    print("-" * 60)
    if state["error"]:
        print(f"FAIL  the audio callback raised: {state['error']}")
        return 2
    if state["blocks"] == 0:
        print("FAIL  the stream opened but delivered ZERO audio blocks.")
        print("      Windows is not sending this process any audio. Check")
        print("      Settings > Privacy > Microphone, and whether another app")
        print("      holds the device exclusively.")
        return 2
    print(f"      {state['blocks']} blocks arrived, loudest {state['peak']:.4f}, "
          f"{state['hits']} double clap(s) detected")
    if state["peak"] < 0.002:
        print("FAIL  audio is arriving but it is silent — wrong device, or muted.")
        return 2
    if not state["hits"]:
        print("OK    the microphone works, but no double clap registered.")
        print()
        print("      The usual cause is Windows filtering the clap out before")
        print("      Pipevoice ever sees it. Audio enhancements — called Windows")
        print("      Studio Effects or Voice Focus on Copilot+ PCs — strip claps,")
        print("      snaps and keyboard noise BY DESIGN, and ship enabled on many")
        print("      laptops. Confirmed as the cause on a Snapdragon machine.")
        print("      Turn them off: Windows Sound settings > your microphone >")
        print("      Audio enhancements > Off, then run this again.")
        print()
        print("      If they are already off, raise Snap sensitivity and clap")
        print("      twice about 0.2s apart.")
        return 1
    print("PASS  microphone works and double claps are detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
