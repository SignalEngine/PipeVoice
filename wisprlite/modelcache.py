"""The local Whisper model cache: where it is, how big, and clearing it.

faster-whisper downloads its models through huggingface_hub, so they land in
the shared HF cache rather than anywhere PipeVoice owns. A part-finished
download there causes slow retries and empty transcripts, and the only fix is
to remove it and let it fetch again.

Deliberately surgical: the HF cache is SHARED with anything else the user runs
that uses HuggingFace models. Only faster-whisper's own directories are
touched, never the whole cache.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger("wisprlite")

# What faster-whisper's repos are called on the hub: Systran/faster-whisper-*
# today, guillaumekln/faster-whisper-* historically. Both contain this.
MARKER = "faster-whisper"


def cache_dir() -> Path:
    """The HuggingFace hub cache, honouring the same env vars the library does."""
    for var in ("HUGGINGFACE_HUB_CACHE", "HF_HUB_CACHE"):
        value = os.getenv(var, "").strip()
        if value:
            return Path(value)
    home = os.getenv("HF_HOME", "").strip()
    if home:
        return Path(home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def model_dirs() -> list[Path]:
    """Every cached faster-whisper model. Nothing else is ever returned."""
    root = cache_dir()
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    return sorted(p for p in entries if p.is_dir() and MARKER in p.name)


def _dir_size(path: Path) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                # A symlinked blob counts once, where it actually lives; the HF
                # cache uses symlinks heavily and following them double-counts.
                total += os.lstat(os.path.join(base, name)).st_size
            except OSError:
                pass
    return total


def size_bytes() -> int:
    return sum(_dir_size(p) for p in model_dirs())


def human_size(size: int) -> str:
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.1f} GB"
    if size >= 1_000_000:
        return f"{size / 1_000_000:.0f} MB"
    if size > 0:
        return f"{max(1, size // 1000)} KB"
    return "empty"


def clear() -> tuple[bool, str]:
    """Delete the cached models. Returns (everything went, message).

    A model that is currently loaded is held open on Windows, so this reports
    what is still locked rather than pretending it succeeded — "cleared" while
    the broken download is still there is the worst possible answer.
    """
    dirs = model_dirs()
    if not dirs:
        return True, "Nothing cached — the model will download next time you use it."
    freed, failed = 0, []
    for path in dirs:
        size = _dir_size(path)
        try:
            shutil.rmtree(path)
            freed += size
        except OSError as exc:
            log.info("modelcache: could not remove %s: %s", path, exc)
            failed.append(path.name)
    if failed:
        return False, (
            f"Removed {human_size(freed)}, but {len(failed)} could not be deleted "
            "because PipeVoice is still using them. Close PipeVoice from the tray "
            "and try again."
        )
    return True, (
        f"Cleared {human_size(freed)}. The model downloads again next time you "
        "use local transcription."
    )
