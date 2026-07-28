"""Standalone settings window (its own process, its own Tk root).

Launched via `python -m wisprlite --settings` (or `Pipevoice.exe --settings`).
Editing config.json here; the running app watches the file and live-reloads.
Kept in a separate process on purpose: the main app already owns a Tk root for
the overlay, and two Tk roots in one process across threads is asking for
trouble.
"""

from __future__ import annotations

import os
import json
import threading
import webbrowser

from . import about, autostart, cleanup, config, history, meeting, meetings_tab, voices, vocab_mine, winui

ENGINES = [("gemini", "Gemini — free, one key does it all"),
           ("groq", "Groq Whisper — fast & cheap, top accuracy"),
           ("deepgram", "Deepgram — fastest, live streaming"),
           ("local", "Local Whisper — private & free, slower")]
MODES = [("ptt", "Push-to-talk (hold)"), ("toggle", "Toggle (tap on/off)")]
OUTPUTS = [("type", "Type keystrokes"), ("paste", "Clipboard + Ctrl+V")]
PASTE_SPEEDS = [("fast", "Fast"), ("normal", "Normal"), ("slow", "Slow")]
CLEANUP_PROVIDERS = [("openai", "OpenAI"), ("gemini", "Google Gemini (free tier)"),
                     ("openrouter", "OpenRouter (free models)"), ("ollama", "Local — Ollama (offline)")]
STYLES = [("tidy", "Tidy — clean up"), ("prompt", "Prompt — for AI tools"), ("custom", "Custom…")]
LOCAL_SIZES = ["tiny.en", "base.en", "small.en", "medium.en",
               "tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
LOCAL_DEVICES = [("auto", "Auto-detect"), ("cpu", "CPU"), ("cuda", "GPU (NVIDIA CUDA)")]
LOCAL_COMPUTE_TYPES = [("int8", "int8 — fastest on CPU"), ("int8_float16", "int8_float16 — GPU"),
                       ("float16", "float16 — GPU"), ("float32", "float32 — most accurate")]
LANGUAGES = [
    ("", "Auto-detect"),
    ("en-US", "English — US"),
    ("en-GB", "English — UK / British"),
    ("en-AU", "English — Australian"),
    ("en-IN", "English — Indian"),
    ("en-NZ", "English — New Zealand"),
    ("es", "Spanish"), ("fr", "French"), ("de", "German"),
    ("pt", "Portuguese"), ("it", "Italian"), ("nl", "Dutch"),
    ("ja", "Japanese"), ("zh", "Chinese"),
]

BG = "#13151d"
CARD = "#1b1e29"
FG = "#e5e7eb"
MUTED = "#94a3b8"
ACCENT = "#e06c75"


def _input_devices():
    """Return [(label, value)] for the device picker; never raises."""
    items = [("System default", "")]
    try:
        import sounddevice as sd

        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                items.append((f"[{i}] {d['name']}", str(i)))
    except Exception:
        pass
    return items


GOOD = "#98c379"
WARN = "#e5c07b"
_URLS = {
    "deepgram": "https://console.deepgram.com/",
    "openai": "https://platform.openai.com/api-keys",
    "groq": "https://console.groq.com/keys",
    "gemini": "https://aistudio.google.com/apikey",
    "openrouter": "https://openrouter.ai/keys",
    "ollama": "https://ollama.com/download",
    "github": "https://github.com/Powleads/PipeVoice",
}


def _launch_child(arg: str) -> None:
    """Spawn another Pipevoice child window (e.g. the --profiles editor)."""
    import os
    import subprocess
    import sys

    try:
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable, arg])
        else:
            from .autostart import _pythonw

            parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            subprocess.Popen([_pythonw(), "-m", "wisprlite", arg], cwd=parent)
    except Exception:
        pass


