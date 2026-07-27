"""Diagnose simultaneous Windows microphone and desktop-loopback capture.

A PASS proves that SoundCard opened the default microphone and default
speaker's WASAPI loopback together, captured both, and observed non-silent
desktop audio. It does not prove transcription quality, hotkey integration,
long-session stability, or compatibility with non-default devices.
"""

import argparse
import math
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
RATE = 16_000
CHUNK = 1_600
SILENCE_RMS = 1e-5
# DO NOT TOUCH device.name ANYWHERE IN THIS FILE.
#
# soundcard 0.4.6's `.name` is not cached: every access runs a full
# CoCreateInstance + release of an IMMDeviceEnumerator and then dereferences a
# device pointer obtained from the enumerator it just released
# (mediafoundation.py:352-355, :366). Repeating that corrupts the process heap
# — observed 2026-07-27 as `Windows fatal exception: code 0xc0000374`
# (STATUS_HEAP_CORRUPTION) while building a name lookup over the mic list.
#
# `.id` is a plain attribute (no COM), so we identify devices by id only.
# Note this also rules out sc.get_microphone(): _match_device builds
# {device.name: device} unconditionally BEFORE its id check, so it always
# walks the poisoned path even when you pass an exact id.
def describe(device):
    return f"id={getattr(device, 'id', getattr(device, '_id', '?'))}"
def show_devices(title, devices):
    print(f"\n{title} ({len(devices)}):")
    for index, device in enumerate(devices, 1):
        print(f"  {index}. {describe(device)}")
    if not devices:
        print("  (none)")
def write_wav(path, data, np):
    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        output.writeframes(pcm.tobytes())
