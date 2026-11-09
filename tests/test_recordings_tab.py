"""Screen recordings tab = Meetings layout, and the three empty states."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pytest

from uistub import have_display, install_platform_stubs

install_platform_stubs()

pytestmark = pytest.mark.skipif(not have_display(), reason="no X display; run under xvfb-run")

from wisprlite import config, history, meetings_tab, screenrec_tab  # noqa: E402


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


def _texts(w):
    return [str(x.cget("text")) for x in _walk(w) if x.winfo_class() in ("Label", "TLabel")]


def _button(w, text):
    return next((b for b in _walk(w) if b.winfo_class() == "TButton"
                 and str(b.cget("text")) == text), None)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("PV_SELECT", raising=False)
    c = config.Config.load()
    c.hotkey, c.meeting_hotkey, c.screenrec_hotkey = "ctrl+\\", "ctrl+alt+m", "ctrl+alt+r"
    c.screenrec_destination = "root@host:/inbox/"
    c.save()
    return c


@pytest.fixture
def tk_root():
    import tkinter as tk
    root = tk.Tk()
    yield root
    root.destroy()


def _recs(monkeypatch, base, names=(), words=None):
    monkeypatch.setattr(screenrec_tab, "recordings_dir", lambda cfg=None: base)
    for n in names:
        (base / f"{n}.mp4").write_bytes(b"video")
        if words is not None:
            (base / f"{n}.txt").write_text(words, encoding="utf-8")


def _build_recs(root, with_settings=False):
    from tkinter import ttk
    from wisprlite import winui
    winui.apply_theme(root)
    frame = ttk.Frame(root)
    frame.pack(fill="both", expand=True)
    screenrec_tab.build(frame, root, with_settings=with_settings)
    root.update()
    return frame


def _menu_labels(menu):
    return [menu.entrycget(i, "label") if menu.type(i) == "command" else "-"
            for i in range(menu.index("end") + 1)]


def test_header_has_send_accent_play_and_more(tmp_path, monkeypatch, cfg, tk_root):
    _recs(monkeypatch, tmp_path, ["a clip", "b clip"], words="hello")
    frame = _build_recs(tk_root)
    send, play, more = (_button(frame, t) for t in ("Send", "Play", "⋯"))
    assert send is not None and play is not None and more is not None
    assert str(send.cget("style")) == "Accent.TButton"
    assert str(play.cget("style")) != "Accent.TButton"
    assert str(send.cget("state")) == "normal"
    assert _button(frame, "Folder") is None and _button(frame, "Delete") is None


def test_more_menu_and_right_click_menu_have_the_same_items(tmp_path, monkeypatch, cfg, tk_root):
    import tkinter as tk
    _recs(monkeypatch, tmp_path, ["a clip"], words="hello")
    frame = _build_recs(tk_root)
    seam = frame.pv_recordings
    other = tk.Menu(tk_root, tearoff=False)
    seam["fill_menu"](other)
    seam["fill_menu"](seam["menu"])
    expect = ["Play", "Send", "-", "Copy text", "Copy path", "Open folder", "-", "Delete…"]
    assert _menu_labels(seam["menu"]) == _menu_labels(other) == expect


def test_each_menu_item_calls_its_function(tmp_path, monkeypatch, cfg, tk_root):
    import tkinter as tk
    _recs(monkeypatch, tmp_path, ["a clip"], words="hello")
    frame = _build_recs(tk_root)
    seam = frame.pv_recordings
    called = []
    seam["cmds"].update({k: (lambda k=k: called.append(k)) for k in seam["cmds"]})
    menu = tk.Menu(tk_root, tearoff=False)
    seam["fill_menu"](menu)
    for i in range(menu.index("end") + 1):
        if menu.type(i) == "command":
            menu.invoke(i)
    assert called == ["play", "send", "copy_text", "copy_path", "folder", "delete"]


def test_real_functions_are_wired_to_the_menu(tmp_path, monkeypatch, cfg, tk_root):
    """Not just labels: Copy text puts the transcript on the clipboard, Copy path the path."""
    seen = []
    monkeypatch.setattr(screenrec_tab, "_copy_to_clipboard", lambda r, t: seen.append(t))
    _recs(monkeypatch, tmp_path, ["a clip"], words="hello there")
    frame = _build_recs(tk_root)
    cmds = frame.pv_recordings["cmds"]
    cmds["copy_text"]()
    cmds["copy_path"]()
    assert seen == ["hello there", str(tmp_path / "a clip.mp4")]


def test_right_click_selects_the_row_first(tmp_path, monkeypatch, cfg, tk_root):
    import os, time
    _recs(monkeypatch, tmp_path, ["old clip", "new clip"], words="x")
    os.utime(tmp_path / "old clip.mp4", (time.time() - 100, time.time() - 100))
    frame = _build_recs(tk_root)
    seam = frame.pv_recordings
    popped = []
    seam["menu"].tk_popup = lambda x, y: popped.append(seam["state"]["selected"]["stem"])
    assert seam["state"]["selected"]["stem"] == "new clip"
    row = next(w for w in _walk(frame) if w.winfo_class() == "Label"
               and str(w.cget("text")) == "old clip")
    row.event_generate("<Button-3>", x=1, y=1)
    tk_root.update()
    assert popped == ["old clip"]


def test_rows_are_two_lines_and_meta_line_is_date_duration_size(tmp_path, monkeypatch, cfg, tk_root):
    monkeypatch.setattr(screenrec_tab, "_duration_seconds", lambda p: 65)
    _recs(monkeypatch, tmp_path, ["a clip"], words="x")
    frame = _build_recs(tk_root)
    texts = _texts(frame)
    assert any(t.endswith("·  1:05") for t in texts), texts      # row grey line
    assert any("1:05" in t and "KB" in t for t in texts), texts        # header meta line


def test_no_words_shows_the_grey_line(tmp_path, monkeypatch, cfg, tk_root):
    _recs(monkeypatch, tmp_path, ["a clip"], words=None)
    frame = _build_recs(tk_root)
    box = next(w for w in _walk(frame) if w.winfo_class() == "Text")
    assert box.get("1.0", "end").strip() == "No narration in this recording."
    assert str(box.tag_cget("none", "foreground")) == screenrec_tab.MUTED


# ---------- empty states ----------

def test_screenrec_empty_state(tmp_path, monkeypatch, cfg, tk_root):
    _recs(monkeypatch, tmp_path)
    frame = _build_recs(tk_root, with_settings=True)
    texts = _texts(frame)
    assert "No screen recordings yet" in texts
    assert ("Press ctrl+alt+r, drag a box over what you want to show, talk, and press it "
            "again to stop.") in texts
    assert _button(frame, "⚙  Recording settings") is not None   # the head link only
    assert "Nothing recorded yet." not in texts


def test_screenrec_empty_destination_variant_and_button_opens_panel(tmp_path, monkeypatch, cfg, tk_root):
    cfg.screenrec_destination = ""
    cfg.save()
    _recs(monkeypatch, tmp_path)
    frame = _build_recs(tk_root, with_settings=True)
    assert any(t.endswith("Set where recordings are sent first.") for t in _texts(frame))
    buttons = [b for b in _walk(frame) if b.winfo_class() == "TButton"
               and str(b.cget("text")) == "⚙  Recording settings"]
    assert len(buttons) == 2            # the head link and the empty-state button
    block_btn = next(b for b in buttons if "Frame" in b.master.winfo_class())
    block_btn.invoke()
    tk_root.update()
    assert any(str(b.cget("text")) == "✕  Close settings" for b in _walk(frame)
               if b.winfo_class() == "TButton"), "the panel did not open"


def test_history_empty_state(monkeypatch, cfg, tk_root):
    from tkinter import ttk
    monkeypatch.setattr(history, "load", lambda n=50: [])
    frame = ttk.Frame(tk_root)
    frame.pack(fill="both", expand=True)
    history.build(frame, tk_root, with_settings=True)
    tk_root.update()
    texts = _texts(frame)
    assert "Your dictations will show up here" in texts
    assert ("Hold ctrl+\\ and talk. Let go and your words are typed where your cursor is.") in texts
    assert "Nothing dictated yet." not in texts


def _build_meetings(tmp_path, monkeypatch, root):
    from tkinter import ttk
    from wisprlite import meeting
    base = tmp_path / "meetings"
    base.mkdir(exist_ok=True)
    monkeypatch.setattr(meeting, "meetings_dir", lambda: base)
    monkeypatch.setattr(meetings_tab, "meetings_dir", lambda: base)
    monkeypatch.setattr(meetings_tab, "default_meetings_dir", lambda: base)
    frame = ttk.Frame(root)
    frame.pack(fill="both", expand=True)
    meetings_tab.build(frame, root, None, with_settings=True)
    root.update()
    return frame


def test_meetings_empty_state_with_hotkey(tmp_path, monkeypatch, cfg, tk_root):
    texts = _texts(_build_meetings(tmp_path, monkeypatch, tk_root))
    assert "No meetings yet" in texts
    assert "Press ctrl+alt+m to start recording a call, and again to stop." in texts
    assert "No meetings recorded yet." not in texts


def test_meetings_empty_state_without_hotkey_opens_settings(tmp_path, monkeypatch, cfg, tk_root):
    cfg.meeting_hotkey = ""
    cfg.meeting_hotkey_migrated = True
    cfg.save()
    frame = _build_meetings(tmp_path, monkeypatch, tk_root)
    assert "Set a meeting hotkey in ⚙ Meeting settings to start." in _texts(frame)
    buttons = [b for b in _walk(frame) if b.winfo_class() == "TButton"
               and str(b.cget("text")) == "⚙  Meeting settings"]
    assert len(buttons) == 2
    next(b for b in buttons if "Frame" in b.master.winfo_class()
         and b.master.master.winfo_class() == "Frame" and b.master is not buttons[0].master).invoke()
    tk_root.update()
    assert any(str(b.cget("text")) == "✕  Close settings" for b in _walk(frame)
               if b.winfo_class() == "TButton")


# ---------- failure states (brain fixes from the failure audit) ----------

def test_play_on_a_deleted_clip_says_so_instead_of_nothing(tmp_path, monkeypatch, cfg, tk_root):
    _recs(monkeypatch, tmp_path, ["a clip"])
    frame = _build_recs(tk_root)
    tk_root.update()
    for p in tmp_path.glob("a clip*"):
        p.unlink()
    shown, played = [], []
    monkeypatch.setattr(screenrec_tab, "_play", lambda p: played.append(p))
    import tkinter.messagebox as mb
    monkeypatch.setattr(mb, "showinfo", lambda title, msg, **k: shown.append(msg))
    _button(frame, "Play").invoke()
    assert played == []
    assert shown and "gone" in shown[0]


def test_failed_send_shows_the_cause_not_connection_closed(tmp_path, monkeypatch):
    import subprocess
    from wisprlite import screenrec
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(screenrec.shutil, "which", lambda _n: "/usr/bin/scp")
    err = ("ssh: Could not resolve hostname nohost: Name or service not known\n"
           "/usr/bin/scp: Connection closed\n")
    monkeypatch.setattr(screenrec.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 255, stdout="", stderr=err))
    ok, msg = screenrec.send([f], "root@nohost:/tmp/")
    assert not ok and "Could not resolve hostname" in msg
