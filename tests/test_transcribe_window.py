"""Backend dispatch for the --transcribe window.

Stubs wisprlite.engines.transcribe so this runs without faster-whisper or the
deepgram SDK installed (neither is present on the Linux dev box).
Run: python3 tests/test_transcribe_window.py
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wisprlite import config  # noqa: E402
from wisprlite import transcribe_window as W  # noqa: E402

RESULT = {"text": "hello", "language": "en", "duration": 3.0, "segments": []}


def _stub_engine():
    """Install a fake engines.transcribe and return the call log."""
    calls = []
    mod = types.ModuleType("wisprlite.engines.transcribe")

    def transcribe_file(path, **kw):
        calls.append(("local", path, kw))
        return RESULT

    def transcribe_file_deepgram(path, **kw):
        calls.append(("cloud", path, kw))
        return RESULT

    mod.transcribe_file = transcribe_file
    mod.transcribe_file_deepgram = transcribe_file_deepgram
    sys.modules["wisprlite.engines.transcribe"] = mod
    return calls


class Cfg:
    def __init__(self, transcribe_model_size="", local_model_size="base.en", language=""):
        self.transcribe_model_size = transcribe_model_size
        self.local_model_size = local_model_size
        self.language = language


def test_local_backend_uses_configured_model():
    calls = _stub_engine()
    out = W._run("a.wav", W.LOCAL, Cfg(local_model_size="small.en"))
    assert out is RESULT
    kind, path, kw = calls[0]
    assert kind == "local" and path == "a.wav", calls
    assert kw["model_size"] == "small.en", kw


def test_transcribe_model_size_overrides_local_model_size():
    """cfg.transcribe_model_size is the MCP-era override; blank falls back."""
    assert W._local_model(Cfg("medium.en", "base.en")) == "medium.en"
    assert W._local_model(Cfg("", "small.en")) == "small.en"
    assert W._local_model(Cfg("", "")) == "base.en"


def test_blank_language_is_passed_as_none_not_empty_string():
    """faster-whisper treats '' as a real language code and would fail."""
    calls = _stub_engine()
    W._run("a.wav", W.LOCAL, Cfg(language=""))
    assert calls[0][2]["language"] is None, calls


def test_cloud_backend_without_key_raises_actionable_error(monkey_key=""):
    calls = _stub_engine()
    real = config.deepgram_key
    config.deepgram_key = lambda: monkey_key
    try:
        W._run("a.wav", W.CLOUD, Cfg())
    except RuntimeError as exc:
        assert "DEEPGRAM_API_KEY" in str(exc), exc
        assert not calls, "must not call the API without a key"
    else:
        raise AssertionError("expected RuntimeError when no key is set")
    finally:
        config.deepgram_key = real


def test_cloud_backend_with_key_calls_deepgram():
    calls = _stub_engine()
    real = config.deepgram_key
    config.deepgram_key = lambda: "sk-test"
    try:
        W._run("a.wav", W.CLOUD, Cfg())
    finally:
        config.deepgram_key = real
    kind, path, kw = calls[0]
    assert kind == "cloud" and path == "a.wav", calls
    assert kw["api_key"] == "sk-test", kw


def test_pretty_duration():
    assert W._pretty_duration(0) == ""
    assert W._pretty_duration(45) == "45s"
    assert W._pretty_duration(90) == "1m 30s"
    assert W._pretty_duration(3725) == "1h 02m"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
