"""The Recordings tab: every screen recording, with its narration and settings.

Deliberately simpler than the Meetings tab. A screen recording is one clip and
one transcript, not a multi-speaker session with bookmarks and summaries, so
this browses and acts rather than edits.

The settings live here too. Splitting "where do my recordings go" from "here
are my recordings" across two tabs is the jumping-around this exists to stop.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import config
from .history import _copy_to_clipboard
from .meetings_tab import _open_folder, format_duration
from .winui import PALETTE, collapsible_settings, tooltip

BG = PALETTE["bg"]
CARD = PALETTE["card"]
FG = PALETTE["fg"]
MUTED = PALETTE["muted"]
ACCENT = PALETTE["accent"]
DIV = PALETTE["div"]
SCROLL = PALETTE["border"]

VIDEO_SUFFIX = ".mp4"


def recordings_dir(cfg=None) -> Path:
    """Where clips live: the user's folder if set and usable, else the default."""
    from . import screenrec

    cfg = cfg or config.Config.load()
    chosen = str(getattr(cfg, "screenrec_dir", "") or "").strip()
    if chosen:
        candidate = Path(chosen).expanduser()
        if candidate.is_dir():
            return candidate
    return screenrec.default_output_dir()


def _size_label(size: int) -> str:
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.1f} GB"
    if size >= 1_000_000:
        return f"{size / 1_000_000:.0f} MB"
    return f"{max(1, size // 1000)} KB"


def _duration_seconds(path: Path) -> float | None:
    """Length of the clip, or None when it cannot be read.

    Read from the container rather than guessed from file size: bitrate varies
    with how much of the screen is moving, so size is not a proxy for length.
    """
    try:
        import av

        with av.open(str(path)) as container:
            if container.duration:
                return float(container.duration) / 1_000_000.0
    except Exception:
        pass
    return None


def list_recordings(base_dir=None) -> list[dict]:
    """Every clip in the folder, newest first, each with its transcript if present."""
    base = Path(base_dir) if base_dir is not None else recordings_dir()
    try:
        videos = [p for p in base.iterdir() if p.suffix.lower() == VIDEO_SUFFIX]
    except OSError:
        return []
    out = []
    for video in videos:
        try:
            stat = video.stat()
        except OSError:
            continue
        transcript = video.with_suffix(".txt")
        out.append({
            "path": video,
            "stem": video.stem,
            "modified": stat.st_mtime,
            "size": stat.st_size,
            "transcript_path": transcript if transcript.exists() else None,
        })
    out.sort(key=lambda item: item["modified"], reverse=True)
    return out


