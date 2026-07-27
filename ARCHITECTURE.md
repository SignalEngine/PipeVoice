# PipeVoice Architecture

PipeVoice is a free Windows voice-typing app: hold a hotkey, speak, and the transcribed (optionally LLM-polished) text is typed into whatever app has focus. It is a small Python desktop app — a single resident process that wires global hotkeys to a record → transcribe → polish → type pipeline, with a tray icon, an overlay HUD, swappable speech-to-text engines (cloud or fully offline), per-app/per-Voice overrides, silent self-update, and an optional Agent MCP server. This repo is the **desktop app only**; the marketing site lives in a separate private repo (see *Developing in this repo*).

> Brand note: the display name is **PipeVoice** (capital V) in human-facing copy, but every wire/file identifier — the `Pipevoice-Setup.exe` artifact, the PyInstaller `--name Pipevoice`, the updater `ASSET`, and the `Pipevoice-updater` User-Agent — stays lowercase-v **`Pipevoice`**. These must match across `build.yml`, `Pipevoice.iss`, and `updater.py` exactly or self-update silently no-ops. (Internally the Python package is still named `wisprlite`, a legacy name.)

## Module map

| File | Responsibility |
| --- | --- |
| `app.py` | Orchestrator. The `App` class: wires hotkeys → record → transcribe → output, owns tray + overlay, the per-utterance state machine, engine cache, config-reload watcher, single-instance lock, MCP bridge. |
| `engines/base.py` | `Engine` / `Session` interface; the load-bearing `streaming` flag. |
| `engines/gemini_engine.py` | Default free-tier batch engine (stdlib `urllib` → `:generateContent` with inline audio). |
| `engines/openai_engine.py` | OpenAI Whisper batch engine + shared `_OpenAISession` base; `_wav_bytes` helper. |
| `engines/groq_engine.py` | Groq batch engine — subclass of `OpenAIEngine` repointed at `GROQ_BASE_URL`. |
| `engines/deepgram_engine.py` | The only **streaming** engine (live websocket). |
| `engines/local_engine.py` | Offline `faster-whisper` batch engine; also the cloud-failure fallback. |
| `engines/transcribe.py` | Numpy-free file transcription (path in, `{text,…,segments}` out): local faster-whisper, plus `transcribe_file_deepgram` (cloud, diarized) behind the same shape. |
| `cleanup.py` | LLM "Flow mode" polish over an OpenAI-compatible client (OpenAI/Gemini/OpenRouter/Ollama). |
| `voices.py` | Named per-utterance override presets; legacy profile→Voice migration. |
| `profiles.py` | App→Voice matching + the `--profiles` editor. |
| `hotkey.py` | `HotkeyManager` — ~100 Hz polling hotkey loop (toggle / push-to-talk). |
| `audio.py` | `Recorder` — 16 kHz mono float32 capture; float buffer (batch) + int16 PCM frames (streaming). |
| `overlay.py` | Frameless tkinter "pill" HUD + VU meter + voice picker, on its own Tk thread. |
| `typer.py` | Text injection (paste/type), `press_enter`, `apply_replacements`, clipboard fallback. |
| `foreground.py` | Windows-only focused-window detection via `ctypes`; no-text-target check. |
| `config.py` | `Config` dataclass → `config.json`; the config-vs-secrets split; `.env` loading. |
| `welcome.py` / `keyprompt.py` / `star_prompt.py` | First-run splash, API-key dialog, GitHub-star nudge. |
| `tray.py` | pystray menu + state-colored mic icon. |
| `updater.py` | Silent GitHub-Releases self-updater (SHA-256 verified). |
| `about.py` | About window / Settings "About" tab + in-window update. |
| `mcp_shim.py` / `agent_bridge.py` | Agent MCP: ephemeral stdio shim + resident loopback `ControlListener`. |
| `vad.py` | Silence endpointer for hands-free MCP listen. |
| `settings.py` / `voices_editor.py` / `history.py` / `feedback.py` / `transcribe_window.py` | Out-of-process Tk windows. |
| `winui.py` / `branding.py` | Shared dark+coral `clam` theme, dark titlebar, wordmark lockup. |
| `launch.py` / `wisprlite/__main__.py` | Twin arg-dispatch entry points (PyInstaller vs `python -m`). |
| `build.yml` / `build_exe.bat` / `installer/Pipevoice.iss` | CI build + Inno Setup installer + release. |

## The core flow

The walk-through of one utterance (hold hotkey → record → transcribe → polish → type). There is **no explicit state enum**; "state" is the combination of `App._busy` (a `threading.Lock` held for one utterance), `App._session` (the live engine session or `None`), and the tray/overlay icon string (`idle`/`recording`/`transcribing`).

