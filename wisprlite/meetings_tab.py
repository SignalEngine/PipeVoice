"""Meeting-session browser used by Settings and ``--meetings``.

The filesystem helpers deliberately have no Tk dependency so session discovery,
status formatting, and search navigation can be tested on headless machines.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from . import config
from .history import _copy_to_clipboard
from .meeting import meetings_dir, render_transcript, transcribe_session
from .winui import PALETTE

BG = PALETTE["bg"]
CARD = PALETTE["card"]
FG = PALETTE["fg"]
MUTED = PALETTE["muted"]
ACCENT = PALETTE["accent"]
DIV = PALETTE["div"]
GOOD = PALETTE["accent_hi"]
ON_ACCENT = PALETTE["bg"]
SCROLL = PALETTE["border"]
SCROLL_HI = PALETTE["muted"]


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
        return "recording"
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


def cycle_match_index(current: int, count: int, step: int) -> int:
    """Move through ``count`` matches, wrapping in either direction."""
    if count <= 0:
        return -1
    if current < 0:
        return 0 if step >= 0 else count - 1
    return (current + (1 if step >= 0 else -1)) % count


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
        return started.strftime("%d %b %Y  %H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return path.name


def _speaker_count(transcript: dict) -> int | None:
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        return None
    speakers = {
        str(segment.get("speaker") or "").strip()
        for segment in segments
        if isinstance(segment, dict) and str(segment.get("speaker") or "").strip()
    }
    return len(speakers) or None


def list_sessions(base_dir: str | Path | None = None) -> list[dict]:
    """List meeting directories newest-first with display-ready metadata."""
    base = Path(base_dir) if base_dir is not None else meetings_dir()
    try:
        paths = [path for path in base.glob("meeting-*") if path.is_dir()]
    except OSError:
        return []

    sessions = []
    for path in paths:
        meta = _read_json(path / "meta.json")
        transcript = _read_json(path / "transcript.json")
        timestamp = _started_timestamp(meta, path)
        duration_seconds = meta.get("duration_seconds", 0)
        sessions.append(
            {
                "path": path,
                "name": path.name,
                "started_at": meta.get("started_at") or "",
                "started_timestamp": timestamp,
                "display_started": _display_started(meta, timestamp, path),
                "duration_seconds": duration_seconds,
                "duration": format_duration(duration_seconds),
                "status": derive_status(path, meta),
                "can_transcribe": bool(meta.get("stopped_at"))
                and _has_audio(path)
                and not (path / "transcript.json").is_file(),
                "speaker_count": _speaker_count(transcript),
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


def _transcript_text(session_dir: str | Path) -> str:
    transcript = _read_json(Path(session_dir) / "transcript.json")
    segments = transcript.get("segments")
    if isinstance(segments, list):
        rendered = render_transcript(segments)
        if rendered:
            return rendered
    return str(transcript.get("text") or "").strip()


def _has_audio(session_dir: str | Path) -> bool:
    path = Path(session_dir)
    try:
        return any(wav.is_file() and wav.stat().st_size > 44 for wav in path.glob("*.wav"))
    except OSError:
        return False


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


def build(container, root, wheel=None) -> None:
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
        "matches": [],
        "match_index": -1,
        "busy": False,
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
    session_list = tk.Listbox(
        list_wrap,
        bg=CARD,
        fg=FG,
        selectbackground=ACCENT,
        selectforeground=ON_ACCENT,
        activestyle="none",
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        exportselection=False,
        font=("Consolas", 9),
        # 52, not 39: a row is "27 Jul 2026  16:15" + right-aligned duration +
        # status + speaker count. At 39 the longest status ("transcribed") and
        # the speaker suffix were clipped off the right edge.
        width=52,
    )
    list_bar = ttk.Scrollbar(
        list_wrap, orient="vertical", command=session_list.yview
    )
    session_list.configure(yscrollcommand=list_bar.set)
    list_bar.pack(side="right", fill="y")
    session_list.pack(side="left", fill="both", expand=True)
    wheel(session_list)

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
    transcript.tag_configure("search_match", background=DIV, foreground=FG)
    transcript.tag_configure(
        "search_current", background=ACCENT, foreground=ON_ACCENT
    )

    actions = tk.Frame(right, bg=BG)
    actions.pack(fill="x", pady=(10, 0))
    transcribe_btn = ttk.Button(actions, text="Transcribe", state="disabled")
    transcribe_btn.pack(side="left")
    copy_btn = ttk.Button(actions, text="Copy", state="disabled")
    copy_btn.pack(side="left", padx=(7, 0))
    save_btn = ttk.Button(actions, text="Save as .txt", state="disabled")
    save_btn.pack(side="left", padx=(7, 0))
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

    def set_transcript(value: str) -> None:
        transcript.config(state="normal")
        transcript.delete("1.0", "end")
        if value:
            transcript.insert("1.0", value)
        transcript.config(state="disabled")

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

    def select_session(_event=None, preferred: Path | None = None) -> None:
        if preferred is not None:
            for index, session in enumerate(state["sessions"]):
                if session["path"] == preferred:
                    session_list.selection_clear(0, "end")
                    session_list.selection_set(index)
                    session_list.activate(index)
                    session_list.see(index)
                    break
        selection = session_list.curselection()
        if not selection:
            return
        session = state["sessions"][selection[0]]
        state["selected"] = session
        speaker_count = session["speaker_count"]
        speaker_text = (
            f" · {speaker_count} speaker{'s' if speaker_count != 1 else ''}"
            if speaker_count is not None
            else ""
        )
        session_title.config(text=session["display_started"])
        session_meta.config(
            text=f"{session['duration']} · {session['status']}{speaker_text}"
        )
        body_text = _transcript_text(session["path"])
        set_transcript(
            body_text
            or (
                "Recording in progress. Transcription is available after it stops."
                if session["status"] == "recording"
                else "No transcript yet. Choose Transcribe to process this recording."
                if session["can_transcribe"]
                else "No transcript or usable audio was found for this session."
            )
        )
        search_var.set("")
        can_transcribe = session["can_transcribe"] and not state["busy"]
        transcribe_btn.config(state="normal" if can_transcribe else "disabled")
        copy_btn.config(state="normal" if body_text else "disabled")
        save_btn.config(state="normal" if body_text else "disabled")
        folder_btn.config(state="normal")
        can_delete = session["status"] != "recording" and not state["busy"]
        delete_btn.config(state="normal" if can_delete else "disabled")
        status_label.config(
            text=session["error"] or "",
            fg=ACCENT if session["error"] else MUTED,
        )

    def refresh(preferred: Path | None = None) -> None:
        state["sessions"] = list_sessions()
        session_list.delete(0, "end")
        for session in state["sessions"]:
            speakers = session["speaker_count"]
            speaker_text = f"  {speakers}spk" if speakers is not None else ""
            session_list.insert(
                "end",
                f"{session['display_started']}  {session['duration']:>8}  "
                f"{session['status']}{speaker_text}",
            )
        count_label.config(
            text=f"{len(state['sessions'])} session"
            f"{'' if len(state['sessions']) == 1 else 's'}"
        )
        if not state["sessions"]:
            state["selected"] = None
            session_list.insert("end", "No meetings recorded yet.")
            session_list.config(state="disabled")
            session_title.config(text="No meetings yet")
            session_meta.config(text="")
            set_transcript(
                "Set a meeting hotkey in Settings, then press it once to start "
                "recording and again to stop."
            )
            for button in (
                transcribe_btn,
                copy_btn,
                save_btn,
                folder_btn,
                delete_btn,
                prev_btn,
                next_btn,
            ):
                button.config(state="disabled")
            match_label.config(text="0/0")
            status_label.config(text="")
            return
        session_list.config(state="normal")
        target = preferred or state["sessions"][0]["path"]
        select_session(preferred=target)

    def set_busy(busy: bool) -> None:
        state["busy"] = busy
        session_list.config(state="disabled" if busy else "normal")
        for button in (transcribe_btn, delete_btn):
            button.config(state="disabled")

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

    def do_copy() -> None:
        value = _transcript_text(selected_path()) if selected_path() else ""
        ok = _copy_to_clipboard(root, value)
        copy_btn.config(text="Copied ✓" if ok else "Copy failed")
        root.after(1100, lambda: copy_btn.config(text="Copy"))

    def do_save() -> None:
        path = selected_path()
        if path is None:
            return
        value = _transcript_text(path)
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

    def do_open_folder() -> None:
        path = selected_path()
        if path is None:
            return
        try:
            _open_folder(path)
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
                select_session(preferred=path)
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

    session_list.bind("<<ListboxSelect>>", select_session)
    search_var.trace_add("write", update_search)
    search_entry.bind("<Return>", lambda _event: move_match(1))
    prev_btn.config(command=lambda: move_match(-1))
    next_btn.config(command=lambda: move_match(1))
    transcribe_btn.config(command=do_transcribe)
    copy_btn.config(command=do_copy)
    save_btn.config(command=do_save)
    folder_btn.config(command=do_open_folder)
    delete_btn.config(command=do_delete)
    refresh()


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
