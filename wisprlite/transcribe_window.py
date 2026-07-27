"""Transcribe an existing audio/video file (`--transcribe`).

A short-lived Tk process, like the other windows: pick a file, pick a backend,
get text back. Two backends, both already shipped with the app:

  * local  — offline faster-whisper. No key, no network, no length cap, but
             CPU-bound (expect minutes on a long recording) and no speakers.
  * cloud  — Deepgram prerecorded. Needs DEEPGRAM_API_KEY, but it's fast and
             returns "Speaker N:" labels via diarization.

The work runs on a daemon thread; results are marshalled back to Tk with
`root.after` (never touch Tk from the worker).
"""

from __future__ import annotations

import os
import threading

from . import config
from .history import _copy_to_clipboard
from .winui import PALETTE  # import-safe: winui only needs ctypes at module scope

BG = PALETTE["bg"]
CARD = PALETTE["card"]
FG = PALETTE["fg"]
MUTED = PALETTE["muted"]
ACCENT = PALETTE["accent"]
ACCENT_HI = PALETTE["accent_hi"]
GOOD = "#98c379"        # success green — not a themed role, so not in PALETTE
ON_ACCENT = "#1a0c0d"   # near-black text on the coral button (matches history.py)
SCROLL = "#2a2f3d"      # same greys winui gives Pick.TButton
SCROLL_HI = "#333a4a"

# faster-whisper decodes whatever PyAV/ffmpeg handles, so video containers work too.
FILETYPES = [
    ("Audio / video", "*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wma *.mp4 *.mov *.mkv *.webm *.avi"),
    ("All files", "*.*"),
]

LOCAL, CLOUD = "local", "cloud"


def _local_model(cfg) -> str:
    return (getattr(cfg, "transcribe_model_size", "") or
            getattr(cfg, "local_model_size", "") or "base.en")


def _run(path: str, backend: str, cfg) -> dict:
    """Transcribe `path`. Raises on failure; the caller reports it.

    Language handling mirrors `App._build_engine`: Deepgram takes the full
    locale ("en-GB"), faster-whisper wants the bare code ("en") — feeding it a
    locale fails in the tokenizer. `cfg.language` really does hold locales
    (cleanup.py's _ACCENTS is keyed on en-US/en-GB/en-AU/…).
    """
    from .engines import transcribe as T

    lang = getattr(cfg, "language", "") or ""
    if backend == CLOUD:
        key = config.deepgram_key()
        if not key:
            raise RuntimeError(
                "No Deepgram API key. Add DEEPGRAM_API_KEY to your .env "
                "(or set it in Settings), then reopen this window.")
        return T.transcribe_file_deepgram(
            path, api_key=key,
            model=(getattr(cfg, "deepgram_model", "") or "nova-3"),
            language=(lang or "en-US"))
    return T.transcribe_file(
        path, model_size=_local_model(cfg),
        language=(lang.split("-")[0] or None),
        # honour the same device/precision dials dictation uses — a user who
        # pinned local_device="cpu" to dodge a broken CUDA install must not get
        # device="auto" here and fail on missing CUDA libs.
        device=(getattr(cfg, "local_device", "") or "auto"),
        compute_type=(getattr(cfg, "local_compute_type", "") or "int8"))


