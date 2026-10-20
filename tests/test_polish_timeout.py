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


def _fake_models(monkeypatch, replies):
    """chat_completion stand-in: reply by model name, record calls."""
    calls = []

    def fake(messages, provider, model="", **kw):
        calls.append((model, kw.get("timeout")))
        return replies.get(model)

    monkeypatch.setattr(cleanup, "chat_completion", fake)
    return calls


def test_busy_gemini_falls_back_to_older_model(monkeypatch):
    calls = _fake_models(monkeypatch, {"gemini-3.1-flash-lite": "Tidy."})
    assert cleanup.clean("um tidy", "gemini", timeout=6.0) == "Tidy."
    assert [m for m, _ in calls] == ["", "gemini-3.1-flash-lite"]
    assert calls[1][1] <= 6.0


def test_no_fallback_when_user_picked_a_model_or_no_budget(monkeypatch):
    calls = _fake_models(monkeypatch, {})
    assert cleanup.clean("um", "gemini", model="my-model", timeout=6.0) is None
    assert cleanup.clean("um", "gemini") is None          # summaries: no timeout
    assert len(calls) == 2


def test_groq_polish_uses_gpt_oss_with_low_reasoning(monkeypatch):
    seen = []
    created = {}

    class Completions:
        def create(self, **kw):
            created.update(kw)
            msg = types.SimpleNamespace(content="Tidy.")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    class OpenAI:
        def __init__(self, **kw):
            seen.append(kw)
            self.chat = types.SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=OpenAI))
    monkeypatch.setenv("GROQ_API_KEY", "x")
    assert cleanup.clean("um tidy", "groq", timeout=6.0) == "Tidy."
    assert seen[0]["base_url"] == "https://api.groq.com/openai/v1"
    assert created["model"] == "openai/gpt-oss-20b"
    assert created["reasoning_effort"] == "low"
