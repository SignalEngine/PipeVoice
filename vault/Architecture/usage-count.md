# Anonymous daily install count

Shipped: website 27 Nov 2026 (pipevoice-site PR #20, merge 4575645). App side: PR #127 (in review).

## What it does
- Once per day, per app start, the app POSTs `{"id": <32-hex random>, "v": <version>}` to `https://pipevoice.app/api/ping`.
- The server stores only `PFADD installs:<YYYY-MM-DD> <id>` in Upstash Redis (HyperLogLog). No IP, no audio, no text. Always answers 204.
- `/admin` shows installs today / last 7 days / last 30 days.

## App side (`wisprlite/usage.py`)
- Switch: `usage_count` in config.json, **on by default**, off in Settings ("Send an anonymous daily count").
- The id and last-sent day live in a separate `usage.json` next to config.json. usage.py never writes config.json (Codex round 2: rewriting config could revert a user's "off").
- Right before sending it re-reads the switch from config.json on disk. A missing or damaged file means do not send.
- Fire-and-forget daemon thread at startup. Any error is ignored. The auto-updater is untouched.

## Known limits
- One ping per app start: a PC left running for days is counted only on the day it started, so the daily number reads low. Use the 7- and 30-day numbers.
- If Upstash env vars are missing on Vercel, the ping still returns 204 but nothing is stored. Check `/admin` ("not configured").

## Privacy copy
- Every "zero telemetry / nothing phones home" claim on the site, README, welcome screen and winget listing was rewritten to describe this count.
- privacy.html also now says dictation history (`history.jsonl`) and meeting recordings are saved locally (it wrongly said "never on disk").

## Open
- `marketing/build_blog.py` regenerates blog HTML from `articles.json`, and the two are out of step (older than this work). Re-running the generator would undo hand fixes. Sync them before the next blog build.
- Website jury run saw only 13% of the diff (articles.json is one huge line); its findings quoted removed text. Codex repo-aware review passed.
