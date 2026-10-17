# After-call answers (v2.49.9)

Meetings → a transcribed meeting → **Their questions** → **Answer from my docs**. This is v1 of the PipeFocus answer cards idea: the work happens after the call, not live. The design and its critique are in [[2026-10-17-pipefocus-answer-cards]], the build plan in [[2026-10-17-after-call-answers-plan]].

## Pipeline (`wisprlite/knowledge.py`, pure, `completion`/`jev`/`fetch`/`pdf_reader` injectable)
1. Sources: Settings → Meetings `knowledge_dir` (MD/TXT/PDF via pypdf) + `knowledge_url` (one page, stdlib) + `session_dir/call_notes.md`. A file with zero words, or a failed URL, goes into `sources.failed` and is shown in the panel, never dropped silently.
2. ~120-word chunks → pure-Python BM25 (rebuilt each run).
3. Question extraction: the WHOLE transcript goes in (You lines give context like "will *that* cover vendors?"), but code keeps only items whose `[t]` (tenths) matches a `Them*` line. Saved transcript corrections are applied first.
4. Per question: BM25(question + LLM keywords/synonyms) top 6 → LLM `{headline, quote, source}` → the quote must exist verbatim (normalised) in the named source, else `not_found`.
5. Optional Jev (`TYPESAFE_API_KEY`, `jev-latest`, one Noul): does the quote support the headline as the answer? If p < 0.5 the item is `doubtful`.
6. Written to `session_dir/answers.json`; 👍/👎 are saved in `vote`.

## Known limits
- Without a TypeSafe key, nothing checks that the headline agrees with the quote ("Yes" over "SSO is not included" shows as answered). That is what Jev is for.
- CJK text without spaces counts as one token per run, so recall is weak there. Per-character n-grams would fix it if needed.
- The live `focus_stream` is untouched (multichannel=False, tests/test_focus.py:174).

## What decides the next step
G8 (James): on 3 real calls, at least 80% of "answered" items get a thumbs-up and every "Not in your docs" is genuinely missing. Live cards unlock per pack only after that.
