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

from . import (about, autostart, cleanup, config, history, meeting, meetings_tab,
               screenrec_tab, voices, vocab_mine, winui)

ENGINES = [("gemini", "Gemini — free, one key does it all"),
           ("groq", "Groq Whisper — fast & cheap, top accuracy"),
           ("deepgram", "Deepgram — fastest, live streaming"),
           ("local", "Local Whisper — private & free, slower")]
MODES = [("ptt", "Push-to-talk (hold)"), ("toggle", "Toggle (tap on/off)")]
OUTPUTS = [("type", "Type keystrokes"), ("paste", "Clipboard + Ctrl+V")]
PASTE_SPEEDS = [("fast", "Fast"), ("normal", "Normal"), ("slow", "Slow")]
CLEANUP_PROVIDERS = [("openai", "OpenAI"), ("gemini", "Google Gemini (free tier)"),
                     ("openrouter", "OpenRouter (free models)"), ("ollama", "Local — Ollama (offline)")]
STYLES = [("tidy", "Tidy — clean up"), ("prompt", "Prompt — for AI tools"),
          ("email", "Email — greeting, body, sign-off"),
          ("code_comment", "Code comment — wrapped in comment syntax"),
          ("meeting_actions", "Meeting actions — bullet the action items"),
          ("custom", "Custom…")]
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

_FIX_SEP = " → "


def fixes_from_lines(lines) -> dict:
    """Parse ["wrong → right", ...] rows back into {wrong: right}. Pure, testable
    without Tk — the Listbox just stores these strings as its display rows."""
    out = {}
    for line in lines:
        wrong, sep, right = str(line).partition(_FIX_SEP)
        wrong = wrong.strip()
        if wrong and sep:
            out[wrong] = right.strip()
    return out


def fixes_to_lines(fixes: dict) -> list:
    return [f"{k}{_FIX_SEP}{v}" for k, v in (fixes or {}).items()]


def write_fixes_csv(path, fixes: dict) -> None:
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["wrong", "right"])
        for k, v in (fixes or {}).items():
            w.writerow([k, v])


def read_fixes_csv(path) -> dict:
    """Merge a wrong,right CSV into a dict. A header row (wrong,right) is skipped
    if present; a plain two-column file with no header works the same."""
    import csv
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if rows and [c.strip().lower() for c in rows[0][:2]] == ["wrong", "right"]:
        rows = rows[1:]
    for row in rows:
        if len(row) >= 2 and row[0].strip():
            out[row[0].strip()] = row[1].strip()
    return out

BG = "#13151d"
CARD = "#1b1e29"
FG = "#e5e7eb"
MUTED = "#94a3b8"
ACCENT = "#e06c75"


def _input_devices(show_all: bool = False):
    """Return [(label, value)] for the device picker; never raises.

    Grouped by default — one entry per physical mic, best host-API endpoint,
    the recommended one labelled. `show_all` returns every raw PortAudio
    endpoint instead, for anyone who needs a specific host API.
    """
    items = [("System default", "")]
    try:
        from . import mics

        raw = mics.list_inputs()
        if show_all:
            for d in raw:
                items.append((f"[{d['index']}] {d['name']} ({d['hostapi']})", str(d["index"])))
        else:
            grouped = mics.group_inputs(raw)
            best = mics.recommend(grouped)
            best_index = best["index"] if best else None
            for g in sorted(grouped, key=lambda g: g["index"]):
                label = f"[{g['index']}] {g['name']}"
                if g["index"] == best_index:
                    label += " (recommended)"
                items.append((label, str(g["index"])))
    except Exception:
        pass
    return items