def main():
    parser = argparse.ArgumentParser(
        description="Test simultaneous default-mic and WASAPI loopback capture."
    )
    parser.add_argument("--seconds", type=float, default=20,
                        help="capture duration (default: 20)")
    parser.add_argument("--outdir", type=Path,
                        help="output directory (default: timestamp under Music or cwd)")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be a positive finite number")
    try:
        import soundcard as sc
    except ModuleNotFoundError as exc:
        if exc.name == "soundcard":
            print("ERROR: The optional 'soundcard' package is not installed.")
            print("Install it with: pip install soundcard")
        else:
            print(f"ERROR: SoundCard could not load because '{exc.name}' is missing.")
            print("Try reinstalling it with: pip install soundcard")
        return 2
    except Exception as exc:
        print(f"ERROR: SoundCard could not be loaded: {exc}")
        print("This diagnostic requires Windows WASAPI and soundcard.")
        return 2
    try:
        import numpy as np
        speakers = list(sc.all_speakers())
        microphones = list(sc.all_microphones())
        all_mics = list(sc.all_microphones(include_loopback=True))
    except Exception as exc:
        print(f"ERROR: Audio device enumeration failed: {exc}")
        print("Run this diagnostic on the target Windows machine.")
        return 2
    # id-only identity; see the warning above about .name
    loopbacks = [device for device in all_mics
                 if getattr(device, "isloopback", False)]
    show_devices("Speakers", speakers)
    show_devices("Microphones", microphones)
    show_devices("Loopback microphones", loopbacks)
    if not loopbacks:
        print("\n========== VERDICT ==========")
        print("FAIL: No WASAPI loopback microphone was found; capture was not attempted.")
        print("=============================")
        return 1
    try:
        # default_microphone()/default_speaker() are safe: they wrap one
        # enumerator call and never read .name.
        default_mic = sc.default_microphone()
        speaker = sc.default_speaker()
        if default_mic is None or speaker is None:
            raise RuntimeError("Windows has no default microphone or speaker")
        # Resolve the loopback by ID rather than sc.get_microphone(id=speaker.name),
        # which would walk every device's .name and corrupt the heap.
        desktop_mic = next((m for m in loopbacks if m.id == speaker.id), None)
        if desktop_mic is None:
            # Default speaker has no loopback twin — fall back to the first one.
            desktop_mic = loopbacks[0]
            print("\nNOTE: no loopback matches the default speaker id; "
                  "using the first loopback endpoint instead.")
    except Exception as exc:
        print(f"\nERROR: Could not resolve the default capture devices: {exc}")
        return 1
    print(f"\nDefault microphone: {describe(default_mic)}")
    print(f"Default speaker:    {describe(speaker)}")
    print(f"Desktop loopback:   {describe(desktop_mic)}")
    stamp = datetime.now().strftime("loopback-spike-%Y%m%d-%H%M%S")
    music = Path.home() / "Music"
    outdir = args.outdir or ((music if music.is_dir() else Path.cwd()) / stamp)
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"ERROR: Could not create output directory '{outdir}': {exc}")
        return 1
    stop, start = threading.Event(), threading.Event()
    deadline, results = [0.0], {}
    def capture(label, device):
        chunks, short_reads, started, error = [], 0, None, None
        try:
            with device.recorder(samplerate=RATE, channels=1) as recorder:
                results[label] = {"ready": True}
                start.wait()
                started = time.monotonic()
                while not stop.is_set():
                    remaining = deadline[0] - time.monotonic()
                    if remaining <= 0:
                        break
                    requested = min(CHUNK, max(1, int(remaining * RATE)))
                    block = np.asarray(recorder.record(numframes=requested),
                                       dtype=np.float32).reshape(-1)
                    short_reads += len(block) < requested
                    if len(block):
                        chunks.append(block)
        except Exception as exc:
            error = str(exc)
            stop.set()
        ended = time.monotonic()
        results[label] = {
            "data": np.concatenate(chunks) if chunks else np.empty(0, np.float32),
            "duration": ended - started if started is not None else 0.0,
            "short_reads": short_reads, "error": error,
        }
    threads = [
        threading.Thread(target=capture, args=("mic", default_mic), daemon=True),
        threading.Thread(target=capture, args=("desktop", desktop_mic), daemon=True),
    ]
    print("\nOpening both capture streams...")
    for thread in threads:
        thread.start()
    interrupted = False
    try:
        ready_until = time.monotonic() + 10
        while not all(results.get(name, {}).get("ready")
                      for name in ("mic", "desktop")):
            if stop.is_set() or time.monotonic() >= ready_until:
                break
            time.sleep(0.05)
        if not all(results.get(name, {}).get("ready")
                   for name in ("mic", "desktop")):
            stop.set()
        deadline[0] = time.monotonic() + args.seconds
        start.set()
        while any(thread.is_alive() for thread in threads) and not stop.is_set():
            remaining = max(0.0, deadline[0] - time.monotonic())
            print(f"\rCapturing... {remaining:4.1f}s remaining", end="", flush=True)
            if remaining <= 0:
                stop.set()
            else:
                stop.wait(min(0.25, remaining))
    except KeyboardInterrupt:
        interrupted = True
        stop.set()
        print("\nCtrl-C received; saving partial capture.")
    finally:
        start.set()
        for thread in threads:
            thread.join(timeout=2)
        print()
    expected, write_errors = round(args.seconds * RATE), []
    for label in ("mic", "desktop"):
        result = results.get(label, {})
        data = result.get("data", np.empty(0, np.float32))
        try:
            write_wav(outdir / f"{label}.wav", data, np)
        except OSError as exc:
            write_errors.append(f"{label}.wav: {exc}")
        frames = len(data)
        drift = (frames - expected) / expected * 100
        peak = float(np.max(np.abs(data))) if frames else 0.0
        rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64)))) if frames else 0.0
        result.update(data=data, peak=peak, rms=rms)
        results[label] = result
        print(f"{label:7}: {frames} frames, {result.get('duration', 0):.2f}s wall, "
              f"expected {expected}, drift {drift:+.3f}%, peak {peak:.6f}, "
              f"RMS {rms:.6f}, short reads {result.get('short_reads', 0)}")
        if result.get("error"):
            print(f"         capture error: {result['error']}")
    mic_ok = len(results["mic"]["data"]) > 0 and not results["mic"].get("error")
    desk_ok = len(results["desktop"]["data"]) > 0 and not results["desktop"].get("error")
    audible = results["desktop"]["rms"] > SILENCE_RMS
    passed = mic_ok and desk_ok and audible and not interrupted and not write_errors
    print(f"\nWAV output: {outdir.resolve()}")
    print("\n========== VERDICT ==========")
    if passed:
        print("PASS: Simultaneous microphone and non-silent desktop capture succeeded.")
    elif interrupted:
        print("INCOMPLETE: Capture was interrupted; partial WAV files were saved.")
    elif not mic_ok or not desk_ok:
        print("FAIL: One or both streams did not capture usable samples.")
    elif not audible:
        print("NO DESKTOP AUDIO: The desktop stream was silent.")
        print("Play audio during the test and re-run. This is a test-operator error,")
        print("not evidence of a hardware failure.")
    else:
        print("FAIL: WAV output could not be written.")
    for error in write_errors:
        print(f"WAV write error: {error}")
    print("=============================")
    return 0 if passed else (130 if interrupted else 1)
if __name__ == "__main__":
    sys.exit(main())
