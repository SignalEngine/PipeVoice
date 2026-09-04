# Gates — audio quality + mic picker

Bound to the code tree (`wisprlite/`, `tests/`), not to a commit id. Committed
last, after every gate below is green.

1. `grep -n "16_000\|16000" wisprlite/screenrec.py wisprlite/meeting.py` returns
   nothing.
   CHECK: `grep -n "16_000\|16000" wisprlite/screenrec.py wisprlite/meeting.py`
   EXPECT: no output, exit 1 (grep found nothing).

2. A recorded `.mp4` reports 48000 Hz from ffprobe and decodes to non-silence.
   Not runnable on this VPS (no mic, no ffprobe fixture pipeline) — covered
   instead by a unit test on `_mux`/`_open_container` asserting `AUDIO_RATE ==
   48_000` is what gets passed to `av.add_stream`/`AudioFrame.sample_rate`.
   Positive control: assert the same assertion fails against a fixture pinned
   to 16_000.
   CHECK: `python3 -m pytest -q tests/test_screenrec.py -k rate`
   EXPECT: pass; reverting `AUDIO_RATE` to `16_000` turns it red.

3. `focus_stream` receives the same rate the meeting writes.
   CHECK: `python3 -m pytest -q tests/test_focus.py -k sample_rate`
   EXPECT: pass; reverting the `sample_rate` param (hardcoding 16_000 again)
   turns it red.

4. Normalisation: a quiet sine is boosted towards -1 dBFS (capped at +18 dB
   gain so a near-silent take isn't blown into hiss), an already-hot sine is
   untouched, and digital silence is untouched (no div-by-zero, no runaway
   gain).
   CHECK: `python3 -m pytest -q tests/test_loudness.py`
   EXPECT: pass; reverting `normalize_peak` to a no-op turns it red.

5. `group_inputs` collapses a fixture with the same mic under four host APIs
   to ONE entry and picks the WASAPI endpoint.
   CHECK: `python3 -m pytest -q tests/test_mics.py -k group`
   EXPECT: pass; removing the host-API preference order turns it red.

6. `recommend` never returns a Stereo Mix endpoint when a real mic is present,
   and returns `None` on an empty list rather than raising.
   CHECK: `python3 -m pytest -q tests/test_mics.py -k recommend`
   EXPECT: pass.

7. `verdict()` returns each of the five strings for a hand-built measurement
   dict.
   CHECK: `python3 -m pytest -q tests/test_mics.py -k verdict`
   EXPECT: pass.

8. Full suite does not add a 16th failure beyond the baseline.
   CHECK: `python3 -m pytest -q --ignore=tests/test_transcribe_deepgram_e2e.py`
   EXPECT: same 15 pre-existing failures (test_env_precedence.py x8,
   test_transcribe_window.py x7), 0 new failures, new tests from this plan
   passing.

## Not verifiable on this VPS

No audio device, no Windows. How the recording actually *sounds*, and how the
real Windows device list groups on James's machine, are handed to him
explicitly rather than claimed here.