def _record_seconds(device, seconds: float, rate: int | None = None):
    """Record `seconds` of float32 mono audio from `device`. Blocks."""
    import sounddevice as sd

    if rate is None:
        try:
            rate = int(sd.query_devices(device, "input")["default_samplerate"]) or 16_000
        except Exception:
            rate = 16_000
    data = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="float32", device=device)
    sd.wait()
    return data.reshape(-1), rate


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
    _g_window = gc.create_window((0, 0), window=g, anchor="nw")
    # A reading column, not the full window: prose at 1080px is unpleasant to
    # read and left the cards mostly empty beside text wrapped at 640.
    winui.fit_scroll_body(gc, _g_window, max_width=740)
    g.bind("<Configure>", lambda e: gc.configure(scrollregion=gc.bbox("all")))
    wheel(gc)

    # The old guide wrapped every line at 470px inside a 1080px column, so the
    # text hugged the left with dead space beside it, and headings were barely
    # larger than body text — one undifferentiated wall to scroll through.
    WRAP = 640
    _anchors: dict[str, tk.Widget] = {}

    def head(t, top=26, key=None):
        """A section heading: large, spaced, and with a rule under it."""
        holder = tk.Frame(g, bg=BG)
        holder.pack(fill="x", padx=26, pady=(top, 0))
        tk.Label(holder, text=t, bg=BG, fg=FG, font=("Segoe UI", 13, "bold"),
                 anchor="w", justify="left").pack(fill="x")
        tk.Frame(holder, bg=ACCENT, height=2, width=44).pack(anchor="w", pady=(5, 0))
        tk.Frame(g, bg=BG, height=9).pack(fill="x")
        if key:
            _anchors[key] = holder

    def body(t, gap=(0, 8)):
        tk.Label(g, text=t, bg=BG, fg=MUTED, font=("Segoe UI", 10), anchor="w",
                 justify="left", wraplength=WRAP).pack(fill="x", padx=26, pady=gap)

    def item(name, t, badge=None, badge_color=GOOD):
        card = tk.Frame(g, bg=CARD)
        card.pack(fill="x", padx=26, pady=(0, 7))
        inner = tk.Frame(card, bg=CARD, padx=14, pady=11)
        inner.pack(fill="x")
        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x")
        tk.Label(row, text=name, bg=CARD, fg=FG,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        if badge:
            tk.Label(row, text=f" {badge} ", bg=badge_color, fg="#10131a",
                     font=("Segoe UI", 7, "bold")).pack(side="left", padx=(9, 0))
        tk.Label(inner, text=t, bg=CARD, fg=MUTED, font=("Segoe UI", 10), anchor="w",
                 justify="left", wraplength=WRAP - 30).pack(fill="x", pady=(5, 0))

    def link(text, key):
        lk = tk.Label(g, text=text, bg=BG, fg=ACCENT, cursor="hand2",
                      font=("Segoe UI", 10, "underline"), anchor="w")
        lk.pack(anchor="w", padx=26, pady=(3, 0))
        lk.bind("<Button-1>", lambda e: webbrowser.open(_URLS[key]))

    def contents(entries):
        """Jump links. The guide is long; scrolling blind through it is the
        reason it read as a wall."""
        wrap = tk.Frame(g, bg=CARD)
        wrap.pack(fill="x", padx=26, pady=(18, 4))
        inner = tk.Frame(wrap, bg=CARD, padx=14, pady=12)
        inner.pack(fill="x")
        tk.Label(inner, text="ON THIS PAGE", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 7))
        # Re-flow into rows on resize. Packed side="left" in one strip, the
        # rightmost chips fall off a narrower window silently — no scrollbar, no
        # sign that navigation is missing.
        strip = tk.Frame(inner, bg=CARD)
        strip.pack(fill="x")
        chips = []
        for label, key in entries:
            chip = tk.Label(strip, text=label, bg=BG, fg=FG, cursor="hand2",
                            font=("Segoe UI", 9), padx=10, pady=4)
            chips.append(chip)

            def jump(_event, k=key):
                target = _anchors.get(k)
                if target is None:
                    return
                gc.update_idletasks()
                top = target.winfo_y()
                total = max(1, g.winfo_height())
                gc.yview_moveto(min(1.0, max(0.0, (top - 20) / total)))

            chip.bind("<Button-1>", jump)
            chip.bind("<Enter>", lambda e, c=chip: c.config(bg=winui.PALETTE["row_hover"]))
            chip.bind("<Leave>", lambda e, c=chip: c.config(bg=BG))

        def reflow(event=None) -> None:
            width = strip.winfo_width()
            if width <= 1:
                return
            for chip in chips:
                chip.grid_forget()
            row = col = used = 0
            for chip in chips:
                need = chip.winfo_reqwidth() + 7
                if used and used + need > width:
                    row += 1
                    col = used = 0
                chip.grid(row=row, column=col, padx=(0, 7), pady=2, sticky="w")
                used += need
                col += 1

        strip.bind("<Configure>", lambda e: reflow(), add="+")
        strip.after(0, reflow)

    contents([
        ("How it works", "how"),
        ("Engines", "engines"),
        ("Polish", "polish"),
        ("Meetings", "meetings"),
        ("After a meeting", "after"),
        ("Make it yours", "yours"),
        ("Help", "help"),
    ])
    head("How it works", top=14, key="how")
    body("Hold your hotkey, talk, then release — your words type in wherever the cursor is: "
         "editor, browser, terminal, anywhere. Default hotkey is Ctrl + \\.")
    body("The second (clipboard) hotkey copies what you say instead of typing it — handy for "
         "pasting feedback into another window.")

    head("Pick your engine — speed lives here", key="engines")
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

    head("Polish (Flow mode) — optional", key="polish")
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

    head("Record a meeting", key="meetings")
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
    item("Say \u201cbookmark that\u201d", "The easiest way to mark a moment: just say it out "
         "loud during the call. Nothing listens live \u2014 the phrase is found when the "
         "recording is transcribed, so it costs nothing, needs no key, and works completely "
         "offline. Change the phrases under Hotkeys.", badge="EASIEST", badge_color=GOOD)
    item("Or press the Bookmark hotkey", "Set one under Hotkeys and tap it during a call. "
         "Handy when you would rather not say anything out loud.")
    item("Or clap twice", "Hands-free, microphone only, so nothing on the call can trigger it. "
         "Off by default \u2014 turn it on under Hotkeys and press Test first.",
         badge="MAY NOT WORK", badge_color=WARN)
    body("Why claps often fail: Windows Audio enhancements DELETE claps, snaps and keyboard "
         "noise on purpose \u2014 that is what they are for \u2014 and they are switched on by "
         "default on many laptops, especially Copilot+ PCs where the feature is called Windows "
         "Studio Effects or Voice Focus. While it is on, no amount of clapping will register. "
         "Turn it off under Windows Sound settings for your microphone, or just use the spoken "
         "phrase, which is speech and so survives the filtering. The Test button tells you "
         "which is happening.")
    body("Whichever you use, a bookmark marks the half-minute BEFORE it, not the instant \u2014 "
         "you always react after the interesting bit, and you say \u201cbookmark that\u201d "
         "after it too. Marked moments appear as Highlights above the transcript, and click one "
         "to jump to it. Summaries are told which moments you flagged, so they get priority "
         "\u2014 without dropping anything else important.")

    item("Tidy up the wording", "Press Polish on a transcribed meeting to strip \u201cum\u201d, "
         "\u201cuh\u201d and false starts and fix punctuation. It only tidies \u2014 it never "
         "rewords anyone or changes what was said \u2014 and the raw transcript stays one click "
         "away, so nothing is lost.", badge="OPT-IN", badge_color=MUTED)

    item("6 · Summarise",
         "Choose Bullets, To-dos or Actions and press Summarise. Actions lists tasks with "
         "an owner — which is why naming people first is worth the ten seconds.")
    body("• Playing music? Your computer's audio is captured as one mix, so a podcast or "
         "Spotify ends up in the transcript alongside the meeting. Pause it first.")
    body("• Recording other people may need their consent where you live. Ask first.")

    head("After the meeting", key="after")
    item("See who did the talking", "Above the transcript: each person's share of the "
         "conversation, how many turns they took, how many questions they asked, and the "
         "longest stretch nobody interrupted. Worked out from the transcript on your PC — "
         "no key, no internet, no cost.", badge="FREE", badge_color=GOOD)
    item("Search every meeting at once", "Tick \u201cAll meetings\u201d beside the search box "
         "and the list narrows to recordings containing your word, with a snippet of where it "
         "came up. It searches what you SEE, so a name you have corrected is found by its "
         "corrected spelling.")
    item("Send it somewhere", "Export writes the meeting as Markdown, a self-contained web "
         "page you can print straight to PDF from any browser, or slides. Highlights, notes "
         "and named speakers all come with it.")
    item("PipeFocus \u2014 a nudge when a call drifts",
         "Optional and off by default. It watches the conversation live and speaks up only "
         "when something concrete is going wrong: an action item nobody has taken, a decision "
         "deferred again. At most one nudge every few minutes, and silent when nothing is "
         "wrong. Needs Deepgram, because it is the only engine that transcribes live, and "
         "costs roughly 30 cents an hour of meeting while it runs.",
         badge="DEEPGRAM ONLY", badge_color=WARN)
    body("Nothing here is automatic except the transcript itself \u2014 summaries, polish and "
         "exports all happen when you press the button, so a meeting you never open costs you "
         "nothing.")

    head("Make it yours", key="yours")
    body("• Accent / language (under Audio): pick yours for a real accuracy boost — UK, US, Indian, "
         "Australian, or Russian-accented English, and more.")
    body("• Speech notes: describe your accent, stutter or filler habits. The AI polish uses it to "
         "fix mis-hearings tailored to how you speak.")
    body("• Vocabulary: add names and jargon so they're always spelled right.")
    body("• Word fixes: a wrong → right list, applied last so they always win. Fix a "
         "misheard word straight from History, or import/export a shared list as CSV.")

    head("Need a hand?", key="help")
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
    _wrap_window = canvas.create_window((0, 0), window=wrap, anchor="nw")
    winui.fit_scroll_body(canvas, _wrap_window)
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
    tab_recordings = tk.Frame(body_wrap, bg=BG)
    tab_guide = tk.Frame(body_wrap, bg=BG)
    tab_about = tk.Frame(body_wrap, bg=BG)
    _tabs = [("Settings", tab_settings), ("Voices", tab_voices), ("History", tab_history),
             ("Meetings", tab_meetings), ("Recordings", tab_recordings),
             ("Guide", tab_guide), ("About", tab_about)]
    _tab_w = {}

    def _show_tab(name):
        if name not in dict(_tabs):
            name = "Settings"
        # A tab's settings panel is a detour INSIDE that tab, not a place of its
        # own, so pressing the tab header is the way back out of it. Do this for
        # every tab, not just the one being opened: leaving a tab with its
        # settings still showing means returning to it lands you in a form you
        # did not ask for.
        for _n, _f in _tabs:
            closer = getattr(_f, "pv_close_settings", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
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
    _frm_window = _canvas.create_window((0, 0), window=frm, anchor="nw")
    frm.bind("<Configure>", lambda e: _canvas.configure(scrollregion=_canvas.bbox("all")))

    # Without this the inner frame keeps its natural width forever, so maximising
    # the window just adds empty space to the right of a ~825px column. Stretching
    # to a 2560px monitor is no better — a form that wide puts a control an inch
    # from its label — so cap it and centre the remainder.
    FORM_MAX_WIDTH = 1080

    def _fit_form(event):
        width = min(event.width, FORM_MAX_WIDTH)
        _canvas.itemconfigure(_frm_window, width=width)
        _canvas.coords(_frm_window, max(0, (event.width - width) // 2), 0)

    _canvas.bind("<Configure>", _fit_form)
    _wheel(_canvas)
    def fix_word_from_history(wrong: str) -> None:
        """"Fix this" on a history row: prefill the wrong side, jump to Settings."""
        fix_wrong_var.set(wrong)
        fix_right_var.set("")
        _show_tab("Settings")
        root.after(50, fix_right_entry.focus_set)

    history.build(tab_history, root, _wheel, on_fix_word=fix_word_from_history)

    def sync_meeting_replacements(replacements):
        items = [f"{k} → {v}" for k, v in replacements.items()]
        fixes_list.delete(0, "end")
        for line in items:
            fixes_list.insert("end", line)

    # Both tabs hand back an empty settings panel. They are built here, but the
    # form helpers (card/row/entry/check) do not exist until further down, so
    # the panels are FILLED later — see _fill_tab_settings().
    meeting_settings_panel = meetings_tab.build(
        tab_meetings, root, _wheel,
        on_replacements_changed=sync_meeting_replacements,
        show_tab=_show_tab,
        with_settings=True,
    )
    recordings_settings_panel = screenrec_tab.build(
        tab_recordings, root, _wheel, with_settings=True)
    _build_guide(tab_guide, _wheel)
    about.build(tab_about, root, _wheel)
    _build_voices_tab(tab_voices, _show_tab, _wheel)
    # First run lands on the GUIDE, not a settings form. Someone who has just
    # installed this needs "how do I use it", and first_run previously changed
    # only the window title. PV_TAB is a render/test seam and still wins.
    _show_tab(os.getenv("PV_TAB") or ("Guide" if first_run else "Settings"))
    DIV = "#272b37"

    def card(title, subtitle=None, parent=None):
        # `parent` lets a card land in the Meetings or Recordings tab instead of
        # the Settings form. Same widgets, same StringVars, same Save — only the
        # frame differs, so nothing about persistence changes.
        wrap = tk.Frame(parent if parent is not None else frm, bg=BG)
        wrap.pack(fill="x", pady=(0, 18), padx=18 if parent is not None else 0)
        tk.Label(wrap, text=title, bg=BG, fg=FG, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=3)
        if subtitle:
            # anchor="w" and a wraplength both matter outside the Settings form:
            # that form caps its own width, a tab panel does not, so an unwrapped
            # subtitle grows past the window and a Label centres what it cannot
            # fit — clipping the text at BOTH edges.
            sub = tk.Label(wrap, text=subtitle, bg=BG, fg=MUTED, font=("Segoe UI", 9),
                           justify="left", anchor="w", wraplength=640)
            sub.pack(fill="x", anchor="w", padx=3, pady=(2, 0))
            if parent is not None:
                wrap.bind("<Configure>",
                          lambda e, w=sub: w.config(wraplength=max(320, e.width - 12)),
                          add="+")
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
            # No tooltip here. The description is already printed above, in full
            # — wraplength wraps, it never truncates — so a popup repeating it
            # said nothing new and covered the next row while it did so.
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
        box = ttk.Checkbutton(r, text=text, variable=var, style="Card.TCheckbutton")
        box.pack(anchor="w")
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
    screenrec_hotkey_var = tk.StringVar(value=cfg.screenrec_hotkey)
    screenrec_dest_var = tk.StringVar(value=cfg.screenrec_destination)
    screenrec_dir_var = tk.StringVar(value=cfg.screenrec_dir)
    screenrec_keep_var = tk.BooleanVar(value=cfg.screenrec_keep_local)
    screenrec_fps_var = tk.StringVar(value=str(cfg.screenrec_fps))
    bookmark_hotkey_var = tk.StringVar(value=cfg.bookmark_hotkey)
    bookmark_acoustic_var = tk.BooleanVar(value=cfg.bookmark_acoustic)
    bookmark_sensitivity_var = tk.DoubleVar(value=cfg.bookmark_sensitivity)
    bookmark_phrases_var = tk.StringVar(value=cfg.bookmark_phrases)
    read_aloud_hotkey_var = tk.StringVar(value=cfg.read_aloud_hotkey)
    read_aloud_tts_opts = [
        ("windows", "Windows natural voices — free, offline, no key"),
        ("deepgram", "Deepgram Aura-2 — cloud, uses your Deepgram key"),
        ("elevenlabs", "ElevenLabs — best quality, your own key, paid"),
    ]
    read_aloud_tts_var = tk.StringVar(
        value=dict(read_aloud_tts_opts).get(cfg.read_aloud_tts, read_aloud_tts_opts[0][1]))
    read_aloud_voice_var = tk.StringVar(value=cfg.read_aloud_voice)
    from . import tts_cloud as _tts_cloud
    read_aloud_deepgram_pick_var = tk.StringVar(value="")
    read_aloud_elevenlabs_id_var = tk.StringVar(value=cfg.read_aloud_elevenlabs_voice_id)
    read_aloud_rate_var = tk.StringVar(value=str(cfg.read_aloud_rate))
    read_aloud_lang_var = tk.StringVar(value=cfg.read_aloud_ocr_language)
    read_aloud_clipboard_var = tk.BooleanVar(value=cfg.read_aloud_clipboard)
    read_aloud_quiet_var = tk.BooleanVar(value=cfg.read_aloud_quiet_with_screenreader)
    lang_var = tk.StringVar(value=dict(LANGUAGES).get(cfg.language, LANGUAGES[0][1]))
    show_all_devices_var = tk.BooleanVar(value=False)
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
    meetings_dir_var = tk.StringVar(value=cfg.meetings_dir)
    paste_speed_var = tk.StringVar(value=dict(PASTE_SPEEDS).get(cfg.paste_speed, PASTE_SPEEDS[1][1]))
    speech_notes_var = tk.StringVar(value=cfg.speech_notes)
    overlay_var = tk.BooleanVar(value=cfg.overlay)
    sounds_var = tk.BooleanVar(value=cfg.sounds)
    autostart_var = tk.BooleanVar(value=autostart.is_enabled())
    auto_update_var = tk.BooleanVar(value=cfg.auto_update)
    beta_channel_var = tk.BooleanVar(value=(cfg.update_channel or "").lower() == "beta")
    pipefocus_var = tk.BooleanVar(value=cfg.pipefocus)
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

    # --- Meetings: rendered into the Meetings tab, not this form ---
    c = card(
        "Meetings",
        "Recording a call captures your microphone and the computer's audio "
        "separately, so nobody gets mixed up.",
        parent=meeting_settings_panel,
    )
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

    r = row(c, "Bookmark hotkey", "Tap while recording to mark the current moment.")
    entry(r, bookmark_hotkey_var, width=14)
    bookmark_cap_btn = ttk.Button(r, text="Capture", width=8)
    bookmark_cap_btn.pack(side="left", padx=(8, 0))
    bookmark_cap_btn.config(command=_mk_capture(bookmark_cap_btn, bookmark_hotkey_var))

    entry(row(c, "Say this to bookmark",
              "Just say one of these during a meeting — no key, no hands. Found when the\n"
              "recording is transcribed, so it costs nothing and works offline.\n"
              "Comma-separated; leave blank to switch off."),
          bookmark_phrases_var, width=34)

    check(c, "Also bookmark on a double clap or snap", bookmark_acoustic_var,
          "Off by default, and it does not work on many laptops: Windows Audio\n"
          "enhancements (Studio Effects / Voice Focus) delete claps and snaps on\n"
          "purpose and ship switched ON. Press Test to see whether yours get through.\n"
          "Microphone only — nothing on the call can trigger it. Prefer the spoken\n"
          "phrase above, which is speech and so survives that filtering.")
    check(c, "PipeFocus \u2014 quiet nudges during a meeting", pipefocus_var,
          "Off by default. Watches the conversation live and speaks up only when something\nconcrete is drifting \u2014 an action item with nobody on it, a decision deferred again.\nAt most one nudge every few minutes, and it stays quiet when nothing is wrong.\nNeeds Deepgram, because it is the only engine that transcribes live; it simply\ndoes not run on the others. Uses your transcription and AI-polish keys while a\nmeeting records. Roughly 30 cents an hour of meeting on Deepgram's standard rate.")
    r = row(c, "Clap sensitivity", "Higher is more sensitive; use Test to calibrate your room.")
    _sens_scale = ttk.Scale(r, from_=0.0, to=1.0, variable=bookmark_sensitivity_var,
                            orient="horizontal", length=130)
    _sens_scale.pack(side="left")
    ttk.Label(r, textvariable=bookmark_sensitivity_var, width=5).pack(side="left", padx=(7, 0))

    def test_snap():
        import tkinter as tk
        from .meeting import smooth_level
        from .overlay import meter_level
        from .snap import SnapDetector
        dialog = tk.Toplevel(root)
        dialog.title("Test acoustic bookmarks")
        dialog.configure(bg=BG)
        tk.Label(dialog, text="Clap twice, or snap twice", bg=BG, fg=FG,
                 font=("Segoe UI", 11, "bold")).pack(padx=18, pady=(16, 6))
        level = ttk.Progressbar(dialog, maximum=1.0, length=260)
        level.pack(padx=18, pady=8)
        result = tk.Label(dialog, text="Listening…", bg=BG, fg=MUTED)
        result.pack(padx=18, pady=(0, 12))
        detector = SnapDetector(16_000, sensitivity=float(bookmark_sensitivity_var.get()))
        stream = None
        # Name the device in every message. "No microphone showing" is impossible
        # to act on without knowing which device the test actually opened.
        _dev = config.device_arg(cfg)
        device_label = "the system default microphone" if _dev is None else f"device {_dev}"
        # The audio callback runs on PortAudio's thread and Tk is not thread-safe,
        # so it only ever writes into this dict — exactly what MeetingRecorder does
        # with self._levels. The UI polls on the main thread below.
        shared = {"level": 0.0, "hits": 0, "error": "", "blocks": 0, "peak": 0.0}

        def callback(block, _frames, _time, _status):
            try:
                shared["blocks"] += 1
                total = 0.0
                count = 0
                for value in block:
                    sample = float(value[0] if getattr(value, "ndim", 0) else value)
                    total += sample * sample
                    count += 1
                rms = (total / count) ** 0.5 if count else 0.0
                if rms > shared["peak"]:
                    shared["peak"] = rms
                # Fast attack, slow release, then the same dB curve the REC meter
                # uses. A linear bar reads as dead: speech peaks near 0.05.
                shared["level"] = smooth_level(shared["level"], rms)
                if detector.feed(block):
                    shared["hits"] += 1
            except Exception as exc:      # never silently: a dead bar told us nothing
                shared["error"] = f"{type(exc).__name__}: {exc}"

        seen_hits = 0
        ticks = 0

        def tick():
            # Never leave a silent bar again: say WHICH of the three states we are
            # in — no audio arriving at all, audio arriving but too quiet, or
            # working. A dead bar with "Listening..." under it is indistinguishable
            # from a crash, which is exactly how this wasted a debugging round.
            nonlocal seen_hits, ticks
            if not dialog.winfo_exists():
                return
            ticks += 1
            level.configure(value=meter_level(shared["level"]))
            if shared["error"]:
                result.config(text=shared["error"], fg=ACCENT)
            elif shared["hits"] > seen_hits:
                seen_hits = shared["hits"]
                result.config(text=f"Detected · {seen_hits}", fg=GOOD)
            elif shared["blocks"] == 0 and ticks > 30:      # ~1.5s with no callback
                result.config(
                    text=f"No audio from {device_label}. Pick a different microphone "
                         "under Audio, then reopen this test.",
                    fg=ACCENT,
                )
            elif shared["blocks"] and shared["peak"] < 0.002 and ticks > 60:
                result.config(
                    text=f"{device_label} is connected but silent — check it is not "
                         "muted, and that it is the mic you are speaking into.",
                    fg=WARN,
                )
            elif detector.transients:
                # A pair was not accepted, but sharp events ARE getting through:
                # say so, or "nothing happened" reads as a dead feature again.
                result.config(
                    text=f"{detector.transients} sharp sound(s) heard — "
                         "clap twice, about a second apart",
                    fg=WARN,
                )
            elif detector.last_peak:
                # Loud but smooth IS the Windows Studio Effects signature: Voice
                # Focus is built to strip non-speech transients — claps, snaps and
                # keyboard noise — so the clap arrives loud and flattened. Confirmed
                # on a Copilot+ machine where it is on by default. Name it, because
                # no amount of clapping harder will ever fix it.
                result.config(
                    text=f"Heard it ({detector.last_peak:.2f} loud) but too smooth — "
                         f"crest {detector.last_crest:.1f}, needs "
                         f"{detector.need_crest:.1f}.\nWindows is filtering the clap "
                         "out. Sound settings → your mic → turn OFF Audio "
                         "enhancements\n(Copilot+ PCs: Windows Studio Effects → "
                         "Voice Focus).",
                    fg=WARN,
                    justify="left",
                )
            elif shared["blocks"]:
                result.config(
                    text=f"Listening · {device_label} · peak {shared['peak']:.3f}",
                    fg=MUTED,
                )
            dialog.after(50, tick)

        def release():
            nonlocal stream
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
                stream = None

        def close():
            release()
            dialog.destroy()

        # Closing the SETTINGS window destroys this dialog without ever running
        # close(), so WM_DELETE_WINDOW alone leaks the microphone — and on first
        # run settings.main() lives in the app process, so the app then cannot
        # acquire its own mic. That is exactly the "something else is holding the
        # device" failure this dialog exists to diagnose. <Destroy> fires for every
        # descendant widget, so only act on the dialog itself.
        dialog.bind(
            "<Destroy>", lambda event: release() if event.widget is dialog else None
        )
        try:
            import sounddevice as sd
            stream = sd.InputStream(samplerate=16_000, channels=1, dtype="float32",
                                    blocksize=800, callback=callback,
                                    device=config.device_arg(cfg))
            stream.start()
            ttk.Button(dialog, text="Close", command=close).pack(pady=(0, 16))
            dialog.protocol("WM_DELETE_WINDOW", close)
            tick()
        except Exception as exc:
            result.config(text=f"Microphone unavailable: {exc}", fg=ACCENT)
            ttk.Button(dialog, text="Close", command=close).pack(pady=(0, 16))
    _test_btn = ttk.Button(r, text="Test", command=test_snap)
    _test_btn.pack(side="left", padx=(8, 0))

    # Acoustic bookmarks are OFF by default and blocked outright on many laptops, but
    # the sensitivity slider and Test button sat live regardless — so the Hotkeys tab
    # foregrounded a switched-off feature next to the spoken phrase that actually
    # works. Grey them out until the box is ticked. (James, 2026-07-28: "the ui still
    # says about snapping ... is this the wrong one again?")
    def _sync_acoustic(*_):
        state = "normal" if bookmark_acoustic_var.get() else "disabled"
        for _w in (_sens_scale, _test_btn):
            try:
                _w.config(state=state)
            except Exception:
                pass

    bookmark_acoustic_var.trace_add("write", _sync_acoustic)
    _sync_acoustic()

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

    # --- Screen recordings: rendered into the Recordings tab, not this form ---
    c = card(
        "Screen recordings",
        "Show a bug instead of describing it. Recordings are sent with a text "
        "transcript of what you said, so a coding agent can read it without "
        "watching the video.",
        parent=recordings_settings_panel,
    )
    r = row(c, "Screen recording hotkey",
            "Tap once to drag a box over what you want to show, and again to stop. "
            "Records that area plus your microphone.")
    entry(r, screenrec_hotkey_var, width=14)
    screenrec_cap_btn = ttk.Button(r, text="Capture", width=8)
    screenrec_cap_btn.pack(side="left", padx=(8, 0))
    screenrec_cap_btn.config(command=_mk_capture(screenrec_cap_btn, screenrec_hotkey_var))
    entry(row(c, "Send to",
              "An scp destination, e.g. root@your-vps:/root/project/inbox/. Uses the "
              "SSH keys you already have — Pipevoice never stores one. Leave blank "
              "to only save locally."),
          screenrec_dest_var, width=34)
    entry(row(c, "Save to",
              "Blank uses your Videos folder."), screenrec_dir_var, width=34)
    check(c, "Keep a local copy after sending", screenrec_keep_var,
          "Off deletes the files once they have arrived. A failed send never deletes anything.")
    entry(row(c, "Frames per second",
              "10-15 is realistic at 1080p. Higher costs CPU on the machine you are "
              "recording, which is the same one running what you are showing."),
          screenrec_fps_var, width=6)

    # --- Read Aloud: press the hotkey, have the screen read to you ---
    c = card(
        "Read Aloud",
        "For text a screen reader can't reach — an image, a canvas, a scanned "
        "PDF. Tap = drag a region, +Shift = the whole screen, +Ctrl = the "
        "focused window. Speaks even with a screen reader running, unless you "
        "turn that off below.",
    )
    r = row(c, "Read Aloud hotkey", "Blank turns the feature off.")
    entry(r, read_aloud_hotkey_var, width=14)
    ra_cap_btn = ttk.Button(r, text="Capture", width=8)
    ra_cap_btn.pack(side="left", padx=(8, 0))
    ra_cap_btn.config(command=_mk_capture(ra_cap_btn, read_aloud_hotkey_var))

    combo(row(c, "Voice engine",
              "Windows is free, offline, no key — nothing ever leaves this "
              "machine. Deepgram and ElevenLabs send the recognized text to "
              "their servers to speak it; only pick those on purpose. Any "
              "cloud failure (dead key, no network) falls back to Windows and "
              "says why."),
          read_aloud_tts_var, [l for _, l in read_aloud_tts_opts])

    wr = row(c, "Get better Windows voices",
             "Windows 11 ships far better \"Natural\" voices, but they aren't "
             "installed by default. Opens Settings → Speech → Manage voices.")
    ttk.Button(wr, text="Open Windows voice settings",
               command=lambda: os.startfile("ms-settings:speech")).pack(side="left")

    entry(row(c, "Voice / model",
              "Blank uses the system default Windows voice, or the default "
              "Deepgram voice (aura-2-draco-en)."),
          read_aloud_voice_var, width=28)
    dg_row = row(c, "Deepgram voice (pick one)",
                 "Only used when the voice engine above is Deepgram.")
    dg_combo = combo(dg_row, read_aloud_deepgram_pick_var,
                     [f"{name} — {desc}" for name, desc in _tts_cloud.DEEPGRAM_VOICES], width=40)

    def _pick_deepgram_voice(_evt=None):
        i = dg_combo.current()
        if 0 <= i < len(_tts_cloud.DEEPGRAM_VOICES):
            read_aloud_voice_var.set(_tts_cloud.DEEPGRAM_VOICES[i][0])
    dg_combo.bind("<<ComboboxSelected>>", _pick_deepgram_voice)

    entry(row(c, "ElevenLabs voice ID",
              "Only used when the voice engine above is ElevenLabs — their "
              "catalogue is per-account, so this is an ID, not a picklist."),
          read_aloud_elevenlabs_id_var, width=28)

    entry(row(c, "Speed", "0.5 (slow) to 2.0 (fast). 1.0 is normal."),
          read_aloud_rate_var, width=6)
    entry(row(c, "OCR language",
              "e.g. en-US. Blank uses your Windows display language."),
          read_aloud_lang_var, width=10)
    check(c, "Also copy the recognized text to the clipboard", read_aloud_clipboard_var,
          "On by default. The overlay always says when it copied.")
    check(c, "Stay quiet while a screen reader is running", read_aloud_quiet_var,
          "Off by default — the hotkey is usually pressed because the screen "
          "reader can't read that spot, so silence there defeats the feature.")

    c = card("Audio")
    mic_row = row(c, "Microphone")
    device_combo = combo(mic_row, device_var, [lbl for lbl, _ in devices], width=30)

    def test_mic():
        from . import mics

        dialog = tk.Toplevel(root)
        dialog.title("Test my mic")
        dialog.configure(bg=BG)
        tk.Label(dialog, text="Keep talking while this runs…", bg=BG, fg=FG,
                 font=("Segoe UI", 11, "bold")).pack(padx=18, pady=(16, 6))
        result = tk.Label(dialog, text="", bg=BG, fg=MUTED, wraplength=320, justify="left")
        result.pack(padx=18, pady=(0, 12))
        remedy_row = tk.Frame(dialog, bg=BG)
        remedy_row.pack()

        def _aim_for(m):
            """Turn the numbers into the one instruction they imply.

            "Too quiet" on its own is a diagnosis with no treatment - it tells
            you something is wrong and leaves you holding it. Say how far off
            it is and in which direction."""
            rms = m["rms_dbfs"]
            if rms == float("-inf"):
                return "Aim for -20 dBFS. Nothing is reaching this device at all."
            if rms < -30:
                return f"Aim for -20 dBFS — about {abs(-20 - rms):.0f} dB louder than this."
            if m["clipping_pct"] > 0.1:
                return "Aim for -20 dBFS — turn the level down until clipping is 0%."
            return "Aim for -20 dBFS. This is in range."

        def _open_sound_settings():
            # Recording tab of the classic Sound panel: the level slider lives
            # in Properties > Levels, which is the thing that actually fixes it.
            import subprocess
            for command in (
                ["rundll32.exe", "shell32.dll,Control_RunDLL", "mmsys.cpl,,1"],
                ["cmd", "/c", "start", "", "ms-settings:sound"],
            ):
                try:
                    subprocess.Popen(command)
                    return
                except Exception:
                    continue
            _show("Could not open Windows sound settings — open Sound > "
                  "Recording > your mic > Properties > Levels.", WARN)

        def _offer_remedy(verdict_text):
            def apply():
                for child in remedy_row.winfo_children():
                    child.destroy()
                if verdict_text.startswith("Good"):
                    return
                ttk.Button(remedy_row, text="Open Windows sound settings",
                           command=_open_sound_settings).pack(pady=(0, 8))
            try:
                if remedy_row.winfo_exists():
                    remedy_row.after(0, apply)
            except Exception:
                pass

        def _selected_device():
            value = dict((lbl, val) for lbl, val in devices).get(device_var.get(), "")
            return int(value) if value else None

        def _show(text, fg):
            # Recording happens on a worker thread, so every UI write comes back
            # through the main loop. A destroyed dialog would abort the whole
            # interpreter, not just this callback - see the about.py incident.
            def apply():
                if result.winfo_exists():
                    result.config(text=text, fg=fg)
            try:
                if dialog.winfo_exists():
                    dialog.after(0, apply)
            except Exception:
                pass

        def _in_background(fn):
            # sd.rec + sd.wait blocks for 3s, or 1.5s PER DEVICE. On the UI
            # thread that is a frozen, "Not Responding" window for the whole
            # test - on the one feature that exists to feel reassuring.
            for button in (single_btn, all_btn):
                button.config(state="disabled")

            def done():
                for button in (single_btn, all_btn):
                    if button.winfo_exists():
                        button.config(state="normal")

            def work():
                try:
                    fn()
                finally:
                    try:
                        if dialog.winfo_exists():
                            dialog.after(0, done)
                    except Exception:
                        pass

            threading.Thread(target=work, daemon=True).start()

        def run_single():
            _show("Recording 3s…", MUTED)
            try:
                samples, rate = _record_seconds(_selected_device(), 3.0)
                m = mics.measure(samples, rate)
                v = mics.verdict(m)
                fg = GOOD if v == "Good" else (ACCENT if "loud" in v or "Nothing" in v else WARN)
                _show(
                    f"{v}\npeak {m['peak_dbfs']:.1f} dBFS · rms {m['rms_dbfs']:.1f} dBFS · "
                    f"snr {m['snr_db']:.1f} dB · clipping {m['clipping_pct']:.2f}%\n"
                    f"{_aim_for(m)}",
                    fg,
                )
                _offer_remedy(v)
            except Exception as exc:
                _show(f"Microphone unavailable: {exc}", ACCENT)

        def run_all():
            _show("Testing every microphone — keep talking…", MUTED)
            try:
                grouped = mics.group_inputs(mics.list_inputs())
                scored = []
                skipped = []
                heard_any = False
                for g in grouped:
                    # PER DEVICE. Windows always enumerates endpoints that will
                    # not open - in use by another app, disconnected, or simply
                    # refusing mono float32. One try around the whole loop meant
                    # the first such device killed the run with "Test failed"
                    # and every mic after it went untested.
                    try:
                        samples, rate = _record_seconds(g["index"], 1.5)
                    except Exception as exc:
                        skipped.append(f"{g['name']} ({exc.__class__.__name__})")
                        continue
                    m = mics.measure(samples, rate)
                    if m["rms_dbfs"] >= -40:
                        heard_any = True
                    in_band = -30 <= m["rms_dbfs"] <= -6
                    scored.append((g, m, in_band))
                note = f"\nSkipped {len(skipped)}: {', '.join(skipped)}" if skipped else ""
                if not scored:
                    _show("No microphone could be opened." + note, ACCENT)
                    return
                if not heard_any:
                    _show("Nothing heard on any device — inconclusive, "
                          "keep talking and try again." + note, WARN)
                    return
                best_g, best_m, _ = max(scored, key=lambda t: (t[2], t[1]["snr_db"]))
                best_label = next(
                    (lbl for lbl, val in devices if val == str(best_g["index"])), None
                )
                if best_label:
                    device_var.set(best_label)
                _show(f"Picked {best_g['name']} — {mics.verdict(best_m)}" + note, GOOD)
                _offer_remedy(mics.verdict(best_m))
            except Exception as exc:
                _show(f"Test failed: {exc}", ACCENT)

        btns = tk.Frame(dialog, bg=BG)
        btns.pack(pady=(0, 8))
        single_btn = ttk.Button(btns, text="Test mic (3s)",
                                command=lambda: _in_background(run_single))
        single_btn.pack(side="left", padx=4)
        all_btn = ttk.Button(btns, text="Test all and pick the best",
                             command=lambda: _in_background(run_all))
        all_btn.pack(side="left", padx=4)
        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=(0, 16))

    ttk.Button(mic_row, text="Test my mic", command=test_mic).pack(side="left", padx=(8, 0))

    def _on_show_all(*_):
        nonlocal devices
        current_value = dict((lbl, val) for lbl, val in devices).get(device_var.get(), "")
        devices = _input_devices(show_all_devices_var.get())
        device_combo.configure(values=[lbl for lbl, _ in devices])
        new_label = next((lbl for lbl, val in devices if val == current_value), devices[0][0])
        device_var.set(new_label)

    show_all_devices_var.trace_add("write", _on_show_all)
    check(c, "Show all endpoints", show_all_devices_var,
          "Every raw device PortAudio reports, including duplicates across host "
          "APIs (MME/DirectSound/WASAPI/WDM-KS) and virtual/loopback inputs.")
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
    combo(row(c, "Polish style", "Tidy keeps your words; Prompt rewrites rambling into a clear AI instruction; Email/Code comment/Meeting actions reshape it for that context; Custom uses your own instruction."),
          cleanup_style_var, [l for _, l in STYLES])
    entry(row(c, "Custom instruction / sign-off name",
              "Your own instruction when Polish style = Custom, or the sign-off name when style = Email."),
          cleanup_instruction_var, width=24)

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
        from tkinter import messagebox  # module-level tkinter isn't imported here

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
            # Say so. A button that does nothing is indistinguishable from a crash.
            messagebox.showinfo(
                "Vocabulary from meetings",
                "No new terms found yet. Record and transcribe a few meetings, or "
                "correct some wording in a transcript, and try again.",
                parent=root,
            )
            return

        picker = tk.Toplevel(root)
        picker.title("Vocabulary from meetings")
        picker.configure(bg=BG)
        picker.transient(root)
        tk.Label(picker, text="Choose terms to add", bg=BG, fg=FG,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18, pady=(16, 4))
        tk.Label(picker, text="Nothing is added unless you tick it.", bg=BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=18, pady=(0, 10))
        # Buttons pack to the BOTTOM first, so the expanding body can never starve
        # them: in the old order 21+ candidates left "Add selected" unrendered
        # entirely, and a realistic corpus produces 30+.
        buttons = tk.Frame(picker, bg=BG)
        buttons.pack(side="bottom", fill="x", padx=18, pady=14)

        outer = tk.Frame(picker, bg=CARD)
        outer.pack(fill="both", expand=True, padx=18)
        canvas = tk.Canvas(outer, bg=CARD, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=CARD, padx=14, pady=8)
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window, width=e.width))
        _wheel(canvas)
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

    wf = stack(c, "Word fixes", "Corrections applied last, so they always win. Double-click a row to edit it.")
    fixes_list = tk.Listbox(wf, height=4, width=30, bg=BG, fg=FG,
                            selectbackground=ACCENT, selectforeground="#1a0c0d",
                            highlightthickness=1, highlightbackground=DIV,
                            relief="flat", activestyle="none", exportselection=False,
                            font=("Segoe UI", 9))
    fixes_list.pack(side="left", anchor="n")
    for _line in fixes_to_lines(cfg.replacements):
        fixes_list.insert("end", _line)
    fside = tk.Frame(wf, bg=CARD)
    fside.pack(side="left", padx=(8, 0), anchor="n")
    fix_wrong_var = tk.StringVar()
    fix_right_var = tk.StringVar()
    ttk.Entry(fside, textvariable=fix_wrong_var, width=16).pack(anchor="w")
    tk.Label(fside, text="→", bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
    fix_right_entry = ttk.Entry(fside, textvariable=fix_right_var, width=16)
    fix_right_entry.pack(anchor="w")

    def _fix_add(*_a):
        wrong = fix_wrong_var.get().strip()
        right = fix_right_var.get().strip()
        if not wrong:
            return
        # .strip() on both sides of the comparison, or a row that ever picks up
        # stray whitespace silently becomes a second entry for the same word
        # instead of replacing the first.
        existing = {line.partition(_FIX_SEP)[0].strip(): i
                    for i, line in enumerate(fixes_list.get(0, "end"))}
        line = f"{wrong}{_FIX_SEP}{right}"
        if wrong in existing:
            fixes_list.delete(existing[wrong])
            fixes_list.insert(existing[wrong], line)
        else:
            fixes_list.insert("end", line)
        fix_wrong_var.set("")
        fix_right_var.set("")

    def _fix_remove():
        for i in reversed(fixes_list.curselection()):
            fixes_list.delete(i)

    def _fix_edit(_event=None):
        sel = fixes_list.curselection()
        if not sel:
            return
        wrong, _sep, right = fixes_list.get(sel[0]).partition(_FIX_SEP)
        fix_wrong_var.set(wrong.strip())
        fix_right_var.set(right)
        fixes_list.delete(sel[0])

    def _fix_export():
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            parent=root, defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            write_fixes_csv(path, fixes_from_lines(fixes_list.get(0, "end")))

    def _fix_import():
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=root, filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        merged = fixes_from_lines(fixes_list.get(0, "end"))
        merged.update(read_fixes_csv(path))
        fixes_list.delete(0, "end")
        for _line in fixes_to_lines(merged):
            fixes_list.insert("end", _line)

    fix_right_entry.bind("<Return>", _fix_add)
    fixes_list.bind("<Double-Button-1>", _fix_edit)
    _frow = tk.Frame(fside, bg=CARD)
    _frow.pack(anchor="w", pady=(6, 0))
    ttk.Button(_frow, text="Add", command=_fix_add, width=7).pack(side="left")
    ttk.Button(_frow, text="Remove", command=_fix_remove, width=8).pack(side="left", padx=(6, 0))
    _frow2 = tk.Frame(fside, bg=CARD)
    _frow2.pack(anchor="w", pady=(4, 0))
    ttk.Button(_frow2, text="Import CSV", command=_fix_import).pack(side="left")
    ttk.Button(_frow2, text="Export CSV", command=_fix_export).pack(side="left", padx=(6, 0))

    entry(row(c, "Speech notes", "Describe your accent, stutter or fillers to guide AI cleanup."),
          speech_notes_var, width=24)

    # --- Behaviour ---
    c = card("Behaviour")
    check(c, "Show live overlay", overlay_var, "A small HUD that shows it's listening.")
    check(c, "Play start/stop sounds", sounds_var)
    check(c, "Start on Windows login", autostart_var)
    check(c, "Automatic updates", auto_update_var, "Check for a newer version on startup and install it silently.")
    check(c, "Get updates early (beta)", beta_channel_var,
          "Receive new versions a few days before everyone else, so problems are found\non a handful of machines instead of all of them. Slightly more likely to hit a\nrough edge — untick to go back to normal releases.")
    check(c, "Keep a local dictation history", history_var, "Saved on your PC; open it from the tray.")
    pr = stack(c, "Voices & app profiles", "Per-app styles and key-bound Voices — PipeVoice's signature feature.")
    ttk.Button(pr, text="Open the Voices tab  →", command=lambda: _show_tab("Voices")).pack(anchor="w")

    # --- Advanced ---
    c = card("Advanced", "Most people never need these.")
    entry(row(c, "Min seconds", "Ignore taps shorter than this."), min_seconds_var, width=7)
    entry(row(c, "Deepgram wait", "Seconds to wait for final words."), dg_timeout_var, width=7)
    combo(row(c, "Paste speed", "Slower is more reliable in some apps."),
          paste_speed_var, [l for _, l in PASTE_SPEEDS], width=10)

    # A part-finished model download makes local transcription slow and empty,
    # and the only cure was deleting a folder by hand from a support reply.
    from . import modelcache

    _cache_row = row(c, "Local model cache",
                     "Whisper's downloaded models. Clear this if local transcription "
                     "is slow or returns nothing —\nit downloads again next time you "
                     "use it.")
    _cache_size = tk.StringVar(value="…")
    ttk.Label(_cache_row, textvariable=_cache_size, width=9).pack(side="left", padx=(0, 8))
    _clear_btn = ttk.Button(_cache_row, text="Clear", width=8)
    _clear_btn.pack(side="left")

    def _refresh_cache_size():
        # Walking the cache touches the disk, so keep it off the UI thread —
        # a big cache on a slow drive would otherwise freeze the window.
        def work():
            try:
                text = modelcache.human_size(modelcache.size_bytes())
            except Exception:
                text = "unknown"
            root.after(0, lambda: _cache_size.set(text))
        threading.Thread(target=work, daemon=True).start()

    def _clear_cache():
        from tkinter import messagebox

        if not messagebox.askyesno(
                "Clear model cache",
                "Delete the downloaded Whisper models?\n\nThey download again "
                "(about 150 MB for the default) the next time you use local "
                "transcription. Nothing you have dictated or recorded is touched."):
            return
        _clear_btn.config(state="disabled", text="…")
        def work():
            ok, message = modelcache.clear()
            def done():
                _clear_btn.config(state="normal", text="Clear")
                _refresh_cache_size()
                (messagebox.showinfo if ok else messagebox.showwarning)(
                    "Model cache", message)
            root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    _clear_btn.config(command=_clear_cache)
    _refresh_cache_size()

    # --- Meeting storage: rendered into the Meetings tab, beside the recordings
    # it governs. These sat under "Advanced" three tabs away from the list they
    # apply to, which is exactly the jumping-around this move removes.
    c = card("Where meetings are kept", parent=meeting_settings_panel)
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
    _mr = row(c, "Save meetings to",
              "Recordings are large — put them on another drive if you like.\n"
              "Blank uses this PC's default folder. Existing recordings stay where they are.")
    entry(_mr, meetings_dir_var, width=30)

    def _browse_meetings():
        from tkinter import filedialog
        chosen = filedialog.askdirectory(
            parent=root, title="Where should meeting recordings be saved?",
            initialdir=meetings_dir_var.get().strip() or str(meeting.meetings_dir()),
        )
        if chosen:
            meetings_dir_var.set(chosen)

    ttk.Button(_mr, text="Browse", width=8,
               command=_browse_meetings).pack(side="left", padx=(8, 0))

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
        cfg.screenrec_hotkey = screenrec_hotkey_var.get().strip()
        cfg.screenrec_destination = screenrec_dest_var.get().strip()
        cfg.screenrec_dir = screenrec_dir_var.get().strip()
        cfg.screenrec_keep_local = bool(screenrec_keep_var.get())
        try:
            cfg.screenrec_fps = max(1, min(60, int(screenrec_fps_var.get().strip() or 12)))
        except ValueError:
            cfg.screenrec_fps = 12
        cfg.read_aloud_hotkey = read_aloud_hotkey_var.get().strip()
        cfg.read_aloud_tts = value_for(read_aloud_tts_var, read_aloud_tts_opts)
        cfg.read_aloud_voice = read_aloud_voice_var.get().strip()
        cfg.read_aloud_elevenlabs_voice_id = read_aloud_elevenlabs_id_var.get().strip()
        try:
            cfg.read_aloud_rate = max(0.5, min(2.0, float(read_aloud_rate_var.get().strip() or 1.0)))
        except ValueError:
            cfg.read_aloud_rate = 1.0
        cfg.read_aloud_ocr_language = read_aloud_lang_var.get().strip()
        cfg.read_aloud_clipboard = bool(read_aloud_clipboard_var.get())
        cfg.read_aloud_quiet_with_screenreader = bool(read_aloud_quiet_var.get())
        cfg.bookmark_hotkey = bookmark_hotkey_var.get().strip()
        cfg.bookmark_acoustic = bool(bookmark_acoustic_var.get())
        cfg.bookmark_phrases = bookmark_phrases_var.get().strip()
        try:
            cfg.bookmark_sensitivity = max(0.0, min(1.0, float(bookmark_sensitivity_var.get())))
        except (TypeError, ValueError):
            pass
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
        cfg.update_channel = "beta" if beta_channel_var.get() else "stable"
        cfg.pipefocus = bool(pipefocus_var.get())
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
        cfg.meetings_dir = meetings_dir_var.get().strip()
        cfg.paste_speed = value_for(paste_speed_var, PASTE_SPEEDS)
        cfg.speech_notes = speech_notes_var.get().strip()
        cfg.replacements = fixes_from_lines(fixes_list.get(0, "end"))
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

    # Save keeps the window OPEN. Closing on save makes it impossible to change
    # two things in a row, and forces a reopen just to check the change stuck.
    # X and Close are what close it.
    save_btn = ttk.Button(footer, text="Save", style="Accent.TButton")
    save_btn.pack(side="right")

    def save_and_stay():
        save(close=False)
        save_btn.config(text="Saved \u2713")
        root.after(1200, lambda: save_btn.config(text="Save"))

    save_btn.config(command=save_and_stay)
    ttk.Button(footer, text="Close", command=root.destroy).pack(side="right", padx=(0, 8))
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
    # The Meetings tab is a two-pane layout with a transcript in it and is much
    # more usable with the whole screen. Maximise, but keep the geometry above as
    # the restore size so un-maximising still lands somewhere sensible.
    try:
        root.state("zoomed")
    except Exception:
        pass
    winui.dark_titlebar(root)
    root.mainloop()


if __name__ == "__main__":
    main()
