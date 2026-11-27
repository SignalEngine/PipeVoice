"""PipeVoice Cloud: the licence (Polar) and word sync.

The licence lives in cloud.json, never in config.json.
Contract: vault/Plans/cloud-build-contract.md.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import threading
import time
import urllib.error
import urllib.request

from . import config

log = logging.getLogger("wisprlite")

# shortcut: sandbox default, switch to https://api.polar.sh and the production org id before the paid launch
POLAR_API = "https://sandbox-api.polar.sh"
ORG_ID = "7ddf99c5-d60b-4e36-8b8f-cd7618fbadb8"
USER_AGENT = "Pipevoice-cloud"  # Polar's Cloudflare rejects Python's default

_sync_lock = threading.Lock()


def _path():
    return config.config_dir() / "cloud.json"


def load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(state: dict) -> None:
    tmp = _path().with_name("cloud.json.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, _path())


def clear() -> None:
    _path().unlink(missing_ok=True)


def has_licence() -> bool:
    return bool(load().get("key"))


def stt_url() -> str:
    return os.getenv("WISPRLITE_CLOUD_URL") or "https://pipevoice.app/api/stt"


def sync_url() -> str:
    return "https://pipevoice.app/api/sync"


def _request(url: str, method: str, body=None, headers=None, timeout: float = 15):
    """JSON in, JSON out. Raises urllib's errors."""
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if body is not None:
        body = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def activate(key: str) -> tuple:
    """Activate the key for this PC. Returns (ok, message)."""
    key = (key or "").strip()
    if not key:
        return False, "Paste your licence key first"
    label = platform.node() or "PC"
    base = os.getenv("WISPRLITE_POLAR_API") or POLAR_API
    try:
        data = _request(base.rstrip("/") + "/v1/customer-portal/license-keys/activate", "POST",
                        {"key": key, "organization_id": ORG_ID, "label": label})
        save({"key": key, "activation_id": data["id"], "label": label})
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return False, "This key is already on 3 PCs, or it was cancelled"
        if exc.code == 404:
            return False, "Key not found"
        return False, f"Activation failed (error {exc.code}). Try again later"
    except Exception as exc:
        log.debug("cloud activation failed: %s", type(exc).__name__)
        return False, "Could not reach the licence server. Check your internet and try again"
    return True, "Active on this PC"


def _apply(cfg, remote) -> None:
    words, fixes = remote["vocabulary"], remote["replacements"]
    if not (isinstance(words, str) and isinstance(fixes, dict)
            and all(isinstance(v, str) for v in fixes.values())):
        raise ValueError("bad sync data")
    # Save onto a fresh load: Settings may have saved while we waited on the network.
    disk = config.Config.load()
    changed = (disk.vocabulary, disk.replacements) != (words, fixes)
    cfg.vocabulary, cfg.replacements = words, fixes
    if changed:
        disk.vocabulary, disk.replacements = words, fixes
        disk.save()


def sync_now(cfg) -> None:
    """Sync vocabulary and replacements; the newer side wins. Never raises."""
    try:
        with _sync_lock:
            state = load()
            key = state.get("key")
            if not key:
                return
            auth = {"Authorization": "Bearer " + key}
            remote = _request(sync_url(), "GET", headers=auth)
            if remote["updated_at"] > state.get("synced_at", 0):
                _apply(cfg, remote["data"])
                stamp = remote["updated_at"]
            else:
                stamp = int(time.time() * 1000)
                body = {"data": {"vocabulary": cfg.vocabulary, "replacements": cfg.replacements},
                        "updated_at": stamp}
                try:
                    _request(sync_url(), "PUT", body, auth)
                except urllib.error.HTTPError as exc:
                    if exc.code != 409:
                        raise
                    newer = json.loads(exc.read())
                    _apply(cfg, newer["data"])
                    stamp = newer["updated_at"]
            current = load()
            if current.get("key") == key:  # not removed or replaced meanwhile
                save({**current, "synced_at": stamp})
    except Exception as exc:
        log.debug("cloud sync failed: %s", exc)
