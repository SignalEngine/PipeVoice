"""The Recordings tab: every screen recording, with its narration and settings.

Deliberately simpler than the Meetings tab. A screen recording is one clip and
one transcript, not a multi-speaker session with bookmarks and summaries, so
this browses and acts rather than edits.

The settings live here too. Splitting "where do my recordings go" from "here
are my recordings" across two tabs is the jumping-around this exists to stop.
"""

from __future__ import annotations

from . import theme
from .theme import px

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config
from .history import _copy_to_clipboard
from .meetings_tab import _open_folder, format_duration
from .winui import PALETTE, collapsible_settings, empty_state, tooltip

log = logging.getLogger("wisprlite")

BG = PALETTE["bg"]
CARD = PALETTE["card"]
FG = PALETTE["fg"]
MUTED = PALETTE["muted"]
ACCENT = PALETTE["accent"]
DIV = PALETTE["div"]
SCROLL = PALETTE["border"]
POPOVER = PALETTE["popover"]
ROW_HOVER = PALETTE["row_hover"]

VIDEO_SUFFIX = ".mp4"
NO_WORDS = "No narration in this recording."


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
    """Open the clip in whatever the user plays video with.

    Called off the Tk thread: os.startfile can sit for seconds while Windows
    resolves the file association, and on the Tk thread that froze the window.
    """
    try:
        if sys.platform == "win32":
            os.startfile(str(path))          # noqa: S606 - the user's own file
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        log.exception("recordings: could not open %s", path)


def _mss(seconds) -> str:
    """m:ss, the way the Meetings header and rows show a length."""
    total = max(0, int(seconds or 0))
    return f"{total // 60}:{total % 60:02d}"


def _when(timestamp: float) -> str:
    return time.strftime("%d %b %Y %H:%M", time.localtime(timestamp))


_DURATIONS: dict = {}


def _cached_duration(item: dict):
    """Length of a clip, read once per (file, size, mtime): the list redraws every poll."""
    key = (str(item["path"]), item["size"], item["modified"])
    if key not in _DURATIONS:
        _DURATIONS[key] = _duration_seconds(item["path"])
    return _DURATIONS[key]


