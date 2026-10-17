# After-call answers — build plan (v1 of PipeFocus answer cards)

2026-10-17 · Design: [[2026-10-17-pipefocus-answer-cards]] (artifact BqQc9kiw2yYDdUJ5chS1S9) · idea-ledger `20261017-125017`

## What James gets
In **Meetings → a transcribed meeting**, a new **"Their questions"** panel with an **Answer from my docs** button. It lists every real question the other side asked, each with:
- a ≤10-word headline answer ("Yes — Enterprise plan only"),
- the **exact sentence** from his docs it came from, plus the file name,
- or **"Not in your docs"**,
- 👍 / 👎 buttons (saved).

Docs come from: a folder (MD/TXT/PDF), one web page URL (both in Settings → Meetings), and an optional **Call notes** box in the panel for this meeting's brief.

## Why it's simpler than the design doc assumed (code facts)
- `transcribe_session` (`wisprlite/meeting.py:599`) already writes `transcript.json` with `segments[{t, speaker, text}]`; speaker is `You` / `Them` / `Them N` (`merge_transcripts`, `meeting.py:260`). **Use only `Them*` segments.**
- So v1 never touches the live PipeFocus stream (`focus_stream` is `multichannel=False` on purpose; `tests/test_focus.py:174` asserts it). **Do not change it.**
- No latency budget → no embeddings in v1. Recall problem ("get our data in" vs "ingestion") is solved by the LLM writing search keywords + synonyms per question.

## Pipeline (new module `wisprlite/knowledge.py`, pure + injectable `completion`, same style as `summarise.py`)
1. **Load sources** → list of `{source, text}`:
   - folder: `*.md *.txt` (read utf-8, errors=replace), `*.pdf` via `pypdf` (new dep). File with 0 extracted words → reported in the panel as "couldn't read: X.pdf (scanned?)", never silent.
   - URL: stdlib `urllib.request` + `html.parser` text extraction, single page, 10 s timeout; failure → reported, not fatal.
   - call notes: `session_dir/call_notes.md`.
2. **Chunk** ~120-word passages on paragraph/sentence boundaries, keep `source`.
3. **Index**: pure-Python BM25 over chunks (~30 lines, no dependency). Built per run in memory. `# ponytail: rebuilt each run, persist if folders get large`.
4. **Extract questions** — ONE LLM call over the `Them*` segments (chunked like `summarise._chunks` if long): JSON list `[{t, question, keywords:[...]}]`. Prompt: only sincere questions the speaker wants answered; skip rhetorical/filler ("you know?", "right?"); rewrite into a standalone question; 3–8 search keywords INCLUDING synonyms.
5. **Answer** — per question, BM25(question + keywords) top 6 chunks → ONE LLM call → JSON `{headline: str|null, quote: str|null, source: str|null}`. Rule: answer only from excerpts, else all null.
6. **Verify quote** (code): normalized `quote` must be a substring of the named source's text, else → treat as not found. (Existence check only — see 7.)
7. **Answer check (Jev, optional)**: if `TYPESAFE_API_KEY` is set, one Jev call with a Noul per answered question: "Does `quote` directly answer `question`?" Store `jev_p`. If `jev_p < 0.5` → mark `doubtful` (shown greyed with "may not answer this"). Raw HTTP via the already-bundled `httpx` or `openai`-free `urllib` — **read https://docs.typesafe.ai/api.md for the request shape; do not add typesafe-sdk**. No key → step skipped, `jev_p: null`.
8. **Save** `session_dir/answers.json`: `{built_at, sources:{ok:[...], failed:[...]}, items:[{t, question, headline, quote, source, jev_p, status: answered|doubtful|not_found, vote: null|up|down}]}` (atomic write like `meeting._write_json`).

LLM calls use `cleanup.chat_completion(messages, cfg.cleanup_provider, cfg.cleanup_model)`; JSON parsed tolerantly like `focus.parse_tip`.

## UI
- **Settings → Meetings card** (`settings.py`, pattern of "Save meetings to" at ~1858): `Docs folder` entry + Browse; `Docs web page` entry. Config fields `knowledge_dir: str = ""`, `knowledge_url: str = ""` (`config.py` dataclass lines; save at ~1879).
- **Meetings tab detail** (`meetings_tab.py`): new `answers_panel` packed `before=transcript_wrap`, following `summary_panel` (951-998): hidden in `refresh()`, shown in `select_session` when the meeting has `transcript.json`. Contents: Call notes `tk.Text` (saved to `call_notes.md` on button press), **Answer from my docs** button (worker thread + `back_on_ui`, like `do_summarise` 2330), source status line, then the item list. 👍/👎 write `vote` into `answers.json`. Disabled with a hint when no docs folder/URL/notes set or no LLM provider ready.

## Out of scope (v1)
Live cards; embeddings; multi-page crawl; Notion/Drive; changing PipeFocus nudges or `focus_stream`; pre-call "brief me".

## Gates (GATES.md at worktree root, each with CHECK + EXPECT)
1. `pytest tests/test_knowledge.py` — new tests, pure, fake `completion`:
   - BM25 ranks the chunk containing a keyword synonym first (positive control: drop the synonym → it loses).
   - fabricated quote (not in source) → `not_found`; real quote → `answered`.
   - LLM returns nulls → `not_found`; malformed JSON → `not_found`, no crash.
   - only `Them*` segments reach the question-extraction prompt (assert `You` text absent).
   - 0-word PDF/failed URL appear in `sources.failed`.
   - Jev: no key → `jev_p is None`; fake Jev returning 0.2 → `doubtful`.
   - `answers.json` round-trip incl. vote.
2. Full suite: no new failures vs main (baseline 15 Linux-env failures, `--ignore=tests/test_transcribe_deepgram_e2e.py`); `tests/test_focus.py` untouched and green.
3. Panel is MOUNTED: grep proves `answers_panel` is packed in `select_session` path and hidden in `refresh()`; `test_meetings_tab.py`-style test on any new pure helper.
4. CI build on the branch green (PyInstaller incl. `pypdf`; add `--collect-all pypdf` only if the smoke test needs it), MCP + OCR smoke PASS.
5. **Manual (James):** on 3 of his real transcribed sales/support calls with his docs folder: ≥80% of "answered" items thumbs-up, and every "Not in your docs" is genuinely missing. This is the number that decides whether live cards get built.

## Build
Builder pane (sonnet), worktree `/root/wt-pv-answers` off origin/main, branch `feat/after-call-answers`. STOP rule: anything this plan doesn't spell out → stop and report. Brain runs verify + review-gate + jury once, then merge → beta.
Estimate: one build session (~2–3 h) + review/ship ~1 h. External wait: James's real-call check.
