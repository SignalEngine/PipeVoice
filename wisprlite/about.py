"""About / Updates: current version, live update check, "Update now", changelog.

`build(container, root, wheel)` populates any frame, so the same UI is used both
as the standalone --about window and as the About tab inside Settings. `main()`
wraps it in its own short-lived Tk process.
"""

from __future__ import annotations

import os
import re
import threading
import webbrowser

from . import __version__, config

BG = "#13151d"
CARD = "#1b1e29"
FG = "#e5e7eb"
MUTED = "#94a3b8"
ACCENT = "#e06c75"
GOOD = "#98c379"
WARN = "#e5c07b"

RELEASES_URL = "https://github.com/Powleads/PipeVoice/releases"
GITHUB_URL = "https://github.com/Powleads/PipeVoice"



FEATURES = (
    ("Dictation", "Hold your hotkey (Ctrl + \\ by default), talk, let go: your words "
                  "land wherever the cursor is."),
    ("Meetings", "The meeting hotkey (Ctrl + Alt + M by default) records both sides of a "
                 "call. Then transcribe it, name the speakers, get notes and actions, and "
                 "see their questions answered from your docs. Meetings tab; "
                 "Guide \u2192 Meetings shows how."),
    ("Show a bug", "The recording hotkey (Ctrl + Alt + R by default): drag a box and talk "
                   "it through. You get an .mp4 plus a transcript of what you said. "
                   "Recordings tab."),
    ("Read aloud", "Box anything on screen and hear it. Set its hotkey under "
                   "Settings \u2192 Hotkeys."),
    ("Voices", "Different styles per app or hotkey. Voices tab."),
)


def _open_feedback() -> None:
    import os
    import subprocess
    import sys
    try:
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable, "--feedback"])
        else:
            from .autostart import _pythonw
            parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            subprocess.Popen([_pythonw(), "-m", "wisprlite", "--feedback"], cwd=parent)
    except Exception:
        pass


def _clean_notes(body: str) -> str:
    """Tidy GitHub's auto-generated release notes into readable lines."""
    lines = []
    for raw in (body or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.lower().startswith("**full changelog**") or s.lower().startswith("## what"):
            continue
        s = re.sub(r"^#{1,6}\s*", "", s)          # drop markdown headers
        s = re.sub(r"^[\*\-]\s*", "• ", s)         # bullets
        # strip "by @user in #123" and "by @user in https://.../pull/123"
        s = re.sub(r"\s+by @\S+ in (?:#\d+|https?://\S+)", "", s)
        s = re.sub(r"\s+in https?://\S+", "", s)
        lines.append(s)
    return "\n".join(lines).strip()


def _date(iso: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    if not m:
        return ""
    months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    y, mo, d = m.groups()
    return f"{int(d)} {months[int(mo)]} {y}"


def _wheel_global(canvas):
    canvas.bind("<Enter>", lambda e: canvas.bind_all(
        "<MouseWheel>", lambda ev: canvas.yview_scroll(int(-ev.delta / 120), "units")))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))