def fill_recording_menu(menu, cmds: dict) -> None:
    """Items shared by the row right-click menu and the header \u22ef button.

    ``cmds`` maps play/send/copy_text/copy_path/folder/delete to callables, so
    both menus list the same labels in the same order from one place.
    """
    menu.delete(0, "end")
    menu.add_command(label="Play", command=cmds["play"])
    menu.add_command(label="Send", command=cmds["send"])
    menu.add_separator()
    menu.add_command(label="Copy text", command=cmds["copy_text"])
    menu.add_command(label="Copy path", command=cmds["copy_path"])
    menu.add_command(label="Open folder", command=cmds["folder"])
    menu.add_separator()
    menu.add_command(label="Delete\u2026", command=cmds["delete"])


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

    head = tk.Frame(container, bg=BG, padx=px(18), pady=px(14))
    head.pack(fill="x")
    tk.Label(head, text="Screen recordings", bg=BG, fg=ACCENT,
             font=theme.font("subtitle")).pack(side="left")
    count_label = tk.Label(head, text="", bg=BG, fg=MUTED, font=theme.font("body"))
    count_label.pack(side="left", padx=px((10, 0)))

    settings_link = ttk.Button(head, text="⚙  Recording settings")
    settings_panel = None

    # anchor="w" is load-bearing: a Label centres its wrapped block, so without
    # it a wraplength wider than the window clips the text at BOTH edges.
    intro = tk.Label(
        container,
        text="Press your screen recording hotkey, drag a box around what you want to "
             "show, and talk through it. Press the hotkey again to stop — you get an "
             "mp4 and a transcript of what you said.",
        bg=BG, fg=MUTED, font=theme.font("body"), justify="left", anchor="w",
        wraplength=px(736),
    )
    intro.pack(fill="x", padx=px(18), pady=px((0, 10)))
    # Re-wrap to the real window width instead of a guess that is wrong on every
    # monitor but the one it was written on.
    container.bind(
        "<Configure>",
        lambda e: intro.config(wraplength=max(px(320), e.width - px(48))),
        add="+",
    )

    body = tk.Frame(container, bg=BG)
    body.pack(fill="both", expand=True, padx=px(18), pady=px((0, 14)))

    if with_settings:
        settings_link.pack(side="right")
        tooltip(settings_link, "Hotkey, where clips are saved, and where they get sent.")
        settings_panel = collapsible_settings(
            container, settings_link,
            [(intro, {"fill": "x", "padx": 18, "pady": (0, 10)}),
             (body, {"fill": "both", "expand": True, "padx": 18, "pady": (0, 14)})],
            BG, wheel)

    empty_box = tk.Frame(body, bg=BG)
    left = tk.Frame(body, bg=CARD, width=px(250))
    left.pack(side="left", fill="y")
    left.pack_propagate(False)

    canvas = tk.Canvas(left, bg=CARD, highlightthickness=0)
    bar = ttk.Scrollbar(left, orient="vertical", command=canvas.yview,
                        style="Vertical.TScrollbar")
    canvas.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    listing = tk.Frame(canvas, bg=CARD)
    canvas.create_window((0, 0), window=listing, anchor="nw", width=px(232))
    listing.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    if callable(wheel):
        wheel(canvas)

    right = tk.Frame(body, bg=BG)
    right.pack(side="left", fill="both", expand=True, padx=px((16, 0)))

    # Same header as Meetings: title + one grey meta line left, the one filled
    # button (Send), Play and the \u22ef menu right. Right side is packed first so
    # a narrow pane clips the title, never the buttons.
    detail_head = tk.Frame(right, bg=BG)
    detail_head.pack(fill="x", pady=px((0, 10)))
    head_right = tk.Frame(detail_head, bg=BG)
    head_right.pack(side="right", padx=px((8, 0)))
    head_left = tk.Frame(detail_head, bg=BG)
    head_left.pack(side="left", fill="x", expand=True)
    title = tk.Label(head_left, text="No recording selected", bg=BG, fg=FG,
                     font=theme.font("body_large_strong"), anchor="w", justify="left")
    title.pack(fill="x")
    detail = tk.Label(head_left, text="", bg=BG, fg=MUTED, font=theme.font("body"),
                      anchor="w", justify="left")
    detail.pack(fill="x", pady=px((2, 0)))
    head_left.bind("<Configure>", lambda e: (
        title.config(wraplength=max(px(120), e.width - px(4))),
        detail.config(wraplength=max(px(120), e.width - px(4)))), add="+")

    tk.Label(right, text="What you said", bg=BG, fg=MUTED,
             font=theme.font("body_strong"), anchor="w").pack(fill="x")
    text = tk.Text(right, bg=CARD, fg=FG, relief="flat", wrap="word", height=12,
                   insertbackground=FG, padx=px(12), pady=px(10), font=theme.font("body"))
    text.pack(fill="both", expand=True, pady=px((4, 0)))
    text.configure(state="disabled")

    text.tag_configure("none", foreground=MUTED)

    def _set_transcript(body_text: str, dim: bool = False) -> None:
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", body_text, "none" if dim else "")
        text.configure(state="disabled")

    def _paint(row, colour) -> None:
        row.configure(bg=colour)
        for child in row.winfo_children():
            child.configure(bg=colour)

    def _select(item) -> None:
        state["selected"] = item
        for row, row_item in state["rows"]:
            if row_item is not None:
                _paint(row, POPOVER if row_item is item else CARD)
        for name in ("Send", "Play", "\u22ef"):
            action_buttons[name].configure(state="normal" if item else "disabled")
        if item is None:
            title.config(text="No recording selected")
            detail.config(text="")
            _set_transcript("")
            return
        title.config(text=item["stem"])
        seconds = _cached_duration(item)
        bits = [_when(item["modified"])]
        if seconds:
            bits.append(_mss(seconds))
        bits.append(_size_label(item["size"]))
        detail.config(text="  \u00b7  ".join(bits))
        words = read_transcript(item)
        if words:
            _set_transcript(words)
        else:
            _set_transcript(NO_WORDS, dim=True)

    def _show_empty(on: bool) -> None:
        if on:
            left.pack_forget()
            right.pack_forget()
            empty_box.pack(fill="both", expand=True)
        else:
            empty_box.pack_forget()
            left.pack(side="left", fill="y")
            right.pack(side="left", fill="both", expand=True, padx=px((16, 0)))

    def _build_empty() -> None:
        for child in empty_box.winfo_children():
            child.destroy()
        cfg = config.Config.load()
        key = str(getattr(cfg, "screenrec_hotkey", "") or "").strip() or "your screen recording hotkey"
        line = f"Press {key}, drag a box over what you want to show, talk, and press it again to stop."
        button = ""
        if not str(getattr(cfg, "screenrec_destination", "") or "").strip():
            line += " Set where recordings are sent first."
            button = "\u2699  Recording settings"
        block = empty_state(
            empty_box, "feat_screenrec", "No screen recordings yet", line, button,
            (lambda: settings_link.invoke()) if with_settings else None)
        if button and not with_settings and block.button is not None:
            block.button.destroy()
            block.button = None
        block.pack(fill="both", expand=True)

    def refresh(select_stem: str | None = None) -> None:
        for row, _ in state["rows"]:
            row.destroy()
        state["rows"] = []
        items = list_recordings()
        state["items"] = items
        count_label.config(text=f"{len(items)} recording{'' if len(items) == 1 else 's'}")
        if not items:
            _build_empty()
            _show_empty(True)
            _select(None)
            return
        _show_empty(False)
        for item in items:
            row = tk.Frame(listing, bg=CARD, cursor="hand2", padx=px(12), pady=px(10))
            row.pack(fill="x")
            tk.Label(row, text=item["stem"], bg=CARD, fg=FG, font=theme.font("body_strong"),
                     anchor="w", wraplength=px(200), justify="left",
                     cursor="hand2").pack(fill="x")
            sub = [_when(item["modified"])]
            seconds = _cached_duration(item)
            if seconds:
                sub.append(_mss(seconds))
            tk.Label(row, text="  \u00b7  ".join(sub), bg=CARD, fg=MUTED,
                     font=theme.font("caption"), anchor="w",
                     cursor="hand2").pack(fill="x", pady=px((3, 0)))

            def choose(event, it=item):
                _select(it)

            def context_menu(event, it=item):
                _select(it)
                fill_recording_menu(menu, cmds)
                menu.tk_popup(event.x_root, event.y_root)
                return "break"

            def enter(event, r=row, it=item):
                if state["selected"] is not it:
                    _paint(r, ROW_HOVER)

            def leave(event, r=row, it=item):
                _paint(r, POPOVER if state["selected"] is it else CARD)

            for widget in (row, *row.winfo_children()):
                widget.bind("<Button-1>", choose, add="+")
                widget.bind("<Button-3>", context_menu, add="+")
                widget.bind("<Enter>", enter, add="+")
                widget.bind("<Leave>", leave, add="+")
            state["rows"].append((row, item))
            tk.Frame(listing, bg=DIV, height=px(1)).pack(fill="x", padx=px(12))
        wanted = next((i for i in items if i["stem"] == select_stem), None)
        _select(wanted or items[0])

    def _need_selection():
        item = state["selected"]
        if item is None:
            messagebox.showinfo("Recordings", "Pick a recording first.")
        return item

    def _play_selected():
        item = _need_selection()
        if item and not Path(item["path"]).exists():
            # Deleted or moved outside PipeVoice: say so instead of opening nothing.
            messagebox.showinfo("Recordings", "That recording is gone. It was moved or deleted.")
            refresh()
        elif item:
            threading.Thread(target=_play, args=(item["path"],), daemon=True).start()

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
                "No destination is set. Open ⚙ Recording settings and put an scp target in "
                "“Send recordings to”, for example root@host:/srv/project/inbox/")
            return
        files = [item["path"]]
        if item["transcript_path"]:
            files.append(item["transcript_path"])
        # scp runs off the Tk thread. It used to run here, on the button click,
        # with a 300s timeout: an upload or a stalled connection froze the whole
        # window until it finished, and a frozen window gets killed mid-upload.
        send_btn = action_buttons["Send"]
        send_btn.configure(text="Sending…", state="disabled")

        def finished(ok, message):
            send_btn.configure(text="Send", state="normal")
            if ok:
                messagebox.showinfo("Send", f"Sent to {destination}")
            else:
                # Never claim it arrived, and never remove the local copy on failure.
                messagebox.showerror("Send failed", message)

        def work():
            t0 = time.monotonic()
            try:
                ok, message = screenrec.send(files, destination)
            except Exception as exc:   # never leave the button stuck on "Sending…"
                ok, message = False, f"{type(exc).__name__}: {exc}"
            took = time.monotonic() - t0
            log.info("recordings: send %s in %.1fs (%s)", "ok" if ok else "FAILED", took, message)
            try:
                root.after(0, lambda: finished(ok, message))
            except Exception:
                pass   # window closed mid-upload; the upload itself still finished

        # NOT a daemon: closing the window mid-upload must not kill the scp.
        # The settings process returns from mainloop and Python waits here.
        threading.Thread(target=work).start()

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

    action_buttons = {
        "Send": ttk.Button(head_right, text="Send", style="Accent.TButton",
                           command=_send_selected),
        "Play": ttk.Button(head_right, text="Play", command=_play_selected),
        "\u22ef": ttk.Button(head_right, text="\u22ef", width=3),
    }
    action_buttons["Send"].pack(side="left")
    action_buttons["Play"].pack(side="left", padx=px((7, 0)))
    action_buttons["\u22ef"].pack(side="left", padx=px((7, 0)))
    tooltip(action_buttons["Send"], "scp this recording to your configured destination.")
    tooltip(action_buttons["Play"], "Open the clip in your video player.")
    tooltip(action_buttons["\u22ef"], "Copy, open the folder, or delete.")

    menu = tk.Menu(root, tearoff=False)
    cmds = {"play": _play_selected, "send": _send_selected, "copy_text": _copy_words,
            "copy_path": _copy_path, "folder": _folder_selected, "delete": _delete_selected}

    def show_more_menu() -> None:
        if state["selected"] is None:
            return
        fill_recording_menu(menu, cmds)
        more = action_buttons["\u22ef"]
        menu.tk_popup(more.winfo_rootx(), more.winfo_rooty() + more.winfo_height())

    action_buttons["\u22ef"].config(command=show_more_menu)
    # Test seam, like pv_open_settings on the History tab.
    container.pv_recordings = {"menu": menu, "cmds": cmds, "buttons": action_buttons,
                               "fill_menu": lambda m: fill_recording_menu(m, cmds),
                               "state": state}

    refresh_btn = ttk.Button(head, text="Refresh", command=lambda: refresh(
        state["selected"]["stem"] if state["selected"] else None))
    refresh_btn.pack(side="right", padx=px((0, 12)))

    # Opened straight after a recording: land on that clip (Send is the one
    # filled button, so the obvious next move is already under the cursor).
    just_recorded = os.environ.get("PV_SELECT", "").strip()
    refresh(just_recorded or None)
    if just_recorded and state["selected"] and state["selected"]["stem"] == just_recorded:
        action_buttons["Send"].focus_set()

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
