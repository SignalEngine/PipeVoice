# Handover — PipeVoice, 27 Nov 2026 (from a session that ran in the BattleLifter workspace by mistake)

## Done
- Monetization research + James's decisions: `vault/Plans/monetization-2026-11-27.md` (Artifact: https://claude.ai/artifact/EdDTnS1KjCJqFbEiov7NBa — republish that same path, never a new link).
  - Supporter licence dropped. Build **PipeVoice Cloud**, £3/month or £25/year: no-key setup (our key), settings/vocab sync across PCs, early-access (beta) updates.
  - Speech-to-text for Cloud: Groq whisper-large-v3-turbo first, Deepgram backup.
  - anyIP proxy SDK rejected (rents out users' connections).
  - Claude-app "PipeVoice Lite": parked (dictation capability is null for users outside the org). Revisit as a Mac/Windows Cloud client after Cloud ships.
- **Anonymous daily install count** — page: `vault/Architecture/usage-count.md`.
  - Website (pipevoice-site PR #20, merged 4575645, live): `api/ping.js` (PFADD installs:<date>, always 204), `/admin` shows installs today/7d/30d, privacy page + ~30 pages rewritten.
  - App (PR #127, merged e9c605a): `wisprlite/usage.py`, on by default, Settings switch "Send an anonymous daily count", state in `usage.json`, raw re-read of the switch before sending.
  - Accepted risk (James): a stale whole-file settings save at app start can undo a just-saved "off" (worst case 1 extra count).
  - Released **v2.50.0 as a PRERELEASE** (beta only). Stable `/releases/latest` is still v2.49.29.

## Open (in order)
1. **Is Upstash configured on Vercel?** Unknown: `vercel env ls` hangs, and the API lookup failed. A live POST to pipevoice.app/api/ping returned 204, but it returns 204 either way. James was asked to open pipevoice.app/admin and see whether "Installs today" shows 1 (my test id 0123…cdef). If it is "not configured", send a key-drop link for UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN.
2. **Promote v2.50.0** after James installs it, About says 2.50.0, and a cold start works: `gh release edit v2.50.0 --prerelease=false --latest`, then check `gh api repos/SignalEngine/PipeVoice/releases/latest -q .tag_name` returns v2.50.0 (RELEASING.md).
3. **PipeVoice Cloud build plan:** Lemon Squeezy checkout + licence key, server proxy with our Groq key + Deepgram fallback, monthly usage cap, sync, beta channel. James must supply a Lemon Squeezy account and a Groq key (key-drop).
4. Tell James: 15 tests already fail on main (test_transcribe_window.py etc.; RELEASING.md says 7). Not caused by this work. `tests/test_transcribe_deepgram_e2e.py` crashes collection, so verify-build needs `--ignore` for it.
5. Site: `marketing/build_blog.py` output is out of step with `articles.json` (older problem). Re-running the generator would undo hand fixes.

## Gotchas
- Both repos use `main`, not master.
- review-gate needs a clean worktree and a class sweep file for every round after a P1 block.
- The jury cannot read pipevoice-site diffs that touch articles.json (one 870 KB line).
