## Round 2 triage (brain, 2026-11-27)
- F1 cross-licence words: REJECTED by design (second time). Vocabulary/replacements are PC data and sync with whichever licence is active on this PC. ARCHITECTURE.md states it. Same class, no new instance.
- F2 bad replacement key disables working licence: FIXED. activate() now calls Polar activate for the new key first; only on success saves it and best-effort deactivates the old one. Class sweep: activate() is the only replacement path (settings.py:1626 calls it; remove() is a separate explicit user action). Tests: test_a_rejected_new_key_keeps_the_working_licence (404/403/network), ordering test.
- F3 pre-digest state shape: REJECTED. No build with cloud.json lacking synced_digest was ever released (branch-only, never shipped), so no PC holds that shape.

## Cross-account data flow with a PC-wide data store but no account identity boundary
REJECTED by design (see F1 above). Places checked:
86:def remove() -> dict:
110:def activate(key: str) -> tuple:
149:def _merge(local, remote) -> dict:
174:def sync_now(cfg) -> None:
- 2026-11-27 James decided (AskUserQuestion): keep this behaviour. Words stay on the PC and sync to the new key. Codex round 3 F1 is closed by owner decision.
