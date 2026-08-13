"""Windows-only window-chrome helpers. Silent no-op on other platforms / older
Windows builds, so callers can always call them.
"""

from __future__ import annotations

import ctypes

DARK = "#13151d"

# Shared dark + coral palette so every window (settings, voices, profiles) matches.
PALETTE = {
    "bg": "#13151d", "card": "#1b1e29", "popover": "#20242c", "fg": "#e5e7eb",
    "muted": "#94a3b8", "accent": "#e06c75", "accent_hi": "#e8838b", "div": "#272b37",
    "good": "#98c379", "amber": "#e5c07b", "row_hover": "#242936",
    "error": "#f87171", "done": "#60a5fa", "picker": "#a78bfa",
    "meeting": "#fb7185", "meeting_hi": "#fda4af", "meter_track": "#39414f",
    "speaker_1": "#61afef", "speaker_2": "#c678dd", "speaker_3": "#56b6c2",
    "speaker_4": "#d19a66", "search_match": "#3a4050",
    "search_current": "#e5c07b",
    # subtle field border instead of clam's default WHITE bevel; coral on focus.
    "border": "#39414f",
}


def apply_theme(root):
    """Apply the shared dark + coral ttk theme to a window and return its Style.

    Centralizes what used to be copy-pasted per window — and, crucially, sets
    lightcolor/darkcolor/bordercolor so ttk Entry/Combobox stop drawing the default
    light (white) 3D bevel on our dark cards. Safe on any platform.
    """
    from tkinter import ttk

    p = PALETTE
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    # base: kill the white bevel (lightcolor/darkcolor = card), subtle dark border
    style.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["card"],
                    bordercolor=p["border"], lightcolor=p["card"], darkcolor=p["card"],
                    troughcolor=p["card"], font=("Segoe UI", 10))
    style.configure("TLabel", background=p["bg"], foreground=p["fg"], font=("Segoe UI", 10))
    style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"], font=("Segoe UI", 9))
    style.configure("Head.TLabel", background=p["bg"], foreground=p["accent"], font=("Segoe UI", 13, "bold"))
    style.configure("TButton", background=p["card"], foreground=p["fg"], padding=6, borderwidth=0)
    style.map("TButton", background=[("active", "#262a3a")])
    style.configure("Accent.TButton", background=p["accent"], foreground="#1a0c0d",
                    font=("Segoe UI", 9, "bold"), padding=7, borderwidth=0)
    style.map("Accent.TButton", background=[("active", p["accent_hi"])])
    # Green, for the one action a recording is waiting on. Distinct from the
    # coral accent so "Transcribe" does not compete with Save for the eye.
    style.configure("Go.TButton", background=p["good"], foreground="#10131a",
                    padding=6, borderwidth=0)
    style.map("Go.TButton", background=[("active", "#a9d189"), ("disabled", p["card"])])
    style.map("Go.TButton", foreground=[("disabled", p["muted"])])
    style.configure("Pick.TButton", background="#2a2f3d", foreground=p["fg"], padding=6, borderwidth=0)
    style.map("Pick.TButton", background=[("active", "#333a4a")])
    style.configure("TCheckbutton", background=p["bg"], foreground=p["fg"])
    style.map("TCheckbutton", background=[("active", p["bg"])])
    style.configure("Card.TCheckbutton", background=p["card"], foreground=p["fg"])
    style.map("Card.TCheckbutton", background=[("active", p["card"])])
    style.configure("TEntry", fieldbackground=p["card"], foreground=p["fg"], insertcolor=p["fg"],
                    bordercolor=p["border"], lightcolor=p["card"], darkcolor=p["card"])
    style.map("TEntry", bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["accent"])],
              darkcolor=[("focus", p["accent"])])
    style.configure("TCombobox", fieldbackground=p["card"], background=p["card"], foreground=p["fg"],
                    arrowcolor=p["fg"], bordercolor=p["border"], lightcolor=p["card"], darkcolor=p["card"])
    style.map("TCombobox",
              fieldbackground=[("readonly", p["card"]), ("disabled", p["card"])],
              foreground=[("readonly", p["fg"]), ("disabled", p["muted"])],
              selectbackground=[("readonly", p["card"])],
              selectforeground=[("readonly", p["fg"])],
              bordercolor=[("focus", p["accent"])],
              lightcolor=[("focus", p["accent"])], darkcolor=[("focus", p["accent"])],
              background=[("readonly", p["card"]), ("active", p["card"])])
    style.configure("TNotebook", background=p["bg"], borderwidth=0, tabmargins=(10, 8, 0, 0))
    style.configure("TNotebook.Tab", background=p["card"], foreground=p["muted"],
                    padding=(26, 12), font=("Segoe UI", 10, "bold"), borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", p["bg"])],
              foreground=[("selected", p["accent"]), ("active", p["fg"])])
    style.configure("Footer.TFrame", background=p["card"])
    style.configure("TScrollbar", background=p["card"], troughcolor=p["bg"],
                    bordercolor=p["bg"], arrowcolor=p["muted"])
    # the combobox dropdown popup is a plain Tk Listbox (not themed by ttk)
    root.option_add("*TCombobox*Listbox.background", p["card"])
    root.option_add("*TCombobox*Listbox.foreground", p["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#1a0c0d")
    return style


# Exactly one tooltip may be on screen. Each widget used to own its own popup
# and hide it only on its OWN <Leave>; a missed leave (the pointer jumping, the
# widget scrolling away, the popup landing under the cursor) stranded it, so
# they stacked up as the mouse moved — three were mapped at once from a single
# hover, two of them duplicates.
_ACTIVE: dict = {"tip": None, "timer": None}


def _hide_tooltip(widget) -> None:
    timer = _ACTIVE.get("timer")
    if timer is not None:
        try:
            widget.after_cancel(timer)
        except Exception:
            pass
        _ACTIVE["timer"] = None
    tip = _ACTIVE.get("tip")
    if tip is not None:
        try:
            tip.destroy()
        except Exception:
            pass
        _ACTIVE["tip"] = None


def tooltip(widget, text: str, *, delay: int = 450) -> None:
    """Attach a hover tooltip (plain Tk, dark themed, coral hairline border).

    Beside the widget, never under it: a tooltip placed below covers whatever
    comes next, so it hid the very row you were moving toward.

    It waits `delay` ms before appearing, so sweeping the pointer across a list
    or scrolling past a control does not fire a trail of popups, and it hides on
    scroll or click. Only one is ever on screen. No-op if text is empty.

    Do NOT attach one whose text is already visible on screen — a popup that
    repeats the description printed under the label adds nothing and occludes
    its neighbours.
    """
    if not text:
        return
    import tkinter as tk

    p = PALETTE

    def build() -> None:
        _ACTIVE["timer"] = None
        try:
            if not widget.winfo_ismapped():
                return
            tip = tk.Toplevel(widget)
            tip.wm_overrideredirect(True)
            tip.configure(bg=p["accent"])  # 1px coral hairline
            frame = tk.Frame(tip, bg=p["popover"], padx=11, pady=8)
            frame.pack(padx=1, pady=1)
            tk.Label(frame, text=text, bg=p["popover"], fg=p["fg"], justify="left",
                     wraplength=320, font=("Segoe UI", 9)).pack()
            tip.update_idletasks()
            width, height = tip.winfo_reqwidth(), tip.winfo_reqheight()
            screen_w, screen_h = tip.winfo_screenwidth(), tip.winfo_screenheight()

            # Beside the control, flipping to its left when the right edge of
            # the screen is too close, and pulled up when it would run off the
            # bottom. Off-screen is just a different way of being unreadable.
            x = widget.winfo_rootx() + widget.winfo_width() + 12
            if x + width > screen_w - 8:
                x = widget.winfo_rootx() - width - 12
            x = max(8, min(x, screen_w - width - 8))
            y = max(8, min(widget.winfo_rooty(), screen_h - height - 8))

            tip.wm_geometry(f"+{x}+{y}")
            try:
                tip.attributes("-topmost", True)
            except Exception:
                pass
            _ACTIVE["tip"] = tip
        except Exception:
            _ACTIVE["tip"] = None

    def show(_=None) -> None:
        _hide_tooltip(widget)          # never let two coexist
        try:
            _ACTIVE["timer"] = widget.after(delay, build)
        except Exception:
            _ACTIVE["timer"] = None

    def hide(_=None) -> None:
        _hide_tooltip(widget)

    widget.bind("<Enter>", show, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<Destroy>", hide, add="+")
    # <Button> covers clicks AND the X11 wheel, which arrives as buttons 4/5.
    # Windows does not: there the wheel is <MouseWheel>, so it needs its own
    # binding — and a Linux test cannot tell the two apart, because <Button>
    # silently satisfies it. That is the binding every real user depends on.
    widget.bind("<Button>", hide, add="+")
    widget.bind("<MouseWheel>", hide, add="+")


def dark_titlebar(root, color: str = DARK) -> None:
    """Force a dark title bar (Windows 10 1809+ / 11) instead of the user's
    accent color. No-op if DWM is unavailable."""
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        dwm = ctypes.windll.dwmapi
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (was 19 on early 1809 builds)
        on = ctypes.c_int(1)
        if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on)) != 0:
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(on), ctypes.sizeof(on))
        # DWMWA_CAPTION_COLOR = 35 (Win 11 22000+): match our dark background. 0x00BBGGRR
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        bgr = ctypes.c_int((b << 16) | (g << 8) | r)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(bgr), ctypes.sizeof(bgr))
    except Exception:
        pass


