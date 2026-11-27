# Gates: PipeVoice Cloud v1, desktop app (vault/Plans/cloud-build-app.md)

OWNS: wisprlite/**, tests/**, ARCHITECTURE.md, GATES.md

Scope: the desktop app side of PipeVoice Cloud v1. Bound to the code tree, not to a commit id.

- [x] G1: 200 returns the text (and the request matches the contract)
  CHECK: run-limited python3 -m pytest -p no:cacheprovider tests/test_cloud.py -k "test_200_returns" 2>&1
  EXPECT: passed
  EVIDENCE: automatic-evidence=v1; definition-sha256=bf2e6bb9ac218172372a268ed6768c2b4af874cfb006969c8fc20c345be23b03; exit=0; EXPECT=matched; output-sha256=9aa79746d45cca29213b3817e79afc55f4f873eb0927d71f821d7fcf9cd29fe8; output-bytes=405; shell=/bin/sh; cwd=/root/wt-pv-cloud-app; path=bea4c23ec980/15 entries

- [x] G2: 402 / 413 / 401 / 503 / network error raise, so _fallback runs local Whisper
  CHECK: run-limited python3 -m pytest -p no:cacheprovider tests/test_cloud.py -k "http_errors or network_error or falls_back" 2>&1
  EXPECT: passed
  EVIDENCE: automatic-evidence=v1; definition-sha256=58ce42762483a320bd3cde7b9635ccc4175fca550c42dfb8fad7c4ddef9cb674; exit=0; EXPECT=matched; output-sha256=569c9e88c7dc78d9a12cb4199415aa67863e5b0ea0d55d78d4822fc9b2cd6614; output-bytes=406; shell=/bin/sh; cwd=/root/wt-pv-cloud-app; path=bea4c23ec980/15 entries

- [x] G3: WAV over 4,400,000 bytes: no HTTP call, exception
  CHECK: run-limited python3 -m pytest -p no:cacheprovider tests/test_cloud.py -k "oversize_wav" 2>&1
  EXPECT: passed
  EVIDENCE: automatic-evidence=v1; definition-sha256=e0d9018fe1fe097faaf52168244cfe3abe93c24b95aeb90802bf1a900033bcc9; exit=0; EXPECT=matched; output-sha256=58406fa6700119c084c89dc0e9808eb46f968ee883c0fb5d5ea153a12c719139; output-bytes=405; shell=/bin/sh; cwd=/root/wt-pv-cloud-app; path=bea4c23ec980/15 entries

- [x] G4: Licence file is written outside config.json
  CHECK: run-limited python3 -m pytest -p no:cacheprovider tests/test_cloud.py -k "outside_config_json" 2>&1
  EXPECT: passed
  EVIDENCE: automatic-evidence=v1; definition-sha256=0f05611be927318f62a718f472e974122e8b8a6793de707b1e175d5b642ec4c3; exit=0; EXPECT=matched; output-sha256=8be85fa435c0c1a9f05578000abf7768d88545c8f1a8407c5a44968b046c142b; output-bytes=405; shell=/bin/sh; cwd=/root/wt-pv-cloud-app; path=bea4c23ec980/15 entries

- [x] G5: Sync picks the newer side
  CHECK: run-limited python3 -m pytest -p no:cacheprovider tests/test_cloud.py -k "sync_applies or sync_pushes or sync_409" 2>&1
  EXPECT: passed
  EVIDENCE: automatic-evidence=v1; definition-sha256=c38427df4bf7b1ad033063321c1334789abd35689ab0c39c2479d5ad7affd22d; exit=0; EXPECT=matched; output-sha256=8e8c585c9b8d7924ea64fe3dfef35f8806b25af681dd5458de3bc1af066c4f81; output-bytes=405; shell=/bin/sh; cwd=/root/wt-pv-cloud-app; path=bea4c23ec980/15 entries

- [x] G6: under a display, the full suite failure count is not above main's baseline (main at 8ee8861 under xvfb-run: 16 failed, 903 passed)
  CHECK: log="$(git rev-parse --git-dir)/g6-suite.log"; run-limited xvfb-run -a python3 -m pytest -q -p no:cacheprovider -rf --ignore=tests/test_transcribe_deepgram_e2e.py > "$log" 2>&1; o=$(tail -1 "$log"); echo "$o"; n=$(echo "$o" | grep -oE '^[0-9]+ failed' | cut -d' ' -f1); echo "$o" | grep -q ' passed' && [ "${n:-0}" -le 16 ] && echo BASELINE-OK
  EXPECT: BASELINE-OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=ee9fd877632a1212b512865cd875d4d5b8d06619734f480c4c25160fa5b5638c; exit=0; EXPECT=matched; output-sha256=d11fcb4551fc2273bd52ee9860a1064c93874541ba42a0d544fd77a33a5fc20f; output-bytes=67; shell=/bin/sh; cwd=/root/wt-pv-cloud-app; path=bea4c23ec980/15 entries

- [ ] G7: James, on Windows with no .env, pastes a sandbox key and dictates
  EVIDENCE: pending

ABANDON: G7 NOT RUN. Handoff to James: needs their Windows PC and a sandbox key; the builder has neither. This is not a pass.

POSITIVE CONTROL: four planted bugs (size check off, HTTP error returns "", sync never pulls, licence path set to config.json) each made tests/test_cloud.py fail.
