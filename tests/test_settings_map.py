"""Tab bar, the one-home-per-setting map, and the settings openers (xvfb).

Run headless with:  xvfb-run -a python -m pytest tests/test_settings_map.py

The settings map is the contract from docs/plans/2026-11-09-ui-tabs-settings.md
section 3: every setting has ONE home, named after the feature it changes.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from uistub import have_display, install_platform_stubs

install_platform_stubs()

pytestmark = pytest.mark.skipif(not have_display(), reason="no X display; run under xvfb-run")

FG = "#e5e7eb"          # settings.FG: the colour of every row / check / card label

# The settings map. `labels` = row, check and card labels (tk.Label in FG colour,
# or a checkbutton); `buttons` = named buttons the plan lists as rows.
MAP = {
    "Settings": {
        "labels": [
            # Pointer card (not settings): sends upgraders to each feature's panel.
            "Looking for something?", "Hotkeys, polish and vocabulary", "Meeting recording and docs",
            "Screen recording and sending", "Voice keys",
            "General", "Engine", "Accent / language",
            "Microphone", "Show all endpoints",
            "API keys", "Gemini key", "Groq key", "Deepgram key", "OpenAI key", "OpenRouter key",
            "Models", "Gemini model", "Groq model", "Deepgram model", "Local model size",
            "Local: device", "Local: compute type",
            "App", "Show live overlay", "Play start/stop sounds", "Start on Windows login",
            "Automatic updates", "Get updates early (beta)",
            "Read Aloud", "Read Aloud hotkey", "Voice engine", "Preview voice",
            "Get better Windows voices", "Voice / model", "Speed", "OCR language",
            "Also copy the recognized text to the clipboard",
            "Stay quiet while a screen reader is running",
            "Advanced", "Deepgram wait", "Local model cache",
            "About PipeVoice", "Version {version}",
        ],
        "buttons": ["Test my mic", "About, licence and updates →"],
    },
    "History": {
        "labels": [
            "Dictation settings",
            "Dictation", "Mode", "Push-to-talk key", "Clipboard hotkey", "Paste speed",
            "Min seconds", "Keep a local dictation history",
            "Polish & text", "Polish with AI (Flow mode)", "Cleanup with", "Cleanup model",
            "Polish style", "Custom instruction / sign-off name", "Spoken commands",
            "Press Enter after typing (auto-send)",
            "Words", "Vocabulary", "Word fixes", "Speech notes",
        ],
        "buttons": ["Import CSV", "Export CSV"],
    },
    "Meetings": {
        "labels": [
            "Meeting settings",
            "Your docs", "Docs folder", "Docs web page", "Show answers during the call (Deepgram only)",
            "Meetings", "Meeting hotkey", "Also name meetings transcribed on this computer",
            "Transcribe automatically when the meeting ends", "Record the screen too (video)",
            "Video smoothness",
            "Send the video when the meeting ends, plus the transcript if it was transcribed "
            "automatically (uses the Screen recording destination)",
            "Bookmark hotkey", "Say this to bookmark", "Also bookmark on a double clap or snap",
            "PipeFocus — quiet nudges during a meeting", "Clap sensitivity",
            "Where meetings are kept", "Meeting max minutes", "Meetings to keep", "Save meetings to",
        ],
        "buttons": [],
    },
    "Screen recordings": {
        "labels": [
            "Recording settings",
            "Screen recordings", "Screen recording hotkey", "Send to", "Save to",
            "Keep a local copy after sending", "Frames per second",
        ],
        "buttons": [],
    },
    "Voices": {
        "labels": ["Voice settings", "Voice hotkeys", "Voice key 1", "Voice key 2",
                   "Voice key 3", "Picker key"],
        "buttons": [],
    },
}


def check_map(found, expected):
    """Problems with a found-vs-expected settings map ([] = one home each, no orphans).

    `found` and `expected` map home -> set of label texts. A label with two homes,
    a label missing from its home, and a label in no expected home are each reported.
    """
    problems = []
    homes_of = {}
    for home, texts in found.items():
        for text in texts:
            homes_of.setdefault(text, []).append(home)
    for text, homes in sorted(homes_of.items()):
        if len(homes) > 1:
            problems.append(f"{text!r} has two homes: {sorted(homes)}")
    for home, texts in expected.items():
        for text in sorted(set(texts) - set(found.get(home, ()))):
            problems.append(f"{text!r} is missing from {home}")
        for text in sorted(set(found.get(home, ())) - set(texts)):
            problems.append(f"{text!r} is an orphan in {home} (not in the map)")
    return problems


@pytest.mark.parametrize("found, fragment", [
    # The positive control: a label added to a second home must be caught.
    ({"Settings": {"Engine", "Mode"}, "History": {"Mode"}},
     "'Mode' has two homes"),
    # ...a label that vanished from its home...
    ({"Settings": set(), "History": {"Mode"}}, "'Engine' is missing from Settings"),
    # ...and a label nobody mapped.
    ({"Settings": {"Engine", "Mode", "Surprise"}}, "'Surprise' is an orphan in Settings"),
])
def test_the_map_check_catches_a_duplicate_a_missing_and_an_orphan_label(found, fragment):
    expected = {"Settings": {"Engine"}, "History": {"Mode"}}
    full = {"Settings": found.get("Settings", set()), "History": found.get("History", set())}
    assert any(fragment in p for p in check_map(full, expected)), check_map(full, expected)


def _walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)


def _shown(widget, home):
    """False when the widget, or an ancestor below `home`, is pack_forgotten."""
    node = widget
    while node is not None and node is not home:
        if not node.winfo_manager():
            return False
        node = node.master
    return True


def _launch(monkeypatch, tab, driver):
    """Build the settings window on `tab`, run driver(root) once, tear it down."""
    import tkinter as tk
    from wisprlite import settings

    if tab is None:
        monkeypatch.delenv("PV_TAB", raising=False)
    else:
        monkeypatch.setenv("PV_TAB", tab)
    out = {}
    real = tk.Misc.mainloop

    def stub(self, _n=0):
        try:
            self.update_idletasks()
            self.update()
            driver(self, out)
        finally:
            try:
                self.destroy()
            except Exception:
                pass

    monkeypatch.setattr(tk.Misc, "mainloop", stub)
    try:
        settings.main()
    finally:
        monkeypatch.setattr(tk.Misc, "mainloop", real)
    return out


def _by_text(root, text, cls=None):
    for w in _walk(root):
        try:
            if str(w.cget("text")) == text and (cls is None or w.winfo_class() == cls):
                return w
        except Exception:
            pass
    return None


def test_every_setting_has_exactly_one_home_and_nothing_is_orphaned(monkeypatch):
    from wisprlite import __version__

    def driver(root, out):
        general = _by_text(root, "General", "Label")
        homes = {"Settings": general.master.master}
        for home, heading in (("History", "Dictation settings"), ("Meetings", "Meeting settings"),
                              ("Screen recordings", "Recording settings"),
                              ("Voices", "Voice settings")):
            homes[home] = _by_text(root, heading, "Label").master
        labels, buttons = {}, {}
        for home, container in homes.items():
            labels[home], buttons[home] = set(), set()
            for w in _walk(container):
                if not _shown(w, container):
                    continue
                cls = w.winfo_class()
                text = str(w.cget("text")) if "text" in w.keys() else ""
                if not text:
                    continue
                if cls == "Label" and str(w.cget("fg")) == FG:
                    labels[home].add(text)
                elif cls == "TCheckbutton":
                    labels[home].add(text)
                elif cls == "TButton":
                    buttons[home].add(text)
        out["labels"], out["buttons"] = labels, buttons

    out = _launch(monkeypatch, "Settings", driver)
    expected = {h: {t.format(version=__version__) for t in m["labels"]} for h, m in MAP.items()}
    assert check_map(out["labels"], expected) == []
    for home, m in MAP.items():
        missing = set(m["buttons"]) - out["buttons"][home]
        assert not missing, f"{home} lacks buttons {missing}"
        for other, found in out["buttons"].items():
            if other != home:
                assert not (set(m["buttons"]) & found), f"{m['buttons']} also in {other}"


# --- tab bar -------------------------------------------------------------------------------

TAB_SIGNATURE = {
    "History": "Dictation history",
    "Meetings": "Before your next call",
    "Screen recordings": "Press your screen recording hotkey",
    "Voices": "VOICES & APP PROFILES",
    "Guide": "ON THIS PAGE",
    "Settings": "General",
    "About": "All releases ↗",
}


def _visible_tab(root):
    """The one tab whose signature text is on screen (viewable), or None."""
    live = []
    for tab, text in TAB_SIGNATURE.items():
        for w in _walk(root):
            try:
                if str(w.cget("text")).startswith(text) and w.winfo_viewable():
                    live.append(tab)
                    break
            except Exception:
                pass
    return live


def _bar_labels(root):
    """(text, label widget) of every tab-bar label, left to right.

    The bar is found through the gear label, so a tab's own header labels (the
    Meetings tab has a "Meetings" title too) are never mistaken for it.
    """
    gear = _by_text(root, "\u2699  Settings", "Label")
    tabbar = gear.master.master.master
    found = []
    for item in tabbar.winfo_children():
        for label in item.winfo_children()[0].winfo_children():
            if label.winfo_class() == "Label":
                found.append((str(label.cget("text")), label))
    return sorted(found, key=lambda tw: tw[1].winfo_rootx())


def test_the_bar_shows_the_five_features_then_the_gear_at_the_far_right(monkeypatch):
    from wisprlite import winui

    def driver(root, out):
        out["bar"] = [(t, str(w.cget("fg")), w.winfo_rootx()) for t, w in _bar_labels(root)]
        tiles = {}
        for t, w in _bar_labels(root):
            sibs = [s for s in w.master.winfo_children() if s is not w]
            tiles[t] = (str(sibs[0].cget("bg")), sibs[0].winfo_reqwidth()) if sibs else None
        out["tiles"] = tiles
        out["right"] = root.winfo_width()

    out = _launch(monkeypatch, "History", driver)
    assert [t for t, _fg, _x in out["bar"]] == [
        "History", "Meetings", "Screen recordings", "Voices", "Guide", "⚙  Settings"]
    assert "About" not in [t for t, _fg, _x in out["bar"]]
    gear_x = out["bar"][-1][2]
    assert gear_x > out["right"] // 2, "the gear belongs at the right end of the bar"
    for tab, key in (("History", "feat_history"), ("Meetings", "feat_meetings"),
                     ("Screen recordings", "feat_screenrec"), ("Voices", "feat_voices"),
                     ("Guide", "feat_guide")):
        assert out["tiles"][tab][0] == winui.PALETTE[key], tab
    assert out["tiles"]["⚙  Settings"] is None, "the gear has no colour tile"
    selected = [t for t, fg, _x in out["bar"] if fg == FG]
    assert selected == ["History"]


@pytest.mark.parametrize("pv_tab, shows, selected_label", [
    ("History", "History", "History"),
    ("Meetings", "Meetings", "Meetings"),
    ("Screen recordings", "Screen recordings", "Screen recordings"),
    ("Recordings", "Screen recordings", "Screen recordings"),       # the old name still works
    ("Voices", "Voices", "Voices"),
    ("Guide", "Guide", "Guide"),
    ("Settings", "Settings", "⚙  Settings"),
    ("About", "About", "⚙  Settings"),                          # About sits under the gear
    ("NoSuchTab", "History", "History"),                             # unknown names fall back to History
    (None, "History", "History"),                                    # no PV_TAB: History
])
def test_every_tab_name_opens_the_right_frame(monkeypatch, pv_tab, shows, selected_label):
    def driver(root, out):
        out["live"] = _visible_tab(root)
        out["selected"] = [t for t, w in _bar_labels(root) if str(w.cget("fg")) == FG]

    out = _launch(monkeypatch, pv_tab, driver)
    assert out["live"] == [shows], out["live"]
    assert out["selected"] == [selected_label], out["selected"]


# --- openers -------------------------------------------------------------------------------

@pytest.mark.parametrize("tab, button, heading, expected_label", [
    ("History", "⚙  Dictation settings", "Dictation settings", "Push-to-talk key"),
    ("Meetings", "⚙  Meeting settings", "Meeting settings", "Meeting hotkey"),
    ("Screen recordings", "⚙  Recording settings", "Recording settings", "Send to"),
    ("Voices", "⚙  Voice settings", "Voice settings", "Picker key"),
])
def test_each_feature_tab_opens_its_own_settings_and_the_tab_header_closes_them(
        monkeypatch, tab, button, heading, expected_label):
    def driver(root, out):
        opener = _by_text(root, button, "TButton")
        out["before"] = opener.winfo_viewable()
        out["heading_before"] = _by_text(root, heading, "Label").winfo_viewable()
        opener.invoke()
        root.update_idletasks()
        root.update()
        out["heading_open"] = _by_text(root, heading, "Label").winfo_viewable()
        out["row_open"] = _by_text(root, expected_label, "Label").winfo_viewable()
        out["opener_text"] = str(opener.cget("text"))
        _tab_label = [w for t, w in _bar_labels(root) if t == tab][0]
        _tab_label.event_generate("<Button-1>")
        root.update_idletasks()
        root.update()
        out["heading_closed"] = _by_text(root, heading, "Label").winfo_viewable()
        out["opener_after"] = str(opener.cget("text"))

    out = _launch(monkeypatch, tab, driver)
    assert out["before"], f"the {button!r} button is not on the {tab} tab"
    assert not out["heading_before"]
    assert out["heading_open"] and out["row_open"]
    assert "Close" in out["opener_text"]
    assert not out["heading_closed"], "pressing the tab header must close the settings"
    assert out["opener_after"] == button


def test_the_standalone_history_window_has_no_settings_opener(monkeypatch):
    import tkinter as tk
    from wisprlite import history

    root = tk.Tk()
    try:
        assert history.build(root, root) is None
        root.update()
        assert _by_text(root, "⚙  Dictation settings") is None
    finally:
        root.destroy()


def test_fix_this_on_a_history_row_opens_dictation_settings_with_the_fix_entry_focused(monkeypatch):
    import time
    from wisprlite import history

    monkeypatch.setattr(history, "load",
                        lambda limit=50: [{"ts": 0, "text": "I saw Dave today", "kind": "typed"}])

    def driver(root, out):
        _by_text(root, "Fix this", "TButton").invoke()
        time.sleep(0.2)
        root.update_idletasks()
        root.update()
        out["heading"] = _by_text(root, "Dictation settings", "Label").winfo_viewable()
        arrow = _by_text(root, "→", "Label")
        entries = [w for w in arrow.master.winfo_children() if w.winfo_class() == "TEntry"]
        out["wrong"], out["right"] = entries[0].get(), entries[1].get()
        out["focus"] = root.focus_get() is entries[1]

    out = _launch(monkeypatch, "History", driver)
    assert out["heading"], "Fix this must open History's Dictation settings"
    assert out["wrong"] == "I saw Dave today"
    assert out["right"] == ""
    assert out["focus"], "the 'right' side of the fix must have the keyboard focus"


def test_the_gear_form_ends_with_an_about_card_that_opens_about(monkeypatch):
    from wisprlite import __version__

    def driver(root, out):
        _by_text(root, "About, licence and updates →", "TButton").invoke()
        root.update_idletasks()
        root.update()
        out["live"] = _visible_tab(root)
        out["selected"] = [t for t, w in _bar_labels(root) if str(w.cget("fg")) == FG]
        out["version"] = _by_text(root, f"Version {__version__}", "Label") is not None

    out = _launch(monkeypatch, "Settings", driver)
    assert out["live"] == ["About"]
    assert out["selected"] == ["⚙  Settings"]
    assert out["version"]


def test_the_pointer_card_opens_each_feature_panel(monkeypatch):
    # Upgraders look in ⚙ for push-to-talk; the pointer card must land them in the panel.
    cases = {"Hotkeys, polish and vocabulary": "Dictation settings",
             "Meeting recording and docs": "Meeting settings",
             "Screen recording and sending": "Recording settings",
             "Voice keys": "Voice settings"}

    def driver(root, out):
        for pointer, heading in cases.items():
            lbl = _by_text(root, pointer, "Label")
            row = lbl
            while not any(c.winfo_class() == "TButton" for c in _walk(row)):
                row = row.master
            btn = next(c for c in _walk(row) if c.winfo_class() == "TButton")
            btn.invoke()
            for _ in range(3):
                root.update_idletasks()
                root.update()
            out[pointer] = _by_text(root, heading, "Label").winfo_viewable()
            _by_text(root, "⚙  Settings", "Label").event_generate("<Button-1>")
            root.update()

    out = _launch(monkeypatch, "Settings", driver)
    assert out == {p: 1 for p in cases}, out
