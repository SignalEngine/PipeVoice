"""Per-app profiles: when the focused app matches, apply a named Voice's
per-utterance overrides. resolve() does the matching; main() is the --profiles
editor window (App -> Voice).

A profile is {name, match:{exe|title_contains}, voice:"<name>"}. Legacy profiles
carrying ``overrides`` are still resolved (back-compat) and migrated to Voices via
voices.migrate_profiles(). Overrides never mutate saved settings; app.py applies
them for one utterance only.
"""

from __future__ import annotations

import os

from . import config, voices, winui


def resolve(cfg, ctx) -> dict:
    """Overrides of the first profile whose match fits ctx, else {}.

    Accepts a Config object (new style) and reads cfg.profiles / cfg.voices.
    On a match: if the profile has a ``voice`` key the Voice's resolved overrides
    are returned; if it has legacy ``overrides`` those are filtered to VOICE_KEYS
    (back-compat); otherwise {}.
    """
    profs = getattr(cfg, "profiles", None) or []
    if not profs or not ctx:
        return {}
    exe = (ctx.get("exe") or "").lower()
    title = (ctx.get("title") or "").lower()
    for p in profs:
        if not isinstance(p, dict):
            continue
        m = p.get("match") or {}
        m_exe = (m.get("exe") or "").lower()
        m_title = (m.get("title_contains") or "").lower()
        if not (m_exe or m_title):
            continue
        if (not m_exe or exe == m_exe) and (not m_title or m_title in title):
            if p.get("voice"):
                return voices.resolve(cfg, p["voice"])
            ov = p.get("overrides") or {}
            return {k: v for k, v in ov.items() if k in voices.VOICE_KEYS}
    return {}


# ---- editor window --------------------------------------------------------
BG = "#13151d"
CARD = "#1b1e29"
FG = "#e5e7eb"
MUTED = "#94a3b8"
ACCENT = "#e06c75"

# Common apps so popular targets appear even when they aren't currently running.
# Full product names so a natural search ("visual studio code") matches.
COMMON_APPS = [
    ("code.exe", "Visual Studio Code"), ("cursor.exe", "Cursor"), ("chrome.exe", "Google Chrome"),
    ("msedge.exe", "Microsoft Edge"), ("firefox.exe", "Mozilla Firefox"),
    ("windowsterminal.exe", "Windows Terminal"), ("powershell.exe", "PowerShell"),
    ("cmd.exe", "Command Prompt"), ("slack.exe", "Slack"), ("discord.exe", "Discord"),
    ("ms-teams.exe", "Microsoft Teams"), ("notepad.exe", "Notepad"), ("notepad++.exe", "Notepad++"),
    ("winword.exe", "Microsoft Word"), ("outlook.exe", "Microsoft Outlook"), ("obsidian.exe", "Obsidian"),
    ("idea64.exe", "IntelliJ IDEA"), ("explorer.exe", "File Explorer"),
    ("telegram.exe", "Telegram"), ("whatsapp.exe", "WhatsApp"),
]


def _searchable(combo, all_values):
    """Type-to-filter a combobox: narrows the dropdown to matches as you type."""
    def on_key(event):
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab", "Left", "Right",
                             "Shift_L", "Shift_R", "Control_L", "Control_R"):
            return
        typed = combo.get().strip().lower()
        if not typed:
            combo["values"] = all_values
        else:
            matches = [v for v in all_values if typed in v.lower()]
            combo["values"] = matches or all_values
    combo.bind("<KeyRelease>", on_key)


