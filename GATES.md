# Gates — Read Aloud long text (`vault/Plans/pipevoice-read-aloud-long-text.md`)

Bound to the code tree (`wisprlite/`, `tests/`), not to a commit id.

1. A 5,000-character text produces multiple chunks, none over the engine
   limit, and concatenating them returns the original words in order.
   CHECK: `python3 -m pytest -q tests/test_readaloud.py -k "long_text or chunks_concatenate"`
   EXPECT: pass.
   RESULT: PASS. `readaloud.split_into_chunks(text, limit)`
   (`wisprlite/readaloud.py`) splits on sentence boundaries first, packs
   sentences greedily under the limit, and `" ".join(chunks)` reproduces the
   whitespace-normalized original exactly — verified directly for a
   400-sentence (~15,000-char) text against `DEEPGRAM_MAX_CHARS` (1900).

2. A single 3,000-character sentence still produces speakable chunks.
   CHECK: `python3 -m pytest -q tests/test_readaloud.py::test_a_single_sentence_over_the_limit_still_produces_speakable_chunks tests/test_readaloud.py::test_a_single_word_longer_than_the_limit_is_hard_cut_not_dropped`
   EXPECT: pass.
   RESULT: PASS. `_split_long_piece` falls back to clause punctuation
   (`,;:`), then whitespace, then a hard per-character cut for a single
   word/clause still over the limit — every level is exercised, never
   dropping a chunk.

3. Stopping during chunk 1 of 4 never starts chunk 2.
   CHECK: `python3 -m pytest -q tests/test_readaloud.py::test_stopping_during_chunk_one_of_four_never_starts_chunk_two`
   EXPECT: pass.
   RESULT: PASS. `Speaker.speak_chunks` (`wisprlite/readaloud.py`) checks the
   same `_stopped` latch `speak()` already uses, both before building each
   chunk's player and again before assigning it as the live player — a stop
   mid-chunk-1 returns before chunk 2's `player_builder` is ever called.

4. The duration estimate appears above ~1,000 characters and not below.
   CHECK: `python3 -m pytest -q tests/test_readaloud.py::test_duration_estimate_is_silent_below_1000_characters tests/test_readaloud.py::test_duration_estimate_appears_above_1000_characters`
   EXPECT: pass.
   RESULT: PASS. `readaloud.duration_estimate(n)` returns `""` at/under 1,000
   chars, else `"Reading {n:,} characters — about {n/5/150} min"`. Wired into
   `App._ask_read_mode_in_pill`, shown on the Read all/Summarise pill before
   speech starts.

5. Choosing Summarise calls `cleanup.clean()` once and speaks its output; a
   failure speaks the FULL text and shows a reason.
   CHECK: `python3 -m pytest -q tests/test_readaloud.py -k summaris`
   EXPECT: pass.
   RESULT: PASS. `App._read_aloud_speak` (`wisprlite/app.py`) calls
   `cleanup.clean(text, ..., style="summarise")` exactly once when
   `mode == "summarise"`; on a falsy return it uses `cleanup.last_error()` as
   the reason, keeps `spoken_text = text` (the ORIGINAL, never the summary),
   and prefixes the pill text with `"Summarise failed (<reason>) — reading
   full text."`. Read all never calls `clean()` at all.

6. The pill states which mode was read.
   CHECK: `python3 -m pytest -q tests/test_readaloud.py::test_the_pill_states_which_mode_was_read`
   EXPECT: pass.
   RESULT: PASS. The "reading" pill text is prefixed `"Read all: "` or
   `"Summary: "` (or the failure note above, which already says it fell back
   to the full text).

