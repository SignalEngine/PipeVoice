# Live answers + call prep + docs where you can see them

2026-10-18 · James: "The live answers during the call is the entire feature." He wants prep before the call too, and asked why the docs folder sits in Settings rather than Meetings. It is in Meetings → Settings ⚙, at the bottom of the "Where meetings are kept" card, which is why he didn't find it.

## What James gets
1. **Meetings tab, top: a "Before your next call" panel**, always visible:
   - The docs line, e.g. "Docs: C:\…\Acme docs · 14 files", plus **Change…**. With nothing set it reads "No docs yet" plus **Choose docs folder…**.
   - A notes box: "Who is it with, what will they ask? Used for your next call."
   - **Try a question**: an entry plus an **Ask** button that shows the card you would get, or "Not in your docs".
2. **During a call**, when the other side asks something your docs or notes answer, a card pops up bottom-right. It shows the headline (10 words or fewer), the exact quote underneath, and the file name. It fades after 20 s and is dismissed with ✕. When there's no match, or it's unsure, nothing pops up.
3. **Meetings → Settings ⚙** gains its own "Your docs" card at the TOP. It holds the docs folder, the web page, and a "Show answers during the call" checkbox (default on). These rows move out of "Where meetings are kept".

## Decisions (made; do not revisit)
- **Live needs Deepgram.** It's the only engine with live streaming. On other engines, the panel's docs line adds "Live answers need Deepgram (Settings → Transcription)."
- **The live stream carries only the far side.** With live answers on, the stream sends ONLY the `desktop` capture: mono, `channels=1`, `multichannel=False`. Every line is "Them". The cost stays at one channel ($0.29/h), and our own questions can't trigger cards. If PipeFocus nudges are also on, they share this same stream and see the far side only. One socket, never two.
- **Grounding reuses the after-call code.** That means `knowledge.load_sources` + `chunk_docs` + `BM25` + `answer_question`. The quote must exist verbatim in the source, or there's no card.
- **Next-call notes** live in the file `meeting.meetings_dir() / "next_call_notes.md"`. When a meeting starts, they're copied to `session_dir/call_notes.md`, so after-call answers use them too, and the next-call file is then deleted. Standing context belongs in the docs folder; the panel's hint text says so.

## Changes
1. **config.py:** add `live_answers: bool = True`.
2. **engines/deepgram_engine.py, `focus_stream(cfg, on_text, *, sample_rate, channels=2)`:** pass `channels` into the options. Keep `multichannel=False`; the existing test still guards it.
3. **focus.py, `FocusSession`:**
   - New kwargs: `them_only: bool = False` and `on_line: Callable[[str, str], None] | None = None`.
   - `them_only`: `feed()` skips `mic`, and enqueues `desktop` pcm straight onto the queue with no interleaver. `on_text` labels the line "Them".
   - Every line goes to `on_line(speaker, text)`, wrapped in try/except.
   - `_maybe_analyse` returns early when `completion is None`, so there are no tip threads without PipeFocus.
4. **New wisprlite/live_answers.py** (pure; no Tk, no network; `completion`, `clock` and `load` are injectable):
   - `looks_like_question(text) -> bool`: True when the text ends with `?`, or its first word is one of: what, how, why, when, where, which, who, do, does, did, can, could, is, are, will, would, should, have, has. Lowercase match, at least 3 words.
   - `class LiveAnswerer(folder, url, notes_path, completion, on_card, *, clock=time.monotonic, deadline=8.0, log_path=None)`.
   - `start()`: builds the index on a daemon thread. Lines that arrive before it's ready are ignored.
   - `on_line(speaker, text)`: ignore anything not "Them", or anything that fails `looks_like_question`. Otherwise put the question into a 1-slot "latest wins" queue for one worker thread. A newer question replaces an unstarted older one.
   - Worker: skip when the BM25 top score is below `MIN_SCORE = 1.0`; no LLM call on small talk. Otherwise call `answer_question({"question": text, "keywords": []}, index, docs, completion)`.
   - Drop the card if `clock() - asked_at > deadline`, or if the headline is too close to one already shown (normalised equality of the question, or of the quote).
   - Otherwise call `on_card(headline, quote, source)`. Append `{t_asked, question, headline, quote, source, shown}` to `log_path` (a JSON list), writing every attempt, shown or not.
   - `stop()` sets a stop flag.
   - Exceptions never escape. Keep the last error in `last_error`.
5. **overlay.py:** `show_answer_card(headline, quote, source)` queues `("answer_card", headline, quote|source)`. `_render_answer_card` copies `_render_focus_tip`, with these differences:
   - The headline is bold.
   - The quote is in “…” italics, `wraplength=380`.
   - The file name is muted and small.
   - `dismiss_after=20000`.
   - `y` is `- 190 - 140`, so a focus tip and a card don't overlap.
   - Route it in the queue loop next to `focus_tip`.
