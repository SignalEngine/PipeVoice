# PipeVoice Cloud v1 — API contract (shared by both builds)

Parent plan: vault/Plans/pipevoice-cloud-plan.md (wisprlite repo). Brain-reviewed: 2026-11-27 brain-written, Fable off.

Fixed values:
- Cap: 36000 seconds per licence per calendar month (UTC), env `CLOUD_MONTHLY_SECONDS`.
- Polar API base: env `POLAR_API` (server) / `WISPRLITE_POLAR_API` (app); sandbox `https://sandbox-api.polar.sh`, prod `https://api.polar.sh`. Always send a `User-Agent` header (Polar's Cloudflare returns 403 "error code: 1010" for Python's default UA).
- Sandbox ids: organization `7ddf99c5-d60b-4e36-8b8f-cd7618fbadb8`, licence benefit `165a8f38-4512-4d7f-adf4-4e0b66a6635b`.
- Polar licence endpoints (no token): `POST {POLAR_API}/v1/customer-portal/license-keys/validate` body `{key, organization_id, benefit_id}`; `/activate` body `{key, organization_id, label}` → returns `{id, ...}` (activation id); 200 = valid, 404 = unknown/revoked, 403 on activate = limit reached / revoked.

## POST https://pipevoice.app/api/stt

Request:
- `Authorization: Bearer <licence key>`
- `Content-Type: audio/wav`; body = 16 kHz mono 16-bit WAV, max 4,400,000 bytes.
- Optional query `lang=<iso code>`; optional header `X-Prompt: <percent-encoded vocab prompt, max 800 chars after decoding>`.

Responses (always JSON):
| code | body |
|---|---|
| 200 | `{"text": "...", "used_seconds": n, "cap_seconds": n}` |
| 400 | `{"error": "bad_audio"}` (not a parseable WAV) |
| 401 | `{"error": "invalid_licence"}` |
| 402 | `{"error": "cap_reached", "used_seconds": n, "cap_seconds": n}` |
| 413 | `{"error": "too_long"}` |
| 503 | `{"error": "cloud_disabled"}` or `{"error": "upstream_failed"}` |

## GET / PUT https://pipevoice.app/api/sync

- Same `Authorization` header and licence check (401 as above).
- GET → 200 `{"data": {...} | null, "updated_at": <unix ms, 0 if none>}`.
- PUT body `{"data": {"vocabulary": "...", "replacements": {...}}, "updated_at": <unix ms>}`, max 65,536 bytes → 200 `{"ok": true}`; 409 `{"error": "stale", "data": ..., "updated_at": n}` when the stored `updated_at` is newer; 413 when too big.
