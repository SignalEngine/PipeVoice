# Call prep pop-up, answers from chosen documents, paste-only output

2026-10-18 · James on v2.49.12. Three findings:

1. He gave it a folder of CV PDFs plus a job-ad web page, then asked "why would this job suit you?". The card quoted the job ad ("You thrive in fast-moving, ambiguous environments") and ignored the CVs.
   - Cause: BM25 picked the top 6 chunks by word overlap, and the ad has "job" in it.
   - The prompt also only finds a quote. It doesn't help *answer*.
2. He wants a pop-up in the middle of the screen when a meeting starts. It asks what the call is and lets him pick the document(s) for this call (one CV, an agenda) instead of the whole folder.
3. "Remove the typing thing completely, because I absolutely hate it."
   - Paste mode falls back to typing letter by letter whenever the clipboard holds an image (`typer._clipboard_holds_non_text`). That's there to protect a copied screenshot, and he hit it right after taking one.

## A. Paste only (no typing anywhere)
1. **typer.py `type_text`:**
   - Never call `keyboard.write`. Always copy + Ctrl+V.
   - When the clipboard holds an image, grab it first with `PIL.ImageGrab.grabclipboard()`, paste, then put the image back as CF_DIB using ctypes:
     - BMP bytes minus the 14-byte file header.
     - `OpenClipboard` / `EmptyClipboard` / `GlobalAlloc(GMEM_MOVEABLE)` / `SetClipboardData(8)`.
   - When it holds anything else that isn't text (copied files), paste anyway and log `clipboard: non-text, non-image contents not restored`.
     - Mark this with `# ponytail: copied files are not restored; CF_HDROP if anyone misses it`.
   - When the copy/paste itself raises, raise. `_deliver` already catches that and saves the words.
   - The `mode` parameter stays for callers, but is ignored except for `"clipboard"`, which `_deliver` handles before this point.
2. **settings.py:** delete the Output row and the `OUTPUTS` "type" option. Save always writes `output_mode = "paste"` unless it is `"clipboard"`.
3. **tray.py:** remove the Output submenu.
4. **voices_editor.py:** `OUTPUTS` loses `("type", "Type")`.
5. **config.py `load`:** any saved `output_mode == "type"` becomes `"paste"` on every load (not once). The same goes for voices/profile overrides: in `App._eff`, an `output_mode` of `"type"` reads as `"paste"`.
6. **app.py `set_output`** stays, but maps "type" to "paste".

## B. Answers from the documents you choose
1. **knowledge.py:**
   - `load_sources(..., files: list[str] | None = None)`. Each file is read like a folder file (MD/TXT/PDF) and named by its file name.
   - New `build_context(question, index, docs)`:
     - If the docs total `FULL_TEXT_WORDS = 6000` words or fewer, return every doc whole, as `[name]\ntext` blocks.
     - Otherwise return BM25 top `TOP_K`, plus the best-scoring chunk from each source that scores > 0 and isn't already in. That way one source can't crowd out the rest.
2. **knowledge.py, new `answer_live(question, index, docs, completion) -> dict`:**
   - Prompt: "Someone on a call just asked me this. Using ONLY these documents (they can include my CV, my notes, their job ad, my product docs), tell me what to say."
   - JSON reply: `{"headline": "<what to say, 12 words max>", "points": ["<short point>", "<short point>"], "quote": "<one supporting sentence copied word for word>", "source": "<its file name>"}`, or all nulls when the documents don't help.
   - The verbatim-quote check works as in `answer_question`: a quote that doesn't exist in the named doc means no answer.
   - `points` is 0–2 strings, each trimmed to 120 chars.
   - It returns `{headline, points, quote, source}`. The after-call `answer_question` is unchanged.
3. **live_answers.py:**
   - Use `answer_live` + `build_context`.
   - Small-doc mode (total words ≤ `FULL_TEXT_WORDS`) skips the word-overlap gate. A real question is enough to make the call.
   - `on_card(headline, points, quote, source)`. The log gains `points`.
   - The constructor takes `files=` and passes it to `load_sources`.
4. **overlay.py `show_answer_card` / `_render_answer_card`:**
   - Add `points`, each shown as `• point` under the headline in normal weight, `wraplength=380`.
   - Join the parts with `CARD_SEP` in the order quote, source, then points.
5. **meetings_tab.py "Try a question":** use `answer_live` + `build_context`, and show the points too.