6. **app.py:**
   - `_start_pipefocus`:
     - Start a session when `pipefocus` is on, or when `live_answers` is on and docs/URL/next-call notes exist. It's Deepgram only, as today.
     - Pass `them_only=answers_wanted`, `channels=1 if them_only else 2`, `completion=completion if cfg.pipefocus else None`, and `on_line=self._on_live_line`.
   - After `self._meeting.start()` succeeds, call `_start_live_answers()`:
     - If `session_dir` exists and `next_call_notes.md` exists, move it to `session_dir/call_notes.md`. Only do this when the session has none; then delete it.
     - When answers are wanted, build a `LiveAnswerer` with `log_path=session_dir/"live_answers.json"` and `on_card=self._show_answer_card`, store it on `self._live_answers`, and `start()` it.
   - `_on_live_line` forwards to `self._live_answers` when it is set.
   - `_stop_pipefocus` also stops and clears `self._live_answers`. Every existing stop path already calls it.
7. **meetings_tab.py:**
   - New "Before your next call" panel, packed above `body`, OUTSIDE the collapsible settings. Use the existing CARD/BG/ACCENT palette.
   - The notes Text is 3 lines. It loads from, and saves to, `next_call_notes.md` on `<FocusOut>` and on Ask. Empty text deletes the file.
   - **Ask** runs on a thread: `load_sources` + chunk + BM25 + `answer_question` with `knowledge._default_completion(cfg.cleanup_provider, cfg.cleanup_model)`. The result goes back through `root.after`. Show a headline, quote and source, or "Not in your docs", or "Unavailable: configure an API key or choose local Ollama." when `provider_ready` is false.
   - **Change…** / **Choose docs folder…** reuse the `pick_docs` flow (askdirectory → save `knowledge_dir`), then refresh the docs line. The file count is the number of MD/TXT/PDF files at the top level of the folder.
8. **settings.py:**
   - Move the Docs folder + Docs web page rows into a new `card("Your docs", "Markdown, text or PDF. Meetings answers the other side's questions from them, live and after the call.", parent=meeting_settings_panel)`. Create it BEFORE the "Meetings" card, so it's first.
   - Add the checkbox "Show answers during the call (Deepgram only)" bound to `live_answers`, and save it in the save handler next to `knowledge_dir`.
   - Guide step 7: add one sentence: "During the call, an answer card pops up when they ask something your docs cover."

## Tests (each must fail without its change)
- `tests/test_live_answers.py`:
  - `looks_like_question`: True for "Do you support SSO" and "pricing for teams?", False for "you know what I mean" (first word is not a question word, no `?`) and for "why?" (under 3 words).
  - "You" lines never call `completion`.
  - A low BM25 score → `completion` is not called.
  - A good doc + a fake completion returning a verbatim quote → `on_card` is called once, and a repeat of the same question → not called again.
  - A fake quote that isn't in the doc → no card.
  - A fake clock past the deadline → no card, but it's logged with `shown: False`.
  - The latest-wins queue: two questions queued while the worker is blocked → only the second is answered.
- `tests/test_focus.py`:
  - `them_only` feeds only desktop pcm, unmodified.
  - Lines reach `on_line` as "Them".
  - `completion=None` never spawns analysis.
  - Keep the `multichannel=False` test.
- App:
  - Answers on + docs + Deepgram → the session starts with `them_only=True`.
  - Answers off + PipeFocus off → no session.
  - Next-call notes get moved into `session_dir/call_notes.md`, and the next-call file is deleted.
  - Stop → `LiveAnswerer.stop` is called.
- Overlay: `show_answer_card` puts the right tuple on the queue, and `_render_answer_card` is routed. Follow the `show_focus_tip` tests.
- Meetings tab, using the `test_after_call_prompt.py` inline-thread patterns:
  - The prep panel shows "No docs yet" with no folder, and the file count with a folder.
  - Notes save to the file on Ask.
  - Ask with a faked `answer_question` shows its headline.
- Settings: the "Your docs" card exists in the meeting settings panel, and the `live_answers` checkbox round-trips through save.
- Full suite: `xvfb-run -a python3 -m pytest -q tests/ -p no:cacheprovider --ignore=tests/test_transcribe_deepgram_e2e.py`. No new failures beyond the known 15 Linux-env ones (`test_env_precedence`, `test_transcribe_window`).

## Out of scope
- Embeddings/hybrid search.
- Jev in the live path.
- Showing live cards in the after-call view.
- Per-pack arming.
- Other engines' live streaming.

## STOP rule
Anything this plan doesn't spell out: stop and report. Don't merge, tag or push. Commit on the branch only.

## Brain's end-to-end check
1. Under Xvfb, feed `LiveAnswerer` a real docs folder and a scripted Them line through a real `FocusSession` (with a fake connect).
2. Record the card appearing on the real overlay.
3. Screenshot the prep panel: empty, with a folder, and an Ask result.
4. Screenshot Meetings → Settings with "Your docs" first.
