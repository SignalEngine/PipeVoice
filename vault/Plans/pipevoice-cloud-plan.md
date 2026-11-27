# PipeVoice Cloud v1 — build plan

*27 Nov 2026 · brain-written (Fable is off) · status: decisions made, payment provider switched to Polar, waiting for the Polar token*

**Goal:** a paid tier, £3/month or £25/year. A payer types a licence key into Settings and dictation works with no API key of their own. Their settings and vocabulary follow them to another PC.

Decisions behind this: [monetization plan](monetization-2026-11-27.md).

## Plan header (/be-ambitious)

- **What a top professional adds:** a hard cost ceiling per user, enforced on the server *before* the Groq call, so one heavy user can never cost more than they pay. Licence checks that keep working for a few hours if Polar is down, so an outage never stops someone dictating. A kill switch for the whole Cloud proxy.
- **Rawest material:** Polar's licence-key and webhook docs (polar.sh/docs: `customer-portal/license-keys/validate`, `/activate`, webhook events + delivery); the live `wisprlitevercel/api/ping.js` and `admin.js` code; `engines/groq_engine.py` and `_wav_bytes` in `engines/openai_engine.py`; Groq's usage page after a real test month.
- **Methods and tools:** Vercel serverless functions (same folder as `ping.js`); Upstash Redis for the licence cache and a per-month seconds counter (`INCRBY`, `EXPIRE`); Polar licence-key API (no token needed, `organization_id` only) + Standard Webhooks signatures; a new `CloudEngine` that is the existing `GroqEngine` pointed at `https://pipevoice.app/api/stt`.
- **External check (pass/fail):** (1) a real Polar *sandbox* purchase → key pasted into the app on a clean Windows install with no `.env` → dictation types text. Pass = text lands; fail = any error. (2) A scripted call over the monthly cap gets the "cap reached" reply, and Groq's usage page shows *no* extra usage. (3) A revoked key is refused within one cache cycle (6 h), or at once after the `subscription.revoked` webhook.

## What already exists (checked 27 Nov)

- `GroqEngine` is the OpenAI engine with a different base URL. A Cloud engine is the same trick: base URL = our proxy, "API key" = the licence key.
- Audio is sent as 16 kHz mono WAV (`_wav_bytes`), about 32 KB per second.
- `ping.js` shows the Upstash REST pattern. `admin.js` shows the `ADMIN_TOKEN` gate.
- The beta update channel is already a free switch in Settings (`update_channel`, `settings.py:944`). It stays free (decision A).

## Payment provider: Polar (James, 27 Nov)

Lemon Squeezy closed to new stores (James's screenshot, 27 Nov). Polar replaces it. Facts from polar.sh/docs, read 27 Nov:

- **Merchant of record:** Polar registers, collects and pays sales tax / VAT.
- **Licence keys built in:** `POST https://api.polar.sh/v1/customer-portal/license-keys/validate` and `/activate`. No access token; `organization_id` is required and stops keys from other Polar stores. Activation limit per key is supported (set 3 PCs). Sandbox: `https://sandbox-api.polar.sh`.
- **Subscription end:** `subscription.canceled` does NOT end access. The key is revoked at period end (`subscription.revoked`); validate then returns 404.
- **Webhooks:** Standard Webhooks headers (`webhook-id`, `webhook-timestamp`, `webhook-signature`), HMAC SHA-256 over the raw body, 5-minute timestamp window.
- **Fees:** free plan 5% + 50¢, plus 1.5% for cards outside the US. On a £3 monthly sale that is about £0.60–0.65 (over 20%). On £25 a year it is about £2.10 (about 8%).

## Build (in order)

1. **Server: `/api/stt`** (pipevoice-site, `wisprlitevercel/api/stt.js`).
   - Reads `Authorization: Bearer <licence key>` and the WAV body.
   - Licence check: Upstash cache `lic:<sha256(key)>` (valid for 6 h). On a miss, call Polar `validate` with `POLAR_ORGANIZATION_ID`. Store only the hash, never the key.
   - Cap check: `secs:<hash>:<YYYY-MM>` against `CLOUD_MONTHLY_SECONDS` (36,000). Seconds = WAV length from the header, counted *before* the Groq call.
   - Forward to Groq `whisper-large-v3-turbo`. On a Groq error, retry once with Deepgram (pre-recorded API).
   - Kill switch: `CLOUD_ENABLED=0` returns 503 with a plain message.
   - Body limit: Vercel functions take at most 4.5 MB, about 2 min 20 s of WAV. Longer clips: the app sends them to local Whisper (decision C).
2. **Server: `/api/polar-webhook`.** Verify the signature. On `subscription.revoked` / `benefit_grant.revoked`, delete the licence cache entry so the next call checks again.
3. **Server: `/api/sync`.** `GET` / `PUT` one JSON blob per licence hash: `vocabulary`, `replacements`, Voices/profiles. Size cap 64 KB. Last write wins, with `updated_at` so the app can tell which side is newer.
4. **App: `engines/cloud_engine.py`** (~20 lines, subclass of `GroqEngine`). New engine name `cloud` in `App._build_engine`. The key comes from the licence, not `.env`.
5. **App: licence box in Settings.** Paste key → Polar `activate` with the PC name as `label` → store the key and activation id outside `config.json` (same rule as API keys). Show plan status and "minutes used this month" (a header from `/api/stt`).
6. **App: sync.** On start and after each Settings save: pull, compare `updated_at`, push. Off unless a licence is set.
7. **App: offline fallback.** The existing `_fallback` to local Whisper already covers a Cloud outage. Check it fires for the new engine.
8. **Site: pricing section + privacy page lines.** What Cloud sends (audio, to our server and to Groq/Deepgram, not stored) and what sync stores.
9. **Admin page:** active licences, Cloud minutes this month, estimated Groq spend.

Estimate: one long session for server + app (steps 1–7), plus a short session for site copy and admin (8–9). Product setup in Polar is done by Claude through the API with James's sandbox token.

## Decisions (James, 27 Nov)

- **A. Beta updates stay free.** Cloud v1 = no-key setup + settings sync. Early-access is not a Cloud perk.
- **B. Monthly cap: 600 minutes (10 hours).** Groq lists turbo at about $0.04 per audio hour (from memory, to check on Groq's pricing page), so the worst case is about $0.40 per user per month.
- **C. Clips longer than about 2 min 20 s go to local Whisper** with a short notice. Long-clip upload is not in v1.
- **D. Keys:** `GROQ_API_KEY` received and tested (27 Nov, models call = 200). Payment provider = **Polar** (Lemon Squeezy closed). Needed: a Polar sandbox organization access token. Later: the production token and the webhook secret, and a Deepgram key on Vercel if one is not there yet.

## Not in v1

Mac client, meeting mode, team plans, pay-per-minute billing.
