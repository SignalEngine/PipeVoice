"""Meeting-session browser used by Settings and ``--meetings``.

The filesystem helpers deliberately have no Tk dependency so session discovery,
status formatting, and search navigation can be tested on headless machines.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .history import _copy_to_clipboard
from .meeting import (
    apply_speaker_map,
    count_speaker_bleed,
    default_meetings_dir,
    find_loudest_speaker_window,
    apply_corrections,
    apply_polished,
    load_bookmarks,
    load_polished,
    load_speaker_map,
    load_corrections,
    meetings_dir,
    render_transcript,
    save_speaker_map,
    save_corrections,
    save_polished,
    transcribe_session,
    write_wav_window,
)
from .stats import render_stats
from .summarise import (
    provider_ready,
    read_summaries,
    render_markdown_lines,
    summarise,
)
from . import export
from .polish import PolishFailed, ProviderNotReady, polish_segments
from .winui import PALETTE, tooltip

BG = PALETTE["bg"]
CARD = PALETTE["card"]
FG = PALETTE["fg"]
MUTED = PALETTE["muted"]
ACCENT = PALETTE["accent"]
WARN = PALETTE["amber"]
DIV = PALETTE["div"]
GOOD = PALETTE["good"]
AMBER = PALETTE["amber"]
POPOVER = PALETTE["popover"]
ROW_HOVER = PALETTE["row_hover"]
SEARCH_MATCH = PALETTE["search_match"]
SEARCH_CURRENT = PALETTE["search_current"]
SPEAKER_COLOURS = tuple(PALETTE[f"speaker_{number}"] for number in range(1, 5))
# 3x the recorder's 5s meta checkpoint — a session whose meta.json is staler than
# this had its owning process die, so it is an orphan, not a live recording.
LIVE_HEARTBEAT_SECONDS = 15.0
ON_ACCENT = PALETTE["bg"]
SCROLL = PALETTE["border"]
SCROLL_HI = PALETTE["muted"]
BOOKMARK_LOOKBACK = 30.0
BOOKMARK_LOOKAHEAD = 3.0


def _replacement_key_allowed(key: str) -> bool:
    """Whether a correction key can survive Settings' comma-separated format."""
    return "," not in key and "=" not in key


def _replacement_value_allowed(value: str) -> bool:
    """Both halves ride the same "k=v, k=v" string, so both must stay clean.

    Guarding only the key still loses data: "ACME" -> "ACME, Inc." survives in
    corrections.json but the next Settings save parses it back as "ACME".
    """
    return "," not in value and "=" not in value


def keeping_fix_for_dictation_allowed(key: str, value: str) -> bool:
    """Whether this pair may be promoted into the GLOBAL cfg.replacements store.

    Only the global store rides Settings' flat "k=v, k=v" string. corrections.json
    is JSON and holds anything, so this must never gate a session-only fix — that
    asymmetry is easy to get wrong, hence one named rule both sides call.
    """
    return _replacement_key_allowed(key) and _replacement_value_allowed(value)


def _selection_contains_click(first: str, click: str, last: str) -> bool:
    """Return whether a Tk Text click index is inside a non-empty selection."""
    def offset(index: str) -> tuple[int, int]:
        line, column = index.split(".", 1)
        return int(line), int(column)

    return offset(first) <= offset(click) < offset(last)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def derive_status(session_dir: str | Path, meta: dict | None = None) -> str:
    """Return the user-facing state: recording, recorded, transcribed, or error."""
    path = Path(session_dir)
    if meta is None:
        meta_path = path / "meta.json"
        if not meta_path.is_file():
            return "error"
        meta = _read_json(meta_path)
    if not meta:
        return "error"
    if not meta.get("stopped_at"):
        # A process crash leaves the checkpoint metadata with no stopped_at, so
        # "no stopped_at" alone cannot mean "live" — that orphans a crashed
        # session forever (it can be neither transcribed nor deleted).
        #
        # Liveness is decided by a HEARTBEAT, not by probing a pid. The recorder
        # rewrites meta.json every HEADER_PATCH_INTERVAL seconds, so a stale file
        # means the owning process is gone.
        #
        # Do NOT use os.kill(pid, 0) here. That is the POSIX idiom, but on Windows
        # — the only platform this app ships on — any signal other than
        # CTRL_C_EVENT/CTRL_BREAK_EVENT UNCONDITIONALLY terminates the target via
        # TerminateProcess. Probing "is the recorder alive?" would have killed the
        # recorder, mid-meeting, just by opening this tab. It is also pid-reuse
        # prone. The heartbeat needs no pid and behaves identically everywhere.
        try:
            age = time.time() - (path / "meta.json").stat().st_mtime
        except OSError:
            return "recorded"
        return "recording" if age < LIVE_HEARTBEAT_SECONDS else "recorded"
    if (path / "transcript.json").is_file():
        return "transcribed"
    raw_status = str(meta.get("status") or "").lower()
    if (
        "error" in raw_status
        or "fail" in raw_status
        or meta.get("transcription_error")
    ):
        return "error"
    for stream in ("mic", "desktop"):
        stream_meta = meta.get(stream)
        if isinstance(stream_meta, dict) and stream_meta.get("error"):
            return "error"
    return "recorded"


def format_duration(seconds: float | int | None) -> str:
    """Format a session length compactly for the meeting list."""
    try:
        seconds = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError, OverflowError):
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def format_bookmark_time(seconds: float | int | None) -> str:
    try:
        value = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError, OverflowError):
        value = 0
    if value >= 3600:
        return f"{value // 3600}:{(value % 3600) // 60:02d}:{value % 60:02d}"
    return f"{value // 60}:{value % 60:02d}"


def cycle_match_index(current: int, count: int, step: int) -> int:
    """Move through ``count`` matches, wrapping in either direction."""
    if count <= 0:
        return -1
    if current < 0:
        return 0 if step >= 0 else count - 1
    return (current + (1 if step >= 0 else -1)) % count


def meetings_signature(base_dir: str | Path | None = None) -> tuple:
    """Describe changes relevant to the meeting list, ignoring live checkpoints."""
    base = Path(base_dir) if base_dir is not None else meetings_dir()
    try:
        paths = [path for path in base.glob("meeting-*") if path.is_dir()]
    except OSError:
        return ()
    signature = []
    for path in paths:
        try:
            meta = _read_json(path / "meta.json")
            transcript = path / "transcript.json"
            transcript_mtime = transcript.stat().st_mtime_ns if transcript.is_file() else None
            mtime = (
                meta.get("stopped_at"),
                meta.get("status"),
                meta.get("transcription_error"),
                tuple(
                    (meta.get(stream) or {}).get("error")
                    for stream in ("mic", "desktop")
                ),
                transcript_mtime,
            )
        except OSError:
            mtime = None
        signature.append((path.name, mtime))
    return tuple(sorted(signature))


def _started_timestamp(meta: dict, path: Path) -> float:
    value = meta.get("started_at")
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _display_started(meta: dict, timestamp: float, path: Path) -> str:
    value = meta.get("started_at")
    try:
        if value:
            started = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if started.tzinfo is not None:
                started = started.astimezone()
        else:
            started = datetime.fromtimestamp(timestamp)
        today = datetime.now().astimezone().date()
        if started.date() == today:
            day = "Today"
        elif (today - started.date()).days == 1:
            day = "Yesterday"
        else:
            day = started.strftime("%d %b")
        return f"{day} {started:%H:%M}"
    except (TypeError, ValueError, OverflowError, OSError):
        return path.name


def _speaker_names(transcript: dict, speaker_map: dict[str, str] | None = None) -> list[str]:
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        return []
    speakers = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        speaker = str((speaker_map or {}).get(
            str(segment.get("speaker") or ""), segment.get("speaker")
        ) or "").strip()
        if speaker and speaker not in speakers:
            speakers.append(speaker)
    return speakers


def _backend_label(value: object) -> str:
    backend = str(value or "").strip().lower()
    if backend == "deepgram":
        return "Deepgram"
    if backend == "local":
        return "Local whisper"
    return str(value or "").strip()


def list_sessions(base_dir: str | Path | None = None) -> list[dict]:
    """List meeting directories newest-first with display-ready metadata."""
    # Scan the configured folder AND the machine-local default. Changing "Save
    # meetings to" moves only where NEW recordings go; the old ones stay put, and
    # listing just the new folder made every past meeting vanish from the browser
    # while still sitting on disk.
    if base_dir is not None:
        roots = [Path(base_dir)]
    else:
        roots = [meetings_dir()]
        default_root = default_meetings_dir()
        if default_root not in roots:
            roots.append(default_root)

    paths = []
    seen_roots = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved in seen_roots:
            continue
        seen_roots.add(resolved)
        try:
            paths.extend(path for path in root.glob("meeting-*") if path.is_dir())
        except OSError:
            continue
    if not paths:
        return []

    sessions = []
    for path in paths:
        meta = _read_json(path / "meta.json")
        status = derive_status(path, meta)
        if status == "recorded" and not meta.get("stopped_at"):
            # Re-read before recovery: a surviving stream may have refreshed
            # the heartbeat between the first status check and this mutation.
            latest_meta = _read_json(path / "meta.json")
            if derive_status(path, latest_meta) == "recording":
                meta = latest_meta
                status = "recording"
            else:
                # Make the recovery durable so later code can use the same stop
                # gate as a normally stopped session.
                duration = float(meta.get("duration_seconds") or 0)
                try:
                    started = datetime.fromisoformat(
                        str(meta["started_at"]).replace("Z", "+00:00")
                    )
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    stopped_at = (started + timedelta(seconds=max(0, duration))).isoformat()
                except (KeyError, TypeError, ValueError, OverflowError):
                    try:
                        stopped_at = datetime.fromtimestamp(
                            (path / "meta.json").stat().st_mtime,
                            timezone.utc,
                        ).isoformat()
                    except (OSError, ValueError, OverflowError):
                        stopped_at = datetime.now(timezone.utc).isoformat()
                meta["stopped_at"] = stopped_at
                try:
                    pending = path / "meta.json.tmp"
                    pending.write_text(json.dumps(meta, indent=2), encoding="utf-8")
                    pending.replace(path / "meta.json")
                except OSError:
                    pass
        transcript = _read_json(path / "transcript.json")
        timestamp = _started_timestamp(meta, path)
        duration_seconds = meta.get("duration_seconds", 0)
        speaker_names = _speaker_names(transcript, load_speaker_map(path))
        sessions.append(
            {
                "path": path,
                "name": path.name,
                "started_at": meta.get("started_at") or "",
                "started_timestamp": timestamp,
                "display_started": _display_started(meta, timestamp, path),
                "duration_seconds": duration_seconds,
                "duration": format_duration(duration_seconds),
                "status": status,
                "can_transcribe": status != "recording"
                and _has_audio(path)
                and not (path / "transcript.json").is_file(),
                "speaker_count": len(speaker_names) or None,
                "speaker_names": speaker_names,
                "transcription_backend": _backend_label(
                    meta.get("transcription_backend")
                ) if (path / "transcript.json").is_file() else "",
                "error": meta.get("transcription_error")
                or next(
                    (
                        stream_meta.get("error")
                        for stream in ("mic", "desktop")
                        if isinstance((stream_meta := meta.get(stream)), dict)
                        and stream_meta.get("error")
                    ),
                    "",
                ),
            }
        )
    sessions.sort(
        key=lambda session: (session["started_timestamp"], session["name"]),
        reverse=True,
    )
    return sessions


