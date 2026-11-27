# Build: PipeVoice Cloud v1 — desktop app (wisprlite repo)

Brain-reviewed: 2026-11-27 brain-written (Fable off); contract fixed, decisions settled.

## Header (/be-ambitious)
- Pro move: Cloud failures of any kind (cap, too long, outage, bad key) still produce text via the existing local fallback, with a clear message; the licence key never lands in `config.json`.
- Raw material: `wisprlite/engines/base.py`, `engines/groq_engine.py`, `engines/openai_engine.py` (`_wav_bytes`), `App._build_engine` and `App._fallback` in `wisprlite/app.py`, `wisprlite/usage.py` (small JSON state file pattern), `wisprlite/settings.py` (beta switch ~line 944 and save ~line 2190 as the pattern for a new control), `ARCHITECTURE.md`.
- Methods: stdlib `urllib.request` + `json` only (no new dependencies); lazy imports inside functions (repo rule); pytest with HTTP stubbed (monkeypatch `urllib.request.urlopen`).
- External check: pytest proves: 200 → text returned; 402/413/401/503/network error → exception raised so `_fallback` runs local Whisper; WAV > 4,400,000 bytes → no HTTP call, exception; licence file written outside `config.json`; sync picks the newer side. James's manual check later: paste a sandbox key on Windows with no `.env` and dictate.

## Contract
Read `vault/Plans/cloud-build-contract.md` first. It is binding.

## Files
1. `wisprlite/cloud.py` — licence + sync client:
   - State file `cloud.json` in `config.config_dir()` (same pattern as `usage.json`): `{key, activation_id, label}`. Functions `load()`, `save()`, `clear()`, `has_licence()`.
   - `activate(key)`: Polar activate (base `WISPRLITE_POLAR_API` env, default `https://sandbox-api.polar.sh` with a `# shortcut: sandbox default, switch to https://api.polar.sh and the production org id before the paid launch` comment), org id constant from the contract, `label` = PC name (`platform.node()`). On 200 save state; return `(ok: bool, message: str)` with plain messages for 403 ("This key is already on 3 PCs, or it was cancelled") and 404 ("Key not found").
   - `stt_url()` = env `WISPRLITE_CLOUD_URL` or `https://pipevoice.app/api/stt`; `sync_url()` likewise with `/api/sync`.
   - Sync: `sync_now(cfg)` → GET; if remote `updated_at` > local `cloud.json` `synced_at`, apply remote `vocabulary` + `replacements` to cfg and save cfg; else PUT local with `updated_at = now ms`; on 409 apply the returned remote data. Record `synced_at`. Every failure is swallowed and logged at debug level (sync must never break dictation). Sync only vocabulary and replacements in v1.
2. `wisprlite/engines/cloud_engine.py` — `CloudEngine(Engine)`, `name="cloud"`, `streaming=False`. Session `finish(audio)`: WAV via `_wav_bytes`; > 4,400,000 bytes → raise `RuntimeError("Clip too long for Cloud")` without calling HTTP. POST per contract with `lang` and `X-Prompt` (vocab, percent-encoded, cut to 800 chars), timeout 30 s. 200 → `text`. Map errors to `RuntimeError` with plain text: 401 "Cloud licence not valid", 402 "Cloud monthly limit reached", 413 "Clip too long for Cloud", others "Cloud unavailable".
3. `app.py` — add the `cloud` branch in `App._build_engine` (raise `RuntimeError("No Cloud licence")` when `cloud.has_licence()` is false, same style as the groq branch). Check `_fallback` already treats `cloud` as a cloud engine; change it only if it does not. Call `cloud.sync_now` on a background thread at start and after `_reload_config`, only when a licence exists.
4. `settings.py` — a "PipeVoice Cloud" section: key entry + Activate button + status line ("Active on this PC" / error message) + Remove button (`cloud.clear()`). Successful activation sets `cfg.engine = "cloud"`. Add `cloud` to the engine choices with the label "PipeVoice Cloud". Follow the existing theme/widgets; no new styling system.
5. `tests/test_cloud.py` — every bullet of the external check above.

## Not in this build
Minutes-used display, pricing links, admin, Mac. Do not touch `usage.py`, the updater or the beta switch.

## Gates
Write `GATES.md` at the worktree root: one CHECK/EXPECT oracle per external-check bullet (`python -m pytest tests/test_cloud.py -q`), plus "full suite failure count not above main's baseline". Main has known failures: run the suite on main first and record the count; `tests/test_transcribe_deepgram_e2e.py` crashes collection, so pass `--ignore=tests/test_transcribe_deepgram_e2e.py`. Commit GATES.md last.

## STOP rule
Any choice this brief and the contract do not settle → stop and report. No release, no version bump, no tag.