1. **Press.** The `HotkeyManager` poll loop (`hotkey.py:_loop`, ~100 Hz) sees the hotkey go down. In **push-to-talk** it fires `on_start` on press / `on_stop` on release; in **toggle** it flips on the rising edge. The hotkey/mode/pause are re-read every tick via injected lambdas, so settings changes take effect with zero re-registration.
2. **`_on_start` (`app.py:219`, hotkey thread).** Does a non-blocking `self._busy.acquire(blocking=False)`; if it fails it returns immediately — this is the **only** guard against overlapping utterances. It then captures the focused app *before* the overlay can steal focus (`foreground.detect()` → `self._fg_ctx`), resolves per-utterance overrides into `self._active` (armed/explicit Voice → `voices.resolve`; else `profiles.resolve` on the foreground context; else `{}`), opens the engine session via `_get_engine(self._active.get("engine"))`, beeps, sets icon `recording`, and shows the overlay.
3. **Record.** `self.recorder.start(on_frame=self._session.feed if engine.streaming else None)`. This one line is the entire streaming/batch fork: streaming engines (Deepgram) get live PCM frames piped to `session.feed`; batch engines get `on_frame=None` and only see audio in `finish`. `Recorder._callback` (audio thread) also updates `self.level` (RMS, fast-attack/slow-release) which the overlay reads for its VU meter.
4. **Release → `_on_stop` (`app.py:251`).** Stops the recorder, gets the concatenated float waveform + duration, and hands the buffer to `_finish` on a throwaway daemon thread so the hotkey thread never blocks on network/model.
5. **`_finish` (`app.py:260`) — transcribe.** Drops sub-`min_seconds`/empty audio, sets icon `transcribing`, calls `self._session.finish(audio)` (Deepgram flushes its websocket and ignores the buffer; batch engines transcribe the whole array). On exception, `_fallback` (`app.py:355`) retries once with a one-off `LocalEngine` if the active engine wasn't already local.
6. **Pipeline (order is load-bearing).** The transcript flows through, in this exact order: `commands.pre` on the **raw** text (so cleanup can't reword "scratch that" / "send it") → `_polish` (`app.py:410`, optional Flow-mode LLM cleanup) → `commands.inline` **after** polish (so inserted newlines survive) → `apply_replacements` **last** (so user word-fixes always win). Reordering silently breaks commands and "new line".
7. **Output.** The target is decided by `_eff("output_mode")` + `_clipboard_only` + `foreground.is_no_text_target`. `typer.type_text` either pastes (clipboard save → copy → `ctrl+v` → restore) or types; a positively-known no-text target (shell/desktop) routes to the clipboard instead of typing into the void; `press_enter` is the gated hands-free send.
8. **Cleanup.** `_finish`'s `finally` always resets `_session`/`_active`/`_fg_ctx` and `_release()`s the `_busy` lock — this is what returns the app to idle.

> Gotcha — new entry paths: `self._busy.acquire(blocking=False)` is the sole overlap guard. Any new entry path (new hotkey, agent op) must acquire it the same way and `_release()` in a `finally`, or you'll corrupt `_session`/`_active`.

### Per-utterance overrides (`_eff` / `self._active`, `app.py:121`)
`_eff(key)` returns `self._active.get(key, getattr(self.cfg, key))` — the per-utterance override if present, else the saved config. This lets a per-app profile or named Voice change engine/output/cleanup-style for one utterance **without ever mutating `self.cfg`**. Precedence is enforced implicitly by which resolver `_on_start` calls: an armed/explicit Voice (`app.py:231`) → `voices.resolve` (wins); otherwise `profiles.resolve` on the focused app (`app.py:233`); otherwise plain `cfg`. There is no merge — a Voice fully replaces a profile for that utterance.

### Voice hotkeys + picker
`_build_voice_hotkeys` (`app.py:135`) tears down existing managers then builds one `HotkeyManager` per `cfg.voice_hotkeys` entry (each arms a specific voice via `_on_start(voice=vn)`), plus an optional picker manager **forced to `"ptt"` mode** (toggle would skip every 2nd press). `_open_picker`/`_picker_loop` (`app.py:174`) show up to 6 voice names in the overlay and poll the `keyboard` lib for digit 1–6 or Esc, with an 8 s auto-cancel. A chosen voice is stored in `self._armed_voice`, consumed by the *next* `_on_start`, and auto-disarmed after 12 s by a `threading.Timer`; a timed-out picker just hides the overlay and arms nothing.

### Engine cache & config reload
`_get_engine` (`app.py:113`) memoises by name in `self._engines`; `_build_engine` (`app.py:69`) is the factory with one lazy-import branch each for `gemini`/`groq`/`openai` (legacy, off-UI)/`deepgram`/`local`, raising `RuntimeError` if the relevant key is missing. `_watch_config` (`app.py:678`) polls `config.CONFIG_PATH` mtime every 1 s (the settings GUI is a separate process that only writes the file). `_reload_config` (`app.py:699`) reloads `.env`, swaps `self.cfg`, and **only** drops/rewarms the engine cache when a key in the hardcoded `engine_keys` tuple changed — add an engine-affecting field and forget to list it there, and live edits won't apply until restart. It also rebuilds voice hotkeys when those entries change.

### Single-instance lock & `run` (`app.py:772`)
`_acquire_single_instance` binds TCP `127.0.0.1:49517`; a second launch fails the bind and exits — this is what stops the installer shortcut and tray autostart from double-launching. It's a socket bind, not a file lock, so it's released only when the process dies; a wedged process can leak it and block new launches. `run` migrates legacy profiles into Voices, prompts for a missing key, starts the overlay/tray/all hotkey managers/MCP bridge, prewarms the engine, spawns the config watcher, and blocks in a 0.2 s `_stop`-event loop until quit/Ctrl-C.

> Agent-MCP gotcha: when an agent-MCP listen is pending, `_finish` routes the transcript to the caller's Future and **discards** it instead of typing. On PTT timeout it deliberately **keeps** `_pending_agent_listen` set if an utterance is mid-flight, so in-flight speech isn't typed into the foreground app.

---

## Engines (speech-to-text backends)

`engines/` holds every transcription backend behind one tiny interface so `app.py` stays engine-agnostic. The contract (`engines/base.py`):

- **`Engine.start_session(on_partial) -> Session`** — `on_partial: Callable[[str], None]` is the overlay's "set live text" hook. `Engine` carries class attributes `name` and the load-bearing **`streaming`** flag.
- **`Session`** — `feed(pcm_int16: bytes)`, `finish(audio: np.ndarray) -> str`, `cancel()`. Base methods are no-ops/empty-string, so a batch engine simply doesn't override `feed`.

**The `streaming` flag is the only branch.** Streaming engines (Deepgram, `streaming=True`) consume `feed` live over a websocket, fire `on_partial` with interim text, and **ignore the buffer in `finish`**. Batch engines (everything else, `streaming=False`) ignore `feed` and transcribe the whole captured `np.ndarray` in `finish`. A new engine that gets this flag wrong will either record nothing or double-process.

**Audio conversion — `_wav_bytes`.** Every batch *cloud* engine needs WAV from the recorder's float array: clip to `[-1,1]`, scale to int16 LE (`"<i2"`), write a mono 16 kHz WAV into a `BytesIO` via stdlib `wave`. `SAMPLE_RATE = 16_000` must match the recorder. (Local Whisper skips this — it takes the float32 array directly.)

The engines:

- **`GeminiEngine`** (`gemini`, batch) — the free-tier default; one `GEMINI_API_KEY` powers both dictation and Flow-mode cleanup, so a new user works at zero cost. Default `gemini-3.1-flash-lite`. Gemini has no Whisper-style endpoint, so `finish` base64-encodes the WAV as an `inline_data` part to `:generateContent` with a "transcribe verbatim" prompt (`temperature: 0`), called **directly via stdlib `urllib.request`** (header `x-goog-api-key`) — no SDK bundled. `HTTPError` bodies are surfaced (truncated to 400 chars).
- **`OpenAIEngine`** (`openai`, batch) — `OpenAI()` client reads `OPENAI_API_KEY`; default `whisper-1`. `_OpenAISession.finish` posts WAV to `audio.transcriptions.create` with optional `language`/`prompt` (term biasing). Shared base for Groq.
- **`GroqEngine`** (`groq`, batch) — subclass of `OpenAIEngine` that repoints the same SDK client at `GROQ_BASE_URL`/`GROQ_API_KEY`, reusing `_OpenAISession` verbatim. Default `whisper-large-v3-turbo` (real Whisper, ~216× real-time, free dev tier).
- **`DeepgramEngine`** (`deepgram`, **streaming**) — the only `streaming=True` engine. Opens a live websocket (`listen.websocket.v("1")`, falling back to `listen.live.v("1")`), accumulates `_finals`. `feed` forwards raw `linear16` PCM; `finish` calls `conn.finish()` and waits on a `threading.Event` up to `finish_timeout` (6 s).
- **`LocalEngine`** (`local`, batch) — fully offline via `faster-whisper` (`WhisperModel`, default `base.en`, ~150 MB on first use). Loads once, pre-warmed at startup; `finish` feeds the float32 array straight in with `vad_filter=True`. Also the cloud-failure fallback.

**`transcribe.py`** is separate from dictation — file transcription for the Agent MCP and the `--transcribe` window. `transcribe_file(path, …)` takes a *path* (no numpy), lazily imports `faster_whisper`, returns `{text, language, duration, segments}` with word-level timestamps. One warm model is cached by `(model_size, device, compute_type)` and all calls serialize behind `_TRANSCRIBE_LOCK`.

`transcribe_file_deepgram(path, api_key=…)` is the cloud sibling, returning the **same dict shape** so callers can swap backends freely. It posts the file to Deepgram's prerecorded API with `diarize`/`paragraphs`/`smart_format`, and `_diarized_text` renders the returned paragraphs as `Speaker N:` blocks (merging consecutive paragraphs from one speaker, falling back to the flat transcript when diarization is absent). Unlike local whisper it needs a key and network, but it has no practical length cap and is fast enough for meeting-length audio — the size caps that block Gemini (~20 MB inline) and Groq/OpenAI (25 MB) do **not** apply here. It reaches the client via `listen.rest` with a `listen.prerecorded` fallback, the same dual-layout guard `deepgram_engine.py` uses.

**Gotchas:**
- Deepgram SDK is pinned `>=3,<4` (v4 changed the API). All handlers use `*args/**kwargs` + the `_pick()` helper because v3 minor versions shifted callback signatures and pass payloads positionally — do **not** tidy them into fixed signatures.
- `_wav_bytes` is duplicated **verbatim** in `openai_engine.py` and `gemini_engine.py` — fix one, fix both. `SAMPLE_RATE=16_000` in each must match the recorder or audio is pitch-shifted/garbled.
- Gemini deliberately uses stdlib `urllib` + native `:generateContent` with inline base64 audio — there is no `/audio/transcriptions` on Gemini. Inline audio caps at ~20 MB (~10 min @16 kHz); PTT never approaches it, but a longer-recording change would need the Files API.
- Groq subclasses `OpenAIEngine` and reuses `_OpenAISession` — changing `_OpenAISession.finish` silently changes Groq too.
- Heavy/optional deps (`openai`, `deepgram`, `faster_whisper`) are imported **lazily inside** `__init__`/`finish` so a missing dep disables one engine instead of crashing the app — keep this when adding engines.
- Deepgram `finish()` blocks up to `finish_timeout` (6 s) on a `threading.Event`; `on_transcript` swallows all exceptions (bad partials just don't update the overlay). Errors raised in `finish` get caught by the app's offline fallback.
- API keys come from env/`.env` only (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`; Deepgram key passed into the constructor) — never written to `config.json`.
- `transcribe_file_deepgram` must post `{"stream": fh}`, **never** `{"buffer": fh.read()}`. Measured on a 315 MB file: the buffer form peaks ~900 MB RSS (≈3× the file — the bytes object gets copied again downstream) versus ~5 MB streaming, so a 90-minute stereo WAV would OOM. `tests/test_transcribe_deepgram_e2e.py` asserts the full byte count arrives; it runs the real SDK against a local fake-Deepgram HTTP server, so it needs no key or network (and skips when the SDK isn't installed).
- `transcribe.py` is intentionally numpy-free and import-light (path in, lazy `faster_whisper`) for MCP/testability — don't couple it to the dictation `Session` or add numpy. It serializes behind `_TRANSCRIBE_LOCK` and must consume the lazy segment generator **inside** the lock; moving the join outside would race the shared warm model.
- Adding an engine requires both implementing this interface **and** adding a branch in `App._build_engine` — the interface alone won't wire it up.

---

## Polish, Voices & App Profiles

This subsystem turns a raw transcript into polished text and lets that polish (plus engine/output/auto-enter) be **overridden per utterance** by a named Voice — picked on demand or auto-applied by the focused app. Three files cooperate: `cleanup.py` (the LLM call), `voices.py` (the override preset), `profiles.py` (app→Voice matching + editor).

**`cleanup.py` — the polish.** `PROVIDERS` maps `openai`/`gemini`/`openrouter`/`ollama` to `(base_url, key_env, default_model)`. The trick: all four speak the OpenAI chat-completions API, so a single `from openai import OpenAI` client points anywhere by swapping `base_url`/`api_key`/`model` — enabling free (Gemini/OpenRouter free tiers) or fully offline (Ollama) polish. `provider_ready(provider)` is True when the key env var is set, or always for keyless Ollama (`key_env is None`). `clean(text, provider, model, language, notes, style, custom_instruction)` builds the system prompt as `_style_system(...) + _accent_clause(language) + _notes_clause(notes)`, sends raw text at `temperature=0`, and returns the trimmed reply (or `None` on any failure, so the caller falls back to raw text).

- **Styles** (`_style_system`, lowercased, defaulting to `tidy`): `tidy` → `_TIDY` (conservative grammar/filler/homophone fixes, no meaning change); `prompt` → `_PROMPT` (reshuffles rambling speech but hard-preserves negation/polarity/utterance-kind — a question stays a question); `custom` → user's `cleanup_instruction` + `_CUSTOM_RAILS`, **falling back to `_TIDY` if the instruction is empty**.
- **Clauses**: `_accent_clause` maps `_ACCENTS` codes (en-US/GB/AU/IN/NZ) to a "misheard because of that accent" hint (GB/AU also pin spelling); a non-`en` base language says "clean in that language, don't translate". `_notes_clause` injects the user's free-text `speech_notes`.

**`voices.py` — the override bag.** A Voice is a dict over `VOICE_KEYS` = `cleanup_style`, `cleanup_instruction`, `engine`, `auto_enter`, `output_mode`, `ai_cleanup`. `""` means "leave as-is" for strings; the two `_TRISTATE` keys (`auto_enter`, `ai_cleanup`) use `None` = "leave as-is". `resolve(cfg, name)` returns **only the non-empty / non-None keys** (tri-states coerced to `bool`), so unset dials fall through to `cfg`. `STARTER_VOICES` (Tidy, Social, Professional, Code/Prompt) all set `ai_cleanup: True` on purpose — picking a polish preset should polish even when global cleanup is off. `migrate_profiles(cfg)` is the one-shot, idempotent legacy bridge: each profile with `overrides` but no `voice` becomes a new Voice `"<exe-base> voice"` (deduped), the profile gets `voice=<name>` and its `overrides` dropped; returns True if anything changed (caller persists).

**`profiles.py` — app matching + editor.** `resolve(cfg, ctx)` walks `cfg.profiles`, lowercase-matches `ctx.exe`/`ctx.title` against each profile's `match.exe` (exact) / `match.title_contains` (substring), and on the **first hit** returns `voices.resolve(cfg, p["voice"])` — or, for legacy rows, the `overrides` filtered to `VOICE_KEYS`. `main()` is the `--profiles` editor: runs `migrate_profiles` on open, polls `foreground.detect()` (skipping `_NOISE_EXES`) into a live "Focused app" readout, and saves cards as `{name, match:{exe}, voice}`, reloading config first so it only rewrites `profiles`.

**Data flow into `App._active`.** On `_on_start`, a one-shot armed Voice wins (`voices.resolve`), else `profiles.resolve(cfg, self._fg_ctx)`, else `{}`; result is stored in `self._active` and **never mutates `cfg`**. Downstream reads `_eff(key)`. The engine is chosen via `_active.get("engine")`; `_polish` gates on `_eff("ai_cleanup")`, checks `provider_ready`, then calls `cleanup.clean` with `_eff("cleanup_style")`/`_eff("cleanup_instruction")` from the Voice but **provider/model/language/notes from `cfg` (global)**. `_active` is cleared at end of utterance.

**Gotchas:**
- `cleanup_provider`, `cleanup_model`, `language`, `speech_notes` are **global `cfg` only** — a Voice can change style/instruction but NOT which LLM or accent runs. Don't assume a Voice fully isolates polish.
- `custom` style with an empty `cleanup_instruction` silently degrades to `_TIDY` — a blank custom Voice looks like Tidy, not an error.
- Tri-state vs string: a string dial set to `""` or a bool dial set to `None` means "inherit"; `resolve()` drops those keys so `_eff` falls through to `cfg`. You **cannot force** a value with empty-string/`None` — only non-empty strings survive `resolve`.
- `STARTER_VOICES` force `ai_cleanup=True`; a Voice meant to **disable** polish must set `ai_cleanup=False` explicitly (not `None`).
- `migrate_profiles` only converts profiles with `overrides` AND no `voice`, and is idempotent. New code should write `voice`, never `overrides`. `profiles.resolve` still honors legacy `overrides` for back-compat — don't delete that branch.
- Profile match is **first-hit, order-sensitive** in `cfg.profiles`; an exact-`exe` and a broad `title_contains` rule can shadow each other depending on list order.
- An armed/picked Voice always overrides app-profile matching — profile resolve is skipped entirely when a Voice is armed.
- The editor's `save()` reloads config and only rewrites `profiles` as `{name, match:{exe}, voice}`; new per-profile fields must be threaded through `add_card`/`save` or they're dropped.

---

## I/O Layer (hotkey, audio, overlay, typer, foreground)

This is the boundary between the state machine and the OS. Everything here is **fault-tolerant and non-blocking** — callbacks wrapped in try/except, OS-specific failures degrade to a no-op rather than crash, nothing blocks `app.py`'s threads. `app.py` owns these objects and feeds them callbacks/lambdas so config can change at runtime without rebuilding them.

**Hotkey → record (`hotkey.py`).** `HotkeyManager` runs a daemon polling loop (`_loop`) at ~100 Hz (`time.sleep(0.01)`) instead of registering OS hooks. Polling is the key design choice: it treats single keys and combos (`"ctrl+alt"`) uniformly via `_all_pressed` (splits on `+`, requires `keyboard.is_pressed` for every part), and re-reads the hotkey/mode **every iteration** through the injected `get_hotkey`/`get_mode` lambdas, so a settings change applies with no re-registration. It holds no config of its own. **Toggle** flips state on the rising edge (`pressed and not prev`); **push-to-talk** mirrors the key (`on_start` on press, `on_stop` on release). `is_paused()` is checked first each tick; if paused mid-record it cleanly fires `on_stop` once then idles at 20 ms. `_safe` wraps every callback so an exception in `app.py` can never kill the loop.

**Audio capture (`audio.py`).** `Recorder` opens a `sounddevice.InputStream` at 16 kHz mono float32 (`SAMPLE_RATE`/`CHANNELS`), 50 ms blocks (`BLOCKSIZE=800`). `start(on_frame)` resets the buffer and arms an optional streaming consumer. In `_callback` (audio thread), per block: updates `self.level` (RMS, fast-attack/slow-release: `rms if rms > level else level*0.7`), appends a `.copy()` of the float buffer under `_lock`, and — if a streaming engine is attached — converts to clipped int16 LE PCM and pushes via `on_frame`. `stop()` tears down the stream and returns the concatenated float waveform (empty array if nothing captured). Float buffer feeds batch engines; int16 `on_frame` feeds streaming ones — both paths coexist by design.

**Overlay HUD (`overlay.py`).** A frameless tkinter "pill" running its own `mainloop` on a daemon thread; `app.py` talks to it only through a thread-safe `queue.Queue` (`show`/`set_state`/`set_text`/`hide`/`show_picker`/`stop` just enqueue tuples). The Tk thread's `tick` (every `FRAME_MS=33`, ~30 fps) calls `drain()` to apply queued messages to the `st` dict, auto-conceals when `hide_at` elapses (done/error windows auto-hide after 1.4 s/2.2 s), then `_draw`s. States map to accent colors (`ACCENT`): listening/transcribing/error/done/idle/picker. While `listening`, `_draw` polls `level_provider()` (wired to `Recorder.level`) into a rolling `hist` for the VU bars. The voice picker reuses the same window: `resize()` grows the pill upward (geometry from `sh - h - 60`) to give each of up to 6 voices a numbered line. Rounded corners use `-transparentcolor` color-keying (`TRANSPARENT="#010203"`), falling back to an opaque bg if unsupported. If tkinter import fails the overlay is silently a no-op.

**Text injection (`typer.py`).** `type_text` has two paths: `mode="paste"` saves the clipboard, `pyperclip.copy`s the text, sends `ctrl+v`, then restores the old clipboard — robust for fast/Unicode-heavy output; `mode="type"` uses `keyboard.write`. `PASTE_TIMINGS` (fast/normal/slow) tune the sleeps around paste / clipboard-restore / Enter, because injection races the target app's clipboard and input handling. `press_enter` is the gated hands-free send. `apply_replacements` runs user word-fixes as case-insensitive `\b`-bounded regex (applied **last** so they always win). `copy_clipboard` is the no-text-target fallback (copy only, no keystrokes).

**Foreground detection (`foreground.py`).** Windows-only via `ctypes`; everything returns `{}`/`[]` off Windows so the app stays cross-runnable. `detect()` returns `{exe,title,cls}` for the focused window; `list_windows()` enumerates visible titled top-level windows for the picker, deduped by exe, filtering `_NOISE_EXES`. The critical `_init_prototypes` declares `restype`/`argtypes` for every user32/kernel32 call: **without this, 64-bit HWNDs/handles get truncated to 32-bit int on Win64**, `GetForegroundWindow`/`OpenProcess` return garbage, the exe comes back empty, and no profile ever matches. `is_no_text_target` is deliberately conservative — True only when it *positively* knows there's no caret (shell `_SHELL_CLASSES`, or desktop = explorer.exe with empty title); on anything unknown it returns False so a real app's text is never wrongly diverted (Chromium apps hide their caret, so guessing would misfire).

**Gotchas:**
- The hotkey loop reads `get_hotkey`/`get_mode`/`is_paused` lambdas **every tick** — don't cache them or add a reload step; that's how runtime key/mode changes work with zero re-registration.
- All overlay public methods only **enqueue**; they never touch Tk. Never call tkinter from `app.py`'s thread — cross-thread Tk access crashes. Add HUD behavior by handling a new queue `kind` inside `drain`.
- `Recorder.level` is written from the audio-callback thread and read from the overlay thread **without a lock** (intentional benign float race); `_frames` *is* lock-protected. Don't move the level update under `_lock` or read `_frames` without it.
- `_init_prototypes` runs once at import under a bare try/except. Any **new** user32/kernel32 call must add its `restype`/`argtypes` here or it silently breaks on Win64 (truncated handle → empty exe → no profile match) — it won't raise.
- `_NOISE_EXES` includes `pipevoice.exe`/`python.exe`/`pythonw.exe` so the app never lists itself in the picker — keep these when renaming/repackaging the exe.
- Paste-mode clobbers and restores the clipboard around `ctrl+v`; if `PASTE_TIMINGS` is too tight the user can paste stale/wrong content. `apply_replacements` must stay **last** in the pipeline.
- `is_no_text_target` returns True ONLY on positively-known no-caret cases; unknown → False on purpose so real apps (esp. Chromium) aren't wrongly diverted to clipboard.
- Rounded corners use color-key `#010203`; never draw that color, and don't exceed the 6-voice picker cap used in both `resize()` and `_draw`.
- `keyboard` injection into an **elevated/admin** window is blocked by Windows unless PipeVoice runs elevated — a no-op injection there is OS policy, not a `typer.py` bug.

---

## Config, First-Run, Tray, Updater & Agent MCP

The unifying rule (in `CLAUDE.md`, enforced in `config.py`) is the **config-vs-secrets split**: non-secret settings live in a JSON file the tray/settings process can rewrite at runtime; API keys live only in the environment / `.env` and are **never** written to that JSON.

**Config (`config.py`).** `Config` is a `@dataclass` persisted to `%APPDATA%\Pipevoice\config.json` via `config_dir()`/`CONFIG_PATH`. `Config.load()` instantiates defaults, then overlays any keys present in the file (`hasattr`-guarded, so unknown keys are ignored and missing keys keep their default — schema-additive). On a *missing* file only, it seeds a handful of fields from `WISPRLITE_*` env vars and calls `save()`, so the file's existence is the "have we run before?" signal. `_load_env()` runs at import time, loading `.env` from cwd, the exe dir (`sys.executable`'s parent), and the config dir — `override=False` so a real OS env var always wins over a file. Secrets are read live via `gemini_key()`/`groq_key()`/`openai_key()`/`deepgram_key()`/`openrouter_key()` (each `os.getenv(...).strip()`), never stored on the dataclass. `save_api_key(env_name, value)` is the **only** writer of `.env`: it sets `os.environ` immediately (so the running process sees it without restart) and rewrites the `.env` line in place. Key fields the rest of the subsystem reads: `auto_update`, `last_version`, `launches`, `star_prompt_shown`, `key_prompt_skipped_for`, and the MCP block (`mcp_enabled`, `mcp_port=49518`, `mcp_default_mode`, `hands_free_silence_ms`, `transcribe_model_size`). `asset_path()` resolves bundled assets under both source and PyInstaller (`sys._MEIPASS`).

**First run (two distinct gates).**
- `app.py:main()` checks `not config.CONFIG_PATH.exists()` → `autostart.enable()`, `Config.load()` (writes the file, so this branch never fires twice), then `welcome.show_welcome()` (tutorial splash, `False`-safe when headless) and, on "Get started", `settings.main(first_run=True)`.
- In the running app's start sequence (`~app.py:787`): `keyprompt.ensure_api_key(cfg)` shows the key dialog **only** when the selected cloud engine has no key AND the user hasn't dismissed it for that engine (`key_prompt_skipped_for`). "Skip"/close stamps `key_prompt_skipped_for = engine`; "Use offline" switches `engine="local"` and clears the skip; "Save & start" calls `config.save_api_key()`. Then `star_prompt.maybe_show(cfg)` increments `launches` every start and fires the one-time GitHub-star nudge exactly once on the 3rd launch (`_AFTER_LAUNCHES=3`), flipping `star_prompt_shown=True`. All three dialogs are short-lived Tk roots on the main thread, fully torn down before the overlay's Tk thread starts, and each swallows exceptions so a headless/no-Tk environment can't block startup.

**Updater (`updater.py`).** Silent GitHub-Releases auto-updater for the per-user Inno Setup install — no server, no UAC. `check()` hits `releases/latest`, compares `tag_name` to `__version__` via `_parse_version()` (tuple compare, tolerant of `v` prefixes/junk), returns the `Pipevoice-Setup.exe` URL plus its `.sha256`. `download_and_run()` downloads (one retry), verifies SHA-256 (the app is unsigned, so this is the tamper check; mismatch aborts and deletes), and spawns the installer detached with `/VERYSILENT /FORCECLOSEAPPLICATIONS /RESTARTAPPLICATIONS` — forcing closure is required because Tk/pystray ignore Windows Restart-Manager close requests and would otherwise deadlock the file swap. `app.py:check_for_updates()` runs this on a daemon thread (on startup when `auto_update`, and on tray "Check for updates"), calling `self.quit()` once the installer launches. `last_version != __version__` on startup is how a just-applied update is detected and announced. `latest_release()`/`recent_releases()`/`info_from_latest()`/`is_newer()` back the About window; `cleanup_old()` deletes a stale downloaded installer.

**Tray (`tray.py`).** `Tray` builds the pystray menu (engine/mode/output radio groups bound to `app.set_*`; toggles for overlay/sounds/autostart/**Agent MCP**/pause; Settings/History/Profiles/About/Feedback/Update/Quit). It draws its own state-colored mic icon (`_state_image`) and degrades to a no-op (`self.ok=False`) if pystray/Pillow are missing. `set_state()` recolors on idle/recording/transcribing/error; `update()` refreshes checkmarks after a config change.

**About (`about.py`).** `build(container, root, wheel)` populates any frame (reused as both `--about` window and the Settings "About" tab); `main()` wraps it in its own Tk process. It loads `recent_releases(8)` once to power **both** the version-status line and the changelog (so they can't disagree), renders cleaned notes (`_clean_notes`), and offers an in-window "Update now" that calls `updater.download_and_run` then `_exit_for_install()` (`os._exit(0)` to release file locks so the installer can replace the exe).

**Agent MCP server — two processes.** (1) `mcp_shim.py` (`--mcp`) is an *ephemeral* stdio `FastMCP("pipevoice")` server registered with an MCP client (e.g. `claude mcp add pipevoice -- python -m wisprlite --mcp`). It exposes `listen(prompt, timeout_seconds, mode)` and `transcribe(path, format, language, model_size)` and does no real work — each call is forwarded over the loopback bridge via `agent_bridge.send_request(port, {...})`, keeping heavy MCP machinery out of the resident app. (2) The resident app runs `agent_bridge.ControlListener` on `127.0.0.1:mcp_port` (49518, distinct from the 49517 single-instance lock), started by `start_mcp_bridge()` when `mcp_enabled` (toggled via tray → `toggle_mcp()`, which prints the `claude mcp add` line). It speaks newline-delimited JSON, one request per connection, dispatching to `App._agent_dispatch` → `on_agent_listen`/`on_agent_transcribe`. `listen` push-to-talk arms `_pending_agent_listen` and waits on a `Future` resolved by `_finish()` (transcript routed to the caller, not typed); `hands_free` mode records and endpoints on trailing silence (`vad.SilenceEndpointer`). `transcribe` runs `engines.transcribe.transcribe_file` (offline local whisper; `srt`/`vtt` produce a `captions` string). The shim maps `socket.timeout` → `{"status":"timeout"}` and `OSError` → `{"status":"app_not_running"}`.

**Routing (`launch.py` + `wisprlite/__main__.py`).** Thin `sys.argv` switchboards picking a `main()` by flag: `--settings`/`--history`/`--about`/`--profiles`/`--voices`/`--transcribe`/`--mcp`/`--feedback`, else `app`. `launch.py` uses absolute `wisprlite.*` imports (PyInstaller entry); `__main__.py` uses relative imports (`python -m wisprlite`). The tray's `open_*` methods spawn these as **separate processes** (frozen: `sys.executable --flag`; source: `pythonw -m wisprlite --flag`) so child GUIs never share the overlay's Tk thread.

**Gotchas:**
- Two copies of the routing table must stay in sync: `launch.py` (absolute imports, PyInstaller entry) and `wisprlite/__main__.py` (relative imports, `python -m`). Add a flag to one → add it to the other.
- `config.json`'s mere existence is the first-run flag. `Config.load()` writes it on first call (in `main()` before welcome), so the env-var seeding branch runs at most once — and the welcome/keyprompt splashes won't re-show after that even if the user cancels them.
- Secrets are **never** on the `Config` dataclass — they're read live via `gemini_key()` etc. Never add an api-key field to `Config` or `save()` will write it to `config.json`. `save_api_key()` writes both `os.environ` and `.env`; editing only one breaks the live process or persistence.
- `_load_env()` uses `override=False`, so a real OS env var beats `.env` — a key set in the shell can silently shadow one saved by `save_api_key()`.
- MCP port (49518) is deliberately distinct from the single-instance lock (49517). `ControlListener` binds with `SO_REUSEADDR` and re-reads its actual port after `listen()` (it supports port 0 / ephemeral), so don't assume the bound port equals `cfg.mcp_port` if you pass 0.
- The MCP shim is throwaway and only proxies; it does nothing if the resident app isn't running or `mcp_enabled` is off — it returns `app_not_running`, not an error. The tray toggle is the only thing that starts/stops the bridge.
- `listen` PTT timeout intentionally **leaves** `_pending_agent_listen` set if the user is mid-utterance (`_busy` locked) so `_finish()` discards that utterance instead of typing it into the foreground app; only disarm logic depends on this — don't "simplify" it.
- Updater spawns the installer with `/FORCECLOSEAPPLICATIONS` because Tk/pystray ignore Windows Restart-Manager close requests; without it the install deadlocks on locked `python311.dll`/`Pipevoice.exe`. About's in-window update additionally calls `os._exit(0)` for the same lock reason — a graceful `root.destroy()` alone is not enough.
- The app is unsigned, so the `.sha256` check in `download_and_run()` is the only tamper protection; if the `.sha256` asset is missing from a release, verification is skipped (`sha256=""`) and the installer runs unverified.
- `star_prompt` counts **every** launch (including ones where config already exists) and fires once at launch #3; `launches`/`star_prompt_shown` live in `config.json`, so deleting that file resets the nudge.
- Tray degrades silently to a no-op when pystray/Pillow are missing (`ok=False`) — there is then no menu and no way to reach Settings/MCP except via CLI flags.
- `keyprompt` only triggers for engines in its PROVIDER map (`gemini`/`groq`/`deepgram`). `openai` is legacy (`has_key` handles it) and `local` needs no key, so changing the default engine to something outside PROVIDER skips the key prompt entirely.

---

## The Tk UI & shared theme

PipeVoice's GUI is intentionally **out-of-process**. The running app already owns a Tk root for the overlay HUD on a background thread; a second Tk root in the same process across threads is unstable, so every window is launched as a fresh process via the `launch.py` / `__main__.py` arg-dispatch table. The two entry files are byte-for-byte twins except for absolute-vs-relative imports (`launch.py` = PyInstaller, `from wisprlite.…`; `__main__.py` = `python -m wisprlite`, `from .…`). Keep both in sync when adding a flag.

**Settings window (`settings.py:main`).** One scrolling Tk form that reads `Config.load()` into a wall of `tk.StringVar`/`BooleanVar`, then writes back through `save()`. The chrome is hand-rolled because ttk Notebook tabs render badly on `clam`: a custom underline tab bar (`_show_tab` swaps `pack_forget`/`pack` on five sibling frames — Settings/Voices/History/Guide/About — and recolors the label + 2px underline). The form is built from a small kit of closures drawing onto dark `CARD` panels: `card(title, subtitle)`, `row()` (label-left/control-right), `stack()` (label-over-control), `check()`, `combo()`/`entry()`, `_divide()` hairlines. Dropdowns store `(value, label)` tuples, converted via `value_for(var, table)` on save. `save()` maps every var to a `cfg` field, writes non-secret settings via `cfg.save()`, and routes API keys to `config.save_api_key(...)` (`.env`, never `config.json`). Two fields — `cfg.profiles` and `cfg.voices` — are **re-loaded from disk inside `save()` before writing** so a concurrent Profiles/Voices window save isn't clobbered (last-writer-wins is scoped per subsystem). `PV_TAB` env var (`_show_tab(os.getenv("PV_TAB") or "Settings")`) is a render/test seam to open straight to a tab. Hotkey "Capture" buttons spawn a daemon thread running `keyboard.read_hotkey()` and marshal the result back with `root.after(0, …)`.

**Transcribe-a-file window (`transcribe_window.py:main`, `--transcribe`).** Pick any audio/video file, pick a backend, get text. Two backends behind one `_run()`: `local` (offline faster-whisper, no key, CPU-bound — minutes on a long file) and `cloud` (Deepgram, needs `DEEPGRAM_API_KEY`, fast, returns `Speaker N:` labels). The backend combo defaults to Deepgram when a key exists, else local. Work runs on a daemon thread and results come back through `root.after` — **never touch Tk from the worker**. `tk.Text` and `Vertical.TScrollbar` need their bevel suppressed locally (`borderwidth`/`highlightthickness` 0, and the `lightcolor`/`darkcolor`/`bordercolor` trio) because `winui.apply_theme` only reaches ttk widgets and doesn't style Scrollbar.

**Voices editor (`voices_editor.py:main`, `--voices`).** CRUD over `cfg.voices`. Each card edits one Voice (name, style, engine, output, auto-enter, AI-cleanup, custom instruction); empty/"(leave as-is)" fields mean "don't override the global". `save()` follows the same defensive pattern: `Config.load()` fresh, set only `.voices`, `.save()`. Tooltips via `winui.tooltip`.

**Shared theme (`winui.py`).** `apply_theme(root)` is the single source of the dark+coral `clam` ttk theme (palette in `winui.PALETTE`). Its load-bearing job is killing clam's default **white 3D bevel** on `TEntry`/`TCombobox`: it sets `lightcolor`/`darkcolor`/`bordercolor` to the card color on the base `.` style and per-widget, with coral on `("focus", …)`. It re-themes the combobox dropdown (a plain Tk `Listbox` ttk doesn't touch) via `root.option_add("*TCombobox*Listbox.…")`. `dark_titlebar(root)` uses DWM (`DwmSetWindowAttribute` attrs 20→fallback 19 for immersive dark mode, 35 for Win11 caption color, BGR-packed) and is a silent no-op off-Windows. `branding.py:lockup_label` shows the `pipevoice-lockup.png` wordmark via `tk.PhotoImage` (Tk 8.6 reads PNG natively, so no Pillow dependency just for the logo) and falls back to a coral text label.

**Gotchas:**
- `settings.py` `save()` re-reads **only** `cfg.voices` and `cfg.profiles` from disk to avoid clobbering sibling windows; all other keys are still last-writer-wins with no field-level merge.
- Live reload triggers off `config.json` mtime (~1 s poll); API keys save to `.env` (not `config.json`), so a key-only change isn't picked up until some `config.json` field also changes.
- `winui.apply_theme`'s `lightcolor`/`darkcolor`/`bordercolor` settings suppress clam's white 3D bevel on Entry/Combobox; removing them or leaving `clam` re-introduces it. The dropdown `Listbox` is themed separately via `option_add`, not the ttk style.
- `PV_TAB` opens the settings window to a given tab (render/test seam) and silently falls back to "Settings" on unknown values — it's not a user setting.
- GUI windows run as separate processes by design (two Tk roots in one process across threads is unstable); the launcher spawns them via `sys.executable` (frozen) or `pythonw -m wisprlite` (source).
- `launch.py` and `__main__.py` are twin arg-dispatch tables; add a flag to one and forget the other and the feature works from source but not the exe (or vice-versa).

---

## Build & release

CI (`build.yml`) runs on `windows-latest`: `pip install -r requirements.txt pyinstaller`, generate the icon, then PyInstaller `--onedir` (**not** onefile — onefile's `_MEI` runtime extraction broke self-updates with "Failed to load Python DLL"). The `--collect-all` flags (`deepgram`, `faster_whisper`, `ctranslate2`, `pystray`, `PIL`, `mcp`, `pydantic`, `anyio`) force-bundle packages whose data files / dynamic imports PyInstaller's static analysis misses — drop one and that engine/feature silently breaks **only in the built exe**, not from source. Inno Setup then compiles `installer/Pipevoice.iss` → `installer/Output/Pipevoice-Setup.exe` (per-user, `PrivilegesRequired=lowest`); CI computes a `.sha256` sidecar. On a `v*` tag, the annotated tag message becomes the release body and `softprops/action-gh-release` publishes the Release with `Pipevoice-Setup.exe` + `.sha256` attached. The in-app `updater.py` polls `releases/latest`, compares `__version__` (in `wisprlite/__init__.py`), downloads `ASSET = "Pipevoice-Setup.exe"`, verifies SHA-256, and runs it `/VERYSILENT`; the `.iss` `[Run]` re-launches the app post-silent-install (postinstall is skipped when silent), made safe by the single-instance TCP lock.

**Gotchas:**
- **Three version strings.** `wisprlite/__init__.py` `__version__` (currently `2.30.2`) is the only one the updater compares; the `.iss` `AppVersion` (`2.25.0`) is just the installer's Add/Remove-Programs label and is already stale. Bump `__init__.py` for a release; don't trust `.iss` as the source of truth.
- **`Pipevoice` vs `PipeVoice` is load-bearing.** Renaming the asset/UA/`--name` to the "PipeVoice" display casing breaks the updater's `name == ASSET` match and the release filename CI uploads. Display copy may say PipeVoice; identifiers stay lowercase-v.
- **`--onedir`, never `--onefile`.** The comment in `.iss` is a scar: onefile's `_MEI` extraction breaks self-update. The `[Files]` section bundles the whole `dist/Pipevoice/` folder (exe + `_internal/`).
- The theme bevel fix is fragile; the `--collect-all` list is fragile (see above).

---

## Developing in this repo

- **Repo split.** This repo is the **desktop app only** (public, star-ready). The `pipevoice.app` website + marketing live in a **separate private repo** (`Powleads/pipevoice-site`, Vercel-deployed). Don't add web/marketing here.
- **Dev-box caveat.** `numpy`/`sounddevice` are **not** installed on the Linux dev box, so `app.py` **can't be imported there**. The Tk windows **do** render, though (`python -m wisprlite --settings|--voices|--profiles`). `cleanup`/`voices`/`profiles`/`config`/`foreground` all import fine without numpy. Verify code with `python3 -m py_compile` plus running the plain `tests/test_*.py` files directly.
- **Render + screenshot the UI (Xvfb).** When changing any UI: start `Xvfb :99`, run `python -m wisprlite --settings` under `DISPLAY=:99` (`PV_FAKE_WINDOWS` seeds the profiles app list; `PV_TAB=Voices` opens a tab), capture with `import -window root out.png`, then `Read` the PNG. Screenshot-verify any UI change — don't trust the code alone.
- **Tests.** Live in `tests/` (`test_voices`, `test_profiles_style`, `test_cleanup_styles`, `test_*`). **No pytest/framework, no CI test step** — run each file directly (`python3 tests/test_x.py`), manually. Tests that need `faster_whisper`/`deepgram` (absent on the dev box) stub the engine module instead — see `test_transcribe_window.py`.
- **Always run codex-review.** Standing instruction: run the `codex-review` skill on any substantive code change before declaring it done, and screenshot-verify UI changes.
- **Release ritual.** Bump `wisprlite/__init__.py` `__version__` → merge to `main` → push an **annotated** tag `vX.Y.Z`. CI builds and publishes the GitHub Release with `Pipevoice-Setup.exe`. Then set the changelog with `gh release edit <tag> --notes-file …` (the CI tag-message auto-notes is flaky). Installed apps auto-update from the latest Release.