def build(container, root, wheel=None) -> None:
    """Populate `container` with the About/Updates UI. `root` is the toplevel
    (used for thread-safe .after); `wheel` scopes mousewheel to our canvas."""
    import tkinter as tk
    from tkinter import ttk
    from . import updater

    if wheel is None:
        wheel = _wheel_global
    state = {"info": None}

    head = tk.Frame(container, bg=BG, padx=28, pady=24)
    head.pack(fill="x")
    from . import branding
    branding.lockup_label(head, BG).pack(anchor="w")
    tk.Label(head, text=f"Version {__version__}", bg=BG, fg=MUTED,
             font=("Consolas", 10)).pack(anchor="w", pady=(3, 16))

    status = tk.Label(head, text="Checking for updates…", bg=BG, fg=MUTED, font=("Segoe UI", 10))
    status.pack(anchor="w")
    actions = tk.Frame(head, bg=BG)
    actions.pack(anchor="w", fill="x", pady=(14, 0))
    btn = ttk.Button(actions, text="Checking…", state="disabled", style="Accent.TButton")
    btn.pack(side="left")
    ttk.Button(actions, text="⭐ Star on GitHub",
               command=lambda: webbrowser.open(GITHUB_URL)).pack(side="left", padx=(10, 0))
    link = tk.Label(actions, text="All releases ↗", bg=BG, fg=ACCENT, cursor="hand2",
                    font=("Segoe UI", 9, "underline"))
    link.pack(side="left", padx=(16, 0))
    link.bind("<Button-1>", lambda e: webbrowser.open(RELEASES_URL))
    fb = tk.Label(actions, text="Send feedback ↗", bg=BG, fg=ACCENT, cursor="hand2",
                  font=("Segoe UI", 9, "underline"))
    fb.pack(side="left", padx=(16, 0))
    fb.bind("<Button-1>", lambda e: _open_feedback())

    bodyf = tk.Frame(container, bg=BG)
    bodyf.pack(fill="both", expand=True)
    canvas = tk.Canvas(bodyf, bg=BG, highlightthickness=0, width=480, height=280)
    vbar = ttk.Scrollbar(bodyf, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vbar.set)
    vbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    from . import winui

    inner = tk.Frame(canvas, bg=BG)
    inner_window = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    # Without this the inner frame keeps its natural width and hugs the left
    # edge once the canvas grows past it — the classic Tk scrollable-frame trap,
    # and a maximised window is the normal case now, not an edge case.
    winui.fit_scroll_body(canvas, inner_window)
    wheel(canvas)

    # What the app does and where each part lives. Release notes below say what
    # CHANGED; this says what is THERE, which the notes never do.
    does = tk.Frame(inner, bg=BG, padx=28)
    does.pack(fill="x", pady=(6, 0))
    tk.Label(does, text="WHAT IT DOES", bg=BG, fg=ACCENT,
             font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
    for name, what in FEATURES:
        row = tk.Frame(does, bg=BG)
        row.pack(fill="x", pady=1)
        tk.Label(row, text=name, bg=BG, fg=FG, width=16, anchor="w",
                 font=("Segoe UI", 10, "bold")).pack(side="left", anchor="n")
        what_label = tk.Label(row, text=what, bg=BG, fg=MUTED, anchor="w", justify="left",
                              wraplength=320, font=("Segoe UI", 10))
        what_label.pack(side="left", fill="x")
        # Wrap to the window, not a fixed width: a fixed 760px stopped the
        # whole tab from narrowing.
        row.bind("<Configure>", lambda e, lab=what_label: lab.config(
            wraplength=max(200, min(760, e.width - 150))), add="+")

    tk.Label(inner, text="WHAT'S NEW", bg=BG, fg=ACCENT,
             font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=28, pady=(20, 8))

    # Release notes load later and replace only this frame, not the block above.
    notes = tk.Frame(inner, bg=BG)
    notes.pack(fill="x")


    def _exit_for_install():
        # This window runs as its own process and, in the onedir build, holds an
        # open lock on the very files the installer must replace (Pipevoice.exe,
        # python311.dll). Closing it frees those locks so the silent installer can
        # swap files and relaunch the app; leaving it open is what made the update
        # hang on "Installing…" forever. Exit promptly (before the installer takes
        # its Restart-Manager snapshot) so it isn't reopened after the update.
        try:
            root.destroy()
        except Exception:
            pass
        os._exit(0)

    def do_update():
        btn.config(state="disabled", text="Downloading…")
        status.config(text="Downloading the update…", fg=MUTED)

        def work():
            ok = bool(state["info"]) and updater.download_and_run(state["info"])

            def done():
                if ok:
                    status.config(text="Update downloaded. Pipevoice will close to install, "
                                       "then reopen on the new version.", fg=GOOD)
                    btn.config(text="Installing…")
                    root.after(900, _exit_for_install)
                else:
                    status.config(text="Update failed. Check your connection and try again.", fg=WARN)
                    btn.config(state="normal", text="Try again", command=do_update)
            # Close the window while this check is still in flight and the root
            # is already gone. Tk then raises "main thread is not in main loop"
            # from THIS thread - at best a traceback the user cannot act on, at
            # worst it aborts the interpreter.
            try:
                if root.winfo_exists():
                    root.after(0, done)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def load():
        status.config(text="Checking for updates…", fg=MUTED)
        btn.config(state="disabled", text="Checking…")

        def work():
            # One call powers BOTH the version check and the changelog, so they
            # can never disagree (the latest release is just the newest in the list).
            rels = updater.recent_releases(8)
            latest = rels[0] if rels else None
            info = None
            if latest and updater.is_newer(latest.get("tag", "")) and latest.get("url"):
                info = updater.info_from_latest(latest)

            def done():
                render_changelog(rels)
                if not rels:
                    status.config(text="Could not reach GitHub. Check your connection.", fg=WARN)
                    btn.config(state="normal", text="Check again", command=load, style="TButton")
                elif info:
                    state["info"] = info
                    status.config(text=f"Update available: v{latest['version']}", fg=ACCENT)
                    btn.config(state="normal", text="Update now  →", command=do_update, style="Accent.TButton")
                else:
                    status.config(text="You're on the latest version.", fg=GOOD)
                    btn.config(state="normal", text="Check again", command=load, style="TButton")
            # Close the window while this check is still in flight and the root
            # is already gone. Tk then raises "main thread is not in main loop"
            # from THIS thread - at best a traceback the user cannot act on, at
            # worst it aborts the interpreter.
            try:
                if root.winfo_exists():
                    root.after(0, done)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def render_changelog(rels):
        for w in notes.winfo_children():
            w.destroy()
        if not rels:
            tk.Label(notes, text="Could not load release notes.", bg=BG, fg=MUTED,
                     font=("Segoe UI", 10), padx=28, pady=10).pack(anchor="w")
            return
        for rel in rels:
            card = tk.Frame(notes, bg=CARD, padx=15, pady=12)
            card.pack(fill="x", padx=24, pady=6)
            top = tk.Frame(card, bg=CARD)
            top.pack(fill="x")
            tag = rel.get("tag", "")
            tk.Label(top, text=tag, bg=CARD, fg=FG, font=("Segoe UI", 11, "bold")).pack(side="left")
            if tag.lstrip("vV") == __version__:
                tk.Label(top, text=" current ", bg=ACCENT, fg="#1a0c0d",
                         font=("Segoe UI", 7, "bold")).pack(side="left", padx=(8, 0))
            dt = _date(rel.get("published_at", ""))
            if dt:
                tk.Label(top, text=dt, bg=CARD, fg=MUTED, font=("Consolas", 8)).pack(side="right")
            note_text = _clean_notes(rel.get("body", "")) or "No notes for this release."
            tk.Label(card, text=note_text, bg=CARD, fg=MUTED, font=("Segoe UI", 9),
                     anchor="w", justify="left", wraplength=430).pack(fill="x", pady=(7, 0))

    tk.Label(notes, text="Loading release notes…", bg=BG, fg=MUTED,
             font=("Segoe UI", 10), padx=28, pady=10).pack(anchor="w")
    load()


def main() -> None:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return
    from . import winui

    root = tk.Tk()
    root.title("About Pipevoice")
    root.configure(bg=BG)
    root.resizable(False, True)
    ico = config.asset_path("wisprlite.ico")
    if ico:
        try:
            root.iconbitmap(ico)
        except Exception:
            pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Accent.TButton", background=ACCENT, foreground="#1a0c0d",
                    font=("Segoe UI", 9, "bold"), padding=8, borderwidth=0)
    style.map("Accent.TButton", background=[("active", "#e8838b")])
    style.configure("TButton", background=CARD, foreground=FG, padding=7, borderwidth=0)
    style.map("TButton", background=[("active", "#262a3a")], foreground=[("disabled", MUTED)])
    style.configure("Vertical.TScrollbar", background=CARD, troughcolor=BG, borderwidth=0, arrowcolor=MUTED)

    # Reserve the bottom for Close first, then build fills the cavity above it.
    foot = tk.Frame(root, bg=BG, padx=28, pady=14)
    foot.pack(side="bottom", fill="x")
    tk.Button(foot, text="Close", command=root.destroy, bg=CARD, fg=FG, relief="flat",
              activebackground="#262a3a", activeforeground=FG, padx=18, pady=6,
              font=("Segoe UI", 9)).pack(side="right")

    build(root, root)

    root.update_idletasks()
    w = max(560, root.winfo_reqwidth())
    h = min(660, max(440, root.winfo_reqheight()))
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")
    winui.dark_titlebar(root)
    root.mainloop()


if __name__ == "__main__":
    main()
