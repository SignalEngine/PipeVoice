# Handover: PipeVoice Cloud v1 (27 Nov evening)

Plan (source of truth, steps 1-7 ticked): vault/Plans/pipevoice-cloud-plan.md — Artifact https://claude.ai/artifact/GhvYHSwmS7bNuASbFUwUhY

## Live now
- pipevoice-site main fb53f65: /api/stt, /api/sync, /api/polar-webhook (PRs #21, #22). Vercel prod env set (sandbox Polar, Groq, cap 36000 s, CLOUD_ENABLED=1, webhook secret). No DEEPGRAM_API_KEY on Vercel.
- PipeVoice v2.51.0 pre-release (main 2d89915). James's Windows test passed (text typed; popup shows no live words = expected, James: leave as is).
- Local secrets (git-ignored): /root/pipevoice-site/wisprlitevercel/.env.cloud — VERCEL_TOKEN (short-lived), POLAR_SANDBOX_TOKEN, POLAR_WEBHOOK_SECRET/ID, POLAR_TEST_LICENCE_KEY.

## Next
1. Step 9 admin stats (api/admin.js): active licences, Cloud seconds this month, est. Groq spend. Keys: secs:<sha256>:<YYYY-MM>, lic:/lg:/lk:. Needs a builder + review-gate.
2. Over-cap test (external check 2) with CLOUD_MONTHLY_SECONDS lowered on a preview, or a throwaway key; confirm Groq usage page shows no extra.
3. Deepgram key on Vercel (key-drop) for the fallback.
4. Step 8 site copy waits for the production Polar store + token (no public price before a real checkout).

## Stack notes found today
- ship.sh hard-codes SignalEngine/intentos + origin/master: do not use it for PipeVoice/pipevoice-site (logged as stack flaw).
- jury GLM-5.2 returned truncated reasoning only on PR #21 (logged).
- review-gate sweep check needs the reviewer's CLASS text verbatim in spec/sweep-rN.md.
