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
    # 0.05 not 0: a sub-frame duration yields an empty capture and made the old
    # frames-vs-expected maths divide by zero.
    if not math.isfinite(args.seconds) or args.seconds < 0.05:
        parser.error("--seconds must be a finite number >= 0.05")
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
    stop, results = threading.Event(), {}
    t_zero = time.monotonic()
    def capture_mic_sounddevice(label):
        """Mic via sounddevice/PortAudio, NOT soundcard.

        soundcard's _AudioClient asserts a device's mix format is float32
        WAVEFORMATEXTENSIBLE (mediafoundation.py:516-525) and cannot open
        anything else — its own comment says "the program crashes if these
        values are different". A mic held by a call app is switched to
        communications mode (commonly 16-bit PCM) and that assert fires with a
        bare AssertionError. PortAudio negotiates the format instead, and the
        app already records the mic this way, so we reuse the proven path and
        confine soundcard to loopback, which nothing else can do.
        """
        chunks, started, error, opened_at = [], None, None, None
        try:
            import sounddevice as sd
            print(f"  opening {label} (sounddevice)...", flush=True)
            def on_block(indata, _frames, _t, _status):
                chunks.append(indata.copy().reshape(-1))
            with sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                blocksize=800, callback=on_block):
                started = opened_at = time.monotonic()
                print(f"  {label} open after {opened_at - t_zero:.2f}s, recording",
                      flush=True)
                stop.wait(args.seconds)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            print(f"  {label} FAILED to open/record -> {error}", flush=True)
        ended = time.monotonic()
        results[label] = {
            "data": np.concatenate(chunks) if chunks else np.empty(0, np.float32),
            "duration": ended - started if started is not None else 0.0,
            "open_offset": (opened_at - t_zero) if opened_at is not None else None,
            "error": error,
        }
    def capture(label, device):
        # NO start/ready handshake. The previous version made each thread wait on
        # a barrier the main thread only lifted once BOTH recorders reported
        # ready within 10s; if a recorder's __enter__ was slow, the main thread
        # set stop and both threads exited having captured nothing, reporting a
        # bare "FAIL" with 0.000s and no reason. Each thread now simply records
        # for `--seconds` from the moment ITS OWN device opens, and we report the
        # open latency so a slow device is visible instead of fatal.
        #
        # no short-read counter: record() zero-pads and always returns numframes,
        # so it would be a constant 0. Skew vs wall-clock is the real signal.
        chunks, started, error, opened_at = [], None, None, None
        try:
            print(f"  opening {label}...", flush=True)
            with device.recorder(samplerate=RATE, channels=1) as recorder:
                started = opened_at = time.monotonic()
                print(f"  {label} open after {opened_at - t_zero:.2f}s, recording",
                      flush=True)
                end_at = started + args.seconds
                while not stop.is_set() and time.monotonic() < end_at:
                    remaining = end_at - time.monotonic()
                    requested = min(CHUNK, max(1, int(remaining * RATE)))
                    block = np.asarray(recorder.record(numframes=requested),
                                       dtype=np.float32).reshape(-1)
                    if len(block):
                        chunks.append(block)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            print(f"  {label} FAILED to open/record -> {error}", flush=True)
        ended = time.monotonic()
        results[label] = {
            "data": np.concatenate(chunks) if chunks else np.empty(0, np.float32),
            "duration": ended - started if started is not None else 0.0,
            "open_offset": (opened_at - t_zero) if opened_at is not None else None,
            "error": error,
        }
    threads = [
        threading.Thread(target=capture_mic_sounddevice, args=("mic",), daemon=True),
        threading.Thread(target=capture, args=("desktop", desktop_mic), daemon=True),
    ]
    print("\nOpening both capture streams...")
    for thread in threads:
        thread.start()
    interrupted = False
    try:
        # Threads self-terminate at their own deadline; just wait them out.
        while any(thread.is_alive() for thread in threads):
            elapsed = time.monotonic() - t_zero
            print(f"\r  capturing... {elapsed:4.1f}s elapsed ", end="", flush=True)
            stop.wait(0.25)
    except KeyboardInterrupt:
        interrupted = True
        stop.set()
        print("\nCtrl-C received; saving partial capture.")
    finally:
        for thread in threads:
            thread.join(timeout=max(5.0, args.seconds + 5))
        print()
    # Frames-vs-requested is a USELESS health metric here: soundcard's
    # _Recorder.record(numframes) (mediafoundation.py:781-820) ZERO-PADS when the
    # device supplies nothing and always returns exactly numframes. A starved or
    # dropping stream therefore reports 0 short reads and ~0% drift, with the
    # padding hiding as silence. The only honest signal is captured audio-seconds
    # (frames / RATE) against real elapsed wall-clock: if the device under-supplies,
    # the read loop races ahead of real time and skew goes negative.
    write_errors = []
    for label in ("mic", "desktop"):
        result = results.get(label, {})
        data = result.get("data", np.empty(0, np.float32))
        try:
            write_wav(outdir / f"{label}.wav", data, np)
        except OSError as exc:
            write_errors.append(f"{label}.wav: {exc}")
        frames = len(data)
        wall = float(result.get("duration", 0.0) or 0.0)
        audio_s = frames / RATE
        skew = audio_s - wall
        skew_pct = (skew / wall * 100) if wall > 0 else 0.0
        result["skew"] = skew
        peak = float(np.max(np.abs(data))) if frames else 0.0
        rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64)))) if frames else 0.0
        result.update(data=data, peak=peak, rms=rms)
        results[label] = result
        offset = result.get("open_offset")
        opened = f"opened +{offset:.2f}s" if offset is not None else "NEVER OPENED"
        print(f"{label:7}: {opened}, {frames} frames = {audio_s:.3f}s audio vs "
              f"{wall:.3f}s wall, skew {skew:+.3f}s ({skew_pct:+.2f}%), "
              f"peak {peak:.6f}, RMS {rms:.6f}")
        if result.get("error"):
            print(f"         capture error: {result['error']}")
    mic_ok = len(results["mic"]["data"]) > 0 and not results["mic"].get("error")
    desk_ok = len(results["desktop"]["data"]) > 0 and not results["desktop"].get("error")
    audible = results["desktop"]["rms"] > SILENCE_RMS
    # Relative skew between the two streams is what a timestamp-merge has to
    # survive; each stream's own skew is measured against the same wall clock.
    if mic_ok and desk_ok:
        rel = results["mic"]["skew"] - results["desktop"]["skew"]
        print(f"\nrelative mic-vs-desktop skew over {args.seconds:g}s: {rel:+.3f}s")
        # Deliberately NOT extrapolated to s/hour. A single run cannot tell a
        # fixed start/stop offset from a real drift rate, and presenting one as
        # the other is a fabricated metric: measured 2026-07-27, this value was
        # -0.072s at 20s and -0.060s at 120s. As a rate that would have implied
        # -13.0s/hr then -1.8s/hr; it is in fact a constant ~-0.065s offset with
        # no measurable drift. Run at two durations and compare.
        print("NOTE: this is one sample. Re-run with a very different --seconds:")
        print("  roughly unchanged -> fixed offset (correct it once at merge time)")
        print("  scales with duration -> real drift (merge needs re-anchoring)")
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
