# Gates — PipeVoice Cloud v1, desktop app (`vault/Plans/cloud-build-app.md`)

Bound to the code tree (`wisprlite/`, `tests/`), not to a commit id.

1. 200 returns the text (and the request matches the contract).
   CHECK: `python3 -m pytest -q tests/test_cloud.py -k "test_200_returns"`
   EXPECT: pass, at least one test selected.
   RESULT: 1 passed, 28 deselected in 0.22s

2. 402 / 413 / 401 / 503 / network error raise, so `_fallback` runs local Whisper.
   CHECK: `python3 -m pytest -q tests/test_cloud.py -k "http_errors or network_error or falls_back"`
   EXPECT: pass, at least one test selected.
   RESULT: 12 passed, 17 deselected in 0.29s

3. WAV over 4,400,000 bytes: no HTTP call, exception.
   CHECK: `python3 -m pytest -q tests/test_cloud.py -k "oversize_wav"`
   EXPECT: pass, at least one test selected.
   RESULT: 

4. Licence file is written outside `config.json`.
   CHECK: `python3 -m pytest -q tests/test_cloud.py -k "outside_config_json"`
   EXPECT: pass, at least one test selected.
   RESULT: 

5. Sync picks the newer side.
   CHECK: `python3 -m pytest -q tests/test_cloud.py -k "sync_applies or sync_pushes or sync_409"`
   EXPECT: pass, at least one test selected.
   RESULT: 

6. The full suite failure count is not above main's baseline.
   CHECK: `python3 -m pytest -q --ignore=tests/test_transcribe_deepgram_e2e.py`
   EXPECT: 15 failed or fewer (main at 8ee8861: 15 failed, 790 passed, 114 skipped).
   RESULT: 15 failed, 819 passed, 114 skipped. Same files: test_env_precedence.py (8), test_transcribe_window.py (7).

Positive control: four planted bugs (size check off, HTTP error returns "", sync never
pulls, licence path = config.json) each made tests/test_cloud.py fail.

7. Manual (James, Windows): paste a sandbox key with no `.env` and dictate.
   RESULT: not run.