def _build_guide(parent, wheel) -> None:
    """Populate the Guide tab: how it works, engine speed, polish, tips."""
    import tkinter as tk
    import webbrowser
    from tkinter import ttk

    gc = tk.Canvas(parent, bg=BG, highlightthickness=0)
    gb = ttk.Scrollbar(parent, orient="vertical", command=gc.yview)
    gc.configure(yscrollcommand=gb.set)
    gb.pack(side="right", fill="y")
    gc.pack(side="left", fill="both", expand=True)
    g = tk.Frame(gc, bg=BG)
    gc.create_window((0, 0), window=g, anchor="nw")
    g.bind("<Configure>", lambda e: gc.configure(scrollregion=gc.bbox("all")))
    wheel(gc)

    def head(t, top=16):
        tk.Label(g, text=t, bg=BG, fg=ACCENT, font=("Segoe UI", 10, "bold"),
                 anchor="w", justify="left").pack(fill="x", padx=22, pady=(top, 5))

    def body(t):
        tk.Label(g, text=t, bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=470).pack(fill="x", padx=22, pady=(0, 2))

    def item(name, t, badge=None, badge_color=GOOD):
        row = tk.Frame(g, bg=BG)
        row.pack(fill="x", padx=22, pady=(7, 0))
        tk.Label(row, text=name, bg=BG, fg=FG,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        if badge:
            tk.Label(row, text=f" {badge} ", bg=badge_color, fg="#10131a",
                     font=("Segoe UI", 7, "bold")).pack(side="left", padx=(8, 0))
        tk.Label(g, text=t, bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=470).pack(fill="x", padx=22)

    def link(text, key):
        lk = tk.Label(g, text=text, bg=BG, fg=ACCENT, cursor="hand2",
                      font=("Segoe UI", 9, "underline"), anchor="w")
        lk.pack(anchor="w", padx=22, pady=(3, 0))
        lk.bind("<Button-1>", lambda e: webbrowser.open(_URLS[key]))

    head("How it works", top=14)
    body("Hold your hotkey, talk, then release — your words type in wherever the cursor is: "
         "editor, browser, terminal, anywhere. Default hotkey is Ctrl + \\.")
    body("The second (clipboard) hotkey copies what you say instead of typing it — handy for "
         "pasting feedback into another window.")

    head("Pick your engine — speed lives here")
    body("Transcription is the slow part; polish is fast. The engine you choose is the single "
         "biggest factor in how snappy Pipevoice feels.")
    item("Gemini", "Genuinely free — no credit card. One Gemini key transcribes AND powers AI "
         "polish, so you're fully set up at zero cost. Transcribes after you release.",
         badge="FREE · DEFAULT", badge_color=GOOD)
    item("Groq Whisper", "Real Whisper accuracy at ~9x lower cost than OpenAI, and so fast it "
         "feels near-instant. Free dev tier. Great if you want top accuracy cheaply.",
         badge="FAST · ACCURATE", badge_color=GOOD)
    item("Deepgram", "Streams text as you talk, so it feels instant. Best for long dictation. "
         "$200 free credit on signup (no card) — about 430 hours.", badge="FASTEST · LIVE", badge_color=GOOD)
    item("Local Whisper", "Runs entirely on your PC — nothing leaves the machine, no key needed. "
         "It is the slowest, especially on bigger models. Start on base.en to test, then raise the "
         "model size to medium.en for much better accuracy if your PC can handle it.",
         badge="PRIVATE · FREE", badge_color=MUTED)
    body("Rule of thumb: want free with zero setup? Use Gemini. Want top accuracy cheap? Groq. "
         "Want words to stream as you talk? Deepgram. Fully private/offline? Local Whisper.")
    link("Get a free Gemini key  ↗", "gemini")
    link("Get a free Groq key  ↗", "groq")
    link("Get a Deepgram key ($200 free)  ↗", "deepgram")

    head("Polish (Flow mode) — optional")
    body("Cleans up filler words, punctuation and casing after transcription. It is fast — the wait "
         "you feel is transcription, not polish. Turn it on under Transcription.")
    item("Google Gemini", "Free tier, and most people already have a Google account. The easiest "
         "free option if you don't have OpenAI credit.", badge="FREE · EASIEST", badge_color=GOOD)
    item("OpenAI", "Uses your OpenAI key — same one as the transcription engine.")
    item("OpenRouter", "Free community models via a single key.")
    item("Ollama", "For 100% private polish, install Ollama and pull a small model "
         "(e.g. llama3.2). Nothing leaves your PC.", badge="OFFLINE", badge_color=MUTED)
    link("Get a free Gemini key  ↗", "gemini")
    link("Get an OpenRouter key  ↗", "openrouter")
    link("Install Ollama  ↗", "ollama")

    head("Record a meeting")
    body("Pipevoice can record a whole call — both sides — and turn it into a transcript, "
         "then into notes and action items. Nothing is uploaded unless you choose a cloud "
         "engine; the recordings stay on your machine.")
    item("1 · Set a meeting hotkey",
         "Settings → Hotkeys → Meeting hotkey → Capture. Tap it once to start recording, "
         "tap it again to stop. It's off until you set one.", badge="START HERE", badge_color=GOOD)
    item("2 · It records both sides",
         "Your microphone and your computer's own audio are captured separately — so what "
         "you said and what they said never get mixed up. A REC bar shows the elapsed time "
         "and a level meter for each side, so you can see at a glance that both are alive.")
    item("3 · Dictation pauses itself",
         "Your normal typing hotkey is switched off while a meeting records, so a stray "
         "press can't interrupt the capture. It comes back the moment you stop.")
    item("4 · Transcribe it",
         "Settings → Meetings, pick the session, press Transcribe. Deepgram is fast and "
         "separates the remote speakers; Local whisper works offline but labels everyone "
         "the same. The tab tells you which one produced each transcript.")
    item("5 · Name the people",
         "Click a “Them 1” label in the transcript. Pipevoice plays the clearest two "
         "seconds of that person talking so you can recognise them, then you type their "
         "name. Names are per-meeting and never overwrite the original transcript.")
    item("6 · Summarise",
         "Choose Bullets, To-dos or Actions and press Summarise. Actions lists tasks with "
         "an owner — which is why naming people first is worth the ten seconds.")
    body("• Playing music? Your computer's audio is captured as one mix, so a podcast or "
         "Spotify ends up in the transcript alongside the meeting. Pause it first.")
    body("• Recording other people may need their consent where you live. Ask first.")

    head("Make it yours")
    body("• Accent / language (under Audio): pick yours for a real accuracy boost — UK, US, Indian, "
         "Australian, or Russian-accented English, and more.")
    body("• Speech notes: describe your accent, stutter or filler habits. The AI polish uses it to "
         "fix mis-hearings tailored to how you speak.")
    body("• Vocabulary: add names and jargon so they're always spelled right.")
    body("• Word fixes: wrong=right pairs, applied last so they always win.")

    head("Need a hand?")
    link("Pipevoice on GitHub — docs, issues, source  ↗", "github")
    tk.Label(g, text="", bg=BG).pack(pady=6)  # bottom breathing room


def _build_voices_tab(parent, show_tab=None, wheel=None) -> None:
    """Flagship 'Voices & App Profiles' page: explain the feature, launch the editors."""
    import tkinter as tk
    from tkinter import ttk

    # scrollable (mirrors the other content tabs) so nothing is clipped on short screens
    canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
    sbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=sbar.set)
    sbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    wrap = tk.Frame(canvas, bg=BG, padx=34, pady=28)
    canvas.create_window((0, 0), window=wrap, anchor="nw")
    wrap.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    if wheel:
        wheel(canvas)

    tk.Label(wrap, text="VOICES & APP PROFILES", bg=BG, fg=ACCENT,
             font=("Segoe UI", 10, "bold")).pack(anchor="w")
    tk.Label(wrap, text="One voice in. The right style out.", bg=BG, fg=FG,
             font=("Segoe UI", 20, "bold")).pack(anchor="w", pady=(4, 6))
    tk.Label(wrap, text="PipeVoice's signature feature: dictate the same way everywhere and your words come out "
                        "tailored to where they land — automatically per app, or on a key.",
             bg=BG, fg=MUTED, font=("Segoe UI", 10), justify="left", wraplength=640).pack(anchor="w", pady=(0, 22))

    def feature(title, badge, desc, bullets, btn_text, arg):
        card = tk.Frame(wrap, bg=CARD, padx=22, pady=18,
                        highlightbackground=ACCENT, highlightthickness=1)
        card.pack(fill="x", pady=(0, 16))
        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text=title, bg=CARD, fg=FG, font=("Segoe UI", 14, "bold")).pack(side="left")
        tk.Label(top, text=f" {badge} ", bg=ACCENT, fg="#1a0c0d",
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(10, 0))
        tk.Label(card, text=desc, bg=CARD, fg=MUTED, font=("Segoe UI", 10),
                 justify="left", wraplength=600).pack(anchor="w", pady=(8, 10))
        for b in bullets:
            r = tk.Frame(card, bg=CARD)
            r.pack(anchor="w", fill="x", pady=1)
            tk.Label(r, text="•", bg=CARD, fg=ACCENT, font=("Segoe UI", 10)).pack(side="left")
            tk.Label(r, text="  " + b, bg=CARD, fg=FG, font=("Segoe UI", 9)).pack(side="left")
        ttk.Button(card, text=btn_text, style="Accent.TButton",
                   command=lambda: _launch_child(arg)).pack(anchor="w", pady=(14, 0))

    feature("Voices", "PRESETS",
            "Named polish presets you build once and reuse. Each Voice decides how your words are cleaned up — "
            "and optionally the engine, whether it presses Enter, and where the text goes.",
            ["Tidy — just clean up your words",
             "Social — casual and emoji-friendly",
             "Professional — formal, British spelling",
             "Code / Prompt — a crisp instruction for AI tools"],
            "Manage voices…", "--voices")

    feature("App profiles", "AUTOMATIC",
            "Assign a Voice to an app. PipeVoice detects the focused window and applies that app's Voice for the "
            "next thing you say, then resets — no menus, no mode switching.",
            ["Terminal → raw + Enter",
             "Slack → Social, auto-send",
             "Cursor / VS Code → Code / Prompt",
             "Outlook → Professional"],
            "Manage app profiles…", "--profiles")

    tk.Label(wrap, text="Tip: bind a Voice to a hotkey (Settings → Voice hotkeys) to switch style mid-sentence. "
                        "A key always wins over the app's default.",
             bg=BG, fg=MUTED, font=("Segoe UI", 9), justify="left", wraplength=640).pack(anchor="w", pady=(4, 0))


