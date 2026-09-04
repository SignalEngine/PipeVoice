# Read Aloud

Hotkey → capture part of the screen → OCR it offline → speak it. Shipped v2.45.6.

**This is the first thing PipeVoice does that is OUTPUT.** Everything else is
speech IN. Worth remembering when judging whether a change belongs here.

## Design, and the four things the panel cut

| Decision | Why the obvious version was wrong |
|---|---|
| **Speak ALWAYS**, even with NVDA/JAWS running | The first design auto-silenced to avoid talking over a screen reader. A blind user triggers OCR *precisely because* the reader cannot read that region - an image, a canvas, a scanned PDF. Silence there defeats the feature at the moment it is needed. Opt-out exists, default OFF. |
| **Default capture is the FOCUSED WINDOW** | Drag-to-select as the default assumes a mouse this audience may not have. +Shift = whole screen, +Ctrl = drag a region. Three modes, not four. |
| **No custom UI Automation provider** | If NVDA can read the overlay the speech is redundant; if it cannot, the control is useless. |
| **Clipboard copy is announced, never silent** | Blind users lean on clipboard managers. Copy by default, say so, and allow turning it off. |

`Windows.Media.SpeechSynthesis`, not SAPI - it reaches the modern natural voices
SAPI's legacy ones do not. `winrt-*` at 3.2.1, **not `winsdk`** (1.0.0b10, a
beta that every proposal named).

Deferred: Piper (a ~50-100MB runtime model download from an unsigned installer is
a SmartScreen problem) and ElevenLabs (opt-in later; never default - screen
contents can be anything).

## The CI gate, and what six builds cost

`--winrt-selftest` activates both namespaces from the FROZEN exe; the release
workflow runs it against `dist/Pipevoice/Pipevoice.exe` and fails the build.

**It found a real bug on the build it first worked:**
`SpeechSynthesizer.all_voices` returns an `IVectorView` from
`winrt.windows.foundation.collections` - a namespace that resolves transitively
in development and is **absent from the frozen exe**. Imports passed, OCR
activation passed, and it failed only at the one call that needed it. The
FastMCP shape exactly, caught in CI instead of on a user's machine.

**Five builds before that were the gate debugging itself.** Recorded because each
is a trap worth not repeating:

1. **It printed nothing.** The exe is built `--noconsole`, so stdout never
   reaches the CI log. A gate that fails without saying why is half a gate.
2. **It never waited.** `& $exe` does not wait for a GUI-subsystem binary, so
   `$LASTEXITCODE` is EMPTY - which is `-ne 0`, failing every build regardless.
   The MCP smoke test eight lines above already solved this with
   `Start-Process -PassThru`; reuse the pattern next to you.
3. **It waited for ever.** A bare `-Wait` on an exe that never exits hung the
   build 30+ minutes. Always `Wait-Process -Timeout`.
4. **A hang left no trail.** `winrt_selftest()` now writes a progress marker
   before each step, flushed and fsynced, because on a kill the file is the only
   surviving evidence.
5. **ROOT CAUSE: the flag was added to the wrong entry point.** PyInstaller
   freezes `launch.py`; `--winrt-selftest` went into `wisprlite/__main__.py`,
   which exists for `python -m wisprlite`. The exe fell through to `else:` and
   launched the TRAY APP, which never exits. Every earlier diagnosis was built on
   a gate that had never run once.

**`tests/test_entrypoints.py` pins the two dispatch tables together.** A flag in
only one works from source and silently does the wrong thing in the shipped exe.

**`tests/test_installer_version.py` asserts every WinRT namespace the code
touches is declared**, so the next one cannot go missing the same way.

## Still unproven
Whether `Windows.Media.Ocr` is good enough on real UI text - 9-11pt labels,
dark mode, low contrast. It is the Snipping Tool engine, tuned for documents.
The kill criterion from the plan stands: if it garbles that kind of text, cut
the feature rather than disappoint the people it is for.
