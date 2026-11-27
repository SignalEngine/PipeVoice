"""The anonymous daily install count: off switch, once a day, never raises."""

import json
import pathlib
import re
import sys
import urllib.error

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import __version__, config, usage

# Taken at import: other test files replace Config.load and never put it back.
REAL_LOAD = config.Config.__dict__["load"]


@pytest.fixture
def calls(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config.Config, "load", REAL_LOAD)
    seen = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        seen.append((req, timeout))
        return _Resp()

    monkeypatch.setattr(usage.urllib.request, "urlopen", fake)
    return seen


def test_off_switch_makes_no_call(calls):
    cfg = config.Config(usage_count=False)
    assert usage.maybe_send(cfg) is False
    assert calls == []
    assert cfg.install_id == ""


def test_same_day_makes_no_second_call(calls):
    cfg = config.Config()
    assert usage.maybe_send(cfg) is True
    assert usage.maybe_send(cfg) is False
    assert len(calls) == 1


def test_first_call_sends_exactly_the_contract_and_persists(calls):
    cfg = config.Config()
    assert usage.maybe_send(cfg) is True
    req, timeout = calls[0]
    assert req.full_url == "https://pipevoice.app/api/ping"
    assert req.get_method() == "POST"
    assert dict(req.header_items()) == {
        "Content-type": "application/json", "User-agent": "Pipevoice-usage"}
    assert timeout == 5
    assert re.fullmatch(r"[0-9a-f]{32}", cfg.install_id)
    assert json.loads(req.data) == {"id": cfg.install_id, "v": __version__}
    saved = config.Config.load()
    assert saved.install_id == cfg.install_id
    assert saved.last_count_day == cfg.last_count_day != ""


def test_save_keeps_settings_written_after_startup(calls):
    cfg = config.Config()
    config.Config(hotkey="f9").save()  # the Settings window, a separate process
    assert usage.maybe_send(cfg) is True
    assert config.Config.load().hotkey == "f9"


def test_network_error_is_swallowed_and_day_unchanged(calls, monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(usage.urllib.request, "urlopen", boom)
    cfg = config.Config(last_count_day="2000-01-01")
    assert usage.maybe_send(cfg) is False
    assert cfg.last_count_day == "2000-01-01"
    assert config.Config.load().last_count_day != usage.date.today().isoformat()


def test_save_round_trips_and_leaves_no_tmp_file(calls):
    config.Config(hotkey="f9").save()
    assert config.Config.load().hotkey == "f9"
    assert not config.CONFIG_PATH.with_suffix(".tmp").exists()
