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

- Accepted risk (James, 27 Nov; Codex round 3 P1): any whole-file settings save from an older in-memory copy (startup `last_version` save, tray saves within the 1 s reload) can undo a just-saved "off". The startup window is the instant of app launch. This risk is the same for every setting and is older than this PR. Worst case: 1 extra anonymous count. A full fix means saves that write only changed fields.

## Privacy copy
- Every "zero telemetry / nothing phones home" claim on the site, README, welcome screen and winget listing was rewritten to describe this count.
- privacy.html also now says dictation history (`history.jsonl`) and meeting recordings are saved locally (it wrongly said "never on disk").

## Open
- `marketing/build_blog.py` regenerates blog HTML from `articles.json`, and the two are out of step (older than this work). Re-running the generator would undo hand fixes. Sync them before the next blog build.
- Website jury run saw only 13% of the diff (articles.json is one huge line); its findings quoted removed text. Codex repo-aware review passed.