def read_transcript(item: dict) -> str:
    path = item.get("transcript_path")
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _play(path: Path) -> None:
    """Open the clip in whatever the user plays video with."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))          # noqa: S606 - the user's own file
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def build(container, root, wheel=None, with_settings=False):
    """Populate ``container`` with the recordings browser.

    Returns the (empty, hidden) settings panel when ``with_settings`` is set, so
    the caller can fill it once its own form helpers exist. Returning the frame
    rather than taking a builder keeps the ordering simple: this tab is built
    early, the settings widgets are defined later.
    """
    import tkinter as tk
    from tkinter import messagebox, ttk

    state = {"items": [], "selected": None, "rows": [], "signature": (),
             "poll_after": None, "destroyed": False}

    head = tk.Frame(container, bg=BG, padx=18, pady=14)
    head.pack(fill="x")
    tk.Label(head, text="Recordings", bg=BG, fg=ACCENT,
             font=("Segoe UI", 14, "bold")).pack(side="left")
    count_label = tk.Label(head, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
    count_label.pack(side="left", padx=(10, 0))

    settings_link = tk.Label(head, text="Settings  ⚙", bg=BG, fg=ACCENT,
                             cursor="hand2", font=("Segoe UI", 9, "underline"))
    settings_panel = None

    # anchor="w" is load-bearing: a Label centres its wrapped block, so without
    # it a wraplength wider than the window clips the text at BOTH edges.
    intro = tk.Label(
        container,
        text="Press your screen recording hotkey, drag a box around what you want to "
             "show, and talk through it. Press the hotkey again to stop — you get an "
             "mp4 and a transcript of what you said.",
        bg=BG, fg=MUTED, font=("Segoe UI", 9), justify="left", anchor="w",
        wraplength=640,
    )
    intro.pack(fill="x", padx=18, pady=(0, 10))
    # Re-wrap to the real window width instead of a guess that is wrong on every
    # monitor but the one it was written on.
    container.bind(
        "<Configure>",
        lambda e: intro.config(wraplength=max(320, e.width - 48)),
        add="+",
    )

    body = tk.Frame(container, bg=BG)
    body.pack(fill="both", expand=True, padx=18, pady=(0, 14))

    if with_settings:
        settings_link.pack(side="right")
        tooltip(settings_link, "Hotkey, where clips are saved, and where they get sent.")
        settings_panel = collapsible_settings(
            container, settings_link,
            [(intro, {"fill": "x", "padx": 18, "pady": (0, 10)}),
             (body, {"fill": "both", "expand": True, "padx": 18, "pady": (0, 14)})],
            BG, wheel)

    left = tk.Frame(body, bg=BG, width=250)
    left.pack(side="left", fill="y")
    left.pack_propagate(False)

    canvas = tk.Canvas(left, bg=BG, highlightthickness=0)
    bar = ttk.Scrollbar(left, orient="vertical", command=canvas.yview,
                        style="Vertical.TScrollbar")
    canvas.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    listing = tk.Frame(canvas, bg=BG)
    canvas.create_window((0, 0), window=listing, anchor="nw", width=232)
    listing.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    if callable(wheel):
        wheel(canvas)

    right = tk.Frame(body, bg=BG)
    right.pack(side="left", fill="both", expand=True, padx=(16, 0))

    title = tk.Label(right, text="No recording selected", bg=BG, fg=FG,
                     font=("Segoe UI", 12, "bold"), anchor="w")
    title.pack(fill="x")
    detail = tk.Label(right, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w")
    detail.pack(fill="x", pady=(2, 10))

    buttons = tk.Frame(right, bg=BG)
    buttons.pack(fill="x", pady=(0, 10))

    tk.Label(right, text="What you said", bg=BG, fg=MUTED,
             font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
    text = tk.Text(right, bg=CARD, fg=FG, relief="flat", wrap="word", height=12,
                   insertbackground=FG, padx=12, pady=10, font=("Segoe UI", 10))
    text.pack(fill="both", expand=True, pady=(4, 0))
    text.configure(state="disabled")

    def _set_transcript(body_text: str) -> None:
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", body_text)
        text.configure(state="disabled")

    def _select(item) -> None:
        state["selected"] = item
        for row, row_item in state["rows"]:
            on = row_item is item
            row.configure(bg=CARD if on else BG)
            for child in row.winfo_children():
                child.configure(bg=CARD if on else BG)
        if item is None:
            title.config(text="No recording selected")
            detail.config(text="")
            _set_transcript("")
            return
        title.config(text=item["stem"])
        seconds = _duration_seconds(item["path"])
        bits = [_size_label(item["size"])]
        if seconds:
            bits.insert(0, format_duration(seconds))
        bits.append(str(item["path"].parent))
        detail.config(text="  ·  ".join(bits))
        words = read_transcript(item)
        _set_transcript(words or "No transcript for this one — check the microphone "
                                 "was on when you recorded it.")

    def refresh(select_stem: str | None = None) -> None:
        for row, _ in state["rows"]:
            row.destroy()
        state["rows"] = []
        items = list_recordings()
        state["items"] = items
        count_label.config(text=f"{len(items)} recording{'' if len(items) == 1 else 's'}")
        if not items:
            empty = tk.Frame(listing, bg=BG)
            empty.pack(fill="x", pady=6)
            tk.Label(empty, text="Nothing recorded yet.", bg=BG, fg=MUTED,
                     font=("Segoe UI", 9), wraplength=260, justify="left").pack(anchor="w")
            state["rows"] = [(empty, None)]
            _select(None)
            return
        for item in items:
            row = tk.Frame(listing, bg=BG, cursor="hand2", padx=10, pady=8)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=item["stem"], bg=BG, fg=FG, font=("Segoe UI", 10),
                     anchor="w", wraplength=250, justify="left").pack(fill="x")
            marks = [_size_label(item["size"])]
            if item["transcript_path"] is None:
                marks.append("no transcript")
            tk.Label(row, text="  ·  ".join(marks), bg=BG, fg=MUTED,
                     font=("Segoe UI", 8), anchor="w").pack(fill="x")
            for widget in (row, *row.winfo_children()):
                widget.bind("<Button-1>", lambda e, it=item: _select(it))
            state["rows"].append((row, item))
        wanted = next((i for i in items if i["stem"] == select_stem), None)
        _select(wanted or items[0])

    def _need_selection():
        item = state["selected"]
        if item is None:
            messagebox.showinfo("Recordings", "Pick a recording first.")
        return item

    def _play_selected():
        item = _need_selection()
        if item:
            _play(item["path"])

    def _folder_selected():
        item = _need_selection()
        if item:
            _open_folder(item["path"].parent)

    def _copy_path():
        item = _need_selection()
        if item:
            _copy_to_clipboard(root, str(item["path"]))

    def _copy_words():
        item = _need_selection()
        if item:
            _copy_to_clipboard(root, read_transcript(item))

    def _send_selected():
        item = _need_selection()
        if not item:
            return
        from . import screenrec

        destination = str(getattr(config.Config.load(), "screenrec_destination", "") or "").strip()
        if not destination:
            messagebox.showinfo(
                "Send",
                "No destination is set. Open Settings above and put an scp target in "
                "“Send recordings to”, for example root@host:/srv/project/inbox/")
            return
        files = [item["path"]]
        if item["transcript_path"]:
            files.append(item["transcript_path"])
        ok, message = screenrec.send(files, destination)
        if ok:
            messagebox.showinfo("Send", f"Sent to {destination}")
        else:
            # Never claim it arrived, and never remove the local copy on failure.
            messagebox.showerror("Send failed", message)

    def _delete_selected():
        item = _need_selection()
        if not item:
            return
        if not messagebox.askyesno(
                "Delete recording",
                f"Delete “{item['stem']}”?\n\nThe video, its transcript and its "
                "audio are removed from this PC. Anything already sent elsewhere stays."):
            return
        for path in (item["path"], item["transcript_path"],
                     item["path"].with_suffix(".wav")):
            if not path:
                continue
            try:
                Path(path).unlink()
            except OSError:
                pass
        refresh()

    # Two rows of three. One row of six overflowed the pane and silently cut the
    # last two buttons off the right edge — an action you cannot see is an
    # action you do not have.
    actions = (
        ("Play", _play_selected, "Open the clip in your video player."),
        ("Send", _send_selected, "scp this recording to your configured destination."),
        ("Folder", _folder_selected, "Open the containing folder."),
        ("Copy path", _copy_path, "Copy the file path, to paste to an agent."),
        ("Copy text", _copy_words, "Copy the transcript."),
        ("Delete", _delete_selected, "Remove the video, transcript and audio from this PC."),
    )
    action_buttons = {}
    for index, (label, command, tip) in enumerate(actions):
        btn = ttk.Button(buttons, text=label, command=command, width=11)
        btn.grid(row=index // 3, column=index % 3, padx=(0, 5), pady=(0, 5), sticky="w")
        tooltip(btn, tip)
        action_buttons[label] = btn

    refresh_btn = ttk.Button(head, text="Refresh", command=lambda: refresh(
        state["selected"]["stem"] if state["selected"] else None))
    refresh_btn.pack(side="right", padx=(0, 12))

    # Opened straight after a recording: land on that clip with Play accented,
    # so the obvious next move is the one under the cursor rather than something
    # to go hunting for. Any other way of opening the tab is unaffected.
    just_recorded = os.environ.get("PV_SELECT", "").strip()
    refresh(just_recorded or None)
    if just_recorded and state["selected"] and state["selected"]["stem"] == just_recorded:
        action_buttons["Play"].configure(style="Accent.TButton")
        action_buttons["Play"].focus_set()

    # Poll, the way the Meetings tab does. Without this the tab is a snapshot
    # from whenever it was opened: record with it on screen and it still says
    # "0 recordings", and a clip listed in the gap between the mux finishing and
    # the transcript being written is stuck reading "no transcript" for ever.
    # James hit exactly that, and the recording he sent to prove it was a video
    # of this tab saying "Nothing recorded yet."
    def _signature():
        return tuple((item["stem"], item["size"], item["transcript_path"] is not None)
                     for item in list_recordings())

    def poll():
        if state.get("destroyed"):
            return
        try:
            # Read the folder ONCE. Scanning twice stores a signature from a
            # later read than the one that triggered the render, so a file that
            # lands between the two reads is recorded as already-seen and never
            # gets drawn.
            current = _signature()
            if current != state.get("signature"):
                state["signature"] = current
                refresh(state["selected"]["stem"] if state["selected"] else None)
        except Exception:
            pass                     # a polling loop must not kill the window
        state["poll_after"] = root.after(2000, poll)

    def stop_polling(event=None):
        if event is not None and event.widget is not container:
            return
        state["destroyed"] = True
        after_id = state.get("poll_after")
        if after_id:
            try:
                root.after_cancel(after_id)
            except Exception:
                pass

    state["signature"] = _signature()
    container.bind("<Destroy>", stop_polling, add="+")
    state["poll_after"] = root.after(2000, poll)
    return settings_panel
