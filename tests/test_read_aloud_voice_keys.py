"""Read Aloud: ElevenLabs key field, per-engine rows, and voice preview.

Run headless with:  xvfb-run -a python -m pytest tests/test_read_aloud_voice_keys.py
Skips cleanly when there is no display.
"""

import pathlib
import sys
import threading
import time
import tkinter as tk
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from uistub import have_display, install_platform_stubs

install_platform_stubs()

DISPLAY = have_display()
SKIP = "no X display; run under xvfb-run"


def _skip_if_headless():
    if not DISPLAY:
        pytest.skip(SKIP)


def _find(widget, predicate):
    for child in widget.winfo_children():
        try:
            if predicate(child):
                return child
        except Exception:
            pass
        found = _find(child, predicate)
        if found is not None:
            return found
    return None


def _find_by_text(widget, prefix):
    return _find(widget, lambda w: str(w.cget("text")).startswith(prefix))


def _build(monkeypatch=None):
    """Build the real Settings window on the Read Aloud tab and return the root."""
    import os
    import tkinter as tk
    from wisprlite import settings

    os.environ["PV_TAB"] = "Settings"
    captured: dict = {}
    real_mainloop = tk.Misc.mainloop

    def stub_mainloop(self, _n=0):
        self.update_idletasks()
        self.update()
        captured["root"] = self

    tk.Misc.mainloop = stub_mainloop
    try:
        settings.main()
    finally:
        tk.Misc.mainloop = real_mainloop
    return captured["root"]


def _pump(root, seconds=3.0, until=None):
    """Run a REAL Tk mainloop until `until()` is true or the timeout hits.

    A worker thread's `root.after(...)` call is only honoured once the
    interpreter is genuinely inside `mainloop()` — a bare `root.update()`
    loop leaves Tcl believing no loop is running, so a cross-thread `after`
    deterministically raises "main thread is not in main loop" instead of
    just being slow. This is exactly how the real app runs (app.py blocks in
    mainloop() the whole time a preview plays), so it is also the more
    faithful test, not just the one that avoids the harness artifact.
    """
    predicate = until or (lambda: True)
    deadline = time.time() + seconds
    real_mainloop = tk.Misc.mainloop

    def poll():
        if predicate() or time.time() > deadline:
            root.quit()
        else:
            root.after(20, poll)

    root.after(20, poll)
    real_mainloop(root)
    return predicate()


def test_elevenlabs_key_is_saved_through_save_api_key():
    """Gate 1: filling the ElevenLabs key field and saving calls save_api_key
    with ELEVENLABS_API_KEY — the single field that never existed before."""
    _skip_if_headless()
    from tkinter import ttk
    from wisprlite import config

    calls = []
    orig = config.save_api_key
    config.save_api_key = lambda name, value: calls.append((name, value))
    try:
        root = _build()
        try:
            key_label = _find_by_text(root, "ElevenLabs API key")
            assert key_label is not None, "no ElevenLabs API key field was built"
            row = key_label.master.master  # label -> left frame -> row frame
            entry = _find(row, lambda w: isinstance(w, ttk.Entry))
            assert entry is not None
            entry.delete(0, "end")
            entry.insert(0, "sk-test-12345")

            save_btn = _find_by_text(root, "Save")
            assert save_btn is not None
            save_btn.invoke()
        finally:
            root.destroy()
    finally:
        config.save_api_key = orig

    assert ("ELEVENLABS_API_KEY", "sk-test-12345") in calls


def test_engine_picker_shows_only_the_matching_rows():
    """Gate 2: switching the engine picker shows the matching rows and hides
    the others, driving the trace callback directly."""
    _skip_if_headless()
    from tkinter import ttk

    root = _build()
    try:
        engine_label = _find_by_text(root, "Voice engine")
        engine_row = engine_label.master.master
        combo = _find(engine_row, lambda w: isinstance(w, ttk.Combobox))
        assert combo is not None

        windows_marker = _find_by_text(root, "Get better Windows voices")
        deepgram_marker = _find_by_text(root, "Deepgram voice (pick one)")
        eleven_marker = _find_by_text(root, "ElevenLabs API key")
        assert windows_marker.winfo_ismapped(), "Windows rows should show by default"
        assert not deepgram_marker.winfo_ismapped()
        assert not eleven_marker.winfo_ismapped()

        combo.set("Deepgram Aura-2 — cloud, uses your Deepgram key")
        root.update()
        assert not windows_marker.winfo_ismapped()
        assert deepgram_marker.winfo_ismapped()
        assert not eleven_marker.winfo_ismapped()

        combo.set("ElevenLabs — best quality, your own key, paid")
        root.update()
        assert not windows_marker.winfo_ismapped()
        assert not deepgram_marker.winfo_ismapped()
        assert eleven_marker.winfo_ismapped()

        combo.set("Windows natural voices — free, offline, no key")
        root.update()
        assert windows_marker.winfo_ismapped()
        assert not deepgram_marker.winfo_ismapped()
        assert not eleven_marker.winfo_ismapped()
    finally:
        root.destroy()


