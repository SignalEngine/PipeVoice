import io
import json
import urllib.error
import urllib.request

import numpy as np
import pytest

from uistub import install_platform_stubs
from wisprlite import cloud, config
from wisprlite.engines import cloud_engine

install_platform_stubs()

KEY = "PV-TEST-KEY-1234"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    for name in ("WISPRLITE_CLOUD_URL", "WISPRLITE_POLAR_API"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def stub(monkeypatch, *replies):
    """Each reply is (code, body) or an exception. Returns the sent requests."""
    sent, queue = [], list(replies)

    def urlopen(req, timeout=None):
        sent.append(req)
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        code, body = reply
        raw = json.dumps(body).encode()
        if code != 200:
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(raw))
        return _Resp(raw)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return sent


def licence(**extra):
    cloud.save({"key": KEY, "activation_id": "a1", "label": "pc", **extra})


def transcribe(audio=None, **kw):
    audio = np.zeros(1600, dtype=np.float32) if audio is None else audio
    return cloud_engine.CloudEngine(**kw).start_session().finish(audio)


def test_200_returns_text_and_sends_the_contract_request(monkeypatch):
    licence()
    sent = stub(monkeypatch, (200, {"text": " hello ", "used_seconds": 1, "cap_seconds": 2}))
    assert transcribe(language="en", prompt="Convex, " + "é" * 900) == "hello"
    req = sent[0]
    assert req.full_url == "https://pipevoice.app/api/stt?lang=en"
    assert req.get_header("Authorization") == "Bearer " + KEY
    assert req.get_header("Content-type") == "audio/wav"
    assert req.get_header("User-agent")
    assert req.data[:4] == b"RIFF"
    from urllib.parse import unquote
    decoded = unquote(req.get_header("X-prompt"))
    assert len(decoded) == 800 and decoded.startswith("Convex, ")


@pytest.mark.parametrize("code,message", [
    (401, "Cloud licence not valid"), (402, "Cloud monthly limit reached"),
    (413, "Clip too long for Cloud"), (503, "Cloud unavailable"),
    (400, "Cloud unavailable")])
def test_http_errors_raise_plain_messages(monkeypatch, code, message):
    licence()
    stub(monkeypatch, (code, {"error": "x"}))
    with pytest.raises(RuntimeError, match=message):
        transcribe()


@pytest.mark.parametrize("failure", [urllib.error.URLError("offline"), TimeoutError()])
def test_network_error_raises(monkeypatch, failure):
    licence()
    stub(monkeypatch, failure)
    with pytest.raises(RuntimeError, match="Cloud unavailable"):
        transcribe()


def test_oversize_wav_raises_without_http(monkeypatch):
    licence()
    sent = stub(monkeypatch)
    with pytest.raises(RuntimeError, match="Clip too long for Cloud"):
        transcribe(np.zeros(2_200_000, dtype=np.float32))
    assert sent == []


@pytest.mark.parametrize("failure", [(402, {}), (413, {}), (401, {}), (503, {}),
                                     urllib.error.URLError("offline")])
def test_cloud_failure_falls_back_to_local_whisper(monkeypatch, failure):
    from wisprlite.app import App

    licence()
    stub(monkeypatch, failure)

    class Local:
        def start_session(self, on_partial=None):
            return type("S", (), {"finish": lambda s, audio: "local text"})()

    app = App.__new__(App)
    app.cfg, app._active = config.Config(engine="cloud"), {}
    app.overlay = type("O", (), {"set_state": lambda *a: None, "set_text": None})()
    app._get_engine = lambda name=None: Local()
    try:
        transcribe()
    except Exception as exc:
        assert app._fallback(np.zeros(10), exc) == "local text"
    else:
        pytest.fail("cloud did not raise")


def test_app_builds_cloud_only_with_a_licence():
    from wisprlite.app import App

    app = App.__new__(App)
    app.cfg = config.Config(engine="cloud")
    with pytest.raises(RuntimeError, match="No Cloud licence"):
        app._build_engine()
    licence()
    assert app._build_engine().name == "cloud"


