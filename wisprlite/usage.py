"""One anonymous daily ping: app version + random install id. Nothing else.

It must never break startup or auto-update, so every error is swallowed.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import uuid
from datetime import date

from . import __version__, config

log = logging.getLogger("wisprlite")

URL = "https://pipevoice.app/api/ping"


def _path():
    return config.CONFIG_PATH.with_name("usage.json")


def _read() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except Exception:
        return {}


def _write(data: dict) -> None:
    tmp = _path().with_name("usage.json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, _path())


def maybe_send(cfg) -> bool:
    """Send today's count if it is on and not yet sent. True only if sent."""
    try:
        today = date.today().isoformat()
        state = _read()
        if not cfg.usage_count or state.get("last_count_day") == today:
            return False
        if not state.get("install_id"):
            state["install_id"] = uuid.uuid4().hex
            _write(state)
        body = {"id": state["install_id"], "v": __version__}
        req = urllib.request.Request(
            URL, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "Pipevoice-usage"})
        # From disk: the user may have switched it off since cfg was loaded.
        # Raw read: Config.load() saves defaults (count on) over a damaged file.
        # Anything unreadable raises, and so does not send.
        if json.loads(config.CONFIG_PATH.read_text(encoding="utf-8")).get("usage_count", True) is not True:
            return False
        with urllib.request.urlopen(req, timeout=5):
            pass
        state["last_count_day"] = today
        _write(state)
        return True
    except Exception as exc:
        log.debug("usage count not sent: %s", exc)
        return False
