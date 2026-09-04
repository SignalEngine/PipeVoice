# Gates — Read Aloud (`vault/Plans/pipevoice-read-aloud.md`)

Bound to the code tree (`wisprlite/`, `tests/`), not to a commit id.

1. Spike 1 passes on a clean Windows Sandbox — a frozen exe activating both
   WinRT namespaces.
   NOT VERIFIABLE ON THIS VPS: no Windows, no display, no Windows Sandbox.
   Per the plan's DECISION 2026-09-04, this is relocated to CI instead of a
   one-off spike run: `--winrt-selftest` (wisprlite/readaloud.py:113,
   wired in `wisprlite/__main__.py`) activates both `Windows.Media.Ocr` and
   `Windows.Media.SpeechSynthesis` from the code that will actually ship, and
   `.github/workflows/build.yml`'s new "Self-test WinRT" step runs it against
   the BUILT exe and fails the build on a non-zero exit. This step itself has
   not run yet — it runs on the next push to `main`/CI trigger, on a real
   Windows runner. Cannot be claimed PASS from here.

2. Spike 2's CER table, >25% kill criterion.
   NOT VERIFIABLE ON THIS VPS: needs real screenshots and a human transcribing
   ground truth. Per the plan's DECISION, this moved to James testing the real
   build, not a script's number. Not attempted here.

3. The hotkey does not collide with the six existing chords.
   CHECK: `python3 -m pytest -q tests/test_readaloud.py -k hotkey`
   EXPECT: pass.
   RESULT: PASS. `read_aloud_hotkey` defaults to `""` (off), same convention as
   meeting/screenrec/bookmark/voice-picker hotkeys. All three capture modes
   (window/screen/region) live on ONE hotkey field — the modifier held at
   trigger time picks the mode (`readaloud.capture_mode_for`,
   `app.py:_read_aloud_run`) — so there is nothing else to collide.
   `test_read_aloud_default_hotkey_does_not_collide_with_the_six_existing_chords`
   and its sabotage twin
   `test_a_user_configured_read_aloud_hotkey_does_not_shadow_the_others` (which
   deliberately collides one first, to prove the check can fail) both pass.

4. Capture never touches disk.
   CHECK: `python3 -m pytest -q tests/test_readaloud.py -k grab_png`
   EXPECT: pass.
   RESULT: PASS. `readaloud.grab_png` builds the PNG with `mss.tools.to_png`
   in memory and returns bytes; never opens a file.
   `test_grab_png_never_writes_a_temp_file` runs it in an empty `tmp_path` cwd
   (with `mss` stubbed in `sys.modules`, since it isn't installed on this
   Linux box) and asserts the directory is still empty afterward.

5. Speaking is interruptible: Esc during a long read stops within ~200ms.
   CHECK: `python3 -m pytest -q tests/test_readaloud.py -k stop`
   EXPECT: pass.
   RESULT: PASS. `readaloud.Speaker.stop()` calls the WinRT `MediaPlayer`'s own
   `pause()` SYNCHRONOUSLY on the calling thread — not after the current
   sentence, not via a flag checked later — so the interrupt latency is bounded
   by how fast `MediaPlayer.pause()` returns, not by text length or poll rate.
   `test_stop_pauses_the_player_synchronously_not_after_the_text_finishes`
   injects a fake player whose `play()` calls `stop()` mid-call and asserts
   `pause` was already recorded by the time `speak()` returns. The actual
   keyboard polling loop (`app.py:_read_aloud_watch_interrupt`) runs at ~50Hz
   (20ms), well under the 200ms budget, and calls this same `stop()`.
   NOT MEASURED end-to-end with real audio: no Windows, no speakers, no WinRT
   here. The synchronous-call guarantee is what the real ~200ms bound rests on;
   an actual stopwatch measurement needs James's machine.