## C. The prep pop-up at meeting start
1. **New `wisprlite/prep_window.py`, `main(session_dir)`.** It's a small centred, topmost Tk window titled "Prepare for this call", in the app's PALETTE. The meeting is ALREADY recording behind it; the window never blocks capture.
   - **"What's this call?"**: an Entry, e.g. "Interview with Sam, backend role".
   - **"Anything it should know?"**: a 4-line Text, pre-filled from `next_call_notes.md` if it exists.
   - **"Documents for this call"**: a list of chosen files, each with a ✕, and **Add file…** (`askopenfilenames`, PDF/MD/TXT, multiple).
   - Below the list: the checkbox **"Also use my docs folder (N files)"**. It only shows when a folder is set. It defaults ON when no files are added, and switches OFF the first time a file is added; the user can switch it back.
   - The buttons are **Start answering** (accent, Enter) and **Skip** (Esc), which means no live answers for this call.
   - On Start, write `session_dir/prep.json`: `{"title", "notes", "files": [...], "use_folder": bool}`. Append the title + notes to `session_dir/call_notes.md`, so after-call answers read them. Then delete `next_call_notes.md` and close.
   - On Skip, write `{"skip": true}` and close.
   - Closing the window with the ✕ counts as Skip.
2. **`__main__.py`:** add `--prep <session_dir>` → `prep_window.main(path)`.
3. **app.py:**
   - After a successful `self._meeting.start()`, when `self._answers_on` is True, spawn the prep window like `open_settings` does. Frozen: `[sys.executable, "--prep", dir]`. Source: `_pythonw() -m wisprlite --prep dir`.
   - `_answers_wanted()` now also returns True when `live_answers` is on but no docs or notes are set, because the pop-up is where you add a file. It stays Deepgram only.
   - `_start_live_answers` no longer moves `next_call_notes.md`; the pop-up does that. It passes `prep_path=session_dir/"prep.json"` to `LiveAnswerer`.
4. **live_answers.py `LiveAnswerer(..., prep_path=None)`:**
   - When `prep_path` is set, `_build` waits for that file, polling every 0.5 s, up to `PREP_WAIT = 600` s, or until `stop()`.
   - It then reads it: skip → stays not ready (no cards). Otherwise it builds from `files` + (folder if `use_folder`) + url + `call_notes.md`.
   - If the file never appears, it builds from the folder + url + notes as today.
   - When it's ready, call `on_ready(n_docs)` if one was given. The app shows it as a focus tip: "Live answers ready — N documents".

## Tests (each must fail without its change)
- **typer:**
  - With `keyboard` mocked, `type_text("hi", "type")` sends ctrl+v and never calls `keyboard.write`.
  - With a fake image on the clipboard (`_clipboard_holds_non_text` → True, `ImageGrab.grabclipboard` → a PIL image, the restore function mocked), it pastes and then calls the restore with that image.
- **config:** a saved `"output_mode": "type"` loads as `"paste"`, even with `paste_default_migrated` True.
- **settings:** there's no "Output" row or "Type keystrokes" text in the settings form.
- **tray:** the menu has no Output item.
- **knowledge:**
  - `build_context` returns every doc whole under the limit.
  - Over the limit, a second source with one matching chunk is included even when source A fills the top-k.
  - `answer_live` with a fake completion returns points. A quote not in the doc → all None.
- **live_answers:**
  - Small-doc mode answers "why would this job suit you?" against a CV text file that shares no non-filler words with the question.
  - prep.json with `files` (CV only) and `use_folder` false → the folder's other file is not in the docs.
  - Skip → no card.
  - No prep file and stop() → the build thread exits.
- **prep_window**, under Xvfb, with the Tk pump pattern from `test_live_answers.py`:
  - Add a file (mock askopenfilenames), then Start → `prep.json` has the file, `use_folder` false, and the title/notes are appended to call_notes.md.
  - Esc → skip.
- **app:** a meeting start with answers on spawns `--prep <session_dir>` (Popen mocked). With answers off, there's no spawn.
- **overlay:** a card with points renders on a real display (copy `test_answer_card_renders_on_a_real_display`).
- **Full suite:** `xvfb-run -a python3 -m pytest -q tests/ -p no:cacheprovider --ignore=tests/test_transcribe_deepgram_e2e.py`.
  - No new failures beyond the 15 known Linux-env ones.
  - Tests that asserted typing behaviour get updated to paste, and the report names each one.

## Out of scope
- Restoring copied files on the clipboard.
- Caching the index between calls.
- Changing the after-call `build_answers` prompt.
- The lander's "types real keystrokes" copy, which is in the site repo.

## STOP rule
Anything this plan doesn't spell out: stop and report. Don't merge, tag or push. Commit on the branch only.

## Brain's end-to-end check
1. Xvfb: start a fake meeting session dir and spawn the prep window. Screenshot it. Add a CV PDF (made with reportlab or an existing test PDF), then Start.
2. Drive a `LiveAnswerer` with `prep_path` and a recorded overlay. The question is "why would this job suit you?". The fake LLM must receive the CV text in its context. Record the card with points.
3. Check that the typing path is unreachable: `rg keyboard.write wisprlite/` returns nothing.
