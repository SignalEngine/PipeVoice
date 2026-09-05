# Gates — ElevenLabs key + voice preview (`vault/Plans/pipevoice-voice-keys-and-preview.md`)

Bound to the code tree (`wisprlite/`, `tests/`), not to a commit id.

1. `save_api_key` is called with `ELEVENLABS_API_KEY` when the field is filled;
   the single-reader test still passes.
   CHECK: `python3 -m pytest -q tests/test_tts_cloud.py::test_the_elevenlabs_key_has_exactly_one_reader`
          `xvfb-run -a python3 -m pytest -q tests/test_read_aloud_voice_keys.py::test_elevenlabs_key_is_saved_through_save_api_key`
   EXPECT: pass.
   RESULT: PASS. `wisprlite/settings.py:1951` calls
   `config.save_api_key("ELEVENLABS_API_KEY", eleven_key_var.get())` when the
   field is non-blank — the same pattern every other engine key already uses,
   and the only new occurrence of the string in the codebase (config.py's
   getter is the other one the single-reader test allows).

2. Switching the engine picker shows the matching rows and hides the others -
   drive the trace callback directly and assert `winfo_ismapped()`.
   CHECK: `xvfb-run -a python3 -m pytest -q tests/test_read_aloud_voice_keys.py::test_engine_picker_shows_only_the_matching_rows`
   EXPECT: pass.
   RESULT: PASS. `read_aloud_tts_var.trace_add("write", _on_read_aloud_tts)`
   (`wisprlite/settings.py:1243`) pack_forgets all three engine-specific
   groups and packs only the chosen one. Windows/Deepgram/ElevenLabs rows are
   hidden entirely (not greyed) when not selected.

3. Preview uses the FORM values, not the saved config.
   CHECK: `xvfb-run -a python3 -m pytest -q tests/test_read_aloud_voice_keys.py::test_preview_uses_the_unsaved_form_value_not_the_saved_config`
   EXPECT: pass.
   RESULT: PASS. `_run_preview` builds the snapshot from the live Tk vars
   (`wisprlite/settings.py:1248` `_preview_snapshot`) at click time, before
   the worker thread starts — changing the voice field without saving and
   clicking Preview builds the speaker with the unsaved value.

4. Preview failure shows the reason and does NOT fall back to Windows
   silently.
   CHECK: `xvfb-run -a python3 -m pytest -q tests/test_read_aloud_voice_keys.py::test_preview_failure_shows_the_reason_and_does_not_fall_back_silently`
   EXPECT: pass.
   RESULT: PASS. When `readaloud.build_speaker` returns a non-empty degrade
   reason, `_run_preview` shows it in the card and returns WITHOUT calling
   `speaker.speak()` — the one path (a real read) that intentionally falls
   back to the Windows voice is not taken here.

5. The preview button is disabled while running and re-enabled after a
   failure.
   CHECK: `xvfb-run -a python3 -m pytest -q tests/test_read_aloud_voice_keys.py::test_preview_button_disabled_while_running_and_reenabled_after_failure`
   EXPECT: pass.
   RESULT: PASS. Disabled synchronously before the worker thread starts;
   re-enabled in a `finally` regardless of success/failure/early-return.
   NOTE ON TEST METHOD: the worker thread's `root.after(...)` call requires
   the interpreter to be genuinely inside `mainloop()` to be delivered from a
   different thread — a bare `root.update()` polling loop makes this
   deterministically raise "main thread is not in main loop" in this harness
   (unrelated to this change; the pre-existing `_cache_size` background
   thread in Settings hits the identical error under the same polling style,
   visible as a warning in every settings test run). The new test runs a real
   `root.mainloop()` with an `after`-scheduled quit instead, which is also
   the closer match to how the shipped app actually runs (blocked in
   `mainloop()` the whole time a preview plays).

6. Full suite under xvfb, baseline `main` first: 15 pre-existing failures, do
   not add a sixteenth.
   CHECK: `python3 -m pytest -q --ignore=tests/test_transcribe_deepgram_e2e.py`
          `xvfb-run -a python3 -m pytest -q --ignore=tests/test_transcribe_deepgram_e2e.py --ignore=tests/test_screenrec.py`
   EXPECT: same 15 pre-existing failures (test_env_precedence.py x8,
   test_transcribe_window.py x7), 0 new failures.
   RESULT: PASS. Non-xvfb: 15 failed (identical set), 480 passed, 32 skipped.
   Xvfb (excluding `test_screenrec.py`, same PyAV/xvfb crash on this VPS
   documented in the prior Read Aloud PR's gates, unrelated to this diff):
   15 failed (identical set), 461 passed, 0 new failures. Did not re-attempt
   including `test_screenrec.py` in the same xvfb run — already known
   resource-pressure crash on this box, not reproduced by anything touched
   here.

## What was built

- `wisprlite/settings.py`: an "ElevenLabs API key" field in the Read Aloud
  card, saved through the existing `save_api_key`. The card now shows only
  the rows for the chosen voice engine (Windows voice picker + "get better
  voices" link / Deepgram voice list + "already configured" note / ElevenLabs
  key + voice ID + a link to find it), toggled by a `trace_add("write", ...)`
  on the engine picker — the same pattern `_on_cleanup_provider` already used
  for reacting to a picker change, extended to actually hide/show rows. A
  "Preview" button next to the engine picker speaks one fixed sentence
  through `readaloud.build_speaker` built from the unsaved form values, off
  the Tk thread, disabled while running and re-enabled in a `finally`; a
  degrade reason from `build_speaker` is shown in the card and does not
  trigger the Windows fallback playback.
- `tests/test_read_aloud_voice_keys.py`: 5 new tests, one per gate above.

## Explicitly not built (per the plan)

- Fetching the ElevenLabs voice catalogue over the API to build a picklist.
  Per-account and needs a valid key to enumerate — the ID field plus a link
  is what the plan asked for.

## Not verifiable on this VPS

- The real ElevenLabs/Deepgram network round-trip during a preview (no
  network keys configured here; `build_speaker` is exercised directly by the
  readaloud/tts_cloud unit tests, and the settings-side wiring is exercised
  with a faked `build_speaker`).
- Real Windows voice enumeration/playback — no Windows, no WinRT, no
  speakers on this box, same limitation as the prior Read Aloud PR.
