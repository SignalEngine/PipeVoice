"""Ctrl+Shift+M is Teams' mute toggle: an old saved meeting hotkey moves off it once."""
import json

from wisprlite import config


def _load(tmp_path, monkeypatch, saved):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(saved), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    return config.Config.load(), path


def test_teams_mute_combo_moves_once_and_is_saved(tmp_path, monkeypatch):
    cfg, path = _load(tmp_path, monkeypatch, {"meeting_hotkey": "ctrl+shift+M"})
    assert cfg.meeting_hotkey == "ctrl+alt+m"
    assert json.loads(path.read_text())["meeting_hotkey"] == "ctrl+alt+m"
    # The user deliberately picks it again: it stays.
    cfg.meeting_hotkey = "ctrl+shift+m"
    cfg.save()
    assert config.Config.load().meeting_hotkey == "ctrl+shift+m"


def test_no_move_onto_a_combo_already_in_use(tmp_path, monkeypatch):
    cfg, _ = _load(tmp_path, monkeypatch,
                   {"meeting_hotkey": "ctrl+shift+m", "screenrec_hotkey": "ctrl+alt+m"})
    assert cfg.meeting_hotkey == "ctrl+shift+m"


def test_other_hotkeys_untouched(tmp_path, monkeypatch):
    cfg, _ = _load(tmp_path, monkeypatch, {"meeting_hotkey": "f9"})
    assert cfg.meeting_hotkey == "f9"


def test_no_move_when_keys_overlap_in_another_order_or_subset(tmp_path, monkeypatch):
    for other in ("alt+ctrl+m", "ctrl+alt"):
        cfg, _ = _load(tmp_path, monkeypatch,
                       {"meeting_hotkey": "ctrl+shift+m", "screenrec_hotkey": other})
        assert cfg.meeting_hotkey == "ctrl+shift+m", other
