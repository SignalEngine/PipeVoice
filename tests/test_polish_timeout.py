import sys
import types

from wisprlite import cleanup


def _fake_openai(monkeypatch, seen):
    class Completions:
        def create(self, **kw):
            msg = types.SimpleNamespace(content="Tidy.")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    class OpenAI:
        def __init__(self, **kw):
            seen.append(kw)
            self.chat = types.SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=OpenAI))
    monkeypatch.setenv("GEMINI_API_KEY", "x")


def test_dictation_polish_has_a_timeout_and_no_retries(monkeypatch):
    seen = []
    _fake_openai(monkeypatch, seen)
    assert cleanup.clean("um tidy", "gemini", timeout=6.0) == "Tidy."
    assert seen[0]["timeout"] == 6.0
    assert seen[0]["max_retries"] == 0


def test_other_callers_keep_client_defaults(monkeypatch):
    seen = []
    _fake_openai(monkeypatch, seen)
    cleanup.clean("um tidy", "gemini")
    assert "timeout" not in seen[0] and "max_retries" not in seen[0]


def test_timeout_grows_with_long_dictation():
    assert cleanup.polish_timeout("a few words") == 6.03
    assert cleanup.polish_timeout("w " * 500) == 11.0
