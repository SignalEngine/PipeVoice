"""Lightweight self-updater.

Checks the GitHub Releases API for a newer Pipevoice-Setup.exe, downloads it,
verifies its SHA-256, and runs it silently so the existing per-user Inno Setup
installer upgrades in place (Windows Restart Manager closes + relaunches the
app). No new dependencies, no server, no admin/UAC (per-user install).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
import urllib.request

from . import __version__, config

log = logging.getLogger("wisprlite")

REPO = "Powleads/PipeVoice"
# /releases/latest EXCLUDES prereleases — that is the whole staging ring. A
# release published as a PRERELEASE is invisible to everyone on stable, and
# visible only to installs that opted into beta. Promote it (prerelease=false)
# and the same build reaches everyone, with no rebuild and no server.
API = f"https://api.github.com/repos/{REPO}/releases/latest"
API_ALL = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
ASSET = "Pipevoice-Setup.exe"
_UA = {"User-Agent": "Pipevoice-updater"}


def _parse_version(v: str) -> tuple:
    v = (v or "").lstrip("vV").strip()
    parts = []
    for p in v.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _newest_release(channel: str) -> dict | None:
    """The release this install should consider, for its channel.

    STABLE keeps hitting /releases/latest exactly as before — that path must
    stay byte-identical, because a mistake here silently ends auto-updates for
    every existing install. BETA additionally sees prereleases.
    """
    if str(channel or "").strip().lower() != "beta":
        req = urllib.request.Request(API, headers={**_UA, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    req = urllib.request.Request(API_ALL, headers={**_UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        releases = json.load(r)
    if not isinstance(releases, list):
        return None
    # Newest by VERSION, not by list order or publish date: a hotfix to an older
    # line can be published after a newer prerelease.
    usable = [
        rel for rel in releases
        if isinstance(rel, dict) and not rel.get("draft") and rel.get("tag_name")
    ]
    return max(usable, key=lambda rel: _parse_version(rel.get("tag_name", "")), default=None)


def check() -> dict | None:
    """Return {'version','tag','url','sha256'} if a newer release exists, else None."""
    try:
        channel = getattr(config.Config.load(), "update_channel", "stable")
    except Exception:
        channel = "stable"          # a broken config must never stop updates
    try:
        data = _newest_release(channel)
    except Exception as exc:
        log.info("update check failed: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name", "")
    if _parse_version(tag) <= _parse_version(__version__):
        return None
    setup_url = sha_url = None
    for a in data.get("assets", []):
        name = a.get("name", "")
        if name == ASSET:
            setup_url = a.get("browser_download_url")
        elif name == ASSET + ".sha256":
            sha_url = a.get("browser_download_url")
    if not setup_url:
        return None
    sha256 = ""
    if sha_url:
        try:
            with urllib.request.urlopen(urllib.request.Request(sha_url, headers=_UA), timeout=10) as r:
                sha256 = r.read().decode("utf-8", "ignore").split()[0].strip().lower()
        except Exception:
            sha256 = ""
    return {"version": tag.lstrip("vV"), "tag": tag, "url": setup_url, "sha256": sha256}


def current_version() -> str:
    return __version__


def _api_json(url: str, timeout: int = 10):
    req = urllib.request.Request(url, headers={**_UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def latest_release() -> dict | None:
    """Full latest-release info incl. notes, for the About window. None on failure."""
    try:
        data = _api_json(API)
    except Exception as exc:
        log.info("latest release fetch failed: %s", exc)
        return None
    tag = data.get("tag_name", "")
    setup_url = sha_url = None
    for a in data.get("assets", []):
        n = a.get("name", "")
        if n == ASSET:
            setup_url = a.get("browser_download_url")
        elif n == ASSET + ".sha256":
            sha_url = a.get("browser_download_url")
    return {
        "version": tag.lstrip("vV"), "tag": tag,
        "name": data.get("name") or tag, "body": data.get("body") or "",
        "published_at": data.get("published_at") or "",
        "url": setup_url, "sha_url": sha_url,
        "newer": bool(setup_url) and _parse_version(tag) > _parse_version(__version__),
    }


def info_from_latest(rel: dict) -> dict | None:
    """Build the download info dict for download_and_run() from latest_release()."""
    if not rel or not rel.get("url"):
        return None
    sha256 = ""
    if rel.get("sha_url"):
        try:
            with urllib.request.urlopen(urllib.request.Request(rel["sha_url"], headers=_UA), timeout=10) as r:
                sha256 = r.read().decode("utf-8", "ignore").split()[0].strip().lower()
        except Exception:
            sha256 = ""
    return {"version": rel["version"], "tag": rel["tag"], "url": rel["url"], "sha256": sha256}


def _on_beta() -> bool:
    try:
        return str(getattr(config.Config.load(), "update_channel", "")).lower() == "beta"
    except Exception:
        return False


def recent_releases(n: int = 6) -> list:
    """Recent releases for the changelog: [{tag, name, published_at, body}].

    Hides prereleases from stable installs. The About window offers its first
    entry as a download, so without this filter "Check again" hands a stable
    user the very build the staging ring is keeping away from them.
    """
    try:
        data = _api_json(f"https://api.github.com/repos/{REPO}/releases?per_page={int(n)}")
    except Exception as exc:
        log.info("releases list fetch failed: %s", exc)
        return []
    out = []
    beta = _on_beta()
    for rel in (data if isinstance(data, list) else []):
        if rel.get("draft"):
            continue
        if rel.get("prerelease") and not beta:
            continue
        setup_url = sha_url = None
        for a in rel.get("assets", []):
            nm = a.get("name", "")
            if nm == ASSET:
                setup_url = a.get("browser_download_url")
            elif nm == ASSET + ".sha256":
                sha_url = a.get("browser_download_url")
        tag = rel.get("tag_name", "")
        out.append({
            "tag": tag,
            "version": tag.lstrip("vV"),
            "name": rel.get("name") or tag,
            "published_at": rel.get("published_at", ""),
            "body": rel.get("body", "") or "",
            "url": setup_url,
            "sha_url": sha_url,
        })
    return out


def is_newer(tag: str) -> bool:
    """True if `tag` is a newer release than the running version."""
    return _parse_version(tag or "") > _parse_version(__version__)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def download_and_run(info: dict) -> bool:
    """Download + verify the installer, then spawn it silently. True if launched."""
    folder = config.config_dir() / "update"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    dest = str(folder / ASSET)
    last_err = None
    for attempt in (1, 2):  # the installer is sizeable; retry once on a transient drop
        try:
            req = urllib.request.Request(info["url"], headers=_UA)
            with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            last_err = None
            break
        except Exception as exc:
            last_err = exc
            log.warning("update download attempt %d failed: %s", attempt, exc)
    if last_err is not None:
        return False
    # Verify integrity (the app is unsigned, so this is our tamper check).
    if info.get("sha256"):
        got = _sha256(dest)
        if got != info["sha256"]:
            log.warning("update SHA-256 mismatch: expected %s, got %s; aborting", info["sha256"], got)
            try:
                os.remove(dest)
            except Exception:
                pass
            return False
    # Say that WE are doing this, before handing over. The installer relaunches
    # us with /RESTARTAPPLICATIONS, which brings the app back exactly as it was
    # - a tray icon and nothing else - so without this the update finishes with
    # no visible sign that anything happened.
    from . import __version__ as _current
    mark_pending(_current)

    # Let the installer (not us) close + replace + relaunch the running exe.
    # FORCECLOSEAPPLICATIONS is essential: the tray app and any open settings
    # window don't answer Windows' Restart Manager (Tk/pystray ignore the close
    # request), so without forcing, the install can't unlock python311.dll /
    # Pipevoice.exe and stalls. Forcing closes them; RESTARTAPPLICATIONS relaunches.
    try:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(
            [dest, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
             "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
            creationflags=flags,
            close_fds=True,
        )
        return True
    except Exception as exc:
        log.warning("could not launch installer: %s", exc)
        return False


PENDING = "pending-update.json"
# A marker older than this is from an update that never completed. Landing on
# About days later, out of nowhere, would be worse than saying nothing.
PENDING_MAX_AGE = 24 * 3600


def _pending_path():
    return config.config_dir() / "update" / PENDING


def mark_pending(from_version: str) -> None:
    """Record that WE are installing an update, so the next start can say so.

    A bare version comparison cannot: it also fires when someone reinstalls by
    hand or restores a backup, and opening a window uninvited on a tray app
    that starts at boot is the wrong answer to "did anything change?".
    """
    try:
        path = _pending_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"from": from_version, "at": time.time()}),
                        encoding="utf-8")
    except Exception:
        log.warning("could not record the pending update", exc_info=True)


def take_pending(current_version: str) -> bool:
    """True exactly once, on the first start after an update we performed.

    Always clears the marker, including when the install failed and the version
    did not move — a marker that survives its own failure would open About on
    every start from then on.
    """
    path = _pending_path()
    try:
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not _consume(path):
        # We could not clear the marker - antivirus holding the file, a
        # read-only folder. Saying "yes, we updated" now would re-fire on every
        # single start from here on, turning a one-shot window into a boot
        # pop-up nobody can switch off. Stay quiet instead.
        log.warning("could not clear the pending-update marker; skipping the About window")
        return False
    try:
        if time.time() - float(data.get("at") or 0) > PENDING_MAX_AGE:
            return False
    except Exception:
        return False
    previous = data.get("from")
    # Must be a version STRING. A truthy non-string compares unequal to every
    # version and would fire About on a marker that means nothing.
    return isinstance(previous, str) and bool(previous) and previous != current_version


def _consume(path) -> bool:
    """Delete the marker, or failing that neuter it. True if it is gone for good."""
    try:
        path.unlink()
        return True
    except Exception:
        pass
    try:
        # Could not remove it, so empty it: an unreadable marker is treated as
        # no marker, and this needs only write permission, not delete.
        path.write_text("", encoding="utf-8")
        return True
    except Exception:
        return False


def cleanup_old() -> None:
    """Delete a stale downloaded installer from a previous update."""
    try:
        p = config.config_dir() / "update" / ASSET
        if p.exists():
            p.unlink()
    except Exception:
        pass
