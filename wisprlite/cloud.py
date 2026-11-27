"""PipeVoice Cloud: the licence (Polar) and word sync.

The licence lives in cloud.json, never in config.json.
Contract: vault/Plans/cloud-build-contract.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
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
ENGINE_AFTER_REMOVE = "local"  # needs no key, so dictation keeps working

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
    # One override variable (preview deployments): .../api/stt -> .../api/sync
    url = os.getenv("WISPRLITE_CLOUD_URL")
    return re.sub(r"/stt$", "/sync", url) if url else "https://pipevoice.app/api/sync"


def _request(url: str, method: str, body=None, headers=None, timeout: float = 15):
    """JSON in, JSON out. Raises urllib's errors."""
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if body is not None:
        body = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None  # 204 has no body


def _polar(action: str, body: dict):
    base = os.getenv("WISPRLITE_POLAR_API") or POLAR_API
    return _request(base.rstrip("/") + "/v1/customer-portal/license-keys/" + action, "POST", body)


def remove() -> dict:
    """Forget the licence and leave the Cloud engine. Returns the old state for deactivate()."""
    state = load()
    clear()
    cfg = config.Config.load()
    if cfg.engine == "cloud":
        cfg.engine = ENGINE_AFTER_REMOVE
        cfg.save()
    return state


def deactivate(state: dict) -> bool:
    """Best effort: free this PC's slot on the key. Never raises."""
    if not (state.get("key") and state.get("activation_id")):
        return False
    try:
        _polar("deactivate", {"key": state["key"], "organization_id": ORG_ID,
                              "activation_id": state["activation_id"]})
        return True
    except Exception as exc:
        log.debug("cloud deactivation failed: %s", type(exc).__name__)
        return False


def activate(key: str) -> tuple:
    """Activate the key for this PC. Returns (ok, message)."""
    key = (key or "").strip()
    if not key:
        return False, "Paste your licence key first"
    state = load()
    if state.get("key") == key and state.get("activation_id"):
        return True, "Active on this PC"  # another activate would use another slot
    lost = ""
    if state.get("key") != key and deactivate(state):  # replacing: free the old slot first
        clear()  # its activation is gone, so we must not keep calling it active
        lost = ". Your previous key was removed from this PC"
    label = platform.node() or "PC"
    try:
        data = _polar("activate", {"key": key, "organization_id": ORG_ID, "label": label})
        save({"key": key, "activation_id": data["id"], "label": label})
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return False, "This key is already on 3 PCs, or it was cancelled" + lost
        if exc.code == 404:
            return False, "Key not found" + lost
        return False, f"Activation failed (error {exc.code}). Try again later" + lost
    except Exception as exc:
        log.debug("cloud activation failed: %s", type(exc).__name__)
        return False, "Could not reach the licence server. Check your internet and try again" + lost
    return True, "Active on this PC"


def _words_and_fixes(remote) -> tuple:
    words, fixes = remote["vocabulary"], remote["replacements"]
    if not (isinstance(words, str) and isinstance(fixes, dict)
            and all(isinstance(v, str) for v in fixes.values())):
        raise ValueError("bad sync data")
    return words, fixes


def _digest(cfg) -> str:
    data = json.dumps([cfg.vocabulary, cfg.replacements], sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


def _merge(local, remote) -> dict:
    """Both sides changed (or never synced): keep both. Local comes first and wins clashes."""
    words, fixes = _words_and_fixes(remote)
    terms = {}
    for term in (local.vocabulary + "," + words).split(","):
        if term.strip():
            terms.setdefault(term.strip().casefold(), term.strip())
    # Word fixes ignore case (typer.apply_replacements), so clashes do too.
    mine = {k.casefold() for k in local.replacements}
    return {"vocabulary": ", ".join(terms.values()),
            "replacements": {**local.replacements,
                             **{k: v for k, v in fixes.items() if k.casefold() not in mine}}}


def _apply(cfg, remote) -> None:
    words, fixes = _words_and_fixes(remote)
    # Save onto a fresh load: Settings may have saved while we waited on the network.
    disk = config.Config.load()
    changed = (disk.vocabulary, disk.replacements) != (words, fixes)
    cfg.vocabulary, cfg.replacements = words, fixes
    if changed:
        disk.vocabulary, disk.replacements = words, fixes
        disk.save()


def sync_now(cfg) -> None:
    """Sync vocabulary and replacements. Never raises.

    One side changed since the last sync: it wins. Both changed, or this PC has
    never synced: merge them.
    """
    try:
        with _sync_lock:
            state = load()
            key = state.get("key")
            if not key:
                return
            auth = {"Authorization": "Bearer " + key}
            remote = _request(sync_url(), "GET", headers=auth)
            # Read after the GET, so words saved meanwhile count as local.
            local = config.Config.load() if config.CONFIG_PATH.exists() else cfg
            first = "synced_at" not in state
            remote_changed = (bool(remote["data"]) if first
                              else remote["updated_at"] > state["synced_at"])
            local_changed = first or _digest(local) != state.get("synced_digest")
            if local_changed:
                if remote_changed:
                    _apply(cfg, _merge(local, remote["data"]))
                    local = cfg
                # Past the server's stamp even if another PC's clock runs ahead.
                stamp = max(int(time.time() * 1000), remote["updated_at"] + 1)
                body = {"data": {"vocabulary": local.vocabulary,
                                 "replacements": local.replacements},
                        "updated_at": stamp}
                # A 409 (someone wrote since the GET) raises like any failure: nothing
                # local is overwritten or recorded, so the next sync merges.
                _request(sync_url(), "PUT", body, auth)
                digest = _digest(local)
            elif remote_changed:
                _apply(cfg, remote["data"])
                stamp, digest = remote["updated_at"], _digest(cfg)
            else:
                return
            current = load()
            if current.get("key") == key:  # not removed or replaced meanwhile
                save({**current, "synced_at": stamp, "synced_digest": digest})
    except Exception as exc:
        log.debug("cloud sync failed: %s", exc)