def _transcript_text(session_dir: str | Path, *, polished: bool = True) -> str:
    """Render a session for Copy/Save.

    ``polished`` MUST follow the Show raw toggle. Exporting polished text while
    the screen says raw hands the user a tidied record they believe is the
    original — the one thing an overlay must never do.
    """
    transcript = _read_json(Path(session_dir) / "transcript.json")
    segments = transcript.get("segments")
    if isinstance(segments, list):
        rendered = render_transcript(
            apply_polished(segments, load_polished(session_dir)) if polished else segments,
            speaker_map=load_speaker_map(session_dir),
            corrections=load_corrections(session_dir),
        )
        if rendered:
            return rendered
    fallback = str(transcript.get("text") or "").strip()
    if fallback:
        return apply_corrections([{"text": fallback}], load_corrections(session_dir))[0]["text"]
    return fallback


def gather_session_export_data(session_dir, *, polished: bool = True) -> dict:
    """Bundle the on-disk overlays into the dict shape ``export.py`` consumes.

    The export module is deliberately Tk-free and file-free: it just turns a
    dict into Markdown, HTML, or a slide deck. Keeping the gathering here
    keeps the exporter pure (and unit-testable on a headless box) while the
    UI reads the same overlays it already shows — same transcript text, same
    speaker renames, same corrections, same bookmarks, same summaries.
    """
    path = Path(session_dir)
    meta = _read_json(path / "meta.json")
    speaker_map = load_speaker_map(path)
    transcript_text = _transcript_text(path, polished=polished)
    transcript = _read_json(path / "transcript.json")
    segments = transcript.get("segments") if isinstance(
        transcript.get("segments"), list
    ) else []
    if polished:
        visible_segments = apply_polished(segments, load_polished(path))
    else:
        visible_segments = [
            segment for segment in segments if isinstance(segment, dict)
        ]
    visible_segments = apply_corrections(visible_segments, load_corrections(path))
    bookmarks = load_bookmarks(path)
    highlights = resolve_bookmarks(bookmarks, visible_segments)
    summaries = read_summaries(path)
    duration_seconds = 0
    try:
        duration_seconds = float(meta.get("duration_seconds") or 0)
    except (TypeError, ValueError, OverflowError):
        duration_seconds = 0.0
    display_started = _display_started(
        meta, _started_timestamp(meta, path), path
    )
    return {
        "title": display_started,
        "duration_seconds": duration_seconds,
        "duration_label": format_duration(duration_seconds),
        "backend": _backend_label(meta.get("transcription_backend")),
        "speaker_names": _speaker_names(transcript, speaker_map),
        "transcript": transcript_text,
        "highlights": highlights,
        "summaries": summaries,
    }


SNIPPET_CONTEXT = 40


def session_search_text(session_dir) -> str:
    """The text a search should look at: what the user can actually SEE.

    Corrections and polish are overlays, so searching the raw transcript would
    miss a name the user has already fixed and hit wording they have replaced.
    """
    path = Path(session_dir)
    transcript = _read_json(path / "transcript.json")
    segments = transcript.get("segments")
    corrections = load_corrections(path)
    if isinstance(segments, list) and segments:
        visible = apply_polished(segments, load_polished(path))
        visible = apply_corrections(visible, corrections)
        # Speaker NAMES are searchable too — "what did Dev say" is a real query,
        # and the sidebar already lists them.
        visible = apply_speaker_map(visible, load_speaker_map(path))
        return " ".join(
            f"{s.get('speaker') or ''} {s.get('text') or ''}"
            for s in visible if isinstance(s, dict)
        )
    # The fallback text path needs the same overlay, or a corrected word is
    # findable in one meeting and not in another purely by transcript shape.
    fallback = str(transcript.get("text") or "")
    if fallback:
        return apply_corrections([{"text": fallback}], corrections)[0]["text"]
    return fallback


def search_sessions(query: str, sessions: list[dict], *, cache: dict | None = None) -> dict:
    """Map session path -> (match count, snippet) for sessions containing query.

    Case-insensitive substring, matching the in-transcript search so the two
    never disagree about whether a meeting contains a word. Reading every
    transcript on each keystroke is wasteful, so callers pass a cache keyed by
    path and mtime.
    """
    needle = " ".join(str(query or "").lower().split())
    if not needle:
        return {}
    results = {}
    for session in sessions or []:
        path = session.get("path")
        if path is None:
            continue
        # Key on EVERY file the visible text is built from. Keying on
        # transcript.json alone served pre-correction text forever: fix David to
        # Dev, search "Dev", and the meeting was missing.
        stamps = []
        for filename in ("transcript.json", "corrections.json", "polished.json",
                         "speaker_map.json"):
            try:
                stamps.append(round((Path(path) / filename).stat().st_mtime, 3))
            except OSError:
                stamps.append(None)
        if stamps[0] is None:
            continue
        key = (str(path), tuple(stamps))
        if cache is not None and key in cache:
            text = cache[key]
        else:
            text = session_search_text(path)
            if cache is not None:
                # Drop only THIS session's stale entries, not the whole cache.
                # Clearing wholesale left just the last session cached, so with
                # several meetings the cache did nothing between keystrokes.
                for stale in [k for k in cache if k[0] == str(path)]:
                    del cache[stale]
                cache[key] = text
        haystack = text.lower()
        count = haystack.count(needle)
        if not count:
            continue
        at = haystack.find(needle)
        start = max(0, at - SNIPPET_CONTEXT)
        end = min(len(text), at + len(needle) + SNIPPET_CONTEXT)
        snippet = text[start:end].strip().replace("\n", " ")
        if start > 0:
            snippet = "…" + snippet
        if end < len(text):
            snippet = snippet + "…"
        results[str(path)] = (count, snippet)
    return results


def resolve_bookmarks(bookmarks: list[dict], segments: list[dict] | None) -> list[dict]:
    """Resolve each mark to the surrounding transcript window."""
    segments = segments if isinstance(segments, list) else []
    resolved = []
    for bookmark in bookmarks or []:
        try:
            timestamp = max(0.0, float(bookmark["t"]))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        window_start = max(0.0, timestamp - BOOKMARK_LOOKBACK)
        window_end = timestamp + BOOKMARK_LOOKAHEAD
        texts = []
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                continue
            try:
                start = float(segment.get("t", segment.get("start", 0)) or 0)
                if segment.get("end") is not None:
                    end = float(segment.get("end"))
                else:
                    next_start = None
                    for following in segments[index + 1:]:
                        if isinstance(following, dict):
                            try:
                                next_start = float(following.get("t", following.get("start")))
                                break
                            except (TypeError, ValueError, OverflowError):
                                pass
                    # Without word-level end times, keep the final segment
                    # alive only for the small trailing-clause allowance. A
                    # mark in later trailing silence must not resurrect it.
                    end = next_start if next_start is not None else start + BOOKMARK_LOOKAHEAD
            except (TypeError, ValueError, OverflowError):
                continue
            if start <= window_end and end >= window_start:
                text = str(segment.get("text") or "").strip()
                if text:
                    texts.append(text)
        resolved.append({
            "t": timestamp,
            "source": bookmark.get("source"),
            "text": " ".join(texts),
            # The joined window NEVER appears literally in the rendered transcript,
            # because rendering inserts speaker labels and blank lines between
            # segments — so searching for it to scroll there always failed. One
            # whole segment does appear verbatim; jump with that.
            "first_text": texts[0] if texts else "",
            "window_start": window_start,
            "window_end": window_end,
        })
    return resolved


def _has_audio(session_dir: str | Path) -> bool:
    path = Path(session_dir)
    try:
        return any(wav.is_file() and wav.stat().st_size > 44 for wav in path.glob("*.wav"))
    except OSError:
        return False


def _correction_parts(text: str, corrections: dict[str, str]) -> list[tuple[str, bool, str | None]]:
    """Split raw text into display pieces, marking wording replacements."""
    usable = {
        str(find): str(replacement).strip()
        for find, replacement in (corrections or {}).items()
        if str(find).strip() and str(replacement).strip()
    }
    if not text or not usable:
        return [(text, False, None)]
    # Group INDEX identifies the matched key — same reasoning as
    # typer.apply_replacements, and it must stay identical to it or the widget
    # underlines a different fix than copy/export/summaries actually apply.
    keys = sorted(usable, key=len, reverse=True)
    try:
        pattern = re.compile(
            r"\b(?:" + "|".join(f"({re.escape(find)})" for find in keys) + r")\b",
            re.IGNORECASE,
        )
    except (re.error, AssertionError, OverflowError):
        return [(text, False, None)]
    parts = []
    cursor = 0
    for match in pattern.finditer(text):
        if match.start() > cursor:
            parts.append((text[cursor:match.start()], False, None))
        find = next(
            (k for index, k in enumerate(keys, start=1) if match.group(index) is not None),
            None,
        )
        if find is None:
            parts.append((match.group(0), False, None))
        else:
            parts.append((usable[find], True, find))
        cursor = match.end()
    if cursor < len(text):
        parts.append((text[cursor:], False, None))
    return parts or [(text, False, None)]


def _joined_correction_parts(
    segments: list[str], corrections: dict[str, str]
) -> list[tuple[str, bool, str | None]]:
    """Correct each raw segment before joining consecutive same-speaker text."""
    parts: list[tuple[str, bool, str | None]] = []
    for index, text in enumerate(segments):
        if index:
            parts.append((" ", False, None))
        parts.extend(_correction_parts(text, corrections))
    return parts or [("", False, None)]


def _open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _wheel_global(widget) -> None:
    widget.bind(
        "<Enter>",
        lambda _event: widget.bind_all(
            "<MouseWheel>",
            lambda event: widget.yview_scroll(int(-event.delta / 120), "units"),
        ),
    )
    widget.bind("<Leave>", lambda _event: widget.unbind_all("<MouseWheel>"))


