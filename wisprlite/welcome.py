"""First-run welcome / tutorial splash.

Shown once on first launch (before the settings window) to explain how Pipevoice
works, which engine to pick for speed, and how to get a key (or stay fully
offline). Returns True if the user clicked "Get started" (so the caller opens
Settings next), False if they chose to set up later.

Plain Tk on the app's dark palette; runs its own short-lived root, fully torn
down before anything else starts (same pattern as keyprompt).
"""

from __future__ import annotations

import webbrowser

from . import config

BG = "#13151d"
CARD = "#1b1e29"
FG = "#e5e7eb"
MUTED = "#94a3b8"
ACCENT = "#e06c75"
GOOD = "#98c379"
WARN = "#e5c07b"

OPENAI_URL = "https://platform.openai.com/api-keys"
DEEPGRAM_URL = "https://console.deepgram.com/"
GEMINI_URL = "https://aistudio.google.com/apikey"
GROQ_URL = "https://console.groq.com/keys"
OLLAMA_URL = "https://ollama.com/download"


def show_welcome() -> bool:
    """Display the welcome/tutorial. Returns True to continue to Settings."""
    try:
        import tkinter as tk
    except Exception:
        return True  # no Tk (headless) -> just go straight to settings

    result = {"go": False}

    root = tk.Tk()
    root.title("Welcome to Pipevoice")
    root.configure(bg=BG)
    root.resizable(False, False)
    ico = config.asset_path("wisprlite.ico")
    if ico:
        try:
            root.iconbitmap(ico)
        except Exception:
            pass

    wrap = tk.Frame(root, bg=BG, padx=34, pady=28)
    wrap.pack()

    # --- header ---
    from . import branding
    branding.lockup_label(wrap, BG).pack(anchor="w", pady=(0, 6))
    tk.Label(wrap, text="Talk faster than you type.", bg=BG, fg=FG,
             font=("Segoe UI", 13)).pack(anchor="w", pady=(2, 0))
    tk.Label(wrap, text="Push-to-talk voice typing for Windows — your words land in any app.",
             bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 18))

    # --- how it works ---
    tk.Label(wrap, text="HOW IT WORKS", bg=BG, fg=ACCENT,
             font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))
    steps = [
        ("1", "Hold your hotkey", "default: Ctrl + \\"),
        ("2", "Talk", "just say what you want typed"),
        ("3", "Release", "your words type wherever the cursor is"),
    ]
    for n, title, sub in steps:
        row = tk.Frame(wrap, bg=BG)
        row.pack(anchor="w", fill="x", pady=3)
        tk.Label(row, text=f" {n} ", bg=CARD, fg=ACCENT,
                 font=("Consolas", 10, "bold")).pack(side="left")
        tk.Label(row, text=f"  {title}", bg=BG, fg=FG,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Label(row, text=f"  — {sub}", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")

    # --- choose an engine (speed!) ---
    tk.Label(wrap, text="CHOOSE YOUR ENGINE — THIS DECIDES YOUR SPEED", bg=BG, fg=ACCENT,
             font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(22, 2))
    tk.Label(wrap, text="Gemini is free and set as your default — one key also does the AI polish. Switch anytime.",
             bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 8))

    def engine_card(name, badge_text, badge_color, desc, url=None):
        card = tk.Frame(wrap, bg=CARD, padx=14, pady=9)
        card.pack(anchor="w", fill="x", pady=4)
        top = tk.Frame(card, bg=CARD)
        top.pack(anchor="w", fill="x")
        tk.Label(top, text=name, bg=CARD, fg=FG,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Label(top, text=f" {badge_text} ", bg=badge_color, fg="#10131a",
                 font=("Segoe UI", 7, "bold")).pack(side="left", padx=(8, 0))
        if url:
            link = tk.Label(top, text="Get free key  ↗", bg=CARD, fg=ACCENT, cursor="hand2",
                            font=("Segoe UI", 9, "underline"))
            link.pack(side="right")
            link.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
        tk.Label(card, text=desc, bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 0))

    engine_card("Gemini", "FREE · DEFAULT", GOOD,
                "Genuinely free, no credit card — and one key also powers the AI polish. Transcribes after you release.",
                GEMINI_URL)
    engine_card("Groq Whisper", "FAST · ACCURATE", GOOD,
                "Real Whisper accuracy, ~9x cheaper than OpenAI and near-instant. Free dev tier.",
                GROQ_URL)
    engine_card("Deepgram", "FASTEST · LIVE", GOOD,
                "Text appears as you speak. Best for long dictation. $200 free credit, no card — about 430 hours.",
                DEEPGRAM_URL)
    engine_card("Local Whisper", "PRIVATE · FREE · NO KEY", MUTED,
                "Runs entirely on your PC, nothing leaves the machine. Slowest — raise the model size for accuracy.")

    # --- optional polish ---
    tk.Label(wrap, text="OPTIONAL: AI POLISH", bg=BG, fg=ACCENT,
             font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(20, 2))
    tk.Label(wrap, text="Tidies filler words, punctuation and casing. It's fast. If you're on Gemini, your free",
             bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
    polish = tk.Frame(wrap, bg=BG)
    polish.pack(anchor="w", pady=(0, 0))
    tk.Label(polish, text="Google Gemini key", bg=BG, fg=ACCENT, cursor="hand2",
             font=("Segoe UI", 9, "underline")).pack(side="left")
    tk.Label(polish, text=" already does this (same key as transcription), or go fully offline with ",
             bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
    tk.Label(polish, text="Ollama", bg=BG, fg=ACCENT, cursor="hand2",
             font=("Segoe UI", 9, "underline")).pack(side="left")
    tk.Label(polish, text=".", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
    for child in polish.winfo_children():
        if child.cget("fg") == ACCENT:
            url = GEMINI_URL if "Gemini" in child.cget("text") else OLLAMA_URL
            child.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))

    tk.Label(wrap, text="You can change any of this anytime in Settings → the Guide tab walks you through it.",
             bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(10, 0))

    # --- buttons ---
    btns = tk.Frame(wrap, bg=BG)
    btns.pack(fill="x", pady=(22, 0))

    def go():
        result["go"] = True
        root.destroy()

    def later():
        # An explicit choice to skip — honour it.
        result["go"] = False
        result["chose"] = True
        root.destroy()

    def dismissed():
        # Closing the window is NOT a choice to skip setup. Treating it as one
        # dropped a brand-new user straight to the tray with nothing on screen.
        result["go"] = True
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", dismissed)

    tk.Button(btns, text="I'll set up later", command=later, bg=CARD, fg=FG,
              activebackground="#262a3a", activeforeground=FG, relief="flat",
              padx=16, pady=8, font=("Segoe UI", 9)).pack(side="left")
    tk.Button(btns, text="Get started   →", command=go, bg=ACCENT, fg="#1a0c0d",
              activebackground="#e8838b", relief="flat", padx=22, pady=8,
              font=("Segoe UI", 10, "bold")).pack(side="right")

    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{max(0, (sh - h) // 5)}")
    from . import winui
    winui.dark_titlebar(root)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    root.mainloop()
    return result["go"]
