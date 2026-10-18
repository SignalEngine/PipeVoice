"""Pop-up shown when a meeting starts: what is this call, which documents to use.

A separate process (``--prep <session_dir>``), like the settings window. The
meeting is already recording behind it; this window never blocks capture. It
leaves ``prep.json`` in the session dir for `live_answers.LiveAnswerer`.
"""

from __future__ import annotations

import json
from pathlib import Path

from .winui import PALETTE

BG, CARD, FG, MUTED, ACCENT = (PALETTE[k] for k in ("bg", "card", "fg", "muted", "accent"))


def _write_json(path: Path, value) -> None:
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, indent=2), encoding="utf-8")
    pending.replace(path)


def main(session_dir, pump_until=None) -> None:
    try:
        import tkinter as tk
        from tkinter import filedialog, ttk
    except Exception:
        return
    from . import config, winui
    from .meeting import meetings_dir
    from .meetings_tab import count_docs

    session = Path(session_dir)
    cfg = config.Config.load()
    folder = (cfg.knowledge_dir or "").strip()
    pending_notes = meetings_dir() / "next_call_notes.md"
    files: list[str] = []
    done = {"yes": False}

    root = tk.Tk()
    root.title("Prepare for this call")
    root.configure(bg=BG)
    root.attributes("-topmost", True)
    winui.apply_theme(root)

    body = tk.Frame(root, bg=BG, padx=22, pady=18)
    body.pack(fill="both", expand=True)

    def label(text: str, top: int = 10) -> None:
        tk.Label(body, text=text, bg=BG, fg=MUTED, font=("Segoe UI", 9, "bold"),
                 anchor="w").pack(fill="x", pady=(top, 3))

    tk.Label(body, text="Prepare for this call", bg=BG, fg=ACCENT,
             font=("Segoe UI", 15, "bold"), anchor="w").pack(fill="x")
    tk.Label(body, text="Recording has started. Live answers wait for you here.",
             bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(2, 0))

    label("What's this call?")
    title_var = tk.StringVar(master=root)
    title_entry = ttk.Entry(body, textvariable=title_var, width=56)
    title_entry.pack(fill="x")

    label("Anything it should know?")
    notes = tk.Text(body, height=4, width=56, bg=CARD, fg=FG, insertbackground=FG,
                    relief="flat", font=("Segoe UI", 10), wrap="word", padx=8, pady=6)
    notes.pack(fill="x")
    try:
        if pending_notes.is_file():
            notes.insert("1.0", pending_notes.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        pass

    label("Documents for this call")
    file_rows = tk.Frame(body, bg=BG)
    file_rows.pack(fill="x")
    use_folder = tk.BooleanVar(master=root, value=True)
    first_add = {"pending": True}

    def redraw() -> None:
        for child in file_rows.winfo_children():
            child.destroy()
        for path in list(files):
            row = tk.Frame(file_rows, bg=CARD)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=Path(path).name, bg=CARD, fg=FG, font=("Segoe UI", 9),
                     anchor="w", padx=8).pack(side="left", fill="x", expand=True)
            tk.Button(row, text="✕", bg=CARD, fg=MUTED, relief="flat", bd=0,
                      command=lambda p=path: remove(p)).pack(side="right", padx=4)

    def remove(path: str) -> None:
        if path in files:
            files.remove(path)
        redraw()

    def add_files() -> None:
        chosen = filedialog.askopenfilenames(
            parent=root, title="Documents for this call",
            filetypes=[("Documents", "*.pdf *.md *.txt"), ("All files", "*.*")])
        for path in chosen or ():
            if path not in files:
                files.append(str(path))
        if files and first_add["pending"]:
            first_add["pending"] = False
            use_folder.set(False)        # a chosen file replaces the folder, once
        redraw()

    ttk.Button(body, text="Add file…", command=add_files).pack(anchor="w", pady=(6, 0))
    if folder:
        n_docs = count_docs(folder)
        ttk.Checkbutton(body, text=f"Also use my docs folder ({n_docs} file{'' if n_docs == 1 else 's'})",
                        variable=use_folder).pack(anchor="w", pady=(8, 0))

    def finish(prep: dict) -> None:
        if done["yes"]:
            return
        done["yes"] = True
        try:
            _write_json(session / "prep.json", prep)
        finally:
            root.destroy()

    def start(_event=None) -> None:
        if done["yes"]:
            return
        title, text = title_var.get().strip(), notes.get("1.0", "end").strip()
        try:
            if title or text:
                with open(session / "call_notes.md", "a", encoding="utf-8") as fh:
                    fh.write("\n\n".join(p for p in (f"Call: {title}" if title else "", text) if p)
                             + "\n\n")
            pending_notes.unlink(missing_ok=True)
        except OSError:
            pass
        finish({"title": title, "notes": text, "files": list(files),
                "use_folder": bool(folder) and bool(use_folder.get())})

    def skip(_event=None) -> None:
        finish({"skip": True})

    buttons = tk.Frame(body, bg=BG)
    buttons.pack(fill="x", pady=(16, 0))
    ttk.Button(buttons, text="Start answering", style="Accent.TButton", command=start).pack(side="right")
    ttk.Button(buttons, text="Skip", command=skip).pack(side="right", padx=(0, 8))
    root.bind("<Return>", lambda e: start() if e.widget is not notes else None)
    root.bind("<Escape>", skip)
    root.protocol("WM_DELETE_WINDOW", skip)

    root.update_idletasks()
    width, height = max(root.winfo_reqwidth(), 520), root.winfo_reqheight()
    # Position only, never a fixed size: each added file grows the window, and a
    # fixed height pushed Start/Skip off the bottom.
    root.minsize(width, 0)
    root.geometry(f"+{(root.winfo_screenwidth() - width) // 2}"
                  f"+{(root.winfo_screenheight() - height) // 3}")
    title_entry.focus_set()
    if pump_until is not None:
        pump_until(root)
    else:
        root.mainloop()
