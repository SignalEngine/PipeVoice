# Gates — vocabulary you control, and output that fits the app

Bound to the code tree (`wisprlite/`, `tests/`), not to a commit id. Committed
last, after every gate below is checked.

1. Existing `cfg.replacements` from a pre-upgrade config load unchanged into the
   new list editor, and still apply.
   CHECK: `python3 -m pytest -q tests/test_word_fixes.py`
   EXPECT: pass. `test_sabotage_broken_round_trip_would_fail` is the positive
   control — it deliberately corrupts a round-tripped value and asserts the
   corruption is detectable.
   RESULT: PASS (5 tests). Storage format (`cfg.replacements`, a plain dict) was
   never touched — only the editor widget changed from a comma-string Entry to
   a two-column Listbox — so no config migration exists or is needed.

2. The three new presets appear in `STYLES` and each produces materially
   different output from `tidy` on the same input. Sabotage: point two presets
   at the same prompt and confirm the test fails.
   CHECK: `python3 -m pytest -q tests/test_cleanup_styles.py`
   EXPECT: pass.
   RESULT: PASS (9 tests, incl.
   `test_new_presets_are_materially_different_from_tidy_and_each_other` and its
   sabotage control). `email`/`code_comment`/`meeting_actions` added to
   `STYLES` in both `settings.py` and `voices_editor.py` (voices_editor.py:19).

3. A per-app profile setting `cleanup_style=email` overrides the global style
   for that app only. Sabotage: remove the override and confirm red.
   CHECK: `python3 -m pytest -q tests/test_profiles_style.py`
   EXPECT: pass.
   RESULT: PASS (6 tests). No code change was needed here — `profiles.resolve()`
   already dispatches through a named Voice (`voices.py`), and Voices carry
   `cleanup_style` generically, so any new style value is a free per-app
   override the moment it exists in `STYLES`. Added
   `test_per_app_profile_overrides_cleanup_style_for_that_app_only` and its
   sabotage twin to make that explicit for `email` specifically.

4. Ollama latency benchmark recorded as a number in the PR, not a claim.
   RESULT: NOT MEASURED. Ollama is not installed or running on this VPS
   (`curl localhost:11434/api/tags` fails to connect, no `ollama` binary).
   Installing a local-LLM daemon plus pulling a model on a machine already
   shared by a dozen sessions (CLAUDE.md's swap-thrashing warning) was not
   attempted without explicit authorization. No number is asserted here —
   fabricating one would be worse than leaving it open. This build does not
   default any new preset to Ollama or to any provider other than the user's
   existing `cleanup_provider` (default `gemini`), so the "must not default to
   local if slow" constraint is not at risk from anything shipped in this PR;
   the benchmark still needs to be run before anyone proposes such a default.
   NEEDS: James's machine, or explicit go-ahead to install Ollama here.

5. Full suite: `python3 -m pytest -q --ignore=tests/test_transcribe_deepgram_e2e.py`.
   Baseline on `main` first — it currently carries 15 pre-existing failures
   and the run must not add a sixteenth.
   CHECK: `python3 -m pytest -q --ignore=tests/test_transcribe_deepgram_e2e.py`
   EXPECT: same 15 pre-existing failures (test_env_precedence.py x8,
   test_transcribe_window.py x7), 0 new failures.
   RESULT: PASS. Baseline (before any edit): 15 failed, 372 passed, 24 skipped
   (no display). After changes: 15 failed (identical set), 384 passed, 24
   skipped. Under `xvfb-run` (real display, so nothing is skipped): 15 failed
   (identical set), 408 passed, 0 new failures either way.

## Blocker fixed before building

`requirements.txt:12` pinned `faster-whisper>=1.0`; `hotwords` did not exist
until 1.0.2. Bumped to `>=1.0.2`. `hotwords` itself is NOT built anywhere in
this codebase (grep confirms 0 occurrences) — the plan's flip-rate gate for it
was never cleared, so this pin bump is purely defensive against a future
change, not cover for code that ships in this PR.

## Explicitly not built (per the plan)

- **Hotword biasing / "learns your words" claim.** Gated behind the flip-rate
  measurement in the plan (≥30% flip on 20 real misheard proper nouns). That
  measurement needs a real mic, Windows, and faster-whisper installed — none
  of which exist on this VPS. Not attempted here; needs James's machine.
- **Automatic correction capture.** Explicitly rejected in the plan
  (RichEdit/UIA/terminal/IME landmines, contradicts logged automation-vs-control
  decisions). Not implemented.
- **Session 3 (site copy, comparison table, winget submission).** Lives in the
  separate private `pipevoice-site` repo, not this one (this repo's own
  CLAUDE.md: "The pipevoice.app website + marketing live in a separate private
  repo"). No artifact for it exists in this worktree — there is nothing to
  build here for that item.

## Not verifiable on this VPS

- The Ollama latency number (gate 4).
- The hotword flip-rate test (needs James's machine — no mic, no Windows, no
  faster-whisper installed here, and the feature itself is not built).
