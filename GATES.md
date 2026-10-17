# GATES — after-call answers (feat/after-call-answers)

Oracles bind to the code tree (exclude GATES.md, PLAN.md, vault/). Positive control = the check must FAIL on origin/main.

G1 unit tests exist and pass
CHECK: python3 -m pytest -q tests/test_knowledge.py -p no:cacheprovider
EXPECT: summary line "N passed", N >= 9, 0 failed. On origin/main: file missing -> fails.

G2 the nine plan behaviours each have a test (names grep-able)
CHECK: grep -cE "def test_.*(synonym|fabricated|real_quote|null|malformed|them_only|failed_source|jev_no_key|jev_doubtful|roundtrip|vote)" tests/test_knowledge.py
EXPECT: >= 9

G3 no regressions
CHECK: xvfb-run -a python3 -m pytest -q tests/ -p no:cacheprovider --ignore=tests/test_transcribe_deepgram_e2e.py
EXPECT: same 15 FAILED names as origin/main (test_env_precedence x8, test_transcribe_window x7), passed count > main's.

G4 live stream untouched
CHECK: git diff origin/main -- wisprlite/engines/deepgram_engine.py wisprlite/focus.py tests/test_focus.py | wc -l
EXPECT: 0

G5 panel is mounted
CHECK: grep -n "answers_panel" wisprlite/meetings_tab.py
EXPECT: a .pack(... before=transcript_wrap) site, a pack_forget in refresh(), and use in select_session.

G6 no new heavy deps
CHECK: git diff origin/main -- requirements.txt
EXPECT: only "pypdf" added (pinned in constraints.txt too); no typesafe-sdk, no embeddings libs.

G7 CI build green on the branch (brain runs: gh workflow run build --ref feat/after-call-answers)
EXPECT: windows-installer success, MCP + OCR smoke PASS.

G8 MANUAL (James only): 3 real transcribed calls + docs folder -> >=80% thumbs-up on answered, every "Not in your docs" genuinely missing.
