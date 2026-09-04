# Audio pipeline

Three capture paths, two sample rates, one normalisation stage. Getting these
confused is how the "recordings sound muffled" bug happened.

## The rates are different on purpose

| Path | Module | Rate | Why |
|---|---|---|---|
| Dictation (push-to-talk) | `audio.py`, `engines/{deepgram,openai,gemini}_engine.py` | **16 kHz** | Never played back. Goes straight to a speech API, which wants 16 kHz. |
| Screen recording | `screenrec.py` (`AUDIO_RATE`) | **48 kHz** | The `.mp4` is a file a human watches. |
| Meeting capture | `meeting.py` (`SAMPLE_RATE`) | **48 kHz** | `mic.wav` / `desktop.wav` get played back. |

**Do not unify them.** 16 kHz hard-cuts everything above 8 kHz — the sibilance
and "air" that make a voice sound present. That is exactly what made recordings
sound like a phone call before v2.41.0. Raising dictation to 48 kHz would waste
bandwidth on every utterance for a listener that does not exist.

### Transcription needs no resampling
`transcribe_file` (faster-whisper via bundled PyAV) and `transcribe_file_deepgram`
both take a **file path** and resample internally. This is why the 48 kHz move
was a small change.

### The one coupling that bites
PipeFocus streams **live PCM**, so it must be told the rate:
`focus_stream(cfg, on_text, sample_rate=...)` in `engines/deepgram_engine.py`,
filled by `app.py` from `meeting.SAMPLE_RATE`. If those disagree, Deepgram
decodes at the wrong speed and every PipeFocus transcript is garbage — not
merely worse-sounding. Pinned by a test in `tests/test_focus.py`.

## Normalisation — `loudness.py`

Peak normalisation at **finalise**, never in an audio callback (an audio
callback that touches disk or blocks causes PortAudio `input overflow`).

- Boost towards **-1 dBFS**, only when the peak is below **-3 dBFS**.
- Gain capped at **8x / +18 dB** so a near-silent take is not amplified into hiss.
- Monotonic gain only. No compression, EQ or noise suppression — so it cannot
  hurt transcription accuracy.

Call sites: `screenrec._mux()` and `MeetingRecorder.stop()`. **Both are guarded
by call-site tests**, because deleting either call once left the entire 349-test
suite green — the maths being right proves nothing about it being wired.

## Microphone selection — `mics.py`

PortAudio enumerates every device once per Windows host API, so one physical mic
appears as MME, DirectSound, WASAPI and WDM-KS entries. The old picker dumped all
of them, unranked.

- `group_inputs()` collapses duplicates on the device name, **truncated to 30 raw
  chars before punctuation is stripped** — MME truncates names to 31 chars, so the
  key must be a prefix of every host API's rendering.
- Endpoint preference: `WASAPI > WDM-KS > DirectSound > MME`.
- `recommend()` prefers the Windows default input, then sample rate, then index.
  **Not channel count** — a stereo endpoint is not a better microphone; webcams
  and headsets report both.
- Loopback/virtual endpoints (`stereo mix`, `cable output`, `voicemeeter out`, …)
  are selectable but never recommended.
- `measure()` / `verdict()` are pure functions over a numpy buffer, so "Test my
  mic" is testable without a sound card. Recording runs on a **worker thread** —
  on the UI thread it froze the window for 3s, or 1.5s per device on "Test all".

### Grade on `speech_dbfs`, never on whole-buffer RMS
v2.41.0 shipped a verdict that judged on the RMS of the entire 3-second capture,
which averages in **every pause between words**. Someone talking for a fifth of
the window reads ~7 dB below their actual speaking level, so a perfectly good
microphone was graded on how much the user happened to pause — and it reported
"Too quiet" to James on his first try. `measure()` now also returns
`speech_dbfs`, the mean of the loudest quarter of 20 ms frames, and `verdict()`
uses that (thresholds -45 / -32, not -40 / -30 on the average).

