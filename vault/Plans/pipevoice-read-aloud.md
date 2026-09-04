
---

## Build handoff (decisions already made — do not re-litigate)

1. **`winrt-*` packages at 3.2.1, NOT `winsdk`.** `winsdk` is 1.0.0b10, a beta.
   Add to `requirements.txt`: `winrt-runtime`, `winrt-Windows.Media.Ocr`,
   `winrt-Windows.Media.SpeechSynthesis`, `winrt-Windows.Graphics.Imaging`,
   `winrt-Windows.Storage.Streams`, `winrt-Windows.Foundation`.
   **Windows-only markers** (`; sys_platform == "win32"`) or the Linux test suite
   cannot install.
2. **`--winrt-selftest` flag on the app**, mirroring the existing `--mcp` shape:
   activates both namespaces, prints one PASS/FAIL line, exits. CI runs it against
   the built exe and fails the build on non-zero. This is spike 1, permanently.
3. **Speak ALWAYS**, even with NVDA/JAWS running. Settings toggle
   "Stay quiet while a screen reader is running", **default OFF**.
4. **Capture modes:** hotkey = focused window (default), +Shift = whole screen,
   +Ctrl = drag a region via the existing `screenrec.select_region()`.
   Three modes. No "re-OCR last region".
5. **No custom UI Automation provider.** Ordinary Tk in the overlay.
6. **Clipboard copy on by default**, with a Settings toggle, and the overlay says
   it copied. Never silently clobber.
7. **`Windows.Media.SpeechSynthesis`, not SAPI** — reaches the modern natural
   voices SAPI's legacy voices do not.
8. **Capture never touches disk.** Screen contents can contain anything.
9. **Do NOT build Piper or ElevenLabs.** Deferred with reasons above.
10. Everything degrades: no OCR engine, no voices, or a WinRT failure must
    produce a clear message and leave dictation working. Read Aloud is additive
    and must never be able to break the app that already works.

**Tests must be importable on Linux** — guard every `winrt` import behind a
runtime check and test the pure logic (mode selection, text cleanup, CER-free
paths) with the WinRT layer mocked, exactly as the Deepgram tests do.
