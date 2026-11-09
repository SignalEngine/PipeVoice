import re
import sys
import pathlib
import types

import pytest

from wisprlite import theme, winui

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONVERTED = ["settings", "meetings_tab", "screenrec_tab", "voices_editor", "profiles",
             "history", "about", "transcribe_window", "prep_window", "feedback", "winui"]
OLD_TUPLE = re.compile(r'\("Segoe UI", *\d')


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(theme, "SCALE", 1.0)
    monkeypatch.setattr(theme, "_inited", False)
    monkeypatch.setattr(theme, "_families", None)
    monkeypatch.delenv("PV_UI_SCALE", raising=False)


def _families(monkeypatch, names):
    monkeypatch.setattr(theme, "_families", set(names))


class FakeRoot:
    def __init__(self, dpi=96.0):
        self.dpi, self.calls = dpi, []
        self.tk = types.SimpleNamespace(call=lambda *a: self.calls.append(a))

    def winfo_fpixels(self, _):
        return self.dpi


def test_px_scales_ints_and_tuples(monkeypatch):
    monkeypatch.setenv("PV_UI_SCALE", "1.5")
    theme.init(FakeRoot())
    assert theme.px(12) == 18
    assert theme.px((12, 0)) == (18, 0)
    assert theme.px(1) == 2


def test_px_is_identity_before_init():
    assert theme.px(13) == 13 and theme.px((3, 4)) == (3, 4)


def test_ramp_sizes_at_scale_one(monkeypatch):
    _families(monkeypatch, ["Segoe UI Variable Text", "Segoe UI Variable Text Semibold"])
    sizes = {r: theme.font(r)[1] for r in theme.RAMP}
    assert sizes == {"caption": -12, "body": -14, "body_strong": -14,
                     "body_large": -18, "body_large_strong": -18,
                     "subtitle": -20, "title": -28}


def test_ramp_scales(monkeypatch):
    monkeypatch.setenv("PV_UI_SCALE", "1.5")
    theme.init(FakeRoot())
    _families(monkeypatch, [])
    assert theme.font("body")[1] == -21


def test_variable_family_preferred(monkeypatch):
    _families(monkeypatch, ["Segoe UI", "Segoe UI Variable Text", "Segoe UI Variable Display"])
    assert theme.font("body")[0] == "Segoe UI Variable Text"
    assert theme.font("title")[0] == "Segoe UI Variable Display"


def test_only_segoe_ui_uses_bold_style(monkeypatch):
    _families(monkeypatch, ["Segoe UI"])
    assert theme.font("body") == ("Segoe UI", -14)
    assert theme.font("body_strong") == ("Segoe UI", -14, "bold")


def test_semibold_family_needs_no_bold_style(monkeypatch):
    _families(monkeypatch, ["Segoe UI", "Segoe UI Semibold"])
    assert theme.font("body_strong") == ("Segoe UI Semibold", -14)
    _families(monkeypatch, ["Segoe UI Variable Text", "Segoe UI Variable Display Semibold",
                            "Segoe UI Variable Text Semibold"])
    assert theme.font("title")[0] == "Segoe UI Variable Display Semibold"
    assert theme.font("body_strong")[0] == "Segoe UI Variable Text Semibold"


def test_no_known_family_falls_back_to_segoe_ui(monkeypatch):
    _families(monkeypatch, ["DejaVu Sans"])
    assert theme.font("body")[0] == "Segoe UI"


def test_underline_and_italic(monkeypatch):
    _families(monkeypatch, ["Segoe UI"])
    assert theme.font("body", underline=True, italic=True) == ("Segoe UI", -14, "italic", "underline")


def test_dpi_awareness_noop_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    theme.enable_dpi_awareness()


def test_dpi_awareness_swallows_a_raising_windll(monkeypatch):
    class Boom:
        def __getattr__(self, name):
            raise OSError("no")

    ct = types.SimpleNamespace(windll=Boom())
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", ct)
    theme.enable_dpi_awareness()


def test_dpi_awareness_falls_back_to_user32(monkeypatch):
    seen = []

    class Shcore:
        def SetProcessDpiAwareness(self, v):
            raise OSError("old windows")

    ct = types.SimpleNamespace(windll=types.SimpleNamespace(
        shcore=Shcore(), user32=types.SimpleNamespace(SetProcessDPIAware=lambda: seen.append(1))))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", ct)
    theme.enable_dpi_awareness()
    assert seen == [1]


def test_init_clamps_real_dpi_but_not_the_env_override(monkeypatch):
    theme.init(FakeRoot(dpi=48.0))
    assert theme.SCALE == 1.0
    monkeypatch.setattr(theme, "_inited", False)
    theme.init(FakeRoot(dpi=1000.0))
    assert theme.SCALE == 4.0
    monkeypatch.setattr(theme, "_inited", False)
    monkeypatch.setenv("PV_UI_SCALE", "0.75")
    r = FakeRoot()
    theme.init(r)
    assert theme.SCALE == 0.75
    assert r.calls == [("tk", "scaling", 0.75 * 96 / 72)]


def test_init_ignores_bad_env_and_first_call_wins(monkeypatch):
    monkeypatch.setenv("PV_UI_SCALE", "9")
    theme.init(FakeRoot(dpi=144.0))
    assert theme.SCALE == 1.5
    r2 = FakeRoot(dpi=192.0)
    theme.init(r2)
    assert theme.SCALE == 1.5 and r2.calls == []


@pytest.mark.parametrize("mod", CONVERTED)
def test_no_hardcoded_segoe_tuple_left(mod):
    text = (ROOT / "wisprlite" / f"{mod}.py").read_text(encoding="utf-8")
    assert not OLD_TUPLE.search(text)


def test_guard_regex_catches_a_fixture():
    assert OLD_TUPLE.search('font=("Segoe UI", 9, "bold")')


def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


P = winui.PALETTE
TEXT_KEYS = ["fg", "muted", "accent", "good", "amber", "error", "done", "picker"] + \
    [k for k in P if k.startswith("feat_")]


@pytest.mark.parametrize("key", TEXT_KEYS)
@pytest.mark.parametrize("bg", ["bg", "card"])
def test_palette_text_contrast(key, bg):
    assert _ratio(P[key], P[bg]) >= 4.5, (key, bg, _ratio(P[key], P[bg]))


def test_accent_button_text_contrast():
    assert _ratio("#1a0c0d", P["accent"]) >= 4.5


def test_contrast_checker_has_a_positive_control():
    assert _ratio("#3a3a3a", P["bg"]) < 4.5


@pytest.mark.parametrize("argv,expected", [
    (["--settings"], True), (["--about"], True), (["--feedback"], True),
    ([], False), (["--mcp"], False), (["--winrt-selftest"], False)])
def test_entry_enables_dpi_awareness_only_off_the_app_branch(monkeypatch, argv, expected):
    import importlib
    calls = []
    monkeypatch.setattr(theme, "enable_dpi_awareness", lambda: calls.append(1))
    for name in ("settings", "about", "feedback", "mcp_shim", "readaloud", "app"):
        monkeypatch.setitem(sys.modules, f"wisprlite.{name}",
                            types.SimpleNamespace(main=lambda: None))
    monkeypatch.setattr(sys, "argv", ["wisprlite"] + argv)
    monkeypatch.delitem(sys.modules, "wisprlite.__main__", raising=False)
    importlib.import_module("wisprlite.__main__")
    assert bool(calls) is expected
