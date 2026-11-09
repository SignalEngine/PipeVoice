"""Replay the synthetic calls through the live-answers code (fake AI, fake clock)."""

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from uistub import install_platform_stubs

install_platform_stubs()

FIXTURES = ROOT / "tests" / "fixtures" / "live_calls"
_spec = importlib.util.spec_from_file_location("replay_live", ROOT / "scripts" / "replay_live.py")
replay_live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay_live)


def _run(name, topic=True):
    return replay_live.replay(FIXTURES / name, str(FIXTURES / name / "docs"), topic=topic, fake_ai=True)


def test_sales_call_gets_topic_cards_but_never_more_than_one_per_120_seconds():
    out = _run("sales")
    topic = [c for c in out["cards"] if c["kind"] == "topic"]
    assert topic and out["max_per_120s"] <= 1
    times = [c["t"] for c in topic]
    assert all(b - a >= 120 for a, b in zip(times, times[1:]))


def test_every_replayed_card_quote_is_verbatim_from_its_source():
    from wisprlite import knowledge

    for name in ("sales", "smalltalk", "kickoff"):
        docs, _ = knowledge.load_sources(str(FIXTURES / name / "docs"), "", None)
        text = {d["source"]: knowledge._norm(d["text"]) for d in docs}
        for card in _run(name)["cards"]:
            assert knowledge._norm(card["quote"]) in text[card["source"]]


def test_small_talk_call_gets_no_topic_cards():
    assert [c for c in _run("smalltalk")["cards"] if c["kind"] == "topic"] == []


def test_topic_off_replays_no_topic_cards_at_all():
    for name in ("sales", "smalltalk", "kickoff"):
        assert not [c for c in _run(name, topic=False)["cards"] if c["kind"] == "topic"]


def test_replay_prints_the_summary_line_and_the_promises(capsys):
    kick = FIXTURES / "kickoff"
    assert replay_live.main([str(kick), "--docs", str(kick / "docs"), "--topic", "--fake-ai"]) == 0
    out = capsys.readouterr().out
    assert "cards=" in out and "max_per_120s=" in out and " promise " in out
