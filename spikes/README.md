# Spikes — run these BEFORE building Read Aloud

Both need Windows. Neither can run on the VPS: no display, no Windows, no audio.

The plan they gate: `vault/Plans/pipevoice-read-aloud.md`.

## Spike 1 — can a frozen exe activate WinRT? (~30 min)

```powershell
pip install pyinstaller winrt-runtime winrt-Windows.Media.Ocr `
    winrt-Windows.Media.SpeechSynthesis winrt-Windows.Graphics.Imaging `
    winrt-Windows.Storage.Streams winrt-Windows.Foundation
pyinstaller --onedir --collect-all winrt spikes\spike1_winrt_frozen.py
```

Then copy `dist\spike1_winrt_frozen\` **into Windows Sandbox** and run the exe
there. The sandbox is the point: a machine with no Python and no dev tooling is
the only honest test of whether the bundle is self-contained.

**Why this exists:** verifying the packages on PyPI is a *version* check. This is
the *bundling* check. The same half-verification shipped a broken exe this week —
`--collect-all` silently missed FastMCP's module-level imports and the app died
with `ModuleNotFoundError` on a real machine.

- **PASS** → both namespaces activate, at least one voice listed. Build it.
- **FAIL on import** → needs a custom PyInstaller hook. That is a whole session
  before any feature exists; add it to the estimate.
- **FAIL with "no engine for this profile's languages"** → that is a language-pack
  result, not a bundling failure. Note which one you got; they mean different things.

## Spike 2 — is Windows OCR good enough on real UI text? (~1 hour)

```powershell
mkdir shots
# Screenshot five things, then type out what each ACTUALLY says into a .txt
# beside it (that is the ground truth — it has to be typed, not OCR'd):
#   dark-ide.png / .txt        dark-mode editor, small code text
#   small-web.png / .txt       web app at default zoom, 9-11pt labels
#   low-contrast.png / .txt    modal with grey-on-grey body text
#   high-contrast.png / .txt   Windows high-contrast theme
#   data-table.png / .txt      dense table, tight rows
python spikes\spike2_ocr_accuracy.py shots\
```

Prints a character-error-rate per category and writes what the OCR actually saw
to `<name>.ocr.txt` so a bad number can be read rather than guessed at.

**KILL CRITERION: >25% CER on any category.** Below that bar the feature
disappoints exactly the people it is for. The answer then is Tesseract (an extra
install, with its own SmartScreen cost) or not shipping — never shipping anyway.

## Reporting back

Paste the console output. Both spikes print a single `RESULT:` line, so the
answer is not a judgement call.
