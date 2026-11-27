"""One anonymous daily ping: app version + random install id. Nothing else.

It must never break startup or auto-update, so every error is swallowed.
"""

from __future__ import annotations

import json
import logging
import urllib.request
import uuid
from datetime import date

from . import __version__, config

log = logging.getLogger("wisprlite")

URL = "https://pipevoice.app/api/ping"


def _save(cfg, **values) -> None:
    for k, v in values.items():
        setattr(cfg, k, v)
    # Write onto a fresh load: saving a stale cfg would undo what Settings wrote meanwhile.
    fresh = config.Config.load()
    for k, v in values.items():
        setattr(fresh, k, v)
    fresh.save()


def maybe_send(cfg) -> bool:
    """Send today's count if it is on and not yet sent. True only if sent."""
    try:
        today = date.today().isoformat()
        if not cfg.usage_count or cfg.last_count_day == today:
            return False
        if not cfg.install_id:
            _save(cfg, install_id=uuid.uuid4().hex)
        body = {"id": cfg.install_id, "v": __version__, "ch": cfg.update_channel}
        req = urllib.request.Request(
            URL, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "Pipevoice-updater"})
        with urllib.request.urlopen(req, timeout=5):
            pass
        _save(cfg, last_count_day=today)
        return True
    except Exception as exc:
        log.debug("usage count not sent: %s", exc)
        return False
