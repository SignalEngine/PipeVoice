# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Orientation: directory nesting

The invoked working directory (`.../Desktop/wisperlite`) is just a container. The actual
project lives one level down and the name drops an "e":

```
wisperlite/                 <- working dir (NOT a git repo)
  wisprlite/                <- project root + git repo (.git is here). Run all commands from here.
    wisprlite/              <- the Python package (app.py, engines/, ...)
    assets/  installer/  *.bat  launch.py  requirements.txt
```

Almost everything below assumes you have `cd wisprlite` first. This repo is the **desktop app only**.
The pipevoice.app website + marketing live in a separate private repo (`Powleads/pipevoice-site`).

## Commands (run from `wisprlite/`, Windows)

- **Run from source:** `run.bat` — first run creates `.venv`, installs `requirements.txt`, then runs `python -m wisprlite`. Subsequent runs just activate and launch. Equivalent manual launch after venv exists: `python -m wisprlite`.
- **Open the settings GUI:** `python -m wisprlite --settings` (the app spawns this as a *separate process*; see Architecture).
- **Transcribe an existing file:** `python -m wisprlite --transcribe` (also tray → "Transcribe a file…"). Local whisper by default; Deepgram with speaker labels when `DEEPGRAM_API_KEY` is set.
- **Build the single .exe:** `build_exe.bat` → `dist\WisprLite.exe` (PyInstaller onefile, no console, bundles all three engines incl. the local Whisper runtime).
- **Build the full installer:** `build_installer.bat` → `installer\Output\WisprLite-Setup.exe` (runs `build_exe.bat` first; needs Inno Setup 6 installed).
- **List microphones** (to set `device` in config): `python -m sounddevice`.
- **CI:** `.github/workflows/build.yml` builds the installer on push to `main`; a `v*` tag additionally publishes a GitHub Release with the installer attached.

There is **no test suite, linter, or formatter** configured — don't look for `pytest`/`tox`/`ruff` targets.

## Architecture (desktop app, `wisprlite/wisprlite/`)

> **Full reference: [`ARCHITECTURE.md`](ARCHITECTURE.md)** — complete module map, the per-utterance
> flow walk-through, engines/Voices/profiles, the Tk UI + shared theme, build/release, and a
> "Developing in this repo" section (incl. the Xvfb render-screenshot loop for verifying the UI).

`app.py` is the orchestrator and the state machine: **idle → recording → transcribing**. It wires
the hotkey to record → transcribe → type, and owns the tray icon and overlay. The README's
"Architecture" section maps every module; the load-bearing design decisions are:

- **Unified engine interface (`engines/base.py`).** Every engine implements `start_session(on_partial) -> Session`, and each `Session` has `feed(pcm_int16)` and `finish(audio) -> text`. The `Engine.streaming` class flag is the *only* branch in `app.py`: streaming engines (Deepgram) consume `feed` live and ignore the buffer in `finish`; batch engines (OpenAI, local Whisper) ignore `feed` and transcribe the whole buffer in `finish`. To add an engine, implement this interface and add a branch in `App._build_engine`.
- **Lazy, fault-tolerant imports.** Engines and heavy/optional deps are imported *inside* the functions that need them (`_build_engine`, `_finish`, etc.), wrapped in try/except. A missing optional dependency disables just that feature instead of crashing the app. Keep this pattern when adding code.
- **Config vs. secrets split (`config.py`).** Non-secret settings are a `@dataclass` persisted to `%APPDATA%\WisprLite\config.json`. **API keys are read from the environment / `.env` only and are never written to `config.json`.** `.env` is searched in cwd, the exe's dir, and the config dir. On first run only, `config.json` is seeded from `WISPRLITE_*` env vars.
- **Live config reload via a second process.** The settings window (`settings.py`, launched with `--settings`) runs as a *separate process* so it can't clash with the overlay's Tk thread. It just writes `config.json`. The main app's `_watch_config` polls that file's mtime every 1s and calls `_reload_config`. Hotkey/mode/output are read live through lambdas (no reload needed); the engine is only rebuilt (and re-prewarmed in a background thread) when an engine-affecting key changes.
- **Threading.** Hotkey runs a ~100 Hz polling loop (`hotkey.py`, uses the `keyboard` lib so combos and runtime key-swaps work uniformly). The overlay is a tkinter HUD on its own thread. Transcription runs on a throwaway thread. `App._busy` (a `Lock`) prevents overlapping utterances.
- **Single-instance lock.** `_acquire_single_instance` binds TCP `127.0.0.1:49517`; a second launch fails the bind and exits. This is what keeps the installer's startup shortcut and the tray autostart toggle from double-launching.
- **Offline fallback.** If a cloud engine raises during `finish` (e.g. no internet), `_fallback` retries the same audio once with local Whisper before giving up.
- **Optional LLM cleanup ("Flow mode").** When `ai_cleanup` is on and an OpenAI key exists, `cleanup.py` post-polishes the transcript. User `replacements` are applied *last* so they always win.

## Gotchas

- **Deepgram SDK is pinned to v3** (`deepgram-sdk>=3,<4` in `requirements.txt`) — v4 changed the API. `deepgram_engine.py` deliberately writes event handlers as `*args/**kwargs` (via the `_pick` helper) because callback signatures shifted across v3 minor versions; don't "tidy" them into fixed signatures.
- **Elevated terminals.** Windows blocks keystroke injection from a normal process into an *administrator* window. If the target terminal is elevated, WisprLite must run elevated too.
- **No Enter is sent by default** — text lands at the cursor for review (`auto_enter` config gates the hands-free send).
