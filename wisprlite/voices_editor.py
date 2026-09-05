"""The --voices editor window: CRUD named polish 'Voices' (cfg.voices).

A Voice is a reusable per-utterance override preset (see voices.py). Each card
edits one Voice; empty fields mean "leave that setting unchanged". Save() reloads
config first so a concurrent Settings save isn't clobbered — only `voices` changes.
"""

from __future__ import annotations

from . import config, voices  # noqa: F401  (voices kept for parity / future use)

# ---- editor window --------------------------------------------------------
BG = "#13151d"
CARD = "#1b1e29"
FG = "#e5e7eb"
MUTED = "#94a3b8"
ACCENT = "#e06c75"

STYLES = [("tidy", "Tidy — clean up"),
          ("prompt", "Prompt — reshuffle into coherent text"),
          ("email", "Email — greeting, body, sign-off"),
          ("code_comment", "Code comment — wrapped in comment syntax"),
          ("meeting_actions", "Meeting actions — bullet the action items"),
          ("custom", "Custom…")]
ENGINES = [("", "(leave as-is)"), ("gemini", "Gemini"), ("groq", "Groq"),
           ("deepgram", "Deepgram"), ("local", "Local")]
OUTPUTS = [("", "(leave as-is)"), ("type", "Type"), ("paste", "Paste"),
           ("clipboard", "Clipboard")]
AUTO = [("leave", "(leave as-is)"), ("yes", "Yes"), ("no", "No")]
CLEANUP = [("leave", "(leave as-is)"), ("on", "On"), ("off", "Off")]


def _value_for(label, table):
    return {l: k for k, l in table}.get(label, table[0][0])


