import json

from wisprlite import history


def test_history_keeps_raw_only_when_polish_changed_it(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "_path", lambda: tmp_path / "history.jsonl")
    history.record("Fill it with bots.", raw="fill it with sort of butts")
    history.record("Same text.", raw="Same text.")
    history.record("No polish.")
    rows = [json.loads(l) for l in (tmp_path / "history.jsonl").read_text().splitlines()]
    assert rows[0]["raw"] == "fill it with sort of butts"
    assert "raw" not in rows[1] and "raw" not in rows[2]
