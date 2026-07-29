"""Let the REAL Tk windows build on a headless Linux box.

Only the Windows-specific audio/input libraries are stubbed. Everything else —
settings.py, meetings_tab.py, overlay.py and their wiring — is the code that
ships, so a smoke test here exercises the same widgets Windows users click.

This exists because a whole class of bug shipped today that a launched window
would have caught instantly: a decorator stolen from the function below it
(crashing every overlay frame), a level meter that never moved, banners left on
screen after their session was deleted, and a Save button that closed the
window. None of those are visible in a unit test; all of them are obvious the
moment the window is built.

What it CANNOT cover, because the platform genuinely differs: WASAPI loopback
capture, Windows Studio Effects filtering, os.startfile / Explorer behaviour,
and the `keyboard` hotkey backend. Those still need a Windows machine.
"""

from __future__ import annotations

import os
import sys
import types


def install_platform_stubs() -> None:
    """Stub the Windows-only audio/input libs so the UI can build on Linux."""
    for name in ("sounddevice", "soundcard", "keyboard", "pystray"):
        sys.modules.setdefault(name, types.ModuleType(name))

    sd = sys.modules["sounddevice"]
    if not hasattr(sd, "InputStream"):
        class _Stream:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

            def read(self, _frames):
                return (None, False)

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        sd.InputStream = _Stream
        sd.query_devices = lambda *_a, **_k: [
            {"name": "Fake Microphone", "max_input_channels": 1, "hostapi": 0}
        ]
        sd.query_hostapis = lambda _index: {"name": "ALSA"}
        sd.default = types.SimpleNamespace(device=(0, 0))

    kb = sys.modules["keyboard"]
    for attr in ("send", "write", "add_hotkey", "remove_hotkey", "unhook_all"):
        if not hasattr(kb, attr):
            setattr(kb, attr, lambda *_a, **_k: None)


def have_display() -> bool:
    """Whether a usable X display exists (CI and dev boxes often have none)."""
    if not os.environ.get("DISPLAY"):
        return False
    try:
        import tkinter as tk

        root = tk.Tk()
        root.destroy()
        return True
    except Exception:
        return False


def build_window(module_main, tab: str = "Settings"):
    """Build a window, run ONE idle pass, and return what was on screen.

    Replaces mainloop so the window is constructed and rendered exactly as it
    would be for a user, then torn down instead of blocking forever.
    """
    import tkinter as tk

    os.environ["PV_TAB"] = tab
    captured: dict = {"error": None, "widgets": 0}
    real_mainloop = tk.Misc.mainloop

    def stub_mainloop(self, _n=0):
        try:
            self.update_idletasks()
            self.update()
            captured["widgets"] = len(self.winfo_children())
            captured["root"] = self
        finally:
            try:
                self.destroy()
            except Exception:
                pass

    tk.Misc.mainloop = stub_mainloop
    try:
        module_main()
    except Exception as exc:                     # noqa: BLE001 - reported, not raised
        captured["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        tk.Misc.mainloop = real_mainloop
    return captured


def make_untranscribed_meeting(base) -> None:
    """A recording that still NEEDS transcribing — audio present, no transcript.

    This is the state the app crashed in: can_transcribe is True, so the
    "not been transcribed yet" banner is packed, and it was packed relative to
    the highlights panel, which is unpacked when a meeting has no bookmarks.
    Fixtures that were already transcribed never reached that line, which is
    exactly why a green smoke suite shipped a launch crash.
    """
    import json
    import wave

    folder = base / "meeting-20260722-120000"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps({
        "started_at": "2026-07-22T12:00:00",
        "stopped_at": "2026-07-22T12:05:00",
    }), encoding="utf-8")
    # _has_audio wants a real wav over 44 bytes, so write one.
    with wave.open(str(folder / "mic.wav"), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16_000)
        out.writeframes(b"\x00\x00" * 800)


def make_meeting_fixtures(base, count: int = 2, with_bookmarks: bool = False) -> None:
    """Write real meeting folders so the Meetings tab renders a SELECTED session.

    Without these the tab short-circuits on "no meetings" and never reaches the
    code that packs the transcribe / bleed / highlights panels — which is how a
    TclError that crashed the app on launch passed a green smoke suite.
    """
    import json

    for index in range(count):
        folder = base / f"meeting-2026072{index}-120000"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(json.dumps({
            "started_at": f"2026-07-2{index}T12:00:00",
            "stopped_at": f"2026-07-2{index}T12:05:00",
            "transcription_backend": "deepgram",
        }), encoding="utf-8")
        (folder / "transcript.json").write_text(json.dumps({"segments": [
            {"speaker": "You", "text": "we should ship the pricing change", "t": 0.0},
            {"speaker": "Them", "text": "agreed, Friday works", "t": 6.0},
        ]}), encoding="utf-8")
        if with_bookmarks:
            (folder / "bookmarks.json").write_text(
                json.dumps([{"t": 6.0, "source": "hotkey"}]), encoding="utf-8"
            )
