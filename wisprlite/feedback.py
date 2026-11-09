"""Send feedback: a small window that POSTs user feedback to the site's
/api/feedback (which emails the maintainer via Resend). User-initiated only —
launched as its own process via ``--feedback`` (like --about)."""

from __future__ import annotations

from . import theme
from .theme import px

import json
import platform
import threading
import urllib.request
import webbrowser

from . import __version__, config

BG = "#13151d"; CARD = "#1b1e29"; FG = "#e5e7eb"; MUTED = "#94a3b8"
ACCENT = "#e06c75"; GOOD = "#98c379"; WARN = "#e5c07b"
ENDPOINT = "https://pipevoice.app/api/feedback"
ISSUES_URL = "https://github.com/Powleads/PipeVoice/issues/new"
CATEGORIES = [("bug", "Bug / something broke"), ("idea", "Feature idea"), ("other", "Something else")]


def _post(payload: dict):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return (200 <= r.status < 300, "")
    except Exception as exc:
        return (False, str(exc))


def main() -> None:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return
    from . import winui

    root = tk.Tk(); theme.init(root); root.title("Send feedback — Pipevoice"); root.configure(bg=BG); root.resizable(False, False)
    ico = config.asset_path("wisprlite.ico")
    if ico:
        try: root.iconbitmap(ico)
        except Exception: pass

    style = ttk.Style(root)
    try: style.theme_use("clam")
    except Exception: pass
    style.configure("Accent.TButton", background=ACCENT, foreground="#1a0c0d",
                    font=theme.font("body_strong"), padding=px(8), borderwidth=0)
    style.map("Accent.TButton", background=[("active", "#e8838b")])
    style.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=FG, arrowcolor=MUTED)

    wrap = tk.Frame(root, bg=BG, padx=px(26), pady=px(22)); wrap.pack(fill="both", expand=True)
    tk.Label(wrap, text="Send feedback", bg=BG, fg=FG, font=theme.font("subtitle")).pack(anchor="w")
    tk.Label(wrap, text="Found a bug or want a feature? Tell me — it goes straight to my inbox.",
             bg=BG, fg=MUTED, font=theme.font("body"), wraplength=px(494), justify="left").pack(anchor="w", pady=px((2, 14)))

    tk.Label(wrap, text="Type", bg=BG, fg=MUTED, font=theme.font("body")).pack(anchor="w")
    cat_var = tk.StringVar(value=CATEGORIES[0][1])
    ttk.Combobox(wrap, textvariable=cat_var, state="readonly",
                 values=[l for _, l in CATEGORIES], width=32).pack(anchor="w", pady=px((2, 12)))

    tk.Label(wrap, text="Message", bg=BG, fg=MUTED, font=theme.font("body")).pack(anchor="w")
    msg = tk.Text(wrap, height=6, width=54, bg=CARD, fg=FG, insertbackground=FG,
                  relief="flat", font=theme.font("body"), wrap="word")
    msg.pack(anchor="w", pady=px((2, 12)))

    tk.Label(wrap, text="Your email (optional — only if you'd like a reply)",
             bg=BG, fg=MUTED, font=theme.font("body")).pack(anchor="w")
    email = tk.Entry(wrap, bg=CARD, fg=FG, insertbackground=FG, relief="flat", font=theme.font("body"), width=54)
    email.pack(anchor="w", pady=px((2, 10)), ipady=px(4))

    tk.Label(wrap, text=f"Sends your message + PipeVoice {__version__} + your Windows version. "
                        "Nothing else — no audio, no transcripts — and only when you click Send.",
             bg=BG, fg=MUTED, font=theme.font("caption"), wraplength=px(494), justify="left").pack(anchor="w", pady=px((0, 12)))

    status = tk.Label(wrap, text="", bg=BG, fg=MUTED, font=theme.font("body")); status.pack(anchor="w")

    row = tk.Frame(wrap, bg=BG); row.pack(fill="x", pady=px((8, 0)))
    send_btn = ttk.Button(row, text="Send", style="Accent.TButton"); send_btn.pack(side="left")
    gh = tk.Label(row, text="Prefer GitHub? Open an issue ↗", bg=BG, fg=ACCENT, cursor="hand2",
                  font=theme.font("body", underline=True)); gh.pack(side="left", padx=px((14, 0)))
    gh.bind("<Button-1>", lambda e: webbrowser.open(ISSUES_URL))

    def do_send():
        text = msg.get("1.0", "end").strip()
        if len(text) < 2:
            status.config(text="Please type a message first.", fg=WARN); return
        label = cat_var.get()
        category = next((c for c, l in CATEGORIES if l == label), "other")
        payload = {"category": category, "message": text, "email": email.get().strip(),
                   "app_version": __version__, "os": platform.platform()}
        send_btn.config(state="disabled"); status.config(text="Sending…", fg=MUTED)

        def work():
            ok, _err = _post(payload)

            def done():
                if ok:
                    status.config(text="Thanks — sent. 🙏", fg=GOOD); send_btn.config(text="Sent")
                    root.after(1200, root.destroy)
                else:
                    status.config(text="Couldn't send — check your connection, or use GitHub.", fg=WARN)
                    send_btn.config(state="normal")
            root.after(0, done)
        threading.Thread(target=work, daemon=True).start()
    send_btn.config(command=do_send)

    root.update_idletasks()
    w = max(px(575), root.winfo_reqwidth()); h = root.winfo_reqheight()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")
    winui.dark_titlebar(root)
    root.mainloop()


if __name__ == "__main__":
    main()
