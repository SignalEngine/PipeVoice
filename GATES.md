# Gates: PipeVoice Cloud v1, desktop app (vault/Plans/cloud-build-app.md)

OWNS: wisprlite/**, tests/**, ARCHITECTURE.md, GATES.md

Scope: the desktop app side of PipeVoice Cloud v1. Bound to the code tree, not to a commit id.

- [x] G1: 200 returns the text (and the request matches the contract)
  CHECK: run-limited python3 -m pytest -p no:cacheprovider tests/test_cloud.py -k "test_200_returns" 2>&1
  EXPECT: passed
  EVIDENCE: automatic-evidence=v1; definition-sha256=bf2e6bb9ac218172372a268ed6768c2b4af874cfb006969c8fc20c345be23b03; exit=0; EXPECT=matched; output-sha256=02ec2ac0e44e2ae7328a5473a05bab55d1bbecca5dc11361c2905cdbe21809ca; output-bytes=405; shell=/bin/sh; cwd=/root/wt-pv-cloud-app; path=bea4c23ec980/15 entries

- [x] G2: 402 / 413 / 401 / 503 / network error raise, so _fallback runs local Whisper
  CHECK: run-limited python3 -m pytest -p no:cacheprovider tests/test_cloud.py -k "http_errors or network_error or falls_back" 2>&1
  EXPECT: passed
  EVIDENCE: automatic-evidence=v1; definition-sha256=58ce42762483a320bd3cde7b9635ccc4175fca550c42dfb8fad7c4ddef9cb674; exit=0; EXPECT=matched; output-sha256=5f1f73601792847da744d7dd4fcb9c3bd612e53e82e85599236c93ff6396e456; output-bytes=406; shell=/bin/sh; cwd=/root/wt-pv-cloud-app; path=bea4c23ec980/15 entries

- [x] G3: WAV over 4,400,000 bytes: no HTTP call, exception
  CHECK: run-limited python3 -m pytest -p no:cacheprovider tests/test_cloud.py -k "oversize_wav" 2>&1
  EXPECT: passed
  EVIDENCE: automatic-evidence=v1; definition-sha256=e0d9018fe1fe097faaf52168244cfe3abe93c24b95aeb90802bf1a900033bcc9; exit=0; EXPECT=matched; output-sha256=8dab619be9f9283debd2991d1c9ac8e945fd7ac73757028560fde9b529be7895; output-bytes=405; shell=/bin/sh; cwd=/root/wt-pv-cloud-app; path=bea4c23ec980/15 entries

- [x] G4: Licence file is written outside config.json
  CHECK: run-limited python3 -m pytest -p no:cacheprovider tests/test_cloud.py -k "outside_config_json" 2>&1
  EXPECT: passed
  EVIDENCE: automatic-evidence=v1; definition-sha256=0f05611be927318f62a718f472e974122e8b8a6793de707b1e175d5b642ec4c3; exit=0; EXPECT=matched; output-sha256=c6cbe437455491c096cb972054ee1aec3cd7e00c58a219b34b7cb181b06b5261; output-bytes=405; shell=/bin/sh; cwd=/root/wt-pv-cloud-app; path=bea4c23ec980/15 entries

- [x] G5: Sync picks the newer side
  CHECK: run-limited python3 -m pytest -p no:cacheprovider tests/test_cloud.py -k "sync_applies or sync_pushes or sync_409" 2>&1
  EXPECT: passed
  EVIDENCE: automatic-evidence=v1; definition-sha256=c38427df4bf7b1ad033063321c1334789abd35689ab0c39c2479d5ad7affd22d; exit=0; EXPECT=matched; output-sha256=8d294b46b1523aad253c848dec6959b3cef96ec744b6609622b411062f7b6d4f; output-bytes=405; shell=/bin/sh; cwd=/root/wt-pv-cloud-app; path=bea4c23ec980/15 entries

- [x] G6: the full suite failure count is not above main's baseline (main at 8ee8861: 15 failed, 790 passed, 114 skipped)
  CHECK: o=$(run-limited python3 -m pytest -q -p no:cacheprovider --ignore=tests/test_transcribe_deepgram_e2e.py 2>&1 | tail -1); echo "$o"; n=$(echo "$o" | grep -oE '^[0-9]+ failed' | cut -d' ' -f1); echo "$o" | grep -q passed && [ "${n:-0}" -le 15 ] && echo BASELINE-OK
  EXPECT: BASELINE-OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=4ab433716e902f80074c555ab4387bde14df4fec41ab0bc70fa9b4cf2cc15993; exit=0; EXPECT=matched; output-sha256=3ca22ad901ec276995b69d982dc8d0801a26131de677b3f12e9676528f7c3e2e; output-bytes=68; shell=/bin/sh; cwd=/root/wt-pv-cloud-app; path=bea4c23ec980/15 entries

- [ ] G7: James, on Windows with no .env, pastes a sandbox key and dictates
  EVIDENCE: pending

POSITIVE CONTROL: four planted bugs (size check off, HTTP error returns "", sync never pulls, licence path set to config.json) each made tests/test_cloud.py fail.