def fit_scroll_body(canvas, window_id, max_width: int = 1080) -> None:
    """Keep a scrolling canvas's inner frame width-tracked, capped and centred.

    A canvas item created with create_window keeps its *natural* width forever
    unless something sets it, so maximising a window just adds dead space to the
    right of a narrow column. Stretching a form to a 2560px monitor is no better
    — it puts a control an inch from its label — so cap the width and centre what
    is left over.
    """

    def _fit(event):
        width = min(event.width, max_width)
        canvas.itemconfigure(window_id, width=width)
        canvas.coords(window_id, max(0, (event.width - width) // 2), 0)

    canvas.bind("<Configure>", _fit, add="+")


def collapsible_settings(container, link, hides, palette_bg, wheel=None):
    """A settings panel a tab can swap in over its own content.

    Returns the frame to fill. Clicking ``link`` hides every widget in ``hides``
    and shows the panel instead; clicking again reverses it.

    It SWAPS rather than pushes because a settings block is taller than the
    window: packed above a browser it shoved the list off the bottom and then
    ran off the bottom itself, so neither was usable. It scrolls for the same
    reason — the Meetings settings alone are taller than the window.
    """
    import tkinter as tk
    from tkinter import ttk

    holder = tk.Frame(container, bg=palette_bg)
    canvas = tk.Canvas(holder, bg=palette_bg, highlightthickness=0)
    bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview,
                        style="Vertical.TScrollbar")
    canvas.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    panel = tk.Frame(canvas, bg=palette_bg)
    window_id = canvas.create_window((0, 0), window=panel, anchor="nw")
    panel.bind("<Configure>",
               lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>",
                lambda e: canvas.itemconfigure(window_id, width=e.width), add="+")
    if callable(wheel):
        wheel(canvas)

    # The link said "Settings" whether the panel was open or shut, so it gave no
    # feedback at all — you could not tell from the screen which state you were
    # in. It now says what pressing it will DO.
    shut_text = link.cget("text")
    open_text = "\u2715  Close settings"

    def close(_event=None):
        if not holder.winfo_ismapped():
            return False
        holder.pack_forget()
        for widget, kwargs in hides:
            widget.pack(**kwargs)
        link.config(text=shut_text, font=("Segoe UI", 9, "underline"))
        return True

    def toggle(_event=None):
        if close():
            return
        for widget, _kwargs in hides:
            widget.pack_forget()
        holder.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        link.config(text=open_text, font=("Segoe UI", 9, "bold"))

    link.bind("<Button-1>", toggle)
    # Clicking the tab you are already on is "take me back to the list" — the
    # settings are a detour inside the tab, not a place of their own, so the
    # tab header has to be a way out of them.
    container.pv_close_settings = close
    return panel