def main(first_run: bool = False) -> None:
    import tkinter as tk
    from tkinter import ttk

    cfg = config.Config.load()
    root = tk.Tk()
    root.title("Set up Pipevoice" if first_run else "Pipevoice settings")
    root.configure(bg=BG)
    root.resizable(True, True)
    ico = config.asset_path("wisprlite.ico")
    if ico:
        try:
            root.iconbitmap(ico)
        except Exception:
            pass

    style = winui.apply_theme(root)

    pad = dict(padx=14, pady=8, sticky="w")

    def _wheel(canvas):
        # Scroll whichever canvas the pointer is over (two scroll areas exist).
        canvas.bind("<Enter>", lambda e: canvas.bind_all(
            "<MouseWheel>", lambda ev: canvas.yview_scroll(int(-ev.delta / 120), "units")))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    # Fixed footer (Save/Cancel always visible), then a tabbed body: the form
    # plus a Guide tab that explains engines, speed and polish options.
    footer = ttk.Frame(root, padding=(16, 10))
    footer.pack(side="bottom", fill="x")

    # Custom underline tab bar (ttk.Notebook tabs render poorly on clam).
    tabbar = tk.Frame(root, bg=BG)
    tabbar.pack(side="top", fill="x", padx=22, pady=(12, 0))
    tk.Frame(root, bg="#272b37", height=1).pack(side="top", fill="x")
    body_wrap = tk.Frame(root, bg=BG)
    body_wrap.pack(side="top", fill="both", expand=True)

    tab_settings = tk.Frame(body_wrap, bg=BG)
    tab_voices = tk.Frame(body_wrap, bg=BG)
    tab_history = tk.Frame(body_wrap, bg=BG)
    tab_meetings = tk.Frame(body_wrap, bg=BG)
    tab_guide = tk.Frame(body_wrap, bg=BG)
    tab_about = tk.Frame(body_wrap, bg=BG)
    _tabs = [("Settings", tab_settings), ("Voices", tab_voices), ("History", tab_history),
             ("Meetings", tab_meetings), ("Guide", tab_guide), ("About", tab_about)]
    _tab_w = {}

    def _show_tab(name):
        if name not in dict(_tabs):
            name = "Settings"
        for _n, _f in _tabs:
            _f.pack_forget()
        dict(_tabs)[name].pack(fill="both", expand=True)
        for _n, (lbl, ul) in _tab_w.items():
            on = _n == name
            lbl.config(fg=ACCENT if on else MUTED)
            ul.config(bg=ACCENT if on else BG)

    for _name, _frame in _tabs:
        w = tk.Frame(tabbar, bg=BG)
        w.pack(side="left", padx=(0, 8))
        lbl = tk.Label(w, text=_name, bg=BG, fg=MUTED, font=("Segoe UI", 11, "bold"),
                       cursor="hand2", padx=10, pady=8)
        lbl.pack()
        ul = tk.Frame(w, bg=BG, height=2)
        ul.pack(fill="x")
        lbl.bind("<Button-1>", lambda e, n=_name: _show_tab(n))
        _tab_w[_name] = (lbl, ul)

    # --- Settings tab: scrollable form ---
    _canvas = tk.Canvas(tab_settings, bg=BG, highlightthickness=0)
    _vbar = ttk.Scrollbar(tab_settings, orient="vertical", command=_canvas.yview)
    _canvas.configure(yscrollcommand=_vbar.set)
    _vbar.pack(side="right", fill="y")
    _canvas.pack(side="left", fill="both", expand=True)
    frm = ttk.Frame(_canvas, padding=26)
    _canvas.create_window((0, 0), window=frm, anchor="nw")
    frm.bind("<Configure>", lambda e: _canvas.configure(scrollregion=_canvas.bbox("all")))
    _wheel(_canvas)
    history.build(tab_history, root, _wheel)
    def sync_meeting_replacements(replacements):
        fixes_var.set(", ".join(f"{k}={v}" for k, v in replacements.items()))

    meetings_tab.build(
        tab_meetings, root, _wheel,
        on_replacements_changed=sync_meeting_replacements,
    )
    _build_guide(tab_guide, _wheel)
    about.build(tab_about, root, _wheel)
    _build_voices_tab(tab_voices, _show_tab, _wheel)
    _show_tab(os.getenv("PV_TAB") or "Settings")  # PV_TAB is a render/test seam
    DIV = "#272b37"

    def card(title, subtitle=None):
        wrap = tk.Frame(frm, bg=BG)
        wrap.pack(fill="x", pady=(0, 18))
        tk.Label(wrap, text=title, bg=BG, fg=FG, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=3)
        if subtitle:
            tk.Label(wrap, text=subtitle, bg=BG, fg=MUTED, font=("Segoe UI", 9),
                     justify="left").pack(anchor="w", padx=3, pady=(2, 0))
        c = tk.Frame(wrap, bg=CARD)
        c.pack(fill="x", pady=(9, 0))
        c._first = True
        return c

    def _divide(c):
        if not getattr(c, "_first", True):
            tk.Frame(c, bg=DIV, height=1).pack(fill="x")
        c._first = False

    def row(c, text, desc=None):
        _divide(c)
        r = tk.Frame(c, bg=CARD, padx=18, pady=13)
        r.pack(fill="x")
        right = tk.Frame(r, bg=CARD)
        right.pack(side="right")
        left = tk.Frame(r, bg=CARD)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=text, bg=CARD, fg=FG, font=("Segoe UI", 10)).pack(anchor="w")
        if desc:
            tk.Label(left, text=desc, bg=CARD, fg=MUTED, font=("Segoe UI", 8),
                     wraplength=330, justify="left").pack(anchor="w", pady=(2, 0))
        return right

    def stack(c, text, desc=None):
        _divide(c)
        r = tk.Frame(c, bg=CARD, padx=18, pady=13)
        r.pack(fill="x")
        tk.Label(r, text=text, bg=CARD, fg=FG, font=("Segoe UI", 10)).pack(anchor="w")
        if desc:
            tk.Label(r, text=desc, bg=CARD, fg=MUTED, font=("Segoe UI", 8),
                     justify="left").pack(anchor="w", pady=(2, 0))
        body = tk.Frame(r, bg=CARD)
        body.pack(fill="x", pady=(8, 0))
        return body

    def check(c, text, var, desc=None):
        _divide(c)
        r = tk.Frame(c, bg=CARD, padx=18, pady=12)
        r.pack(fill="x")
        ttk.Checkbutton(r, text=text, variable=var, style="Card.TCheckbutton").pack(anchor="w")
        if desc:
            tk.Label(r, text=desc, bg=CARD, fg=MUTED, font=("Segoe UI", 8),
                     wraplength=470, justify="left").pack(anchor="w", padx=(25, 0), pady=(3, 0))

    # --- values ---
    # Show the current engine even if it's a legacy/hidden one (e.g. "openai"),
    # so saving settings never silently switches an existing user to the default.
    engine_opts = list(ENGINES)
    if cfg.engine not in dict(engine_opts):
        engine_opts.append((cfg.engine, f"{cfg.engine} (current)"))
    engine_var = tk.StringVar(value=dict(engine_opts).get(cfg.engine, engine_opts[0][1]))
    mode_var = tk.StringVar(value=dict(MODES).get(cfg.mode, MODES[0][1]))
    output_var = tk.StringVar(value=dict(OUTPUTS).get(cfg.output_mode, OUTPUTS[0][1]))
    hotkey_var = tk.StringVar(value=cfg.hotkey)
    clip_hotkey_var = tk.StringVar(value=cfg.clipboard_hotkey)
    meeting_hotkey_var = tk.StringVar(value=cfg.meeting_hotkey)
    lang_var = tk.StringVar(value=dict(LANGUAGES).get(cfg.language, LANGUAGES[0][1]))
    devices = _input_devices()
    dev_label = next((lbl for lbl, val in devices if val == cfg.device), devices[0][0])
    device_var = tk.StringVar(value=dev_label)
    gemini_model_var = tk.StringVar(value=cfg.gemini_model)
    groq_model_var = tk.StringVar(value=cfg.groq_model)
    oai_var = tk.StringVar(value=cfg.openai_model)
    dg_var = tk.StringVar(value=cfg.deepgram_model)
    local_var = tk.StringVar(value=cfg.local_model_size)
    local_device_var = tk.StringVar(value=dict(LOCAL_DEVICES).get(cfg.local_device, LOCAL_DEVICES[0][1]))
    local_compute_var = tk.StringVar(value=dict(LOCAL_COMPUTE_TYPES).get(cfg.local_compute_type, LOCAL_COMPUTE_TYPES[0][1]))
    oai_key_var = tk.StringVar()
    dg_key_var = tk.StringVar()
    gem_key_var = tk.StringVar()
    or_key_var = tk.StringVar()
    groq_key_var = tk.StringVar()
    ai_cleanup_var = tk.BooleanVar(value=cfg.ai_cleanup)
    cleanup_var = tk.StringVar(value=dict(CLEANUP_PROVIDERS).get(cfg.cleanup_provider, CLEANUP_PROVIDERS[0][1]))
    cleanup_model_var = tk.StringVar(value=cfg.cleanup_model)
    cleanup_style_var = tk.StringVar(value=dict(STYLES).get(cfg.cleanup_style, STYLES[0][1]))
    cleanup_instruction_var = tk.StringVar(value=cfg.cleanup_instruction)
    auto_enter_var = tk.BooleanVar(value=cfg.auto_enter)
    min_seconds_var = tk.StringVar(value=str(cfg.min_seconds))
    dg_timeout_var = tk.StringVar(value=str(cfg.deepgram_finish_timeout))
    meeting_max_minutes_var = tk.StringVar(value=str(cfg.meeting_max_minutes))
    meetings_keep_var = tk.StringVar(value=str(cfg.meetings_keep))
    paste_speed_var = tk.StringVar(value=dict(PASTE_SPEEDS).get(cfg.paste_speed, PASTE_SPEEDS[1][1]))
    fixes_var = tk.StringVar(value=", ".join(f"{k}={v}" for k, v in cfg.replacements.items()))
    speech_notes_var = tk.StringVar(value=cfg.speech_notes)
    overlay_var = tk.BooleanVar(value=cfg.overlay)
    sounds_var = tk.BooleanVar(value=cfg.sounds)
    autostart_var = tk.BooleanVar(value=autostart.is_enabled())
    auto_update_var = tk.BooleanVar(value=cfg.auto_update)
    voice_commands_var = tk.BooleanVar(value=cfg.voice_commands)
    history_var = tk.BooleanVar(value=cfg.history_enabled)

    # Voice hotkeys: option table for the dropdowns, StringVars for 3 key+voice
    # rows plus the picker. Prefilled from the first three saved voice_hotkeys.
    voice_opts = [("", "(none)")] + [(n, n) for n in voices.names(cfg)]
    _vh = list(cfg.voice_hotkeys or [])

    def _capture_factory():
        def _mk_capture(btn, var):
            def cap():
                btn.config(text="Press keys…")

                def work():
                    hk = None
                    try:
                        import keyboard
                        hk = keyboard.read_hotkey(suppress=False)
                    except Exception:
                        hk = None

                    def done():
                        if hk:
                            var.set(hk)
                        btn.config(text="Capture")
                    root.after(0, done)
                threading.Thread(target=work, daemon=True).start()
            return cap
        return _mk_capture

    _mk_capture = _capture_factory()

    vk_vars, vv_vars = [], []
    for _i in range(3):
        _e = _vh[_i] if _i < len(_vh) else {}
        vk_vars.append(tk.StringVar(value=(_e.get("hotkey") if isinstance(_e, dict) else "") or ""))
        vv_vars.append(tk.StringVar(
            value=dict(voice_opts).get((_e.get("voice") if isinstance(_e, dict) else ""), "(none)")))
    picker_var = tk.StringVar(value=cfg.voice_picker_hotkey)

    def combo(parent, var, options, width=22):
        c = ttk.Combobox(parent, textvariable=var, values=options, state="readonly", width=width)
        c.pack(side="left")
        return c

    def entry(parent, var, width=22, show=None):
        e = ttk.Entry(parent, textvariable=var, width=width, show=(show or ""))
        e.pack(side="left")
        return e

    # --- General ---
    c = card("General", "How Pipevoice listens, and where your words go.")
    combo(row(c, "Engine", "Gemini is free (one key also does AI polish). Groq is fast, accurate Whisper. Deepgram streams live. Local is offline."),
          engine_var, [l for _, l in engine_opts])
    combo(row(c, "Mode", "Push-to-talk holds the key; toggle taps it on and off."),
          mode_var, [l for _, l in MODES])
    combo(row(c, "Output", "Type the keystrokes, or paste from the clipboard."),
          output_var, [l for _, l in OUTPUTS])

    # --- Hotkeys ---
    c = card("Hotkeys")
    r = row(c, "Push-to-talk key", "Hold this to dictate into the focused window.")
    entry(r, hotkey_var, width=14)
    cap_btn = ttk.Button(r, text="Capture", width=8)
    cap_btn.pack(side="left", padx=(8, 0))

    def capture():
        cap_btn.config(text="Press keys…")

        def work():
            hk = None
            try:
                import keyboard
                hk = keyboard.read_hotkey(suppress=False)
            except Exception:
                hk = None

            def done():
                if hk:
                    hotkey_var.set(hk)
                cap_btn.config(text="Capture")
            root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    cap_btn.config(command=capture)

    r = row(c, "Clipboard hotkey", "Hold to dictate to the clipboard instead of typing into the app.")
    entry(r, clip_hotkey_var, width=14)
    clip_cap_btn = ttk.Button(r, text="Capture", width=8)
    clip_cap_btn.pack(side="left", padx=(8, 0))

    def clip_capture():
        clip_cap_btn.config(text="Press keys…")

        def work():
            hk = None
            try:
                import keyboard
                hk = keyboard.read_hotkey(suppress=False)
            except Exception:
                hk = None

            def done():
                if hk:
                    clip_hotkey_var.set(hk)
                clip_cap_btn.config(text="Capture")
            root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    clip_cap_btn.config(command=clip_capture)

    r = row(
        c,
        "Meeting hotkey",
        "Tap once to start meeting recording and again to stop. "
        "Dictation is suppressed while a meeting is recording.",
    )
    entry(r, meeting_hotkey_var, width=14)
    meeting_cap_btn = ttk.Button(r, text="Capture", width=8)
    meeting_cap_btn.pack(side="left", padx=(8, 0))
    meeting_cap_btn.config(
        command=_mk_capture(meeting_cap_btn, meeting_hotkey_var)
    )

    # --- Voice hotkeys ---
    c = card("Voice hotkeys",
             "Press a key to dictate with a specific Voice. Optional picker key chooses on the fly.")
    for _i in range(3):
        r = row(c, f"Voice key {_i + 1}", "Hold this to dictate using the chosen Voice.")
        entry(r, vk_vars[_i], width=14)
        _vcap = ttk.Button(r, text="Capture", width=8)
        _vcap.pack(side="left", padx=(8, 0))
        _vcap.config(command=_mk_capture(_vcap, vk_vars[_i]))
        combo(r, vv_vars[_i], [l for _, l in voice_opts], width=18)
    r = row(c, "Picker key", "Hold to pop a numbered voice list on the overlay.")
    entry(r, picker_var, width=14)
    _pcap = ttk.Button(r, text="Capture", width=8)
    _pcap.pack(side="left", padx=(8, 0))
    _pcap.config(command=_mk_capture(_pcap, picker_var))

    # --- Audio ---
    c = card("Audio")
    combo(row(c, "Microphone"), device_var, [lbl for lbl, _ in devices], width=30)
    combo(row(c, "Accent / language", "Pick yours for better accuracy, including non-native accents."),
          lang_var, [l for _, l in LANGUAGES])

    # --- Models ---
    c = card("Models", "Per-engine model names. The defaults are good for most people.")
    entry(row(c, "Gemini model", "flash-lite is free & fast; try a Flash model for more accuracy."), gemini_model_var, width=22)
    entry(row(c, "Groq model", "whisper-large-v3-turbo is fast; whisper-large-v3 is a touch more accurate."), groq_model_var, width=22)
    entry(row(c, "Deepgram model"), dg_var, width=22)
    combo(row(c, "Local model size", "Bigger is more accurate but slower."), local_var, LOCAL_SIZES)
    combo(row(c, "Local: device", "Auto picks GPU if available, else CPU."),
          local_device_var, [l for _, l in LOCAL_DEVICES])
    combo(row(c, "Local: compute type", "int8 is fastest on CPU; float16/int8_float16 for GPU."),
          local_compute_var, [l for _, l in LOCAL_COMPUTE_TYPES], width=26)

    # --- API keys ---
    c = card("API keys", "Stored locally in your .env, never uploaded. Leave a field blank to keep its current key.")

    def key_row(name, var, present):
        entry(row(c, name + " key", "Saved" if present else "Not set"), var, width=26, show="•")

    key_row("Gemini", gem_key_var, config.gemini_key())
    key_row("Groq", groq_key_var, config.groq_key())
    key_row("Deepgram", dg_key_var, config.deepgram_key())
    key_row("OpenAI", oai_key_var, config.openai_key())
    key_row("OpenRouter", or_key_var, config.openrouter_key())

    # --- Polish & text ---
    c = card("Polish & text", "Clean up and shape what gets typed.")
    check(c, "Polish with AI (Flow mode)", ai_cleanup_var,
          "Tidies filler words, punctuation and casing after transcription.")
    combo(row(c, "Cleanup with", "OpenAI, free Google Gemini, OpenRouter, or fully offline Ollama."),
          cleanup_var, [l for _, l in CLEANUP_PROVIDERS])
    entry(row(c, "Cleanup model", "Blank uses the provider's default."), cleanup_model_var, width=22)
    combo(row(c, "Polish style", "Tidy keeps your words; Prompt rewrites rambling into a clear AI instruction; Custom uses your own instruction."),
          cleanup_style_var, [l for _, l in STYLES])
    entry(row(c, "Custom polish instruction", "Used when Polish style = Custom."), cleanup_instruction_var, width=24)

    def _on_cleanup_provider(*_):
        prov = value_for(cleanup_var, CLEANUP_PROVIDERS)
        cleanup_model_var.set(cleanup.PROVIDERS.get(prov, cleanup.PROVIDERS["openai"])[2])
    cleanup_var.trace_add("write", _on_cleanup_provider)

    check(c, "Spoken commands", voice_commands_var,
          'Say "new line", "scratch that", or end with "send it" while dictating.')
    check(c, "Press Enter after typing (auto-send)", auto_enter_var,
          "Submits the line. Handy for chat, leave off for editors.")

    vb = stack(c, "Vocabulary", "Names and jargon, so they're recognised and spelled correctly.")
    vocab_list = tk.Listbox(vb, height=4, width=26, bg=BG, fg=FG,
                            selectbackground=ACCENT, selectforeground="#1a0c0d",
                            highlightthickness=1, highlightbackground=DIV,
                            relief="flat", activestyle="none", exportselection=False,
                            font=("Segoe UI", 9))
    vocab_list.pack(side="left", anchor="n")
    for _t in [t.strip() for t in (cfg.vocabulary or "").split(",") if t.strip()]:
        vocab_list.insert("end", _t)
    vside = tk.Frame(vb, bg=CARD)
    vside.pack(side="left", padx=(8, 0), anchor="n")
    vocab_add_var = tk.StringVar()
    _vadd = ttk.Entry(vside, textvariable=vocab_add_var, width=16)
    _vadd.pack(anchor="w")

    def _vocab_add(*_a):
        t = vocab_add_var.get().strip().strip(",")
        if t and t not in vocab_list.get(0, "end"):
            vocab_list.insert("end", t)
        vocab_add_var.set("")

    def _vocab_remove():
        for i in reversed(vocab_list.curselection()):
            vocab_list.delete(i)

    def _vocab_from_meetings():
        """Offer local meeting candidates; only checked rows are returned."""
        sessions = []
        corrections = []
        for row in meetings_tab.list_sessions():
            path = row.get("path")
            if not path:
                continue
            try:
                transcript = json.loads((path / "transcript.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(transcript, dict):
                continue
            sessions.append({
                "name": row.get("name") or str(path),
                "segments": transcript.get("segments", []),
            })
            corrections.append(meeting.load_corrections(path))
        candidates = vocab_mine.mine_candidates(
            sessions, list(vocab_list.get(0, "end")), corrections
        )
        if not candidates:
            return

        picker = tk.Toplevel(root)
        picker.title("Vocabulary from meetings")
        picker.configure(bg=BG)
        picker.transient(root)
        tk.Label(picker, text="Choose terms to add", bg=BG, fg=FG,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18, pady=(16, 4))
        tk.Label(picker, text="Nothing is added unless you tick it.", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=18, pady=(0, 10))
        body = tk.Frame(picker, bg=CARD, padx=14, pady=8)
        body.pack(fill="both", expand=True, padx=18)
        choices = []
        for candidate in candidates:
            selected = tk.BooleanVar(value=False)
            label = (f"{candidate['term']} — seen {candidate['count']}x in "
                     f"{candidate['sessions']} meeting{'s' if candidate['sessions'] != 1 else ''}")
            tk.Checkbutton(body, text=label, variable=selected, bg=CARD, fg=FG,
                           activebackground=CARD, activeforeground=FG, selectcolor=BG,
                           anchor="w").pack(fill="x", pady=2)
            choices.append((candidate, selected))

        def confirm():
            existing = {str(value).casefold() for value in vocab_list.get(0, "end")}
            for candidate, selected in choices:
                term = candidate["term"]
                if selected.get() and term.casefold() not in existing:
                    vocab_list.insert("end", term)
                    existing.add(term.casefold())
            picker.destroy()

        buttons = tk.Frame(picker, bg=BG)
        buttons.pack(fill="x", padx=18, pady=14)
        ttk.Button(buttons, text="Add selected", command=confirm).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=picker.destroy).pack(side="right", padx=(0, 8))
        picker.geometry(f"520x{min(620, 150 + 30 * len(candidates))}")
        picker.grab_set()

    _vadd.bind("<Return>", _vocab_add)
    _vrow = tk.Frame(vside, bg=CARD)
    _vrow.pack(anchor="w", pady=(6, 0))
    ttk.Button(_vrow, text="Add", command=_vocab_add, width=7).pack(side="left")
    ttk.Button(_vrow, text="Remove", command=_vocab_remove, width=8).pack(side="left", padx=(6, 0))
    ttk.Button(_vrow, text="From meetings", command=_vocab_from_meetings).pack(
        side="left", padx=(6, 0)
    )

    entry(row(c, "Word fixes", "Auto-corrections as wrong=right, comma separated."), fixes_var, width=24)
    entry(row(c, "Speech notes", "Describe your accent, stutter or fillers to guide AI cleanup."),
          speech_notes_var, width=24)

    # --- Behaviour ---
    c = card("Behaviour")
    check(c, "Show live overlay", overlay_var, "A small HUD that shows it's listening.")
    check(c, "Play start/stop sounds", sounds_var)
    check(c, "Start on Windows login", autostart_var)
    check(c, "Automatic updates", auto_update_var, "Check for a newer version on startup and install it silently.")
    check(c, "Keep a local dictation history", history_var, "Saved on your PC; open it from the tray.")
    pr = stack(c, "Voices & app profiles", "Per-app styles and key-bound Voices — PipeVoice's signature feature.")
    ttk.Button(pr, text="Open the Voices tab  →", command=lambda: _show_tab("Voices")).pack(anchor="w")

    # --- Advanced ---
    c = card("Advanced", "Most people never need these.")
    entry(row(c, "Min seconds", "Ignore taps shorter than this."), min_seconds_var, width=7)
    entry(row(c, "Deepgram wait", "Seconds to wait for final words."), dg_timeout_var, width=7)
    entry(
        row(c, "Meeting max minutes", "Safety limit for an unattended recording."),
        meeting_max_minutes_var,
        width=7,
    )
    entry(
        row(c, "Meetings to keep", "Newest local meeting sessions retained on this PC."),
        meetings_keep_var,
        width=7,
    )
    combo(row(c, "Paste speed", "Slower is more reliable in some apps."),
          paste_speed_var, [l for _, l in PASTE_SPEEDS], width=10)

    # --- Save / Cancel (live in the fixed footer) ---
    def value_for(var, table):
        label_to_value = {l: k for k, l in table}
        return label_to_value.get(var.get(), table[0][0])

    def save(close=True):
        # Don't clobber app profiles edited in the separate Profiles window.
        try:
            cfg.profiles = config.Config.load().profiles
        except Exception:
            pass
        # don't clobber voices edited in the separate Voices window
        try:
            cfg.voices = config.Config.load().voices
        except Exception:
            pass
        cfg.engine = value_for(engine_var, engine_opts)
        cfg.mode = value_for(mode_var, MODES)
        cfg.output_mode = value_for(output_var, OUTPUTS)
        cfg.hotkey = hotkey_var.get().strip() or "right ctrl"
        cfg.clipboard_hotkey = clip_hotkey_var.get().strip()
        cfg.meeting_hotkey = meeting_hotkey_var.get().strip()
        cfg.language = value_for(lang_var, LANGUAGES)
        cfg.device = dict((lbl, val) for lbl, val in devices).get(device_var.get(), "")
        cfg.gemini_model = gemini_model_var.get().strip() or "gemini-3.1-flash-lite"
        cfg.groq_model = groq_model_var.get().strip() or "whisper-large-v3-turbo"
        cfg.openai_model = oai_var.get().strip() or "whisper-1"
        cfg.deepgram_model = dg_var.get().strip() or "nova-2"
        cfg.local_model_size = local_var.get().strip() or "base.en"
        cfg.local_device = value_for(local_device_var, LOCAL_DEVICES)
        cfg.local_compute_type = value_for(local_compute_var, LOCAL_COMPUTE_TYPES)
        cfg.overlay = bool(overlay_var.get())
        cfg.sounds = bool(sounds_var.get())
        cfg.auto_update = bool(auto_update_var.get())
        cfg.voice_commands = bool(voice_commands_var.get())
        cfg.history_enabled = bool(history_var.get())
        vh = []
        for kv, vv in zip(vk_vars, vv_vars):
            hk = kv.get().strip(); vn = value_for(vv, voice_opts)
            if hk and vn:
                vh.append({"hotkey": hk, "voice": vn})
        cfg.voice_hotkeys = vh
        cfg.voice_picker_hotkey = picker_var.get().strip()
        cfg.ai_cleanup = bool(ai_cleanup_var.get())
        cfg.cleanup_provider = value_for(cleanup_var, CLEANUP_PROVIDERS)
        cfg.cleanup_model = cleanup_model_var.get().strip()
        cfg.cleanup_style = value_for(cleanup_style_var, STYLES)
        cfg.cleanup_instruction = cleanup_instruction_var.get().strip()
        cfg.auto_enter = bool(auto_enter_var.get())
        cfg.vocabulary = ", ".join(vocab_list.get(0, "end"))
        try:
            cfg.min_seconds = max(0.05, min(2.0, float(min_seconds_var.get())))
        except ValueError:
            pass
        try:
            cfg.deepgram_finish_timeout = max(1.0, min(30.0, float(dg_timeout_var.get())))
        except ValueError:
            pass
        try:
            cfg.meeting_max_minutes = max(0, int(meeting_max_minutes_var.get()))
        except ValueError:
            pass
        try:
            cfg.meetings_keep = max(1, int(meetings_keep_var.get()))
        except ValueError:
            pass
        cfg.paste_speed = value_for(paste_speed_var, PASTE_SPEEDS)
        cfg.speech_notes = speech_notes_var.get().strip()
        fixes = {}
        for part in fixes_var.get().split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                if k:
                    fixes[k] = v.strip()
        cfg.replacements = fixes
        cfg.save()
        if oai_key_var.get().strip():
            config.save_api_key("OPENAI_API_KEY", oai_key_var.get())
        if dg_key_var.get().strip():
            config.save_api_key("DEEPGRAM_API_KEY", dg_key_var.get())
        if gem_key_var.get().strip():
            config.save_api_key("GEMINI_API_KEY", gem_key_var.get())
        if groq_key_var.get().strip():
            config.save_api_key("GROQ_API_KEY", groq_key_var.get())
        if or_key_var.get().strip():
            config.save_api_key("OPENROUTER_API_KEY", or_key_var.get())
        try:
            if autostart_var.get():
                autostart.enable()
            else:
                autostart.disable()
        except Exception:
            pass
        if close:
            root.destroy()

    ttk.Button(footer, text="Save", style="Accent.TButton", command=save).pack(side="right")
    ttk.Button(footer, text="Cancel", command=root.destroy).pack(side="right", padx=(0, 8))
    ttk.Button(footer, text="⭐ Star on GitHub",
               command=lambda: webbrowser.open(_URLS["github"])).pack(side="left")
    ttk.Label(footer, text="Free & open-source — a star really helps.",
              style="Muted.TLabel").pack(side="left", padx=(12, 0))

    root.update_idletasks()
    cw = frm.winfo_reqwidth() + 22          # form width + scrollbar
    ch = frm.winfo_reqheight()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    win_w = max(cw, 760)                    # wider: descriptions sit clear of the controls
    win_h = min(ch + 110, sh - 150)         # leave room for the taskbar so the footer is never cut off
    x = max(0, (sw - win_w) // 2)
    y = max(16, (sh - win_h) // 5)          # sit near the top so the bottom stays on-screen
    root.geometry(f"{win_w}x{win_h}+{x}+{y}")
    winui.dark_titlebar(root)
    root.mainloop()


if __name__ == "__main__":
    main()
