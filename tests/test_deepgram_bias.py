"""A word list must never cost you dictation.

James, 2026-09-04, minutes after v2.44.0 shipped the starter vocabulary:
"i just tried to use the tool and it said deepgram connection failed to start".

Cause: the starter vocabulary filled `cfg.vocabulary`, which app.py hands to
Deepgram as `keywords`. nova-3 - the DEFAULT model - replaced `keywords` with
`keyterm` and rejects the old parameter outright, 400ing the whole connection.
Before the vocabulary shipped, the list was empty and the parameter was never
sent, so this had been latent since nova-3 became the default.

From the user's side a rejected option is indistinguishable from being offline.
"""

import pathlib
import sys
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite.engines import deepgram_engine as dg


def test_nova3_gets_keyterm_and_older_models_get_keywords():
    assert dg.bias_param("nova-3") == "keyterm"
    assert dg.bias_param("nova-3-general") == "keyterm"
    assert dg.bias_param("nova-2") == "keywords"
    assert dg.bias_param("base") == "keywords"


def test_an_unset_model_does_not_crash_the_lookup():
    assert dg.bias_param("") == "keywords"
    assert dg.bias_param(None) == "keywords"


def _session_with(model, start_results):
    """Build a _DeepgramSession with the SDK stubbed, returning the opts used."""
    seen = []
    conn = mock.Mock()
    conn.start.side_effect = lambda options: (seen.append(dict(options)), start_results.pop(0))[1]

    class _LiveOptions(dict):
        def __init__(self, **kw):
            super().__init__(**kw)

    engine = mock.Mock(model=model, language="en-US", keywords=["CLAUDE.md", "npm"])
    fake = mock.Mock(LiveOptions=_LiveOptions, LiveTranscriptionEvents=mock.Mock())
    with mock.patch.dict(sys.modules, {"deepgram": fake}):
        engine.client.listen.websocket.v.return_value = conn
        session = dg._DeepgramSession(engine, None)
    session._connector.join(timeout=5)
    return session, seen


def test_the_default_model_sends_keyterm_not_keywords():
    _, seen = _session_with("nova-3", [True])
    assert "keyterm" in seen[0], f"nova-3 was not sent keyterm: {sorted(seen[0])}"
    assert "keywords" not in seen[0], "nova-3 was sent the parameter it rejects"


def test_a_rejected_bias_parameter_retries_without_it_instead_of_failing():
    """The whole point: a word list must not be able to break dictation."""
    session, seen = _session_with("nova-3", [False, True])

    assert len(seen) == 2, "it gave up instead of retrying without the biasing"
    assert "keyterm" in seen[0]
    assert "keyterm" not in seen[1] and "keywords" not in seen[1], \
        "the retry still carried a biasing parameter"
    assert session._retried_without_bias is True


def test_a_genuinely_dead_connection_still_raises():
    """The retry must not mask being offline or having a bad key."""
    import pytest

    session, _ = _session_with("nova-3", [False, False])
    with pytest.raises(RuntimeError, match="failed to start"):
        session.finish(None)


def test_a_slow_failure_is_not_retried(monkeypatch):
    """A timeout is the network, not the word list: retrying doubled the wait."""
    monkeypatch.setattr(dg, "REJECT_WINDOW", 0.0)
    session, seen = _session_with("nova-3", [False])
    assert len(seen) == 1, "a timeout was retried as if the word list were rejected"


def test_the_press_does_not_wait_for_the_socket_and_no_audio_is_lost():
    """2026-09-29 log: the hotkey thread blocked on connect, so a press on a
    slow network looked ignored and the first words were never recorded."""
    import threading

    gate = threading.Event()
    sent = []
    conn = mock.Mock()
    conn.start.side_effect = lambda options: gate.wait(5)
    conn.send.side_effect = sent.append
    engine = mock.Mock(model="nova-3", language="en-US", keywords=None)
    fake = mock.Mock(LiveOptions=dict, LiveTranscriptionEvents=mock.Mock())
    with mock.patch.dict(sys.modules, {"deepgram": fake}):
        engine.client.listen.websocket.v.return_value = conn
        session = dg._DeepgramSession(engine, None)   # returns while start() is blocked

    session.feed(b"1")
    session.feed(b"2")
    assert sent == [], "audio was sent before the socket opened"
    gate.set()
    session._connector.join(timeout=5)
    session.feed(b"3")
    assert sent == [b"1", b"2", b"3"], f"early audio lost or reordered: {sent}"