def test_activate_saves_licence_outside_config_json(monkeypatch, isolated):
    config.Config().save()
    sent = stub(monkeypatch, (200, {"id": "act-9"}))
    ok, _ = cloud.activate("  " + KEY + " ")
    assert ok and cloud.has_licence()
    assert sent[0].full_url == "https://sandbox-api.polar.sh/v1/customer-portal/license-keys/activate"
    body = json.loads(sent[0].data)
    assert body["key"] == KEY and body["organization_id"] == cloud.ORG_ID and "label" in body
    assert sent[0].get_header("User-agent")
    assert json.loads((isolated / "cloud.json").read_text())["activation_id"] == "act-9"
    config.Config.load().save()
    assert KEY not in (isolated / "config.json").read_text()
    cloud.clear()
    assert not cloud.has_licence()


@pytest.mark.parametrize("reply,message", [
    ((403, {}), "This key is already on 3 PCs, or it was cancelled"),
    ((404, {}), "Key not found"),
    (urllib.error.URLError("offline"), "Could not reach")])
def test_activate_failures_save_nothing(monkeypatch, reply, message):
    stub(monkeypatch, reply)
    ok, msg = cloud.activate(KEY)
    assert not ok and message in msg and not cloud.has_licence()


def test_sync_applies_newer_remote_and_saves_config(monkeypatch):
    licence(synced_at=100)
    cfg = config.Config(vocabulary="old")
    sent = stub(monkeypatch, (200, {"data": {"vocabulary": "new", "replacements": {"a": "b"}},
                                    "updated_at": 200}))
    cloud.sync_now(cfg)
    assert [r.get_method() for r in sent] == ["GET"]
    assert (cfg.vocabulary, cfg.replacements) == ("new", {"a": "b"})
    assert config.Config.load().vocabulary == "new"
    assert cloud.load()["synced_at"] == 200


def test_sync_pushes_local_when_remote_is_not_newer(monkeypatch):
    licence(synced_at=200)
    cfg = config.Config(vocabulary="mine", replacements={"x": "y"})
    sent = stub(monkeypatch, (200, {"data": {"vocabulary": "theirs"}, "updated_at": 200}),
                (200, {"ok": True}))
    cloud.sync_now(cfg)
    put = sent[1]
    assert put.get_method() == "PUT" and put.full_url == "https://pipevoice.app/api/sync"
    body = json.loads(put.data)
    assert body["data"] == {"vocabulary": "mine", "replacements": {"x": "y"}}
    assert cfg.vocabulary == "mine"
    assert cloud.load()["synced_at"] == body["updated_at"] > 200


def test_sync_409_applies_the_returned_data(monkeypatch):
    licence(synced_at=200)
    cfg = config.Config(vocabulary="mine")
    stub(monkeypatch, (200, {"data": None, "updated_at": 0}),
         (409, {"error": "stale", "data": {"vocabulary": "won", "replacements": {}},
                "updated_at": 999}))
    cloud.sync_now(cfg)
    assert cfg.vocabulary == "won" and cloud.load()["synced_at"] == 999


def test_sync_apply_keeps_settings_saved_while_the_request_was_in_flight(monkeypatch):
    licence()
    stale = config.Config(hotkey="f8")
    newer = config.Config(hotkey="f9")
    newer.save()
    stub(monkeypatch, (200, {"data": {"vocabulary": "v", "replacements": {}}, "updated_at": 5}))
    cloud.sync_now(stale)
    assert config.Config.load().hotkey == "f9"


@pytest.mark.parametrize("reply", [(401, {}), (503, {}), urllib.error.URLError("offline"),
                                   (200, ["junk"])])
def test_sync_failures_are_swallowed(monkeypatch, reply):
    licence(synced_at=1)
    cfg = config.Config(vocabulary="mine")
    stub(monkeypatch, reply)
    cloud.sync_now(cfg)
    assert cfg.vocabulary == "mine" and cloud.load()["synced_at"] == 1


def test_sync_does_nothing_without_a_licence(monkeypatch):
    sent = stub(monkeypatch)
    cloud.sync_now(config.Config())
    assert sent == []


def test_remove_during_sync_is_not_undone(monkeypatch):
    licence()

    def urlopen(req, timeout=None):
        cloud.clear()
        return _Resp(json.dumps({"data": None, "updated_at": 0}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    cloud.sync_now(config.Config())
    assert not cloud.has_licence()
