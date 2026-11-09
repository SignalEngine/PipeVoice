#!/usr/bin/env python3
"""Replay a call's transcript through the live-answers code with a fake clock.

    python3 scripts/replay_live.py <session_dir_or_fixture> --docs <folder> [--topic] [--fake-ai]

Feeds every Them line to a LiveAnswerer at its real start time and prints each card.
--fake-ai echoes the first sentence of the top doc chunk as the quote (no network);
without it the configured cleanup provider answers. Prints `cards=N max_per_120s=M`:
N is every shown card, M is the most TOPIC cards in any 120 s window.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wisprlite import knowledge, live_answers, promises  # noqa: E402


def fake_completion(docs: list[dict]):
    """Quote the first sentence of the top chunk for what was just said. Offline."""
    index = knowledge.BM25(knowledge.chunk_docs(docs))

    def complete(messages):
        user = messages[-1]["content"]
        said = user.split("Documents:")[0]
        said = said.split("Question:")[-1].split("What they said:")[-1]
        top = index.top(said, 1)
        if not top:
            return "{}"
        doc = next(d for d in docs if d["source"] == top[0]["source"])
        body = next((p for p in re.split(r"\n\s*\n", doc["text"]) if not p.strip().startswith("#")), "")
        quote = re.split(r"(?<=[.!?])\s+", body.strip())[0]
        return json.dumps({"headline": " ".join(quote.split()[:8]), "quote": quote,
                           "source": doc["source"]})

    return complete


def replay(session: Path, docs_folder: str, *, topic: bool = False, fake_ai: bool = False) -> dict:
    data = json.loads((Path(session) / "transcript.json").read_text(encoding="utf-8"))
    segments = data.get("segments") or []
    docs, _report = knowledge.load_sources(docs_folder, "", None)
    if fake_ai:
        completion = fake_completion(docs)
    else:
        from wisprlite import config

        cfg = config.Config.load()
        completion = knowledge._default_completion(cfg.cleanup_provider, cfg.cleanup_model)
    now = [0.0]
    cards: list[dict] = []

    def on_card(headline, points, quote, source, kind="answer"):
        cards.append({"t": now[0], "kind": kind, "headline": headline, "quote": quote,
                      "source": source})

    answerer = live_answers.LiveAnswerer(docs_folder, "", None, completion, on_card,
                                         clock=lambda: now[0], topic_cards=topic)
    answerer._build()
    for seg in segments:
        if not str(seg.get("speaker", "")).startswith("Them"):
            continue
        now[0] = float(seg.get("t") or 0)
        answerer.on_line("Them", str(seg.get("text") or ""))
        question, answerer._pending = answerer._pending, None
        if question:
            answerer._answer(*question)
        job, answerer._pending_topic = answerer._pending_topic, None
        if job:
            answerer._topic(*job)
    topic_times = [c["t"] for c in cards if c["kind"] == "topic"]
    worst = max((sum(1 for u in topic_times if t <= u < t + 120) for t in topic_times), default=0)
    return {"cards": cards, "max_per_120s": worst,
            "promises": promises.find_promises(segments)}


def _mmss(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session")
    ap.add_argument("--docs", required=True)
    ap.add_argument("--topic", action="store_true")
    ap.add_argument("--fake-ai", action="store_true")
    args = ap.parse_args(argv)
    out = replay(Path(args.session), args.docs, topic=args.topic, fake_ai=args.fake_ai)
    for c in out["cards"]:
        print(f"[{_mmss(c['t'])}] {c['kind']:6} {c['headline']} | “{c['quote']}” | {c['source']}")
    for p in out["promises"]:
        print(f"[{_mmss(p['t'])}] promise {p['text']}" + (f" ({p['due']})" if p["due"] else ""))
    topics = sum(1 for c in out["cards"] if c["kind"] == "topic")
    print(f"cards={len(out['cards'])} max_per_120s={out['max_per_120s']} topic_cards={topics}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
