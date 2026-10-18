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

## After-call prompt (v2.49.11)
James couldn't find the feature on v2.49.10, so stopping a meeting now prompts.
- `App.toggle_meeting` stop → `overlay.show_meeting_done(AFTER_CALL_PROMPT)` when the overlay is on and `session_dir` exists. The pill shows "Meeting saved" + **Transcribe + answer** / **Later**, auto-hides after 30 s, and sends `md_answer`/`md_later` to `_screenrec_action`. `_on_meeting_auto_stop` does not prompt.
- `md_answer` → `open_settings(tab="Meetings", select=<session path>, auto="answer")` → env `PV_SELECT` + `PV_AUTO=answer`. At the end of `meetings_tab.build`, it selects that session. Untranscribed → `do_transcribe()` with `state["auto_answer"]`, then `do_answers()` on success. Already transcribed → `do_answers()`.
- No docs/URL/notes → a **Choose docs folder…** button (`pick_docs_btn`) saves `knowledge_dir`, then answers. It's hidden while no engine is ready.
- Tests: `tests/test_after_call_prompt.py`. Meetings-tab tests run threads inline. The brain checked the real-mainloop path under Xvfb (PR #97).

## Live answers + call prep (v2.49.12)
James: "The live answers during the call is the entire feature." Plan: [[2026-10-18-live-answers-and-call-prep]].
- **Live:** `live_answers.LiveAnswerer`. It takes Them lines from the live stream (`FocusSession(them_only=True, on_line=…)`) and runs `looks_like_question`, joining a Them line from the last 4 s.
  - It then needs at least one real word in common with the docs index. That's not a BM25 cut-off: scores shrink with fewer chunks, so a one-file FAQ scored a perfect question at 0.99.
  - Then `knowledge.answer_question` runs, with the verbatim-quote check.
  - The card shows up only if it's within the 8 s deadline and isn't a repeat. Then `overlay.show_answer_card` puts it bottom-right for 20 s.
  - Every attempt goes to `session_dir/live_answers.json`.
- **Stream:** with `cfg.live_answers` on (default) and docs/URL/next-call notes present, the Deepgram live stream sends the DESKTOP channel only (mono, `channels=1`, `multichannel=False`).
  - It's one billed channel, and your own questions can't trigger cards.
  - PipeFocus, when on, shares this stream and sees the far side only.
  - Deepgram only.
- **Prep:** the Meetings tab has a "Before your next call" panel above the list. It holds the docs line + Change…, the notes box, and Try a question.
  - The notes are saved to `meetings_dir()/next_call_notes.md`. At meeting start they're moved into `session_dir/call_notes.md`, so after-call answers read them too.
- **Settings:** "Your docs" is the first card in Meetings → Settings ⚙. It holds the folder, the web page, and "Show answers during the call".
  - Settings save keeps a folder picked from the Meetings tab when the form's field was untouched. Before this, saving the form reverted it to the stale value.
- **Not proven on the server:** a real Deepgram socket or a real LLM in the live path. The overlay card, the stream wiring, the notes move and the gate were exercised under Xvfb with a fake LLM.