The regression test is built so the two grading methods **disagree** on the
fixture; a first attempt at it did not discriminate and stayed green when the
fix was reverted.

### Every device gets its own try
Windows always enumerates endpoints that will not open — in use by another app,
disconnected, or refusing mono float32. "Test all" originally wrapped the whole
loop in one `try`, so the first such device aborted the run with "Test failed"
and every microphone after it went untested.

### A verdict must carry its remedy
"Too quiet" alone is a diagnosis with no treatment. The dialog states the gap
and direction ("Aim for -20 dBFS — about 12 dB louder than this") and offers a
button to the Windows Recording tab, where the level slider actually lives.

## Failure modes seen in the wild

Read from a real 2,453-line `pipevoice.log`, 20 Jun – 4 Sep 2026:

| Symptom | Cause | Fixed |
|---|---|---|
| Output quietly worse than usual | AI polish failed 235× (dead Gemini model id, then `PERMISSION_DENIED`) and returned raw text with only a log line | v2.40.6 — the overlay now names the reason |
| 59 × Deepgram `1011` ERROR pairs, 5% of dictations | A tap under `min_seconds` opened a websocket on key-down and abandoned it; the server killed it 10s later | v2.40.6 — `_finish` cancels the session it opened |
| Recordings sound muffled | 16 kHz capture on paths people listen to | v2.41.0 |
| Quiet mic → quiet video | No gain stage anywhere in the capture path | v2.41.0 |
| "Which of these 12 mics is good?" | Raw PortAudio dump | v2.41.0 |
| "It kept saying it was too quiet" | Verdict graded on whole-buffer RMS, so pauses counted as quietness | v2.41.1 |
| "Test all came up with an error" | One `try` around the whole loop; first unopenable device killed the run | v2.41.1 |

**Not verified on the VPS:** there is no audio device and no Windows here. How a
recording *sounds*, and how a real device list groups, can only be confirmed on
James's machine.

## Finishing a screen recording

The order matters, and it was wrong until v2.42.0.

`_finish_screen_recording` ran **mux → name → transcribe → send → show buttons**
in sequence. Whisper over a two-minute clip sat in the middle, so the first
button appeared minutes after the recording that produced it.

Now: the pill reaches `done` as soon as the file is muxed and named, and
`_hand_over_then_transcribe` does the rest on a worker — video sent first and
alone, then transcribe, then the transcript. **An agent still waits**, because
it is blocked on the text it asked for; only the human path is async.

Three rules that came out of getting this wrong:
- **Delete last.** The Play and Open buttons point at the local file, so
  `screenrec_keep_local=False` must not remove it until everything that was
  going to be sent has been.
- **Gate the cleanup on the SEND, not on the note.** It was gated on `not note`,
  and "no transcript" sets a note — so a clip with no narration was never
  tidied up, however the setting was configured.
- **A `finally` that writes the closing message must know whether the message
  was already written.** It overwrote a send failure with "Recording saved and
  sent": a failed upload reported as a success.

`Path(None).unlink()` raises `TypeError`, which `except OSError` does not catch,
so a `None` transcript path aborted the delete loop partway.

## Updating

The installer relaunches with `/RESTARTAPPLICATIONS`, which restores the app
exactly as it was — a tray icon, nothing open. So an update the user asked for
used to finish with no visible sign at all.

`updater.mark_pending()` writes a marker **before** the installer is spawned
(after would never run: the installer force-closes this process), and
`take_pending()` consumes it on the next start, which opens Settings on About.

- Gated on the marker, never on the version alone — a hand reinstall or a
  restored backup also changes the version, and a tray app that starts at boot
  must not open windows uninvited.
- The marker is always consumed, including when the install failed. A marker
  that cannot be deleted is emptied; if neither works, stay silent. Returning
  "yes we updated" on a marker you cannot clear turns a one-shot window into a
  boot pop-up nobody can switch off.
- Markers older than 24h are ignored rather than ambushing someone days later.