def main() -> None:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return
    from . import winui

    cfg = config.Config.load()
    cards = []

    root = tk.Tk()
    root.title("Pipevoice voices")
    root.configure(bg=BG)
    ico = config.asset_path("wisprlite.ico")
    if ico:
        try:
            root.iconbitmap(ico)
        except Exception:
            pass

    style = winui.apply_theme(root)

    head = tk.Frame(root, bg=BG, padx=22, pady=18)
    head.pack(fill="x")
    tk.Label(head, text="Voices", bg=BG, fg=ACCENT, font=("Segoe UI", 16, "bold")).pack(anchor="w")
    tk.Label(head, text="Named polish presets. Bind them to hotkeys or apps. "
                        "Empty fields = leave that setting unchanged.",
             bg=BG, fg=MUTED, font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(5, 0))

    footer = tk.Frame(root, bg=BG, padx=22, pady=12)
    footer.pack(side="bottom", fill="x")

    bodyf = tk.Frame(root, bg=BG)
    bodyf.pack(fill="both", expand=True)
    canvas = tk.Canvas(bodyf, bg=BG, highlightthickness=0, width=640, height=360)
    vbar = ttk.Scrollbar(bodyf, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vbar.set)
    vbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    holder = tk.Frame(canvas, bg=BG)
    _holder_window = canvas.create_window((0, 0), window=holder, anchor="nw")
    holder.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    winui.fit_scroll_body(canvas, _holder_window)
    canvas.bind("<Enter>", lambda e: canvas.bind_all(
        "<MouseWheel>", lambda ev: canvas.yview_scroll(int(-ev.delta / 120), "units")))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    def add_card(voice=None):
        voice = voice or {}
        wrap = tk.Frame(holder, bg=BG)
        wrap.pack(fill="x", padx=18, pady=(0, 12))
        c = tk.Frame(wrap, bg=CARD)
        c.pack(fill="x")
        inner = tk.Frame(c, bg=CARD, padx=18, pady=16)
        inner.pack(fill="x")
        card = {}

        # Name
        toprow = tk.Frame(inner, bg=CARD)
        toprow.pack(fill="x")
        name_col = tk.Frame(toprow, bg=CARD)
        name_col.pack(side="left", fill="x", expand=True)
        tk.Label(name_col, text="NAME", bg=CARD, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        name_var = tk.StringVar(value=voice.get("name", ""))
        _nm = ttk.Entry(name_col, textvariable=name_var, width=32)
        _nm.pack(anchor="w", pady=(5, 0))
        winui.tooltip(_nm, "A short name for this voice (e.g. 'Email', 'Reddit'). You pick it from hotkeys and app profiles.")
        ttk.Button(toprow, text="Remove",
                   command=lambda: (wrap.destroy(), card in cards and cards.remove(card))).pack(side="right")

        # Style / Engine / Output
        ctl = tk.Frame(inner, bg=CARD)
        ctl.pack(fill="x", pady=(16, 0))

        sty_col = tk.Frame(ctl, bg=CARD)
        sty_col.pack(side="left", padx=(0, 26))
        tk.Label(sty_col, text="STYLE", bg=CARD, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        style_var = tk.StringVar(value=dict(STYLES).get(voice.get("cleanup_style", ""), STYLES[0][1]))
        _sty = ttk.Combobox(sty_col, textvariable=style_var, values=[l for _, l in STYLES],
                            state="readonly", width=30)
        _sty.pack(anchor="w", pady=(5, 0))
        winui.tooltip(_sty, "How this voice polishes. Tidy = light clean-up that keeps your wording. Prompt = reshuffle rambling into a clear instruction for an AI tool. Custom = your own instruction (below).")

        eng_col = tk.Frame(ctl, bg=CARD)
        eng_col.pack(side="left", padx=(0, 26))
        tk.Label(eng_col, text="ENGINE", bg=CARD, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        engine_var = tk.StringVar(value=dict(ENGINES).get(voice.get("engine", ""), ENGINES[0][1]))
        _eng = ttk.Combobox(eng_col, textvariable=engine_var, values=[l for _, l in ENGINES],
                            state="readonly", width=14)
        _eng.pack(anchor="w", pady=(5, 0))
        winui.tooltip(_eng, "Transcription engine for this voice. Leave as-is to use whatever you set globally.")

        out_col = tk.Frame(ctl, bg=CARD)
        out_col.pack(side="left")
        tk.Label(out_col, text="OUTPUT", bg=CARD, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        output_var = tk.StringVar(value=dict(OUTPUTS).get(voice.get("output_mode", ""), OUTPUTS[0][1]))
        _out = ttk.Combobox(out_col, textvariable=output_var, values=[l for _, l in OUTPUTS],
                            state="readonly", width=14)
        _out.pack(anchor="w", pady=(5, 0))
        winui.tooltip(_out, "Where the text goes: type it, paste it, or copy to clipboard. Leave as-is to use your default.")

        # Auto-Enter
        ae_row = tk.Frame(inner, bg=CARD)
        ae_row.pack(fill="x", pady=(16, 0))
        ae_col = tk.Frame(ae_row, bg=CARD)
        ae_col.pack(side="left")
        tk.Label(ae_col, text="AUTO-ENTER", bg=CARD, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        ae = voice.get("auto_enter", None)
        ae_label = AUTO[0][1] if ae is None else (AUTO[1][1] if ae else AUTO[2][1])
        autoenter_var = tk.StringVar(value=ae_label)
        _ae = ttk.Combobox(ae_col, textvariable=autoenter_var, values=[l for _, l in AUTO],
                           state="readonly", width=14)
        _ae.pack(anchor="w", pady=(5, 0))
        winui.tooltip(_ae, "Press Enter after typing (e.g. to send a chat message). Leave as-is to use your default.")

        cl_col = tk.Frame(ae_row, bg=CARD)
        cl_col.pack(side="left", padx=(26, 0))
        tk.Label(cl_col, text="AI CLEANUP", bg=CARD, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        ac = voice.get("ai_cleanup", None)
        ac_label = CLEANUP[0][1] if ac is None else (CLEANUP[1][1] if ac else CLEANUP[2][1])
        cleanup_var = tk.StringVar(value=ac_label)
        _cl = ttk.Combobox(cl_col, textvariable=cleanup_var, values=[l for _, l in CLEANUP],
                           state="readonly", width=14)
        _cl.pack(anchor="w", pady=(5, 0))
        winui.tooltip(_cl, "Whether the AI polishes at all. On = always polish with this style. Off = type your raw words, no AI. Leave as-is = use your global setting.")

        # Custom instruction
        instr_frame = tk.Frame(inner, bg=CARD)
        instr_frame.pack(fill="x", pady=(16, 0))
        tk.Label(instr_frame, text="Custom instruction (used when Style = Custom)",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        instr_var = tk.StringVar(value=voice.get("cleanup_instruction", ""))
        _instr = ttk.Entry(instr_frame, textvariable=instr_var, width=60)
        _instr.pack(anchor="w", pady=(4, 0))
        winui.tooltip(_instr, "Used only when Style = Custom. Describe how to rewrite, e.g. 'Rewrite as a polite, formal email.'")

        card.update(name=name_var, style=style_var, engine=engine_var, output=output_var,
                    auto_enter=autoenter_var, ai_cleanup=cleanup_var, instruction=instr_var)
        cards.append(card)

    for v in (cfg.voices or []):
        if isinstance(v, dict):
            add_card(v)
    if not cfg.voices:
        add_card()

    def save():
        out = []
        for card in cards:
            name = card["name"].get().strip()
            if not name:
                continue
            style = _value_for(card["style"].get(), STYLES)
            ae_key = _value_for(card["auto_enter"].get(), AUTO)   # "leave"/"yes"/"no"
            auto_enter = None if ae_key == "leave" else (ae_key == "yes")
            ac_key = _value_for(card["ai_cleanup"].get(), CLEANUP)  # "leave"/"on"/"off"
            ai_cleanup = None if ac_key == "leave" else (ac_key == "on")
            out.append({
                "name": name,
                "cleanup_style": style,
                "cleanup_instruction": card["instruction"].get().strip(),
                "engine": _value_for(card["engine"].get(), ENGINES),
                "auto_enter": auto_enter,
                "output_mode": _value_for(card["output"].get(), OUTPUTS),
                "ai_cleanup": ai_cleanup,
            })
        # Reload first so we only change `voices`, not other settings the user may
        # have edited in the Settings window meanwhile.
        fresh = config.Config.load()
        fresh.voices = out
        fresh.save()
        root.destroy()

    ttk.Button(footer, text="+ Add voice", command=lambda: add_card()).pack(side="left")
    ttk.Button(footer, text="Save", style="Accent.TButton", command=save).pack(side="right")
    ttk.Button(footer, text="Cancel", command=root.destroy).pack(side="right", padx=(0, 8))

    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    w = max(700, root.winfo_reqwidth())
    h = min(max(460, root.winfo_reqheight() + 16), sh - 150)
    root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(16, (sh - h) // 5)}")
    winui.dark_titlebar(root)
    root.mainloop()


if __name__ == "__main__":
    main()