def _preview_widgets(root):
    from tkinter import ttk

    btn = _find(root, lambda w: isinstance(w, ttk.Button) and w.cget("text") == "Preview")
    assert btn is not None, "no Preview button was built"
    status = None
    for child in btn.master.winfo_children():
        import tkinter as tk
        if isinstance(child, tk.Label):
            status = child
    assert status is not None, "no preview status label next to the Preview button"
    return btn, status


def test_preview_uses_the_unsaved_form_value_not_the_saved_config():
    """Gate 3: change the voice in the form without saving, click preview,
    and the speaker must be built with the FORM's voice."""
    _skip_if_headless()
    from wisprlite import readaloud, config

    cfg = config.Config()
    cfg.read_aloud_tts = "windows"
    cfg.read_aloud_voice = "SavedVoice"
    orig_load = config.Config.load
    config.Config.load = classmethod(lambda cls: cfg)

    seen = {}
    fake_speaker = types.SimpleNamespace(spoken=[])
    fake_speaker.speak = lambda text: fake_speaker.spoken.append(text)

    def fake_build_speaker(text, snap_cfg):
        seen["voice"] = snap_cfg.read_aloud_voice
        return fake_speaker, ""

    orig_build = readaloud.build_speaker
    readaloud.build_speaker = fake_build_speaker
    try:
        root = _build()
        try:
            from tkinter import ttk
            voice_label = _find_by_text(root, "Voice / model")
            voice_row = voice_label.master.master
            entry = _find(voice_row, lambda w: isinstance(w, ttk.Entry))
            entry.delete(0, "end")
            entry.insert(0, "FormVoice")

            btn, status = _preview_widgets(root)
            btn.invoke()
            _pump(root, until=lambda: "voice" in seen)
        finally:
            root.destroy()
    finally:
        readaloud.build_speaker = orig_build
        config.Config.load = orig_load

    assert seen.get("voice") == "FormVoice", seen


def test_preview_failure_shows_the_reason_and_does_not_fall_back_silently():
    """Gate 4: a degraded/failed cloud voice must show the reason in the card
    and must NOT speak through the Windows fallback the way a real read does."""
    _skip_if_headless()
    from wisprlite import readaloud

    fake_speaker = types.SimpleNamespace(spoken=[])
    fake_speaker.speak = lambda text: fake_speaker.spoken.append(text)

    def fake_build_speaker(text, snap_cfg):
        # Mirrors readaloud.build_speaker's real contract: on failure it still
        # returns a (fallback) Speaker plus a non-empty reason.
        return fake_speaker, "no ElevenLabs API key configured — using the Windows voice instead"

    orig_build = readaloud.build_speaker
    readaloud.build_speaker = fake_build_speaker
    status_text = ""
    try:
        root = _build()
        try:
            btn, status = _preview_widgets(root)
            btn.invoke()
            _pump(root, until=lambda: str(status.cget("text")) not in ("", "Speaking…"))
            status_text = str(status.cget("text"))
        finally:
            root.destroy()
    finally:
        readaloud.build_speaker = orig_build

    assert fake_speaker.spoken == [], "preview must not fall back to the Windows voice silently"
    assert "no ElevenLabs API key" in status_text


def test_preview_button_disabled_while_running_and_reenabled_after_failure():
    """Gate 5: the preview button is disabled while running and re-enabled
    after a failure."""
    _skip_if_headless()
    from wisprlite import readaloud

    release = threading.Event()

    def fake_build_speaker(text, snap_cfg):
        release.wait(timeout=5)
        raise RuntimeError("boom")

    orig_build = readaloud.build_speaker
    readaloud.build_speaker = fake_build_speaker
    try:
        root = _build()
        try:
            btn, status = _preview_widgets(root)
            btn.invoke()
            _pump(root, until=lambda: str(btn.cget("state")) == "disabled")
            assert str(btn.cget("state")) == "disabled"
            release.set()
            _pump(root, until=lambda: str(btn.cget("state")) == "normal")
            assert str(btn.cget("state")) == "normal"
        finally:
            root.destroy()
    finally:
        readaloud.build_speaker = orig_build
