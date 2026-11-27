# PipeVoice Cloud v1 — build plan

*27 Nov 2026 · brain-written (Fable is off) · status: DRAFT, waiting for James on the open decisions*

**Goal:** a paid tier, £3/month or £25/year. A payer types a licence key into Settings and dictation works with no API key of their own. Their settings and vocabulary follow them to another PC.

Decisions behind this: [monetization plan](monetization-2026-11-27.md).

## Plan header (/be-ambitious)

- **What a top professional adds:** a hard cost ceiling per user, enforced on the server *before* the Groq call, so one heavy user can never cost more than they pay. Licence checks that keep working for a few hours if Lemon Squeezy is down, so an outage never stops someone dictating. A kill switch for the whole Cloud proxy.
- **Rawest material:** the Groq and Lemon Squeezy API docs (licence `activate` / `validate`, subscription webhooks); the live `wisprlitevercel/api/ping.js` and `admin.js` code; `engines/groq_engine.py` and `_wav_bytes` in `engines/openai_engine.py`; Groq's billing page after a real test month.
- **Methods and tools:** Vercel serverless functions (same folder as `ping.js`); Upstash Redis for the licence cache and a per-month seconds counter (`INCRBY`, `EXPIRE`); Lemon Squeezy licence API + signed webhooks (HMAC SHA-256); a new `CloudEngine` that is the existing `GroqEngine` pointed at `https://pipevoice.app/api/stt`.
- **External check (pass/fail):** (1) a real Lemon Squeezy *test-mode* purchase → key pasted into the app on a clean Windows install with no `.env` → dictation types text. Pass = text lands; fail = any error. (2) A scripted call over the monthly cap gets the "cap reached" reply, and Groq's usage page shows *no* extra usage. (3) A wrong or cancelled key is refused within one validation cycle.

## What already exists (checked 27 Nov)

- `GroqEngine` is the OpenAI engine with a different base URL. A Cloud engine is the same trick: base URL = our proxy, "API key" = the licence key.
- Audio is sent as 16 kHz mono WAV (`_wav_bytes`), about 32 KB per second.
- `ping.js` shows the Upstash REST pattern. `admin.js` shows the `ADMIN_TOKEN` gate.
- The **beta update channel is already a free switch in Settings** (`update_channel`, `settings.py:944`). See open decision A.

## Build (in order)

1. **Server: `/api/stt`** (pipevoice-site, `wisprlitevercel/api/stt.js`).
   - Reads `Authorization: Bearer <licence key>` and the WAV body.
   - Licence check: Upstash cache `lic:<sha256(key)>` (valid for 6 h). On a miss, call Lemon Squeezy `validate`. Store only the hash, never the key.
   - Cap check: `secs:<hash>:<YYYY-MM>` against `CLOUD_MONTHLY_SECONDS`. Seconds = WAV length from the header, counted *before* the Groq call.
   - Forward to Groq `whisper-large-v3-turbo`. On a Groq error, retry once with Deepgram (pre-recorded API).
   - Kill switch: `CLOUD_ENABLED=0` returns 503 with a plain message.
   - Body limit: Vercel functions take at most 4.5 MB, about 2 min 20 s of WAV. See open decision C.
2. **Server: `/api/ls-webhook`.** Verify the signature. On `subscription_cancelled` / `subscription_expired` / `subscription_payment_failed`, delete the licence cache entry so the next call checks again.
3. **Server: `/api/sync`.** `GET` / `PUT` one JSON blob per licence hash: `vocabulary`, `replacements`, Voices/profiles. Size cap 64 KB. Last write wins, with `updated_at` so the app can tell which side is newer.
4. **App: `engines/cloud_engine.py`** (~20 lines, subclass of `GroqEngine`). New engine name `cloud` in `App._build_engine`. The key comes from the licence, not `.env`.
5. **App: licence box in Settings.** Paste key → Lemon Squeezy `activate` with the PC name → store the key outside `config.json` (same rule as API keys). Show plan status and "minutes used this month" (a header from `/api/stt`).
6. **App: sync.** On start and after each Settings save: pull, compare `updated_at`, push. Off unless a licence is set.
7. **App: offline fallback.** The existing `_fallback` to local Whisper already covers a Cloud outage. Check it fires for the new engine.
8. **Site: pricing section + privacy page lines.** What Cloud sends (audio, to our server and to Groq/Deepgram, not stored) and what sync stores.
9. **Admin page:** active licences, Cloud minutes this month, estimated Groq spend.

Estimate: one long session for server + app (steps 1–7), plus a short session for site copy and admin (8–9). External wait: Lemon Squeezy store approval for live payments (test mode works before that).

## Open decisions for James

- **A. Early-access updates.** The beta channel is already free. Making it paid would take away a free feature, against the monetization decisions. Recommendation: keep it free; Cloud v1 = no-key setup + sync.
- **B. Monthly cap.** Recommendation: 600 minutes (10 hours) a month. Groq lists turbo at about $0.04 per audio hour (from memory, to check on Groq's pricing page), so the worst case is about $0.40 per user per month.
- **C. Clips longer than about 2 min 20 s.** Recommendation for v1: refuse with a clear message and fall back to local Whisper. Later: upload FLAC, or send audio straight to Groq.
- **D. What James must supply:** a Lemon Squeezy store (test mode is enough to build), a Groq API key, and a Deepgram key on Vercel if one is not there yet.

## Not in v1

Mac client, meeting mode, team plans, pay-per-minute billing.
