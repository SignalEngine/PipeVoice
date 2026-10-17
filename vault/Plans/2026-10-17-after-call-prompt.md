# After-call prompt: one click from "stop meeting" to answered questions

2026-10-17 · James on v2.49.10 couldn't find After-call answers: nothing appears when a meeting stops, the docs folder setting is buried, and the panel only shows after a manual Transcribe. He picked "prompt when you stop".

## What James gets
1. He presses the meeting hotkey to stop. The pill changes to **Meeting saved — get their questions answered from your docs?** with two buttons: **Transcribe + answer** and **Later**.
2. **Transcribe + answer** opens Settings on the Meetings tab with that meeting selected. If it isn't transcribed yet, it transcribes; then it runs Answer from my docs.
3. If no docs folder, web page or call notes are set, the Their questions panel shows **Choose docs folder…**. Picking a folder saves it to config and runs the answers straight away.

## Changes
Branch: build on `app/about-and-meetings-guide` (worktree `/root/wt-pv-prompt`); it already holds the About/Guide commit that ships with this.

1. **overlay.py — new pill state `meeting_done`.** Copy the `ra_choice` pattern exactly: `show_ra_choice` → `show_meeting_done(subtitle: str)`; `RA_CHOICE_BUTTONS` → `MEETING_DONE_BUTTONS = (("md_answer", "Transcribe + answer"), ("md_later", "Later"))`; `_draw_ra_choice` → `_draw_meeting_done` (title "Meeting saved", the subtitle underneath); `_ra_choice_hit` → `_meeting_done_hit`; route through `_pill_click`. Clicks go to `on_screenrec_action`, the same callback `ra_*` uses. Accent "Transcribe + answer" as the primary button. Auto-hide after 30 s with no click.
2. **app.py**
   - `toggle_meeting` stop branch: read `self._meeting.session_dir` (still set after `stop()`; see `meeting.py:940`). When `self.cfg.overlay` is on and that dir exists, call `self.overlay.show_meeting_done(<dir name>)` instead of `overlay.hide()`, and store `self._last_meeting_dir`. Otherwise hide as today.
   - `_on_meeting_auto_stop`: unchanged.
   - `open_settings(tab, select, auto="")`: a new `auto` arg sets env `PV_AUTO`.
   - `_screenrec_action`: `"md_answer"` → `self.open_settings(tab="Meetings", select=str(self._last_meeting_dir), auto="answer")` then `overlay.hide()`; `"md_later"` → `overlay.hide()`.
3. **meetings_tab.py**
   - At the end of `build`, read `PV_SELECT` (a full session path) and call `refresh(preferred=Path(it))`, following `screenrec_tab.py:397`.
   - If `PV_AUTO == "answer"` and that session is selected, set `state["auto_answer"] = True`. If it can be transcribed and has no transcript, call `do_transcribe()`. When the transcribe finishes successfully and `state["auto_answer"]` is set, clear the flag and call `do_answers()`. If it's already transcribed, call `do_answers()` directly.
   - In `do_answers`, when no docs, URL or notes are set, show a **Choose docs folder…** button in the answers panel next to `answers_status`. Status text becomes: "Pick a docs folder (PDF, Markdown, text), or type call notes above."
   - The button runs `filedialog.askdirectory` → `cfg = config.Config.load(); cfg.knowledge_dir = chosen; cfg.save()` → `do_answers()`. Hide the button once a folder is set.
4. **settings.py, Guide step 7:** start with "When you stop a meeting, the pill offers Transcribe + answer: one click does both." Mention the Choose docs folder… button. Keep the rest.

## Tests (each fails without its change)
- `tests/test_overlay.py`: the `meeting_done` hit test returns `md_answer` / `md_later` on each button box and `""` elsewhere, through `_pill_click`.
- App:
  - Stopping a meeting with the overlay on calls `show_meeting_done`; with the overlay off it calls `hide`.
  - `md_answer` calls `open_settings(tab="Meetings", select=<dir>, auto="answer")`, using mocks.
  - Follow the existing app tests' fake-meeting pattern.
- Meetings tab, using the `test_meetings_tab.py` patterns:
  - `PV_AUTO=answer` on a transcribed session with `knowledge_dir` set → `knowledge.build_answers` (faked) is called once.
  - On an untranscribed session → transcribe (faked) runs first, then answers.
  - With no docs set → the Choose docs folder… button is shown.
  - With a mocked `askdirectory` → the button saves `knowledge_dir` and then answers.
- Full suite: `xvfb-run -a python3 -m pytest -q tests/ -p no:cacheprovider --ignore=tests/test_transcribe_deepgram_e2e.py` shows no new failures against the 15 known Linux-env ones.

## Out of scope
Auto-transcribing without a click. Changing `_on_meeting_auto_stop`. Live cards. PipeFocus.

## STOP rule
Anything this plan doesn't spell out: stop and report. Do not merge, tag or push.

## Brain's end-to-end check
- Record the `meeting_done` pill under Xvfb.
- Open Settings with `PV_TAB=Meetings PV_SELECT=<demo session> PV_AUTO=answer` and fake completions, then screenshot that the answers fill in.
- With no docs set, screenshot that the Choose docs folder… button shows.