def build(container, root, wheel=None, on_replacements_changed=None) -> None:
    """Populate ``container`` with the reusable Meetings browser."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    if wheel is None:
        wheel = _wheel_global

    style = ttk.Style(root)
    style.configure(
        "Vertical.TScrollbar",
        background=SCROLL,
        troughcolor=BG,
        bordercolor=BG,
        lightcolor=SCROLL,
        darkcolor=SCROLL,
        borderwidth=0,
        arrowcolor=MUTED,
    )
    style.map("Vertical.TScrollbar", background=[("active", SCROLL_HI)])

    state = {
        "sessions": [],
        "selected": None,
        "rows": [],
        "matches": [],
        "match_index": -1,
        "busy": False,
        "signature": (),
        "poll_after": None,
        "destroyed": False,
        "summaries": {},
        "summary_expanded": True,
        "summary_ready": False,
        "speaker_map": {},
        "corrections": {},
        "search_cache": {},
        "visible_sessions": None,
        "search_hits": {},
        "show_polished": True,
        "correction_tags": {},
    }

    head = tk.Frame(container, bg=BG, padx=18, pady=14)
    head.pack(fill="x")
    tk.Label(
        head,
        text="Meetings",
        bg=BG,
        fg=ACCENT,
        font=("Segoe UI", 14, "bold"),
    ).pack(side="left")
    count_label = tk.Label(head, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
    count_label.pack(side="left", padx=(10, 0))

    # pady must be a SINGLE distance on a widget constructor — a (0, 12) tuple is
    # only valid on pack()/grid() and raises TclError: bad screen distance "0 12",
    # which kills the window before it draws.
    body = tk.Frame(container, bg=BG, padx=18)
    body.pack(fill="both", expand=True, pady=(0, 12))

    left = tk.Frame(body, bg=CARD, width=400)
    left.pack(side="left", fill="y")
    left.pack_propagate(False)
    tk.Label(
        left,
        text="PAST SESSIONS",
        bg=CARD,
        fg=MUTED,
        anchor="w",
        font=("Segoe UI", 8, "bold"),
        padx=12,
        pady=9,
    ).pack(fill="x")
    list_wrap = tk.Frame(left, bg=CARD)
    list_wrap.pack(fill="both", expand=True)
    session_canvas = tk.Canvas(
        list_wrap,
        bg=CARD,
        highlightthickness=0,
        borderwidth=0,
        width=380,
        takefocus=True,
    )
    list_bar = ttk.Scrollbar(
        list_wrap, orient="vertical", command=session_canvas.yview
    )
    session_canvas.configure(yscrollcommand=list_bar.set)
    list_bar.pack(side="right", fill="y")
    session_canvas.pack(side="left", fill="both", expand=True)
    session_rows = tk.Frame(session_canvas, bg=CARD)
    rows_window = session_canvas.create_window(
        (0, 0), window=session_rows, anchor="nw"
    )
    session_rows.bind(
        "<Configure>",
        lambda _event: session_canvas.configure(
            scrollregion=session_canvas.bbox("all")
        ),
    )
    session_canvas.bind(
        "<Configure>",
        lambda event: session_canvas.itemconfigure(rows_window, width=event.width),
    )
    wheel(session_canvas)

    right = tk.Frame(body, bg=BG)
    right.pack(side="left", fill="both", expand=True, padx=(14, 0))
    session_title = tk.Label(
        right,
        text="Select a meeting",
        bg=BG,
        fg=FG,
        anchor="w",
        font=("Segoe UI", 11, "bold"),
    )
    session_title.pack(fill="x")
    session_meta = tk.Label(
        right,
        text="",
        bg=BG,
        fg=MUTED,
        anchor="w",
        font=("Segoe UI", 9),
    )
    session_meta.pack(fill="x", pady=(2, 8))

    search_row = tk.Frame(right, bg=BG)
    search_row.pack(fill="x", pady=(0, 8))
    search_var = tk.StringVar()
    search_entry = ttk.Entry(search_row, textvariable=search_var)
    search_entry.pack(side="left", fill="x", expand=True)
    search_all_var = tk.BooleanVar(value=False)
    search_all_check = tk.Checkbutton(
        search_row, text="All meetings", variable=search_all_var,
        bg=BG, fg=MUTED, activebackground=BG, activeforeground=FG,
        selectcolor=CARD, font=("Segoe UI", 9), takefocus=False,
    )
    search_all_check.pack(side="left", padx=(8, 0))
    prev_btn = ttk.Button(search_row, text="Prev", width=6, state="disabled")
    prev_btn.pack(side="left", padx=(7, 0))
    next_btn = ttk.Button(search_row, text="Next", width=6, state="disabled")
    next_btn.pack(side="left", padx=(5, 0))
    match_label = tk.Label(
        search_row,
        text="0/0",
        bg=BG,
        fg=MUTED,
        width=7,
        anchor="e",
        font=("Consolas", 9),
    )
    match_label.pack(side="left", padx=(7, 0))

    summarise_controls = tk.Frame(right, bg=BG)
    tk.Label(
        summarise_controls,
        text="Summarise",
        bg=BG,
        fg=FG,
        font=("Segoe UI", 9, "bold"),
    ).pack(side="left")
    summary_mode_var = tk.StringVar(value="Bullets")
    summary_mode = ttk.Combobox(
        summarise_controls,
        textvariable=summary_mode_var,
        values=("Bullets", "To-dos", "Actions"),
        state="readonly",
        width=10,
    )
    summary_mode.pack(side="left", padx=(8, 0))
    summarise_btn = ttk.Button(
        summarise_controls,
        text="Summarise",
        state="disabled",
    )
    summarise_btn.pack(side="left", padx=(7, 0))

    # A recording with no transcript has nothing to show and no obvious next
    # step — the Transcribe control was one small grey button among six at the
    # bottom edge. This banner puts the next action where the eye already is.
    needs_transcribe = tk.Frame(right, bg=CARD)
    _nt_inner = tk.Frame(needs_transcribe, bg=CARD, padx=14, pady=12)
    _nt_inner.pack(fill="x")
    tk.Label(_nt_inner, text="This recording has not been transcribed yet",
             bg=CARD, fg=FG, font=("Segoe UI", 10, "bold"),
             anchor="w").pack(side="left")
    transcribe_cta = ttk.Button(_nt_inner, text="Transcribe now",
                                style="Go.TButton")
    transcribe_cta.pack(side="right")

    # Detected speaker bleed. A warning, never an edit: the transcript keeps
    # every word and the user fixes the cause with headphones.
    bleed_banner = tk.Frame(right, bg=CARD)
    bleed_label = tk.Label(
        bleed_banner,
        text="", bg=CARD, fg=WARN, anchor="w", justify="left",
        wraplength=760, padx=14, pady=10,
    )
    bleed_label.pack(fill="x")

    highlights_panel = tk.Frame(right, bg=CARD)
    highlights_title = tk.Label(highlights_panel, text="Highlights", bg=CARD, fg=ACCENT,
                                font=("Segoe UI", 9, "bold"), anchor="w")
    highlights_title.pack(fill="x", padx=10, pady=(7, 2))
    highlights_body = tk.Frame(highlights_panel, bg=CARD)
    highlights_body.pack(fill="x", padx=10, pady=(0, 7))

    # Who-spoke figures. Computed from the transcript already on disk — no LLM,
    # no key, no cost — so it works for offline users exactly as for everyone
    # else, and appears whether or not a summary has been generated.
    stats_panel = tk.Frame(right, bg=CARD)
    stats_text = tk.Label(stats_panel, text="", bg=CARD, fg=FG, anchor="w",
                          justify="left", padx=14, pady=10,
                          font=("Consolas", 9))
    stats_text.pack(fill="x")

    summary_panel = tk.Frame(right, bg=CARD)
    summary_head = tk.Frame(summary_panel, bg=CARD)
    summary_head.pack(fill="x")
    summary_toggle = tk.Button(
        summary_head,
        text="▾ Summary",
        command=lambda: toggle_summary(),
        bg=CARD,
        fg=FG,
        activebackground=ROW_HOVER,
        activeforeground=FG,
        relief="flat",
        borderwidth=0,
        cursor="hand2",
        font=("Segoe UI", 9, "bold"),
        padx=10,
        pady=7,
    )
    summary_toggle.pack(side="left")
    summary_copy_btn = ttk.Button(summary_head, text="Copy", state="disabled")
    summary_copy_btn.pack(side="right", padx=(0, 7), pady=(5, 5))
    summary_body = tk.Frame(summary_panel, bg=CARD)
    summary_body.pack(fill="x")
    summary_text = tk.Text(
        summary_body,
        bg=CARD,
        fg=FG,
        selectbackground=ACCENT,
        selectforeground=ON_ACCENT,
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        wrap="word",
        font=("Segoe UI", 9),
        height=7,
        padx=12,
        pady=8,
        state="disabled",
    )
    summary_bar = ttk.Scrollbar(
        summary_body,
        orient="vertical",
        command=summary_text.yview,
    )
    summary_text.configure(yscrollcommand=summary_bar.set)
    summary_bar.pack(side="right", fill="y")
    summary_text.pack(side="left", fill="both", expand=True)
    wheel(summary_text)

    transcript_wrap = tk.Frame(right, bg=CARD)
    transcript_wrap.pack(fill="both", expand=True)
    transcript = tk.Text(
        transcript_wrap,
        bg=CARD,
        fg=FG,
        selectbackground=ACCENT,
        selectforeground=ON_ACCENT,
        insertbackground=FG,
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        wrap="word",
        font=("Segoe UI", 10),
        padx=12,
        pady=10,
        state="disabled",
    )
    transcript_bar = ttk.Scrollbar(
        transcript_wrap, orient="vertical", command=transcript.yview
    )
    transcript.configure(yscrollcommand=transcript_bar.set)
    transcript_bar.pack(side="right", fill="y")
    transcript.pack(side="left", fill="both", expand=True)
    wheel(transcript)
    transcript.tag_configure("speaker_you", foreground=ACCENT,
                             font=("Segoe UI", 10, "bold"), spacing1=8)
    for index, colour in enumerate(SPEAKER_COLOURS):
        transcript.tag_configure(
            f"speaker_{index}",
            foreground=colour,
            font=("Segoe UI", 10, "bold"),
            spacing1=8,
        )
    transcript.tag_configure(
        "timestamp", foreground=MUTED, font=("Segoe UI", 8)
    )
    transcript.tag_configure(
        "body", foreground=FG, font=("Segoe UI", 10),
        spacing1=4, spacing2=2, spacing3=8,
    )
    transcript.tag_configure(
        "placeholder", foreground=MUTED, font=("Segoe UI", 10),
        spacing1=4, spacing2=2, spacing3=8,
    )
    transcript.tag_configure(
        "search_match", background=SEARCH_MATCH, foreground=FG
    )
    transcript.tag_configure(
        "search_current", background=SEARCH_CURRENT, foreground=BG
    )
    summary_text.tag_configure(
        "heading", foreground=ACCENT, font=("Segoe UI", 9, "bold"), spacing1=5
    )
    summary_text.tag_configure("bullet", foreground=ACCENT, lmargin1=14, lmargin2=14)
    summary_text.tag_configure("checkbox", foreground=ACCENT, lmargin1=14, lmargin2=14)
    summary_text.tag_configure("bullet_text", foreground=FG, lmargin1=14, lmargin2=30)
    summary_text.tag_configure("body", foreground=FG)
    summary_text.tag_configure("bold", foreground=FG, font=("Segoe UI", 9, "bold"))
    summary_text.tag_configure("italic", foreground=FG, font=("Segoe UI", 9, "italic"))

    actions = tk.Frame(right, bg=BG)
    actions.pack(fill="x", pady=(10, 0))
    transcribe_btn = ttk.Button(actions, text="Transcribe", state="disabled",
                                style="Go.TButton")
    transcribe_btn.pack(side="left")
    polish_btn = ttk.Button(actions, text="Polish", state="disabled")
    polish_btn.pack(side="left", padx=(7, 0))
    toggle_polish_btn = ttk.Button(actions, text="Show raw", state="disabled")
    toggle_polish_btn.pack(side="left", padx=(7, 0))
    copy_btn = ttk.Button(actions, text="Copy", state="disabled")
    copy_btn.pack(side="left", padx=(7, 0))
    save_btn = ttk.Button(actions, text="Save as .txt", state="disabled")
    save_btn.pack(side="left", padx=(7, 0))
    export_btn = ttk.Button(actions, text="Export", state="disabled")
    export_btn.pack(side="left", padx=(7, 0))
    folder_btn = ttk.Button(actions, text="Open folder", state="disabled")
    folder_btn.pack(side="left", padx=(7, 0))
    delete_btn = ttk.Button(actions, text="Delete session", state="disabled")
    delete_btn.pack(side="right")
    status_label = tk.Label(
        right,
        text="",
        bg=BG,
        fg=MUTED,
        anchor="w",
        justify="left",
        wraplength=600,
        font=("Segoe UI", 9),
    )
    status_label.pack(fill="x", pady=(8, 0))

    def selected_path() -> Path | None:
        selected = state["selected"]
        return selected["path"] if selected else None

    def selected_summary_mode() -> str:
        return {
            "Bullets": "bullets",
            "To-dos": "todos",
            "Actions": "actions",
        }.get(summary_mode_var.get(), "bullets")

    def name_speaker(raw_speaker: str, anchor_event=None, speaker_colour=None) -> None:
        """Open the inline naming dialog for one remote diarization label."""
        if raw_speaker.casefold() == "you" or state["busy"]:
            return
        import tkinter as tk
        from tkinter import ttk

        path = selected_path()
        if path is None:
            return
        dialog = tk.Toplevel(root)
        dialog.overrideredirect(True)
        dialog.configure(bg=PALETTE["border"])
        panel = tk.Frame(dialog, bg=CARD, padx=9, pady=7)
        panel.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(
            panel, text=raw_speaker, bg=CARD, fg=speaker_colour or ACCENT,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=(0, 6))
        entry = ttk.Entry(panel, width=22)
        entry.insert(0, state["speaker_map"].get(raw_speaker, ""))
        play_btn = ttk.Button(panel, text="▶", width=2, state="disabled", takefocus=False)
        play_btn.pack(side="left")
        entry.pack(side="left", padx=(0, 6))
        if sys.platform != "win32":
            tooltip(play_btn, "Audio preview is only available on Windows.")
        save_btn = ttk.Button(panel, text="Save", style="Accent.TButton", takefocus=False)
        save_btn.pack(side="left")
        sample_status = tk.Label(panel, text="", bg=CARD, fg=MUTED)
        temp_path = {"value": None}
        closed = {"value": False}

        def stop_audio() -> None:
            if sys.platform == "win32":
                try:
                    import winsound
                    winsound.PlaySound(None, 0)
                except Exception:
                    pass

        outside_binding = {"id": None}

        def close() -> None:
            if closed["value"]:
                return
            closed["value"] = True
            stop_audio()
            if temp_path["value"]:
                try:
                    Path(temp_path["value"]).unlink()
                except OSError:
                    pass
            dialog.destroy()
            try:
                # Misc.unbind_all takes ONE argument. Passing the funcid raised
                # TypeError straight into this except, so the binding was never
                # removed and every popover leaked a permanent all-tag <Button-1>
                # handler pinning a destroyed dialog and its whole closure.
                if outside_binding["id"] is not None:
                    root.unbind_all("<Button-1>")
                    outside_binding["id"] = None
            except Exception:
                pass

        def outside_click(event) -> None:
            try:
                if event.widget.winfo_toplevel() == dialog:
                    return
            except tk.TclError:
                pass
            close()

        # Install the dismiss-on-outside-click binding only AFTER the click that
        # opened this popover has finished propagating. Binding it synchronously
        # means that very click is still in flight, outside_click sees the
        # transcript widget (correctly, it IS outside the dialog) and closes the
        # popover instantly — so clicking a speaker name appeared to do nothing.
        def arm_outside_click() -> None:
            if closed["value"]:
                return
            outside_binding["id"] = root.bind_all("<Button-1>", outside_click, add="+")

        # after(0), NOT after_idle: dialog.update_idletasks() further down runs
        # inside this same <Button-1> handler and SERVICES the idle queue, so an
        # idle callback fires before the handler even returns — reproducing the
        # exact bug it was meant to fix. Timer events are not serviced by
        # update_idletasks, so this genuinely lands after the click is dispatched.
        root.after(0, arm_outside_click)

        def play_sample() -> None:
            sample = temp_path["value"]
            if not sample or sys.platform != "win32":
                return
            try:
                import winsound
                winsound.PlaySound(str(sample), winsound.SND_FILENAME | winsound.SND_ASYNC)
                sample_status.config(text="Playing…")
            except Exception as exc:
                sample_status.config(text=f"Could not play: {exc}", fg=ACCENT)

        play_btn.config(command=play_sample)

        def save_name() -> None:
            new_name = entry.get().strip()
            updated = dict(state["speaker_map"])
            if new_name:
                updated[raw_speaker] = new_name
            else:
                updated.pop(raw_speaker, None)
            try:
                save_speaker_map(path, updated)
            except OSError as exc:
                sample_status.config(text=f"Could not save: {exc}", fg=ACCENT)
                return
            state["speaker_map"] = updated
            close()
            refresh(preferred=path, preserve_scroll=transcript.yview()[0])
            regenerate_summaries(path, updated, state["corrections"])

        save_btn.config(command=save_name)
        dialog.update_idletasks()
        if anchor_event is not None:
            x = int(anchor_event.x_root) + 5
            y = int(anchor_event.y_root) + 20
        else:
            x = root.winfo_rootx() + 20
            y = root.winfo_rooty() + 20
        x = min(max(0, x), max(0, dialog.winfo_screenwidth() - dialog.winfo_width()))
        y = min(max(0, y), max(0, dialog.winfo_screenheight() - dialog.winfo_height()))
        dialog.geometry(f"+{x}+{y}")
        entry.focus_set()
        dialog.bind("<Escape>", lambda _event: close())
        entry.bind("<Return>", lambda _event: save_name())

        def dismiss_if_outside(_event=None) -> None:
            try:
                focused = dialog.focus_get()
                if focused is None or focused.winfo_toplevel() != dialog:
                    close()
            except tk.TclError:
                close()

        dialog.bind("<FocusOut>", lambda event: dialog.after_idle(dismiss_if_outside, event))

        def prepare_sample() -> None:
            transcript_data = _read_json(path / "transcript.json")
            segments = transcript_data.get("segments")
            wav_path = path / "desktop.wav"
            if not isinstance(segments, list) or not wav_path.is_file():
                return None
            meta = _read_json(path / "meta.json")
            offsets = [
                float((meta.get(stream) or {}).get("first_block_monotonic"))
                for stream in ("mic", "desktop")
                if (meta.get(stream) or {}).get("first_block_monotonic") is not None
            ]
            desktop_offset = (meta.get("desktop") or {}).get("first_block_monotonic")
            stream_shift = (
                float(desktop_offset) - min(offsets)
                if desktop_offset is not None and offsets
                else 0.0
            )
            window = find_loudest_speaker_window(
                wav_path, segments, raw_speaker, stream_shift=stream_shift
            )
            if window is None:
                return None
            return write_wav_window(wav_path, *window, directory=tempfile.gettempdir())

        def sample_ready(sample) -> None:
            if closed["value"]:
                if sample:
                    try:
                        Path(sample).unlink()
                    except OSError:
                        pass
                return
            if sample is None or sys.platform != "win32":
                if sample:
                    try:
                        Path(sample).unlink()
                    except OSError:
                        pass
                sample_status.config(
                    text=("Audio samples unavailable on this platform." if sys.platform != "win32"
                          else "No audio sample found."),
                    fg=MUTED,
                )
                return
            temp_path["value"] = sample
            play_btn.config(state="normal")
            sample_status.config(text="")

        def sample_work() -> None:
            try:
                sample = prepare_sample()
            except Exception:
                sample = None
            try:
                root.after(0, lambda: sample_ready(sample))
            except Exception:
                pass

        threading.Thread(target=sample_work, daemon=True).start()

    def regenerate_summaries(
        path: Path,
        speaker_map: dict[str, str],
        corrections: dict[str, str] | None = None,
    ) -> None:
        existing = read_summaries(path)
        if not existing:
            return
        cfg = config.Config.load()
        if not provider_ready(cfg.cleanup_provider):
            status_label.config(
                text="Speaker renamed; saved summaries are stale until a provider is available.",
                fg=ACCENT,
            )
            return
        set_busy(True)
        status_label.config(text="Updating summaries for the new speaker name…", fg=MUTED)

        def finished(error, values) -> None:
            set_busy(False)
            if error:
                status_label.config(text=f"Summary update failed: {error}", fg=ACCENT)
                return
            state["summaries"].update(values)
            display_summary()
            status_label.config(text="Summaries updated.", fg=GOOD)

        def work() -> None:
            values = {}
            error = None
            try:
                transcript_data = _read_json(path / "transcript.json")
                segments = transcript_data.get("segments") or []
                segments = apply_corrections(
                    apply_polished(segments, load_polished(path)), corrections
                )
                bookmark_windows = resolve_bookmarks(
                    load_bookmarks(path), segments
                )
                for mode in existing:
                    value = summarise(
                        segments, mode, cfg.cleanup_provider, cfg.cleanup_model,
                        session_dir=path, speaker_map=speaker_map,
                        bookmarks=bookmark_windows,
                    )
                    if not value:
                        raise RuntimeError("the summary provider was not ready")
                    values[mode] = value
            except Exception as exc:
                error = str(exc)
            try:
                root.after(0, lambda: finished(error, values))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def toggle_summary() -> None:
        state["summary_expanded"] = not state["summary_expanded"]
        if state["summary_expanded"]:
            summary_body.pack(fill="x")
            summary_toggle.config(text="▾ Summary")
        else:
            summary_body.pack_forget()
            summary_toggle.config(text="▸ Summary")

    def display_summary(*_args) -> None:
        value = state["summaries"].get(selected_summary_mode(), "")
        summary_text.config(state="normal")
        summary_text.delete("1.0", "end")
        if value:
            lines = render_markdown_lines(value)
            for line_index, segments in enumerate(lines):
                for text, tag in segments:
                    summary_text.insert("end", text, tag)
                if line_index < len(lines) - 1:
                    summary_text.insert("end", "\n")
        summary_text.config(state="disabled")
        summary_copy_btn.config(state="normal" if value else "disabled")
        if value:
            summary_panel.pack(
                fill="x",
                pady=(0, 8),
                before=transcript_wrap,
            )
        else:
            summary_panel.pack_forget()

    def display_highlights(bookmarks, segments):
        for child in highlights_body.winfo_children():
            child.destroy()
        resolved = resolve_bookmarks(bookmarks, segments)
        if not resolved:
            highlights_panel.pack_forget()
            return
        for item in resolved:
            text = item["text"]
            preview = text[-280:].lstrip() if len(text) > 280 else text
            stamp = format_bookmark_time(item["t"])
            label = f"{stamp} — {preview}" if preview else stamp
            button = tk.Label(highlights_body, text=label, bg=CARD, fg=FG,
                              activeforeground=ACCENT, cursor="hand2", anchor="w",
                              justify="left", wraplength=560, padx=5, pady=4)
            button.pack(fill="x")
            if text:
                button.bind("<Button-1>",
                            lambda _event, needle=item.get("first_text") or text:
                            _see_transcript(needle))
        highlights_panel.pack(fill="x", pady=(0, 8), before=transcript_wrap)

    def _see_transcript(needle):
        transcript.config(state="normal")
        found = transcript.search(str(needle or ""), "1.0", "end")
        transcript.config(state="disabled")
        if found:
            transcript.see(found)

    def set_transcript(
        value: str,
        segments: list[dict] | None = None,
        *,
        placeholder: bool = False,
    ) -> None:
        transcript.config(state="normal")
        transcript.delete("1.0", "end")
        state["correction_tags"] = {}
        valid_segments = [
            segment for segment in (segments or [])
            if isinstance(segment, dict)
            and str(segment.get("text") or "").strip()
        ]
        if valid_segments:
            blocks = []
            for segment in valid_segments:
                raw_speaker = str(segment.get("speaker") or "Speaker").strip()
                body_text = str(segment.get("text") or "").strip()
                if blocks and blocks[-1]["raw_speaker"] == raw_speaker:
                    blocks[-1]["segment_texts"].append(body_text)
                else:
                    blocks.append(
                        {
                            "raw_speaker": raw_speaker,
                            "speaker": state["speaker_map"].get(raw_speaker, raw_speaker),
                            "segment_texts": [body_text],
                            "time": segment.get("t", segment.get("start", 0)),
                        }
                    )
            remote_tags = {}
            for block_index, block in enumerate(blocks):
                speaker = block["speaker"]
                raw_speaker = block["raw_speaker"]
                if speaker.casefold() == "you":
                    speaker_tag = "speaker_you"
                else:
                    if speaker not in remote_tags:
                        remote_tags[speaker] = (
                            f"speaker_{len(remote_tags) % len(SPEAKER_COLOURS)}"
                        )
                    speaker_tag = remote_tags[speaker]
                    click_tag = f"speaker_click_{block_index}"
                    transcript.tag_configure(
                        click_tag,
                        foreground=transcript.tag_cget(speaker_tag, "foreground"),
                        font=("Segoe UI", 10, "bold"),
                        spacing1=8,
                        # NO cursor= : a Text TAG has no -cursor option (only the
                        # widget does). Passing it raises TclError: unknown option
                        # "-cursor" and kills the window before it draws. The hover
                        # cursor is set on the widget by the <Enter> binding below.
                    )
                    transcript.tag_bind(
                        click_tag,
                        "<Enter>",
                        lambda _event, tag=click_tag: (
                            transcript.config(cursor="hand2"),
                            transcript.tag_configure(tag, underline=True),
                        ),
                    )
                    transcript.tag_bind(
                        click_tag,
                        "<Leave>",
                        lambda _event, tag=click_tag: (
                            transcript.config(cursor=""),
                            transcript.tag_configure(tag, underline=False),
                        ),
                    )
                    transcript.tag_bind(
                        click_tag,
                        "<Button-1>",
                        lambda event, raw=raw_speaker, colour=transcript.tag_cget(speaker_tag, "foreground"):
                            name_speaker(raw, event, colour),
                    )
                try:
                    elapsed = max(0, int(float(block["time"] or 0)))
                except (TypeError, ValueError, OverflowError):
                    elapsed = 0
                stamp = (
                    f"{elapsed // 3600}:"
                    f"{(elapsed % 3600) // 60:02d}:"
                    f"{elapsed % 60:02d}"
                    if elapsed >= 3600
                    else f"{elapsed // 60}:{elapsed % 60:02d}"
                )
                speaker_display = speaker if speaker_tag == "speaker_you" else f"{speaker}  ✎"
                transcript.insert(
                    "end", speaker_display,
                    click_tag if speaker_tag != "speaker_you" else speaker_tag,
                )
                transcript.insert("end", f"  ·  {stamp}\n", "timestamp")
                for piece_index, (piece, corrected, find) in enumerate(
                    _joined_correction_parts(block["segment_texts"], state["corrections"])
                ):
                    if corrected:
                        tag = f"correction_{block_index}_{piece_index}"
                        state["correction_tags"][tag] = find
                        transcript.tag_configure(tag, underline=True)
                        transcript.insert("end", piece, ("body", tag))
                    else:
                        transcript.insert("end", piece, "body")
                transcript.insert("end", "\n\n", "body")
        elif value:
            transcript.insert(
                "1.0", value, "placeholder" if placeholder else "body"
            )
        transcript.config(state="disabled")

    def _word_at(event) -> str:
        index = transcript.index(f"@{event.x},{event.y}")
        line, column = index.split(".", 1)
        text = transcript.get(f"{line}.0", f"{line}.end")
        if "·" in text:
            return ""
        column = int(column)
        left = right = column
        while left > 0 and not text[left - 1].isspace():
            left -= 1
        while right < len(text) and not text[right].isspace():
            right += 1
        return text[left:right].strip(".,!?;:()[]{}\"'")

    def _correction_target(event) -> tuple[str, str | None]:
        tags = transcript.tag_names(transcript.index(f"@{event.x},{event.y}"))
        existing = next(
            (state["correction_tags"].get(tag) for tag in tags
             if tag in state["correction_tags"]),
            None,
        )
        try:
            selection = transcript.get("sel.first", "sel.last")
        except tk.TclError:
            selection = ""
        click_index = transcript.index(f"@{event.x},{event.y}")
        try:
            contains_click = _selection_contains_click(
                transcript.index("sel.first"), click_index, transcript.index("sel.last")
            )
        except tk.TclError:
            contains_click = False
        if (
            contains_click
            and selection
            and "\n" not in selection
            and "·" not in selection
        ):
            return selection.strip(), existing
        return _word_at(event), existing

    def undo_correction(find: str) -> None:
        path = selected_path()
        if path is None:
            return
        updated = dict(state["corrections"])
        updated.pop(find, None)
        try:
            save_corrections(path, updated)
        except OSError as exc:
            status_label.config(text=f"Could not save: {exc}", fg=ACCENT)
            return
        state["corrections"] = updated
        refresh(preferred=path, preserve_scroll=transcript.yview()[0])
        regenerate_summaries(path, state["speaker_map"], updated)

    def fix_wording(original: str, anchor_event=None) -> None:
        path = selected_path()
        if path is None or not original.strip():
            return
        dialog = tk.Toplevel(root)
        dialog.overrideredirect(True)
        dialog.configure(bg=PALETTE["border"])
        panel = tk.Frame(dialog, bg=CARD, padx=9, pady=7)
        panel.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(panel, text=original, bg=CARD, fg=ACCENT).pack(side="left", padx=(0, 6))
        entry = ttk.Entry(panel, width=max(18, min(36, len(original) + 8)))
        entry.insert(0, original)
        entry.pack(side="left", padx=(0, 7))
        future_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            panel, text="Also fix this in future dictation", variable=future_var
        ).pack(side="left", padx=(0, 7))
        save_btn = ttk.Button(panel, text="Save", style="Accent.TButton")
        save_btn.pack(side="left")
        closed = {"value": False}

        def close() -> None:
            if not closed["value"]:
                closed["value"] = True
                dialog.destroy()

        def save_fix() -> None:
            replacement = entry.get().strip()
            if not replacement or replacement.casefold() == original.casefold():
                close()
                return
            # Only the GLOBAL store rides Settings' flat "k=v, k=v" string, so only
            # it needs clean delimiters. corrections.json is JSON and holds anything,
            # so a session-only fix to "C=64" must still be allowed.
            if future_var.get() and not keeping_fix_for_dictation_allowed(
                original, replacement
            ):
                status_label.config(
                    text="A fix kept for future dictation cannot contain a comma "
                         "or an equals sign. Untick the box to fix it here only.",
                    fg=ACCENT,
                )
                return
            # Corrections are applied per segment, so a phrase the user dragged
            # across a segment boundary matches nothing. Saving it anyway looks
            # like it worked and silently changes nothing — say so instead.
            raw_segments = _read_json(path / "transcript.json").get("segments") or []
            if isinstance(raw_segments, list) and raw_segments:
                if apply_corrections(raw_segments, {original: replacement}) == raw_segments:
                    status_label.config(
                        text=f"Could not find “{original}” as a whole phrase in one "
                             "part of the transcript, so nothing was changed.",
                        fg=ACCENT,
                    )
                    return
            updated = dict(state["corrections"])
            updated[original] = replacement
            try:
                save_corrections(path, updated)
            except OSError as exc:
                status_label.config(text=f"Could not save: {exc}", fg=ACCENT)
                return
            if future_var.get():
                cfg = config.Config.load()
                replacements = dict(cfg.replacements or {})
                replacements[original] = replacement
                cfg.replacements = replacements
                cfg.save()
                if on_replacements_changed is not None:
                    on_replacements_changed(replacements)
            state["corrections"] = updated
            close()
            refresh(preferred=path, preserve_scroll=transcript.yview()[0])
            regenerate_summaries(path, state["speaker_map"], updated)

        save_btn.config(command=save_fix)
        dialog.update_idletasks()
        x = int(anchor_event.x_root) + 5 if anchor_event is not None else root.winfo_rootx() + 20
        y = int(anchor_event.y_root) + 20 if anchor_event is not None else root.winfo_rooty() + 20
        x = min(max(0, x), max(0, dialog.winfo_screenwidth() - dialog.winfo_width()))
        y = min(max(0, y), max(0, dialog.winfo_screenheight() - dialog.winfo_height()))
        dialog.geometry(f"+{x}+{y}")
        entry.focus_set()
        dialog.bind("<Escape>", lambda _event: close())
        entry.bind("<Return>", lambda _event: save_fix())

    # One reusable menu, rebuilt per click, so nothing leaks. Do NOT create-and-destroy
    # per right-click: tk::MenuInvoke runs MenuUnpost BEFORE [$w invoke], so destroying
    # on <Unmap> kills the widget before its own command fires and the entry silently
    # does nothing at all.
    transcript_menu = tk.Menu(root, tearoff=False)

    def show_transcript_menu(event) -> str:
        if state["busy"] or selected_path() is None:
            return "break"
        target, existing = _correction_target(event)
        menu = transcript_menu
        menu.delete(0, "end")
        if existing and existing in state["corrections"]:
            menu.add_command(label="Undo this fix", command=lambda: undo_correction(existing))
        elif target:
            menu.add_command(label="Fix wording…", command=lambda: fix_wording(target, event))
        if menu.index("end") is not None:
            menu.tk_popup(event.x_root, event.y_root)
        return "break"

    transcript.bind("<Button-3>", show_transcript_menu, add="+")

    def apply_current_match() -> None:
        transcript.tag_remove("search_current", "1.0", "end")
        matches = state["matches"]
        current = state["match_index"]
        if not matches or current < 0:
            match_label.config(text="0/0")
            return
        start, end = matches[current]
        transcript.tag_add("search_current", start, end)
        transcript.see(start)
        match_label.config(text=f"{current + 1}/{len(matches)}")

    def update_search(*_args) -> None:
        transcript.tag_remove("search_match", "1.0", "end")
        transcript.tag_remove("search_current", "1.0", "end")
        state["matches"] = []
        state["match_index"] = -1
        query = search_var.get().strip()
        if query:
            start = "1.0"
            while transcript.compare(start, "<", "end"):
                count = tk.IntVar(root, 0)
                found = transcript.search(
                    query,
                    start,
                    stopindex="end",
                    nocase=True,
                    count=count,
                )
                if not found or count.get() <= 0:
                    break
                end = transcript.index(f"{found}+{count.get()}c")
                state["matches"].append((found, end))
                transcript.tag_add("search_match", found, end)
                start = end
        enabled = "normal" if state["matches"] else "disabled"
        prev_btn.config(state=enabled)
        next_btn.config(state=enabled)
        if state["matches"]:
            state["match_index"] = 0
        apply_current_match()

    def move_match(step: int) -> None:
        state["match_index"] = cycle_match_index(
            state["match_index"], len(state["matches"]), step
        )
        apply_current_match()

    def paint_row(row_info: dict, colour: str) -> None:
        for widget in row_info["widgets"]:
            widget.config(bg=colour)

    def select_by_path(path, **kwargs) -> None:
        """Select a meeting by identity, not position.

        Cross-meeting search filters the rendered list, so any caller that
        computes an index over state["sessions"] and passes it to
        select_session lands on the wrong meeting — or silently on none. Five
        separate sites got this wrong. Callers that already know which meeting
        they want must not be doing index arithmetic.
        """
        shown = state["visible_sessions"]
        if shown is None:
            shown = state["sessions"]
        index = next(
            (i for i, session in enumerate(shown) if session["path"] == path), None
        )
        if index is not None:
            select_session(index, **kwargs)

    def select_session(
        index: int, *, reveal: bool = False, preserve_search: bool = False
    ) -> None:
        # Index the list that was RENDERED. Cross-meeting search filters the
        # rows, so a row index is meaningless against the unfiltered sessions —
        # clicking the one search result selected a different meeting, and
        # Delete would then have removed the wrong recording.
        #
        # NOT `or state["sessions"]`: an empty list is falsy, so a search
        # matching NOTHING fell back to the full list and Delete stayed live on
        # an unrelated meeting. None = no filter; [] = filtered to nothing.
        shown = state["visible_sessions"]
        if shown is None:
            shown = state["sessions"]
        if state["busy"] or not 0 <= index < len(shown):
            return
        session = shown[index]
        state["selected"] = session
        for row_index, row_info in enumerate(state["rows"]):
            row_info["selected"] = row_index == index
            paint_row(row_info, POPOVER if row_info["selected"] else CARD)
        if reveal:
            session_canvas.update_idletasks()
            row_y = state["rows"][index]["frame"].winfo_y()
            content_height = max(1, session_rows.winfo_height())
            session_canvas.yview_moveto(row_y / content_height)

        speaker_names = session["speaker_names"]
        has_you = any(name.casefold() == "you" for name in speaker_names)
        other_count = len(speaker_names) - (1 if has_you else 0)
        if has_you and other_count:
            speaker_text = (
                f"You + {other_count} other{'s' if other_count != 1 else ''}"
            )
        elif has_you:
            speaker_text = "You"
        elif other_count:
            speaker_text = (
                f"{other_count} other{'s' if other_count != 1 else ''}"
            )
        else:
            speaker_text = ""
        meta_bits = [session["duration"], session["status"]]
        if speaker_text:
            meta_bits.append(speaker_text)
        if session["transcription_backend"]:
            meta_bits.append(session["transcription_backend"])
        session_title.config(text=session["display_started"])
        session_meta.config(text=" · ".join(meta_bits))

        state["speaker_map"] = load_speaker_map(session["path"])
        state["corrections"] = load_corrections(session["path"])
        transcript_data = _read_json(session["path"] / "transcript.json")
        segments = transcript_data.get("segments")
        raw_segments = segments if isinstance(segments, list) else []
        visible_segments = (
            apply_polished(raw_segments, load_polished(session["path"]))
            if state["show_polished"] else raw_segments
        )
        visible_segments = apply_corrections(visible_segments, state["corrections"])
        body_text = render_transcript(
            visible_segments, speaker_map=state["speaker_map"]
        ) or _transcript_text(session["path"], polished=state["show_polished"])
        fallback = (
            "Recording in progress. Transcription is available after it stops."
            if session["status"] == "recording"
            else "No transcript yet. Choose Transcribe to process this recording."
            if session["can_transcribe"]
            else "No transcript or usable audio was found for this session."
        )
        set_transcript(
            body_text or fallback,
            visible_segments if isinstance(segments, list) else None,
            placeholder=not bool(body_text),
        )
        display_highlights(load_bookmarks(session["path"]), visible_segments)
        has_polish = bool(load_polished(session["path"]))
        toggle_polish_btn.config(
            state="normal" if has_polish else "disabled",
            text="Show raw" if state["show_polished"] else "Show polished",
        )
        polish_btn.config(state="normal" if body_text and not state["busy"] else "disabled")
        state["summaries"] = read_summaries(session["path"])
        # Point the chooser at a mode that actually HAS a saved summary. The pane
        # only renders summaries[selected_mode], so without this a session
        # summarised as Actions looks empty on reopen (the chooser defaults to
        # Bullets) and the user's saved work appears lost.
        if state["summaries"] and selected_summary_mode() not in state["summaries"]:
            for mode, label in (("bullets", "Bullets"), ("todos", "To-dos"),
                                ("actions", "Actions")):
                if mode in state["summaries"]:
                    summary_mode_var.set(label)
                    break
        if not preserve_search:
            search_var.set("")
        can_transcribe = session["can_transcribe"] and not state["busy"]
        transcribe_btn.config(state="normal" if can_transcribe else "disabled")
        transcribe_cta.config(state="normal" if can_transcribe else "disabled")
        if session["can_transcribe"]:
            # before=transcript_wrap, NOT before=highlights_panel: that panel is
            # pack_forget() whenever a meeting has no bookmarks, and packing
            # relative to an UNPACKED widget raises TclError — which killed the
            # settings window on open for anyone with a recording still waiting
            # to be transcribed. transcript_wrap is always packed, and packing
            # these in sequence before it keeps the order.
            needs_transcribe.pack(fill="x", pady=(0, 8), before=transcript_wrap)
        else:
            needs_transcribe.pack_forget()

        who_spoke = render_stats(raw_segments) if raw_segments else ""
        if who_spoke:
            stats_text.config(text=who_spoke)
            stats_panel.pack(fill="x", pady=(0, 8), before=transcript_wrap)
        else:
            stats_panel.pack_forget()

        doubled = count_speaker_bleed(raw_segments) if raw_segments else 0
        if doubled >= 2:
            bleed_label.config(
                text=f"Your microphone also picked up the call — about {doubled} lines "
                     "appear twice, once from each side. Nothing has been removed. "
                     "Wearing headphones prevents it on the next recording."
            )
            bleed_banner.pack(fill="x", pady=(0, 8), before=transcript_wrap)
        else:
            bleed_banner.pack_forget()
        copy_btn.config(state="normal" if body_text else "disabled")
        save_btn.config(state="normal" if body_text else "disabled")
        # Export needs a transcript too. Even an empty "Export" button offers
        # the user no obvious next step, so we keep it in lockstep with Save.
        export_btn.config(state="normal" if body_text else "disabled")
        folder_btn.config(state="normal")
        can_delete = session["status"] != "recording" and not state["busy"]
        delete_btn.config(state="normal" if can_delete else "disabled")
        if body_text:
            cfg = config.Config.load()
            state["summary_ready"] = provider_ready(cfg.cleanup_provider)
            summarise_controls.pack(
                fill="x",
                pady=(0, 8),
                before=transcript_wrap,
            )
            summary_mode.config(
                state="readonly" if state["summary_ready"] and not state["busy"]
                else "disabled"
            )
            summarise_btn.config(
                state="normal" if state["summary_ready"] and not state["busy"]
                else "disabled"
            )
            if not state["summary_ready"]:
                status_label.config(
                    text=(
                        "Summarising unavailable: configure an API key or "
                        "choose local Ollama."
                    ),
                    fg=ACCENT,
                )
            else:
                status_label.config(
                    text=session["error"] or "",
                    fg=ACCENT if session["error"] else MUTED,
                )
        else:
            state["summary_ready"] = False
            summarise_controls.pack_forget()
            status_label.config(
                text=session["error"] or "",
                fg=ACCENT if session["error"] else MUTED,
            )
        display_summary()

    def add_session_row(session: dict, index: int) -> None:
        row = tk.Frame(session_rows, bg=CARD, padx=12, pady=10, cursor="hand2")
        row.pack(fill="x")
        top = tk.Frame(row, bg=CARD)
        top.pack(fill="x")
        dot_colour = {
            "transcribed": GOOD,
            "recorded": MUTED,
            "recording": AMBER,
            "error": ACCENT,
        }.get(session["status"], MUTED)
        dot = tk.Label(
            top, text="●", bg=CARD, fg=dot_colour,
            font=("Segoe UI Symbol", 9), cursor="hand2",
        )
        dot.pack(side="left", padx=(0, 7))
        tooltip(dot, session["status"].capitalize())
        title = tk.Label(
            top, text=session["display_started"], bg=CARD, fg=FG,
            font=("Segoe UI", 10, "bold"), anchor="w", cursor="hand2",
        )
        title.pack(side="left", fill="x", expand=True)
        duration = tk.Label(
            top, text=session["duration"], bg=CARD, fg=FG,
            font=("Segoe UI", 9, "bold"), anchor="e", cursor="hand2",
        )
        duration.pack(side="right")

        secondary_bits = []
        speakers = session["speaker_count"]
        if speakers is not None:
            secondary_bits.append(" · ".join(session["speaker_names"]))
        if session["transcription_backend"]:
            secondary_bits.append(session["transcription_backend"])
        if not secondary_bits:
            secondary_bits.append(
                "Recording in progress"
                if session["status"] == "recording"
                else "No transcript yet"
            )
        # When filtering across meetings, show WHY this one matched — a list of
        # dates with no context makes the user open each in turn.
        hit = state["search_hits"].get(str(session["path"]))
        if hit:
            count, snippet = hit
            tk.Label(
                row, text=f"{count} match{'' if count == 1 else 'es'}", bg=CARD,
                fg=GOOD, anchor="w", font=("Segoe UI", 8, "bold"),
            ).pack(fill="x", pady=(3, 0))
            tk.Label(
                row, text=snippet, bg=CARD, fg=FG, anchor="w", justify="left",
                wraplength=340, font=("Segoe UI", 8),
            ).pack(fill="x")

        secondary = tk.Label(
            row, text=" · ".join(secondary_bits), bg=CARD, fg=MUTED,
            font=("Segoe UI", 8), anchor="w", cursor="hand2",
        )
        secondary.pack(fill="x", padx=(19, 0), pady=(3, 0))

        widgets = (row, top, dot, title, duration, secondary)
        row_info = {
            "frame": row,
            "widgets": widgets,
            "selected": False,
            "path": session["path"],
            "duration": duration,
        }
        state["rows"].append(row_info)

        def enter(_event) -> None:
            if not row_info["selected"] and not state["busy"]:
                paint_row(row_info, ROW_HOVER)

        def leave(_event) -> None:
            paint_row(row_info, POPOVER if row_info["selected"] else CARD)

        def choose(_event) -> None:
            session_canvas.focus_set()
            select_session(index)

        for widget in widgets:
            widget.bind("<Button-1>", choose, add="+")
            widget.bind("<Enter>", enter, add="+")
            widget.bind("<Leave>", leave, add="+")

        tk.Frame(session_rows, bg=DIV, height=1).pack(fill="x", padx=12)

    def refresh(
        preferred: Path | None = None,
        *,
        preserve_scroll: float | None = None,
        preserve_search: bool = False,
    ) -> None:
        search_query = search_var.get()
        search_match_index = state["match_index"]
        transcript_scroll = transcript.yview()
        if state["rows"]:
            if preferred is None:
                preferred = selected_path()
            if preserve_scroll is None:
                view = session_canvas.yview()
                preserve_scroll = view[0] if view else 0.0
        state["sessions"] = list_sessions()
        state["signature"] = meetings_signature()
        state["rows"] = []
        for child in session_rows.winfo_children():
            child.destroy()
        # Cross-meeting search filters the LIST; the in-transcript search still
        # highlights inside whichever session you open, so the two compose.
        visible = state["sessions"]
        query = search_var.get().strip() if search_all_var.get() else ""
        if query:
            hits = search_sessions(query, state["sessions"], cache=state["search_cache"])
            state["search_hits"] = hits
            visible = [s for s in state["sessions"] if str(s["path"]) in hits]
        else:
            state["search_hits"] = {}
        state["visible_sessions"] = visible if query else None
        for index, session in enumerate(visible):
            add_session_row(session, index)
        if state["visible_sessions"] is not None:
            found = len(state["visible_sessions"])
            count_label.config(
                text=f"{found} of {len(state['sessions'])} match"
                if found else "no meetings match"
            )
        else:
            count_label.config(
                text=f"{len(state['sessions'])} session"
                f"{'' if len(state['sessions']) == 1 else 's'}"
            )
        if not state["sessions"]:
            state["selected"] = None
            # None, not []: there is no FILTER here, there are simply no
            # meetings. [] would read as "filtered to nothing".
            state["visible_sessions"] = None
            tk.Label(
                session_rows, text="No meetings recorded yet.", bg=CARD, fg=MUTED,
                font=("Segoe UI", 9), padx=14, pady=20,
            ).pack(anchor="w")
            session_title.config(text="No meetings yet")
            session_meta.config(text="")
            set_transcript(
                "Set a meeting hotkey in Settings, then press it once to start "
                "recording and again to stop.",
                placeholder=True,
            )
            for button in (
                transcribe_btn,
                transcribe_cta,
                polish_btn,
                toggle_polish_btn,
                summarise_btn,
                copy_btn,
                summary_copy_btn,
                save_btn,
                folder_btn,
                delete_btn,
                prev_btn,
                next_btn,
            ):
                button.config(state="disabled")
            match_label.config(text="0/0")
            status_label.config(text="")
            summarise_controls.pack_forget()
            summary_panel.pack_forget()
            # Delete the only untranscribed session and these stayed on screen,
            # advertising a recording that no longer exists.
            needs_transcribe.pack_forget()
            bleed_banner.pack_forget()
            highlights_panel.pack_forget()
            stats_panel.pack_forget()
            return
        # Resolve against the RENDERED list. Computing this over all sessions and
        # then indexing the filtered one left a hidden meeting selected while a
        # different one was on screen — and Delete acted on the hidden one.
        addressable = state["visible_sessions"]
        if addressable is None:
            addressable = state["sessions"]
        if not addressable:
            state["selected"] = None
            return
        target = preferred or addressable[0]["path"]
        target_index = next(
            (
                index for index, session in enumerate(addressable)
                if session["path"] == target
            ),
            0,
        )
        select_session(target_index, reveal=True, preserve_search=True)
        search_var.set(search_query)
        update_search()
        if state["matches"]:
            state["match_index"] = min(
                max(search_match_index, 0), len(state["matches"]) - 1
            )
            apply_current_match()
        if transcript_scroll:
            transcript.yview_moveto(transcript_scroll[0])
        if preserve_scroll is not None:
            session_canvas.update_idletasks()
            session_canvas.yview_moveto(preserve_scroll)

    def update_live_durations() -> None:
        now = time.time()
        selected_changed = False
        # Rows were built from the RENDERED list; zipping the unfiltered one
        # wrote a live recording's duration into whichever row happened to sit
        # at the same index.
        rendered = state["visible_sessions"]
        if rendered is None:
            rendered = state["sessions"]
        for session, row_info in zip(rendered, state["rows"]):
            if session["status"] != "recording":
                continue
            started = session.get("started_timestamp") or now
            duration_seconds = max(
                float(session.get("duration_seconds") or 0),
                now - float(started),
            )
            duration = format_duration(duration_seconds)
            if duration == session["duration"]:
                continue
            session["duration_seconds"] = duration_seconds
            session["duration"] = duration
            row_info["duration"].config(text=duration)
            if (
                state["selected"]
                and state["selected"]["path"] == session["path"]
            ):
                state["selected"] = session
                selected_changed = True
        if selected_changed:
            selected = state["selected"]
            meta_bits = [selected["duration"], selected["status"]]
            session_meta.config(text=" · ".join(meta_bits))

    def poll_sessions() -> None:
        if state["destroyed"]:
            return
        signature = meetings_signature()
        if signature != state["signature"] and not state["busy"]:
            selected = selected_path()
            view = session_canvas.yview()
            refresh(
                preferred=selected,
                preserve_scroll=view[0] if view else 0.0,
            )
        else:
            update_live_durations()
        try:
            state["poll_after"] = root.after(2000, poll_sessions)
        except Exception:
            state["poll_after"] = None

    def stop_polling(event) -> None:
        if event.widget is not container:
            return
        state["destroyed"] = True
        after_id = state["poll_after"]
        state["poll_after"] = None
        if after_id is not None:
            try:
                root.after_cancel(after_id)
            except Exception:
                pass

    def set_busy(busy: bool) -> None:
        state["busy"] = busy
        widget_state = "disabled" if busy else "normal"
        for button in (transcribe_btn, transcribe_cta, polish_btn, toggle_polish_btn,
                       summarise_btn, delete_btn):
            button.config(state=widget_state)
        summary_mode.config(state=widget_state)

    def move_session(step: int) -> str:
        # Walk the RENDERED list, or Up/Down computes a position in one list and
        # applies it to another — a filtered result became unreachable.
        walkable = state["visible_sessions"]
        if walkable is None:
            walkable = state["sessions"]
        if not walkable or state["busy"]:
            return "break"
        current = next(
            (
                index for index, session in enumerate(walkable)
                if state["selected"] and session["path"] == state["selected"]["path"]
            ),
            0,
        )
        target = min(max(0, current + step), len(walkable) - 1)
        select_session(target, reveal=True)
        return "break"

    def do_transcribe() -> None:
        path = selected_path()
        selected = state["selected"]
        if (
            path is None
            or selected is None
            or not selected["can_transcribe"]
            or state["busy"]
        ):
            return
        set_busy(True)
        status_label.config(
            text="Transcribing… long recordings can take several minutes.",
            fg=MUTED,
        )
        cfg = config.Config.load()

        def back_on_ui(callback, value) -> None:
            try:
                root.after(0, lambda: callback(value))
            except Exception:
                pass

        def succeeded(_result) -> None:
            set_busy(False)
            refresh(preferred=path)
            status_label.config(text="Transcription complete.", fg=GOOD)

        def failed(exc) -> None:
            set_busy(False)
            refresh(preferred=path)
            status_label.config(text=f"Transcription failed: {exc}", fg=ACCENT)

        def work() -> None:
            try:
                result = transcribe_session(path, cfg)
            except Exception as exc:
                back_on_ui(failed, exc)
                return
            back_on_ui(succeeded, result)

        threading.Thread(target=work, daemon=True).start()

    def do_polish() -> None:
        path = selected_path()
        if path is None or state["busy"]:
            return
        raw = _read_json(path / "transcript.json").get("segments") or []
        if not isinstance(raw, list) or not raw:
            return
        cfg = config.Config.load()
        set_busy(True)
        status_label.config(text="Polishing transcript…", fg=MUTED)

        def work() -> None:
            # Two failures the user fixes in completely different places, so name
            # which one happened rather than blending them into one message.
            try:
                overlay = polish_segments(raw, cfg.cleanup_provider, cfg.cleanup_model)
            except ProviderNotReady as exc:
                overlay = None
                error = (f"no API key set for {exc}. Settings \u2192 Transcription \u2192 "
                         "AI polish provider, then add its key.")
            except PolishFailed as exc:
                # Say exactly what went wrong. "replied with something unusable"
                # covered an API error, an empty response, unparseable text and a
                # count mismatch — four different fixes behind one sentence.
                overlay = None
                error = str(exc)
            else:
                error = None
            try:
                if overlay is not None:
                    save_polished(path, overlay)
            except Exception as exc:
                error = str(exc)
            try:
                root.after(0, lambda: (
                    set_busy(False), refresh(preferred=path),
                    status_label.config(
                        text="Polishing complete." if error is None else f"Polishing unavailable: {error}",
                        fg=GOOD if error is None else ACCENT,
                    )
                ))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def toggle_polish() -> None:
        state["show_polished"] = not state["show_polished"]
        path = selected_path()
        if path is not None:
            select_by_path(path)

    def do_summarise() -> None:
        path = selected_path()
        if (
            path is None
            or state["busy"]
            or not state["summary_ready"]
        ):
            return
        transcript_data = _read_json(path / "transcript.json")
        segments = transcript_data.get("segments")
        if not isinstance(segments, list) or not segments:
            return
        # Summarise what the user can SEE. Without this the first summary of a
        # meeting is built from the raw transcript, so it quotes the wording the
        # user already corrected. regenerate_summaries does the same for reruns.
        segments = apply_corrections(
            apply_polished(segments, load_polished(path)), load_corrections(path)
        )
        bookmark_windows = resolve_bookmarks(load_bookmarks(path), segments)
        mode = selected_summary_mode()
        cfg = config.Config.load()
        set_busy(True)
        status_label.config(text="Summarising…", fg=MUTED)

        def back_on_ui(callback, value) -> None:
            try:
                root.after(0, lambda: callback(value))
            except Exception:
                pass

        def succeeded(result: str) -> None:
            set_busy(False)
            state["summaries"][mode] = result
            state["summary_expanded"] = True
            summary_body.pack(fill="x")
            summary_toggle.config(text="▾ Summary")
            display_summary()
            summary_mode.config(state="readonly")
            summarise_btn.config(state="normal")
            selected = state["selected"]
            can_delete = bool(selected and selected["status"] != "recording")
            delete_btn.config(state="normal" if can_delete else "disabled")
            status_label.config(text="Summary complete.", fg=GOOD)

        def failed(message: str) -> None:
            set_busy(False)
            summary_mode.config(state="readonly")
            summarise_btn.config(state="normal")
            selected = state["selected"]
            can_delete = bool(selected and selected["status"] != "recording")
            delete_btn.config(state="normal" if can_delete else "disabled")
            status_label.config(
                text=f"Summarising failed: {message}",
                fg=ACCENT,
            )

        def work() -> None:
            try:
                result = summarise(
                    segments,
                    mode,
                    cfg.cleanup_provider,
                    cfg.cleanup_model,
                    session_dir=path,
                    speaker_map=load_speaker_map(path),
                    bookmarks=bookmark_windows,
                )
            except Exception as exc:
                back_on_ui(failed, str(exc))
                return
            if not result:
                back_on_ui(failed, "the provider returned no summary")
                return
            back_on_ui(succeeded, result)

        threading.Thread(target=work, daemon=True).start()

    def do_copy() -> None:
        # Follow the Show raw toggle: exporting polished text while the screen
        # says raw would hand over a tidied record believed to be the original.
        value = (_transcript_text(selected_path(), polished=state["show_polished"])
                 if selected_path() else "")
        ok = _copy_to_clipboard(root, value)
        copy_btn.config(text="Copied ✓" if ok else "Copy failed")
        root.after(1100, lambda: copy_btn.config(text="Copy"))

    def do_summary_copy() -> None:
        value = state["summaries"].get(selected_summary_mode(), "")
        ok = _copy_to_clipboard(root, value)
        summary_copy_btn.config(text="Copied ✓" if ok else "Copy failed")
        root.after(1100, lambda: summary_copy_btn.config(text="Copy"))

    def do_save() -> None:
        path = selected_path()
        if path is None:
            return
        value = _transcript_text(path, polished=state["show_polished"])
        if not value:
            return
        destination = filedialog.asksaveasfilename(
            title="Save meeting transcript",
            defaultextension=".txt",
            initialfile=f"{path.name}.txt",
            filetypes=[("Text file", "*.txt")],
        )
        if not destination:
            return
        try:
            Path(destination).write_text(value, encoding="utf-8")
            status_label.config(text=f"Saved to {destination}", fg=GOOD)
        except OSError as exc:
            status_label.config(text=f"Could not save: {exc}", fg=ACCENT)

    def do_export() -> None:
        # Export MUST honour the Show raw toggle. Writing polished text while
        # the screen says raw hands the user a tidied record they believe is
        # the original — the one thing this feature must never do.
        path = selected_path()
        if path is None:
            return
        try:
            data = gather_session_export_data(path, polished=state["show_polished"])
        except (OSError, ValueError) as exc:
            status_label.config(text=f"Could not export: {exc}", fg=ACCENT)
            return
        if not data.get("transcript"):
            status_label.config(
                text="Nothing to export — this meeting has no transcript yet.",
                fg=ACCENT,
            )
            return
        destination = filedialog.asksaveasfilename(
            title="Export meeting",
            defaultextension=".md",
            initialfile=f"{path.name}.md",
            filetypes=[
                ("Markdown", "*.md"),
                ("Slides (Marp/reveal)", "*.slides.md"),
                ("HTML (printable to PDF)", "*.html"),
            ],
        )
        if not destination:
            return
        # Validate the destination BEFORE writing — Windows rejects names with
        # characters like "<>:\"/\\|?*" or control bytes, AND reserved device
        # names like CON / PRN / AUX / NUL / COM1–COM9 / LPT1–LPT9 (with or
        # without an extension) which crash with PermissionError deep in the
        # exporter. Catching them here keeps the user message legible.
        target = Path(destination)
        forbidden_chars = '<>:"/\\|?*\0'
        basename = target.name
        stem = basename.split(".", 1)[0].upper()
        reserved = {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        if basename != basename.strip() or any(
            char in basename for char in forbidden_chars
        ) or stem in reserved:
            status_label.config(
                text=f"That filename isn't valid here: {basename}",
                fg=ACCENT,
            )
            return
        try:
            export.write_export(data, target)
            status_label.config(text=f"Exported to {target}", fg=GOOD)
        except ValueError as exc:
            # ValueError from _resolve_format: unknown extension. Tell the
            # user what we accepted, never let them guess.
            status_label.config(text=str(exc), fg=ACCENT)
        except OSError as exc:
            status_label.config(text=f"Could not export: {exc}", fg=ACCENT)

    def do_open_folder() -> None:
        path = selected_path()
        if path is None:
            return
        # The selected session can be gone by now — retention prunes older
        # recordings when a new one starts, and the list holds a path captured
        # earlier. Handing Windows a dead path raises a modal "Location is not
        # available" that looks like a crash. Fall back to the meetings folder.
        # is_dir(), not exists(): os.startfile is fire-and-forget on Windows, so
        # Explorer raises its own "Location is not available" dialog and the
        # except below can never see it. Everything must be checked BEFORE the
        # call, and whatever we do open has to be named in the status bar —
        # otherwise a failure here is indistinguishable from doing nothing.
        if not path.is_dir():
            fallback = meetings_dir()
            status_label.config(
                text=f"That recording is no longer on disk. Opening {fallback} instead.",
                fg=WARN,
            )
            refresh()
            path = fallback
        if not path.is_dir():
            status_label.config(
                text=f"Could not open {path} — the folder does not exist.", fg=ACCENT
            )
            return
        try:
            _open_folder(path)
            status_label.config(text=f"Opened {path}", fg=MUTED)
        except (OSError, subprocess.SubprocessError) as exc:
            status_label.config(text=f"Could not open folder: {exc}", fg=ACCENT)

    def do_delete() -> None:
        path = selected_path()
        selected = state["selected"]
        if (
            path is None
            or selected is None
            or selected["status"] == "recording"
            or state["busy"]
        ):
            return
        if not messagebox.askyesno(
            "Delete meeting?",
            f"Delete {path.name} and all of its audio and transcript files?",
            parent=root,
        ):
            return
        set_busy(True)
        status_label.config(text="Deleting session…", fg=MUTED)

        def finished(error) -> None:
            set_busy(False)
            if error is not None:
                status_label.config(
                    text=f"Could not delete session: {error}", fg=ACCENT
                )
                select_by_path(path)
                return
            state["selected"] = None
            refresh()

        def work() -> None:
            error = None
            try:
                shutil.rmtree(path)
            except OSError as exc:
                error = exc
            try:
                root.after(0, lambda: finished(error))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_search_changed(*_args) -> None:
        update_search()
        if search_all_var.get():
            refresh(preferred=selected_path(), preserve_scroll=None, preserve_search=True)

    search_var.trace_add("write", _on_search_changed)
    search_all_var.trace_add(
        "write",
        lambda *_a: refresh(preferred=selected_path(), preserve_search=True),
    )
    session_canvas.bind("<Up>", lambda _event: move_session(-1))
    session_canvas.bind("<Down>", lambda _event: move_session(1))
    session_canvas.bind("<Home>", lambda _event: move_session(-len(state["sessions"])))
    session_canvas.bind("<End>", lambda _event: move_session(len(state["sessions"])))
    search_entry.bind("<Return>", lambda _event: move_match(1))
    prev_btn.config(command=lambda: move_match(-1))
    next_btn.config(command=lambda: move_match(1))
    transcribe_btn.config(command=do_transcribe)
    transcribe_cta.config(command=do_transcribe)
    summarise_btn.config(command=do_summarise)
    polish_btn.config(command=do_polish)
    toggle_polish_btn.config(command=toggle_polish)
    summary_copy_btn.config(command=do_summary_copy)
    summary_mode_var.trace_add("write", display_summary)
    copy_btn.config(command=do_copy)
    save_btn.config(command=do_save)
    export_btn.config(command=do_export)
    folder_btn.config(command=do_open_folder)
    delete_btn.config(command=do_delete)
    refresh()
    container.bind("<Destroy>", stop_polling, add="+")
    state["poll_after"] = root.after(2000, poll_sessions)


def main() -> None:
    """Open the standalone Meetings browser."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return

    from . import winui

    root = tk.Tk()
    root.title("Pipevoice meetings")
    root.configure(bg=BG)
    root.minsize(780, 440)
    ico = config.asset_path("wisprlite.ico")
    if ico:
        try:
            root.iconbitmap(ico)
        except Exception:
            pass
    winui.apply_theme(root)

    closebar = tk.Frame(root, bg=BG, padx=18, pady=12)
    closebar.pack(side="bottom", fill="x")
    ttk.Button(closebar, text="Close", command=root.destroy).pack(side="right")
    build(root, root)

    root.update_idletasks()
    width = max(900, root.winfo_reqwidth())
    height = min(680, max(520, root.winfo_reqheight()))
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(
        f"{width}x{height}+{(screen_w - width) // 2}+"
        f"{(screen_h - height) // 3}"
    )
    winui.dark_titlebar(root)
    root.mainloop()


if __name__ == "__main__":
    main()