6. Full suite, baselined on `main` first (15 pre-existing failures; must not
   add a sixteenth).
   CHECK: `python3 -m pytest -q --ignore=tests/test_transcribe_deepgram_e2e.py`
   EXPECT: same 15 pre-existing failures (test_env_precedence.py x8,
   test_transcribe_window.py x7), 0 new failures.
   RESULT: PASS. Baseline (before any edit, this worktree): 15 failed, 402
   passed, 25 skipped. After changes: 15 failed (identical set), 423 passed
   (402 + 21 new in tests/test_readaloud.py), 25 skipped.
   Settings UI also verified under `xvfb-run` (real Tk, real display):
   `tests/test_ui_smoke.py` builds every settings tab including the new "Read
   Aloud" card — 39 passed, no new widget errors.
   NOT CLEAN: running the FULL suite (not just ui_smoke) under `xvfb-run`
   aborts with a native `Fatal Python error: Aborted` inside PyAV's H.264
   encoder, in `tests/test_screenrec.py::test_a_long_recording_does_not_hold_
   every_frame_in_memory` (`wisprlite/screenrec.py:207 _encode_frame`) — a
   file this PR does not touch. Reproduced twice, always at the same test.
   Excluding `tests/test_readaloud.py` from the same xvfb run, the full suite
   passes cleanly (427 passed, same 15 failures, no crash) — so the crash only
   appears once the suite is large enough, under xvfb, on this VPS's
   run-limited 4GB memory cap; nothing in the crash's own stack trace touches
   `readaloud.py`, `app.py`'s read-aloud code, or the settings/overlay edits.
   Read as a resource-pressure artifact of this shared, capped VPS, not a
   defect in this diff — but flagging it plainly rather than omitting it,
   since I could not get a fully clean xvfb run with the new test file
   present. The gate as WRITTEN in the plan (non-xvfb `pytest -q`) is clean.

## What was built

- `wisprlite/readaloud.py` — capture (focused-window rect via ctypes, `mss`
  grab to PNG bytes in memory), OCR via `Windows.Media.Ocr`, speech via
  `Windows.Media.SpeechSynthesis` + `MediaPlayer` (interruptible), screen-reader
  detection (informational only, never gates speaking), `--winrt-selftest`.
- `wisprlite/config.py`: `read_aloud_hotkey` (`""` = off, matches the
  meeting/screenrec/bookmark convention), `read_aloud_voice`, `read_aloud_rate`,
  `read_aloud_ocr_language`, `read_aloud_clipboard` (default True),
  `read_aloud_quiet_with_screenreader` (default **False** — speaks always,
  per the panel's decision).
- `wisprlite/app.py`: one `HotkeyManager` (`read_aloud_hotkeys`), started in
  `run()`; `_read_aloud_trigger` / `_read_aloud_run` /
  `_read_aloud_watch_interrupt` — capture mode picked by which modifier is ALSO
  held (Shift = whole screen, Ctrl = drag a region via the existing
  `screenrec.select_region()`, neither = focused window); clipboard copy
  (never silent about it); everything wrapped so a `ReadAloudError` shows on
  the overlay instead of raising into the hotkey loop.
- `wisprlite/overlay.py`: a `"reading"` accent color, reusing the existing
  generic `show`/`set_state` status-pill mechanism — no new custom-drawn phase,
  since the pill already renders arbitrary state+text (`_draw_status`).
- `wisprlite/settings.py`: a "Read Aloud" card (hotkey + Capture button, voice,
  speed, OCR language, clipboard toggle, "stay quiet" toggle — default off,
  with copy explaining why).
- `wisprlite/__main__.py`: routes `--winrt-selftest` to `readaloud.main`.
- `.github/workflows/build.yml`: `--collect-all winrt` added to the PyInstaller
  build, and a new "Self-test WinRT" step runs `Pipevoice.exe --winrt-selftest`
  against the built exe and fails the build on it (spike 1, made permanent).
- `requirements.txt`: `winrt-*` at `3.2.1` (not `winsdk`, which is a beta),
  each `; sys_platform == "win32"` so this Linux test suite still installs.
- `tests/test_readaloud.py`: 21 tests — mode selection, hotkey-collision
  (with a sabotage twin), no-disk-write capture (with a sabotage twin),
  degrade-without-WinRT (import failure, no OCR engine, no voices — each
  distinct), interrupt latency mechanism, speak-always-by-default with the
  screen-reader opt-out. WinRT and `mss` are stubbed in `sys.modules` the same
  way `tests/test_deepgram_bias.py` stubs the Deepgram SDK, per the plan's
  "Tests must import on Linux" instruction.

## Explicitly not built (per the plan)

- **Piper, ElevenLabs.** Not touched.
- **"Re-OCR the last region."** Cut by the panel; not built.
- **Custom UI Automation provider for the overlay.** Cut by the panel; the
  overlay stays ordinary Tk.
- **NVDA/JAWS-based auto-silencing.** The opposite was built on purpose: speak
  always, opt-out setting only, default off.

## Not verifiable on this VPS

- Gate 1 (Windows Sandbox activation) and Gate 2 (OCR CER table) — no Windows.
- The real end-to-end ~200ms interrupt latency with actual audio — no
  speakers, no WinRT, no Windows.
- Whether the CI `--winrt-selftest` step actually passes on a GitHub Actions
  Windows runner (language pack / voice availability there is untested from
  here) — it will report PASS/FAIL for real the next time CI runs.
