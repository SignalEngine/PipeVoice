# Build: PipeVoice Cloud v1 — server (pipevoice-site repo)

Brain-reviewed: 2026-11-27 brain-written (Fable off); contract fixed, decisions settled.

## Header (/be-ambitious)
- Pro move: cost cap enforced before the Groq call; licence check survives a Polar outage; kill switch.
- Raw material: `wisprlitevercel/api/ping.js` (Upstash REST pattern), `api/admin.js`, `tests/test_ping_api.mjs` (test style), Polar docs polar.sh/docs (license keys, webhooks/delivery), Groq OpenAI-compatible `/openai/v1/audio/transcriptions`.
- Methods: Vercel Node functions (ESM, Node >=18, global fetch/FormData/Blob, no new npm deps), Upstash REST `/pipeline`, Standard Webhooks HMAC-SHA256.
- External check: node tests with a stubbed `fetch` prove: valid key → Groq called once, text returned; over cap → 402 and Groq NOT called; Groq error → Deepgram called; both fail → 503 and seconds refunded; Polar down + last-good cache → allowed; bad webhook signature → 401.

## Contract
Read `/root/wisprlite/vault/Plans/cloud-build-contract.md` first. It is binding.

## Files (all in `wisprlitevercel/`)
1. `api/_cloud.js` — shared helpers (underscore = not a route; `api/_emails/` is the precedent):
   - `redis(cmds)` → Upstash pipeline (env `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`).
   - `hashKey(key)` → sha256 hex. Never store or log the raw key.
   - `checkLicence(key)`: cache `lic:<hash>` "1" (EX 21600) / "0" (EX 300). On miss call Polar validate with env `POLAR_ORGANIZATION_ID`, `POLAR_BENEFIT_ID`. 200 → set `lic` "1", `lg:<hash>` "1" EX 604800, and `lk:<licence key id from response .id>` = hash EX 604800 (for the webhook). 404/422 → "0". Network error or 5xx → allow only if `lg:<hash>` exists.
   - `readRaw(req)` → Buffer from the request stream; functions using it export `config = { api: { bodyParser: false } }`.
2. `api/stt.js`:
   - `CLOUD_ENABLED` must equal "1", else 503 cloud_disabled.
   - Body > 4,400,000 bytes → 413. Parse the WAV header (find the `fmt ` and `data` chunks; do not assume 44 bytes) → seconds = data bytes / (rate × channels × bytes per sample), rounded up. Parse failure → 400.
   - Licence check → 401.
   - `INCRBY secs:<hash>:<YYYY-MM> <seconds>` + `EXPIRE` 40 days; if the new total > cap → `DECRBY` the same amount, 402.
   - Groq: multipart to `https://api.groq.com/openai/v1/audio/transcriptions`, model `whisper-large-v3-turbo`, `response_format=json`, `language` from `lang`, `prompt` from `X-Prompt`. Env `GROQ_API_KEY`. Timeout 25 s (AbortController).
   - On Groq failure and env `DEEPGRAM_API_KEY` set: `POST https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true[&language=..]`, `Authorization: Token <key>`, body = the WAV; text = `results.channels[0].alternatives[0].transcript`.
   - Both fail → `DECRBY` the seconds, 503 upstream_failed. Never return upstream error text.
   - No logging of audio, text, prompt or key.
3. `api/sync.js` — per the contract. Store `sync:<hash>` as JSON `{data, updated_at}`, no expiry.
4. `api/polar-webhook.js` — verify Standard Webhooks: secret env `POLAR_WEBHOOK_SECRET`; key = base64-decode of the part after `whsec_`; signed content = `${webhook-id}.${webhook-timestamp}.${rawBody}`; header `webhook-signature` may hold several space-separated `v1,<b64>` values; timing-safe compare; reject timestamps more than 300 s off. Bad → 401. On `benefit_grant.revoked` or `subscription.revoked`: find the licence key id (try `data.properties.license_key_id`, then `data.license_key_id`); `GET lk:<id>` → hash → `DEL lic:<hash> lg:<hash>`. Unknown shapes → 200 and note it in your report. Always 200 for verified events.
5. `tests/test_cloud_api.mjs` — same style as `tests/test_ping_api.mjs` (stub `globalThis.fetch`, fake Upstash). Cover every row of the contract tables and every bullet of the external check above. Include a webhook test signed with a known secret, and a bad-signature test.

## Not in this build
Admin page stats, pricing page, privacy copy (brain does these after). Do not change `ping.js` or existing tests.

## Gates
Write `GATES.md` at the worktree root with one CHECK/EXPECT oracle per external-check bullet (command = `node tests/test_cloud_api.mjs`) plus "all existing tests in tests/ still pass". Commit it last.

## STOP rule
Any choice this brief and the contract do not settle → stop and report. Do not deploy and do not set Vercel env vars.