def _pretty_duration(seconds: float) -> str:
    seconds = int(seconds or 0)
    if seconds <= 0:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def main() -> None:
    try:
        import tkinter as tk
        from tkinter import filedialog, ttk
    except Exception:
        return

    cfg = config.Config.load()
    has_key = bool(config.deepgram_key())

    root = tk.Tk()
    root.title("Pipevoice — transcribe a file")
    root.configure(bg=BG)
    ico = config.asset_path("wisprlite.ico")
    if ico:
        try:
            root.iconbitmap(ico)
        except Exception:
            pass

    from . import winui
    style = winui.apply_theme(root)
    # apply_theme doesn't style Scrollbar; without the light/dark/border trio
    # clam draws its white 3D bevel on the thumb (same fix as TEntry there).
    style.configure("Vertical.TScrollbar", background=SCROLL, troughcolor=BG,
                    bordercolor=BG, lightcolor=SCROLL, darkcolor=SCROLL,
                    borderwidth=0, arrowcolor=MUTED)
    style.map("Vertical.TScrollbar", background=[("active", SCROLL_HI)])

    state = {"path": "", "busy": False}  # transcript lives in the Text widget

    # ---- header -------------------------------------------------------
    head = tk.Frame(root, bg=BG, padx=18, pady=14)
    head.pack(fill="x")
    tk.Label(head, text="Transcribe a file", bg=BG, fg=ACCENT,
             font=("Segoe UI", 14, "bold")).pack(side="left")

    # ---- controls -----------------------------------------------------
    card = tk.Frame(root, bg=CARD, padx=14, pady=12)
    card.pack(fill="x", padx=18)

    filerow = tk.Frame(card, bg=CARD)
    filerow.pack(fill="x")
    pick_btn = ttk.Button(filerow, text="Choose file…")
    pick_btn.pack(side="left")
    file_lbl = tk.Label(filerow, text="No file selected", bg=CARD, fg=MUTED,
                        font=("Segoe UI", 9), anchor="w")
    file_lbl.pack(side="left", padx=(12, 0), fill="x", expand=True)

    engrow = tk.Frame(card, bg=CARD)
    engrow.pack(fill="x", pady=(12, 0))
    tk.Label(engrow, text="Backend", bg=CARD, fg=FG,
             font=("Segoe UI", 9)).pack(side="left")

    cloud_label = ("Deepgram — fast, speaker labels" if has_key
                   else "Deepgram — needs API key")
    options = [(LOCAL, f"Local ({_local_model(cfg)}) — offline, slower"),
               (CLOUD, cloud_label)]
    labels = [lbl for _, lbl in options]
    engine_var = tk.StringVar(value=labels[1] if has_key else labels[0])
    combo = ttk.Combobox(engrow, textvariable=engine_var, values=labels,
                         state="readonly", width=34)
    combo.pack(side="left", padx=(10, 0))

    go_btn = ttk.Button(engrow, text="Transcribe", state="disabled")
    go_btn.pack(side="right")

    status = tk.Label(card, text="Pick an audio or video file to begin.", bg=CARD,
                      fg=MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                      wraplength=560)
    status.pack(fill="x", pady=(10, 0))

    # ---- transcript ---------------------------------------------------
    body = tk.Frame(root, bg=BG, padx=18, pady=12)
    body.pack(fill="both", expand=True)
    # borderwidth/highlightthickness 0: tk.Text is not a ttk widget, so the
    # shared theme can't suppress clam's light bevel for us.
    text = tk.Text(body, bg=CARD, fg=FG, insertbackground=FG, relief="flat",
                   borderwidth=0, highlightthickness=0,
                   wrap="word", font=("Segoe UI", 10), padx=12, pady=10,
                   height=14, width=64)
    vbar = ttk.Scrollbar(body, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=vbar.set)
    vbar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)

    foot = tk.Frame(root, bg=BG, padx=18, pady=12)
    foot.pack(fill="x")
    copy_btn = ttk.Button(foot, text="Copy", state="disabled")
    copy_btn.pack(side="left")
    save_btn = ttk.Button(foot, text="Save as .txt", state="disabled")
    save_btn.pack(side="left", padx=(8, 0))
    tk.Button(foot, text="Close", command=root.destroy, bg=ACCENT, fg=ON_ACCENT,
              activebackground=ACCENT_HI, relief="flat", padx=18, pady=6,
              font=("Segoe UI", 9, "bold")).pack(side="right")

    # ---- behaviour ----------------------------------------------------
    def backend() -> str:
        for value, lbl in options:
            if lbl == engine_var.get():
                return value
        return LOCAL

    def set_busy(busy: bool) -> None:
        state["busy"] = busy
        go_btn.config(state=("disabled" if busy or not state["path"] else "normal"))
        pick_btn.config(state=("disabled" if busy else "normal"))
        combo.config(state=("disabled" if busy else "readonly"))

    def choose() -> None:
        path = filedialog.askopenfilename(title="Choose an audio or video file",
                                          filetypes=FILETYPES)
        if not path:
            return
        state["path"] = path
        # Drop the previous transcript, or Copy/Save would hand back the OLD
        # file's text under the NEW file's name.
        text.delete("1.0", "end")
        for b in (copy_btn, save_btn):
            b.config(state="disabled")
        file_lbl.config(text=os.path.basename(path), fg=FG)
        status.config(text="Ready. Long recordings can take a while on the local "
                           "backend — the window stays responsive.", fg=MUTED)
        set_busy(False)

    def show_result(result: dict) -> None:
        set_busy(False)
        body_text = (result.get("text") or "").strip()
        text.delete("1.0", "end")
        text.insert("1.0", body_text or "(no speech detected)")
        dur = _pretty_duration(result.get("duration") or 0)
        bits = [b for b in ("transcribed", dur and f"{dur} of audio",
                            f"{len(body_text.split())} words" if body_text else "") if b]
        status.config(text=" · ".join(bits), fg=GOOD)
        for b in (copy_btn, save_btn):
            b.config(state=("normal" if body_text else "disabled"))

    def show_error(msg: str) -> None:
        set_busy(False)
        status.config(text=msg, fg=ACCENT)

    def transcribe() -> None:
        if state["busy"] or not state["path"]:
            return
        set_busy(True)
        for b in (copy_btn, save_btn):
            b.config(state="disabled")
        chosen = backend()
        status.config(
            text=("Uploading to Deepgram…" if chosen == CLOUD else
                  "Transcribing locally — this can take several minutes on a long "
                  "file (the first run also downloads the model)."), fg=MUTED)

        path = state["path"]  # read here, not in the worker: no cross-thread state

        def back_on_ui(fn, arg) -> None:
            """Marshal to the Tk thread. Silently drops if the user closed the
            window mid-transcription — .after on a dead root raises."""
            try:
                root.after(0, lambda a=arg: fn(a))
            except Exception:
                pass

        def work() -> None:
            try:
                result = _run(path, chosen, cfg)
            except Exception as exc:  # surfaced in the status line, never a crash
                back_on_ui(lambda e: show_error(f"Failed: {e}"), exc)
                return
            back_on_ui(show_result, result)

        threading.Thread(target=work, daemon=True).start()

    def current_text() -> str:
        """The widget is the single source of truth — it's editable, so a cached
        copy would silently discard the user's corrections on Copy/Save."""
        return text.get("1.0", "end-1c").strip()

    def copy() -> None:
        ok = _copy_to_clipboard(root, current_text())
        copy_btn.config(text="Copied ✓" if ok else "Copy failed")
        root.after(1100, lambda: copy_btn.config(text="Copy"))

    def save() -> None:
        base = os.path.splitext(os.path.basename(state["path"]))[0] or "transcript"
        dest = filedialog.asksaveasfilename(
            title="Save transcript", defaultextension=".txt",
            initialfile=f"{base}.txt", filetypes=[("Text file", "*.txt")])
        if not dest:
            return
        try:
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(current_text())
            status.config(text=f"Saved to {dest}", fg=GOOD)
        except Exception as exc:
            status.config(text=f"Could not save: {exc}", fg=ACCENT)

    pick_btn.config(command=choose)
    go_btn.config(command=transcribe)
    copy_btn.config(command=copy)
    save_btn.config(command=save)

    root.update_idletasks()
    w = max(640, root.winfo_reqwidth())
    h = min(660, max(460, root.winfo_reqheight()))
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")
    winui.dark_titlebar(root)
    root.mainloop()


if __name__ == "__main__":
    main()