def main() -> None:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return
    from . import winui

    cfg = config.Config.load()
    from . import voices
    if voices.migrate_profiles(cfg):
        cfg.save()
    cards = []
    from . import foreground
    running = foreground.list_windows()
    have = {w["exe"].lower() for w in running}
    merged = list(running) + [{"exe": e, "title": t} for e, t in COMMON_APPS if e.lower() not in have]
    app_values = sorted(
        (w["exe"] + ("  —  " + w["title"][:34] if w.get("title") else "") for w in merged),
        key=str.lower,
    )

    root = tk.Tk()
    root.title("Pipevoice app profiles")
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
    tk.Label(head, text="App profiles", bg=BG, fg=ACCENT, font=("Segoe UI", 16, "bold")).pack(anchor="w")
    tk.Label(head, text="Give an app its own behaviour. Terminal: raw + Enter. Chat: polished + auto-send.\n"
                        "Editor: no AI cleanup. Search your open apps, or Browse to pick any program.",
             bg=BG, fg=MUTED, font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(5, 0))

    # Live readout of the focused app's exe — the exact string a profile matches on.
    # Skips our own windows + the shell, so switching to your target app and back
    # keeps showing that app (not this editor).
    focus_var = tk.StringVar(value="Focused app:  detecting…")
    tk.Label(head, textvariable=focus_var, bg=BG, fg="#98c379",
             font=("Consolas", 9, "bold")).pack(anchor="w", pady=(11, 0))
    tk.Label(head, text="↑ click into the app you want, and this shows the exact name to match on.",
             bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
    _seen = {"exe": ""}

    def _poll_focus():
        try:
            exe = (foreground.detect().get("exe") or "")
            if exe and exe not in foreground._NOISE_EXES:
                _seen["exe"] = exe
            focus_var.set(f"Focused app:  {_seen['exe'] or '—'}")
        except Exception:
            pass
        try:
            root.after(900, _poll_focus)
        except Exception:
            pass

    root.after(400, _poll_focus)

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
    winui.fit_scroll_body(canvas, _holder_window)
    holder.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Enter>", lambda e: canvas.bind_all(
        "<MouseWheel>", lambda ev: canvas.yview_scroll(int(-ev.delta / 120), "units")))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    def add_card(exe="", voice=""):
        overrides_voice = voice
        wrap = tk.Frame(holder, bg=BG)
        wrap.pack(fill="x", padx=18, pady=(0, 12))
        c = tk.Frame(wrap, bg=CARD)
        c.pack(fill="x")
        inner = tk.Frame(c, bg=CARD, padx=18, pady=16)
        inner.pack(fill="x")
        card = {}

        tk.Label(inner, text="APP", bg=CARD, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(inner, text="Type to search your open apps, or Browse to pick any program (.exe).",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 0))
        pick = tk.Frame(inner, bg=CARD)
        pick.pack(fill="x", pady=(6, 0))
        exe_var = tk.StringVar(value=exe)
        cb = ttk.Combobox(pick, textvariable=exe_var, values=app_values, width=32)
        cb.pack(side="left")
        _searchable(cb, app_values)

        def browse(var=exe_var):
            # Native file picker, so the user can grab any installed program by
            # navigating to its .exe instead of guessing the process name.
            from tkinter import filedialog
            la = os.environ.get("LOCALAPPDATA")
            cands = [os.path.join(la, "Programs")] if la else []
            cands += [la, os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")]
            initial = next((c for c in cands if c and os.path.isdir(c)), os.path.expanduser("~"))
            path = filedialog.askopenfilename(
                parent=root, title="Pick a program",
                initialdir=initial,
                filetypes=[("Programs", "*.exe"), ("All files", "*.*")])
            if path:
                var.set(os.path.basename(path))

        ttk.Button(pick, text="Browse…", style="Pick.TButton", command=browse).pack(side="left", padx=(8, 0))
        ttk.Button(pick, text="Remove",
                   command=lambda: (wrap.destroy(), card in cards and cards.remove(card))).pack(side="right")

        vrow = tk.Frame(inner, bg=CARD)
        vrow.pack(fill="x", pady=(14, 0))
        tk.Label(vrow, text="VOICE", bg=CARD, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(vrow, text="The polish preset to use in this app. Create/edit voices in Settings → Manage voices.",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 0))
        voice_opts = voices.names(cfg) or ["Tidy"]
        cur = overrides_voice if overrides_voice in voice_opts else voice_opts[0]
        voice_var = tk.StringVar(value=cur)
        ttk.Combobox(vrow, textvariable=voice_var, values=voice_opts, state="readonly", width=24).pack(anchor="w", pady=(5, 0))

        card.update(exe=exe_var, voice=voice_var)
        cards.append(card)

    for p in (cfg.profiles or []):
        if isinstance(p, dict):
            add_card((p.get("match") or {}).get("exe", ""), p.get("voice", ""))
    if not cfg.profiles:
        add_card()

    def save():
        out = []
        for card in cards:
            exe = card["exe"].get().split("—")[0].strip().lower()
            if not exe:
                continue
            out.append({"name": exe, "match": {"exe": exe}, "voice": card["voice"].get()})
        # Reload first so we only change `profiles`, not other settings the user
        # may have edited in the Settings window meanwhile.
        fresh = config.Config.load()
        fresh.profiles = out
        fresh.save()
        root.destroy()

    ttk.Button(footer, text="+ Add app profile", command=lambda: add_card()).pack(side="left")
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
