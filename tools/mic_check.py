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
        print("      Raise Snap sensitivity and clap harder, twice, ~0.2s apart.")
        return 1
    print("PASS  microphone works and double claps are detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
