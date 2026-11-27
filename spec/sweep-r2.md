## Cross-account sync reuses local data from a previous licence
Brain ruling 2026-11-27: REJECTED by design. Local vocabulary/replacements belong to this PC and sync with whichever licence is active on this PC (documented in ARCHITECTURE.md).

### places checked
51:def clear() -> None:
110:def activate(key: str) -> tuple:
151:def _merge(local, remote) -> dict:
165:def _apply(cfg, remote) -> None:
176:def sync_now(cfg) -> None:
18:| `cloud.py` | Cloud licence (Polar activate, stored in `cloud.json`, never `config.json`) + vocabulary/replacements sync. Accepted for v1: Save in a Settings window that was open while a sync pulled new words writes the old words back. By design, local words belong to this PC and sync with whichever licence is active here. Accepted: an offline Remove cannot free the PC's slot (the Polar customer portal can). |
