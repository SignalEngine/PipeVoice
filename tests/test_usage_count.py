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
    config.Config.load()  # as at app startup: config.json exists and is migrated
    return seen


def _state():
    return json.loads(config.CONFIG_PATH.with_name("usage.json").read_text(encoding="utf-8"))


def test_off_switch_makes_no_call(calls):
    config.Config(usage_count=False, starter_vocab_seeded=True,
                  paste_default_migrated=True, meeting_hotkey_migrated=True).save()
    assert usage.maybe_send(config.Config.load()) is False
    assert calls == []
    assert not config.CONFIG_PATH.with_name("usage.json").exists()


def test_off_on_disk_beats_a_stale_cfg(calls):
    cfg = config.Config.load()
    off = config.Config.load()
    off.usage_count = False
    off.save()  # the Settings window, after the app loaded cfg
    assert cfg.usage_count is True
    assert usage.maybe_send(cfg) is False
    assert calls == []


@pytest.mark.parametrize("content", ['{"usage_count": false, BROKEN', "[]", None])
def test_damaged_or_missing_config_makes_no_call(calls, content):
    cfg = config.Config.load()
    if content is None:
        config.CONFIG_PATH.unlink()
    else:
        config.CONFIG_PATH.write_text(content, encoding="utf-8")
    assert usage.maybe_send(cfg) is False
    assert calls == []
    if content is not None:
        assert config.CONFIG_PATH.read_text(encoding="utf-8") == content


def test_missing_key_sends(calls):
    cfg = config.Config.load()
    config.CONFIG_PATH.write_text("{}", encoding="utf-8")
    assert usage.maybe_send(cfg) is True
    assert len(calls) == 1


def test_same_day_makes_no_second_call(calls):
    cfg = config.Config.load()
    assert usage.maybe_send(cfg) is True
    assert usage.maybe_send(cfg) is False
    assert len(calls) == 1


def test_first_call_sends_exactly_the_contract_and_persists(calls):
    assert usage.maybe_send(config.Config.load()) is True
    req, timeout = calls[0]
    assert req.full_url == "https://pipevoice.app/api/ping"
    assert req.get_method() == "POST"
    assert dict(req.header_items()) == {
        "Content-type": "application/json", "User-agent": "Pipevoice-usage"}
    assert timeout == 5
    state = _state()
    assert re.fullmatch(r"[0-9a-f]{32}", state["install_id"])
    assert state["last_count_day"] == usage.date.today().isoformat()
    assert json.loads(req.data) == {"id": state["install_id"], "v": __version__}
    assert not config.CONFIG_PATH.with_name("usage.json.tmp").exists()


def test_a_send_leaves_config_json_unchanged(calls):
    before = config.CONFIG_PATH.read_bytes()
    assert usage.maybe_send(config.Config.load()) is True
    assert config.CONFIG_PATH.read_bytes() == before


def test_unreadable_state_file_counts_as_empty(calls):
    config.CONFIG_PATH.with_name("usage.json").write_text("{broken", encoding="utf-8")
    assert usage.maybe_send(config.Config.load()) is True
    assert re.fullmatch(r"[0-9a-f]{32}", _state()["install_id"])


def test_network_error_is_swallowed_and_day_unchanged(calls, monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(usage.urllib.request, "urlopen", boom)
    path = config.CONFIG_PATH.with_name("usage.json")
    path.write_text(json.dumps({"last_count_day": "2000-01-01"}), encoding="utf-8")
    assert usage.maybe_send(config.Config.load()) is False
    assert _state()["last_count_day"] == "2000-01-01"


def test_save_round_trips_and_leaves_no_tmp_file(calls):
    config.Config(hotkey="f9").save()
    assert config.Config.load().hotkey == "f9"
    assert not config.CONFIG_PATH.with_suffix(".tmp").exists()
