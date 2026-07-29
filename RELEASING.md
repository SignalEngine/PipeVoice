# Releasing PipeVoice

Updates are silent and automatic, so a bad release reaches every install within
a day. On 2026-07-29, v2.32.0 shipped a crash that stopped the settings window
opening for anyone with an untranscribed recording. It was found by a user, not
by CI. The staging ring exists so the next one is found on a handful of machines
instead of all of them.

## How the ring works

GitHub's `/releases/latest` **excludes prereleases**. That single fact is the
whole mechanism — no server, no percentage rollout, no extra infrastructure:

| channel | endpoint | sees |
|---|---|---|
| stable (default) | `/releases/latest` | promoted releases only |
| beta (opt-in) | `/releases` | prereleases too |

Users opt in with **Settings → Automatic updates → "Get updates early (beta)"**.

## Cutting a release

1. **Bump the version.** `wisprlite/__init__.py` `__version__` must match the
   tag, or the updater compares against the old number and nobody updates.

2. **Tag and push.**
   ```
   git tag -a v2.33.0 -F release-notes.txt
   git push origin v2.33.0
   ```

3. **CI already publishes it as a prerelease** (see build.yml). Beta installs
   pick it up automatically. Everyone else still sees the previous version —
   verify that:
   ```
   gh api repos/Powleads/PipeVoice/releases/latest -q .tag_name   # the OLD tag
   ```

4. **Soak it.** At least a day of real use, and specifically open the app cold:
   the v2.32.0 crash happened on launch, not during any feature.

5. **Promote when it holds.**
   ```
   gh release edit v2.33.0 --prerelease=false --latest
   ```
   Confirm the endpoint installs actually poll:
   ```
   gh api repos/Powleads/PipeVoice/releases/latest -q .tag_name   # the NEW tag
   ```

## If a promoted release is bad

Do **not** delete it — installs that already updated will not roll back, and a
missing `/releases/latest` breaks the check for everyone.

Ship a hotfix forward: bump the patch version, fix, tag, and promote straight to
stable. That is what v2.32.1 did.

## Before tagging anything

- `python -m pytest tests/ -q --ignore=tests/test_transcribe_deepgram_e2e.py`
  (7 failures are pre-existing — the Deepgram SDK is absent)
- `xvfb-run -a python -m pytest tests/test_ui_smoke.py -q` — builds the real
  windows **with meeting fixtures present**. A window built with no data
  exercises almost nothing, which is exactly how the v2.32.0 crash got through.
- A cross-lineage review of the diff (`review-gate <path> claude`).
