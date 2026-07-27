"""System-tray icon + menu (pystray). Degrades to a no-op if unavailable."""

from __future__ import annotations

import threading
import webbrowser

STATE_COLOR = {
    "idle": (148, 163, 184, 255),
    "recording": (52, 211, 153, 255),
    "meeting": (96, 165, 250, 255),
    "meeting_degraded": (251, 146, 60, 255),
    "transcribing": (251, 191, 36, 255),
    "error": (248, 113, 113, 255),
}


class Tray:
    def __init__(self, app) -> None:
        self.app = app
        self.icon = None
        self.ok = False

    # ---- icon art ---------------------------------------------------------
    def _image(self, color):
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), fill=(19, 21, 29, 255))
        # a little microphone glyph in the state color
        d.rounded_rectangle((26, 16, 38, 38), radius=6, fill=color)
        d.arc((20, 22, 44, 46), 0, 180, fill=color, width=3)
        d.line((32, 46, 32, 52), fill=color, width=3)
        d.line((24, 52, 40, 52), fill=color, width=3)
        return img

    def _state_image(self, state):
        return self._image(STATE_COLOR.get(state, STATE_COLOR["idle"]))

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> None:
        try:
            import pystray
            from pystray import Menu, MenuItem as Item
        except Exception:
            self.ok = False
            return

        app = self.app

        def engine_item(name, label):
            return Item(
                label,
                lambda icon, item: app.set_engine(name),
                checked=lambda item: app.cfg.engine == name,
                radio=True,
            )

        def mode_item(name, label):
            return Item(
                label,
                lambda icon, item: app.set_mode(name),
                checked=lambda item: app.cfg.mode == name,
                radio=True,
            )

        def output_item(name, label):
            return Item(
                label,
                lambda icon, item: app.set_output(name),
                checked=lambda item: app.cfg.output_mode == name,
                radio=True,
            )

        menu = Menu(
            Item("Pipevoice", None, enabled=False),
            Menu.SEPARATOR,
            Item("Engine", Menu(
                engine_item("gemini", "Gemini  (free)"),
                engine_item("groq", "Groq Whisper  (fast, cheap)"),
                engine_item("deepgram", "Deepgram  (streaming)"),
                engine_item("local", "Local Whisper  (offline)"),
            )),
            Item("Mode", Menu(
                mode_item("ptt", "Push-to-talk"),
                mode_item("toggle", "Toggle"),
            )),
            Item("Output", Menu(
                output_item("type", "Type"),
                output_item("paste", "Paste"),
            )),
            Menu.SEPARATOR,
            Item("Settings…", lambda i, it: app.open_settings(), default=True),
            Item("History…", lambda i, it: app.open_history()),
            Item("Transcribe a file…", lambda i, it: app.open_transcribe()),
            Item("App profiles…", lambda i, it: app.open_profiles()),
            Item("Record meeting", lambda i, it: app.toggle_meeting(),
                 checked=lambda it: app.meeting_active,
                 enabled=lambda it: not app.paused or app.meeting_active),
            Item("Show overlay", lambda i, it: app.toggle_overlay(),
                 checked=lambda it: app.cfg.overlay),
            Item("Sounds", lambda i, it: app.toggle_sounds(),
                 checked=lambda it: app.cfg.sounds),
            Item("Start on login", lambda i, it: app.toggle_autostart(),
                 checked=lambda it: app.autostart_enabled()),
            Item("Agent MCP (listen + transcribe)", lambda i, it: app.toggle_mcp(),
                 checked=lambda it: app.cfg.mcp_enabled),
            Item("Paused", lambda i, it: app.toggle_pause(),
                 checked=lambda it: app.paused),
            Menu.SEPARATOR,
            Item("About / What's new…", lambda i, it: app.open_about()),
            Item("Send feedback…", lambda i, it: app.open_feedback()),
            Item("Check for updates…", lambda i, it: app.check_for_updates(manual=True)),
            Item("★ Star us on GitHub",
                 lambda i, it: webbrowser.open("https://github.com/Powleads/PipeVoice")),
            Item("Review on Product Hunt",
                 lambda i, it: webbrowser.open("https://www.producthunt.com/products/pipevoice/reviews?utm_source=pipevoice_app")),
            Menu.SEPARATOR,
            Item("Quit", lambda i, it: app.quit()),
        )

        self.icon = pystray.Icon("wisprlite", self._state_image("idle"), "Pipevoice", menu)
        self.ok = True
        try:
            self.icon.run_detached()
        except (NotImplementedError, Exception):
            threading.Thread(target=self.icon.run, daemon=True).start()

    def set_state(self, state) -> None:
        if self.icon is not None:
            try:
                self.icon.icon = self._state_image(state)
            except Exception:
                pass

    def update(self) -> None:
        if self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