7. Full suite under xvfb, baseline `main` first: 15 pre-existing failures, do
   not add a sixteenth.
   CHECK: `python3 -m pytest -q --ignore=tests/test_transcribe_deepgram_e2e.py`
          `xvfb-run -a python3 -m pytest -q --ignore=tests/test_transcribe_deepgram_e2e.py --ignore=tests/test_screenrec.py`
   EXPECT: same 15 pre-existing failures (test_env_precedence.py x8,
   test_transcribe_window.py x7), 0 new failures.
   RESULT: PASS. Non-xvfb: 15 failed (identical set), 508 passed, 32 skipped.
   Xvfb (excluding `test_screenrec.py` — same PyAV/xvfb crash on this VPS
   documented in the prior Read Aloud PRs' gates, unrelated to this diff):
   15 failed (identical set), 489 passed, 0 new failures.

## What was built

- `wisprlite/tts_cloud.py`: `DEEPGRAM_MAX_CHARS = 1900`,
  `ELEVENLABS_MAX_CHARS = 5000` — per-engine caps, not one shared guess.
- `wisprlite/readaloud.py`: `split_into_chunks(text, limit)` (sentence
  boundaries first, clause punctuation then whitespace then a hard cut for a
  single piece still over the limit, `None` limit = one chunk for the
  uncapped Windows voice); `duration_estimate(n)`; `Speaker.speak_chunks`
  (speaks an ordered list of pieces through one Speaker so Stop/Pause govern
  the whole read); `build_chunked_speaker(text, cfg)` — the chunked sibling
  of `build_speaker` (which is untouched, still used by the Settings preview's
  one fixed sentence), synthesizing the first chunk eagerly to catch a dead
  key/no-network before anything plays and falling back the WHOLE read to the
  Windows voice on that failure, exactly like `build_speaker` already does.
- `wisprlite/cleanup.py`: a `"summarise"` fixed style (`_SUMMARISE` prompt),
  reusing the existing `clean()`/provider/failure machinery — no second LLM
  path.
- `wisprlite/config.py`: `read_aloud_last_mode` (`"read_all"` default) —
  remembered and highlighted, never overriding the Enter/hotkey fast path.
- `wisprlite/overlay.py`: a new `ra_choice` pill state — caption + duration
  estimate, two buttons (`ra_read_all` / `ra_summarise`), the remembered
  choice highlighted — drawn/hit-tested the same way the existing `reading`
  pill is (`_draw_ra_choice`, `_ra_choice_hit`, wired into `_pill_click` and
  the draw/resize dispatch).
- `wisprlite/app.py`: `_ask_read_mode_in_pill` shows the choice pill and waits
  (15s timeout, falls back to `read_all`) via a `threading.Event`;
  `_ra_choice_watch_hotkey` resolves to `read_all` the instant Enter or the
  read-aloud hotkey is pressed again, regardless of the remembered default;
  `_ra_choice_answer` (also reached from the two new pill buttons via
  `_screenrec_action`) sets the result, remembers it in config, and unblocks
  the wait; `_read_aloud_speak` now takes a `mode` and speaks through
  `build_chunked_speaker`/`speak_chunks` instead of one whole-text
  `speaker.speak()` call; `_read_aloud_restart` replays the SAME mode
  (never re-asks).
- `tests/test_readaloud.py`: chunking (gates 1-2), `speak_chunks` stop
  behaviour (gate 3), `duration_estimate` (gate 4), the Read all/Summarise
  choice and its pill text (gates 5-6), plus `build_chunked_speaker`'s
  tier-dispatch/fallback/single-fetch-per-chunk behaviour.
- Updated `tests/test_readaloud.py::test_ra_restart_speaks_again_without_re_running_ocr`
  for `_read_aloud_speak`'s new second argument (the mode restart replays).

## Explicitly not built (per the plan)

- Any OCR change — blocked on James pasting the clipboard capture to confirm
  whether OCR itself is also dropping lines. Nothing here touches
  `readaloud.ocr_png`.
- Streaming synthesis — chunking already fixes the hard failure; the plan
  calls streaming a latency optimization to revisit only with evidence the
  pause between chunks matters.

## Not verifiable on this VPS

- Real Deepgram/ElevenLabs network calls during an actual long read (no
  network keys configured here) — `build_chunked_speaker` and `speak_chunks`
  are exercised with the HTTP layer and WinRT player mocked, matching how
  `build_speaker` was already tested before this plan.
- Real Windows voice playback, the actual pill rendering pixels, and real
  keyboard input for the Enter/hotkey fast path (no Windows, no WinRT, no
  physical keyboard on this box) — the hit-test/dispatch/state-machine logic
  is covered directly; `keyboard.is_pressed`/`_all_pressed` themselves are
  exercised elsewhere in this suite, not here.
