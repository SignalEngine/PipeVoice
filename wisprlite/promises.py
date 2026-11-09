"""Your promises: what you told the other side you would do, found after the call.

Regex finds candidates on "You" lines only; one optional AI call drops the ones that
are not real commitments. Pure apart from the file write and the injected completion.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("wisprlite")

PROMISES_FILE = "promises.json"

_VERBS = ("send|share|email|forward|follow up|get back|call|book|schedule|set up|check|"
          "look into|put together|write|draft|update|fix")
_PROMISE = re.compile(
    rf"\b(?:i'?ll|i will|i'?m going to|let me|we'?ll|we will)\b(?:\W+\w+){{0,6}}?\W+(?:{_VERBS})\b",
    re.IGNORECASE)
_DAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_MONTHS = ("january|february|march|april|may|june|july|august|september|october|"
           "november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
_DUE = re.compile(
    rf"\b(?:by (?:{_DAYS})|end of (?:the )?(?:day|week)|next week|today|tomorrow|{_DAYS}"
    rf"|\d{{1,2}}(?:st|nd|rd|th)? (?:of )?(?:{_MONTHS})|(?:{_MONTHS}) \d{{1,2}}(?:st|nd|rd|th)?"
    rf"|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?)\b",
    re.IGNORECASE)


def find_promises(segments: list[dict]) -> list[dict]:
    """[{t, text, due}] for each promise-shaped sentence on a "You" line."""
    found = []
    for seg in segments or []:
        if not isinstance(seg, dict) or str(seg.get("speaker") or "").casefold() != "you":
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", str(seg.get("text") or "").strip()):
            match = _PROMISE.search(sentence)
            if not match:
                continue
            due = _DUE.search(sentence, match.end())
            try:
                t = float(seg.get("t") or 0)
            except (TypeError, ValueError):
                t = 0.0
            found.append({"t": t, "text": sentence.strip(), "due": due.group(0) if due else ""})
    return found


_FILTER_PROMPT = (
    "Below are sentences the user said on a call, numbered from 0. Return the numbers of the "
    "ones that are real commitments the user made TO THE OTHER PERSON. Drop notes to self "
    "('I'll check my notes'), hypotheticals ('if it breaks I'll call them'), things done during "
    "the call itself ('let me share my screen'), and questions. Reply with a JSON list of "
    "numbers only, for example [0, 2]."
)


def ai_filter(candidates: list[dict], completion) -> list[dict]:
    """Keep the real commitments. Any failure keeps every candidate."""
    if not candidates:
        return candidates
    try:
        reply = completion([
            {"role": "system", "content": _FILTER_PROMPT},
            {"role": "user", "content": "\n".join(f"{i}: {c['text']}" for i, c in enumerate(candidates))},
        ])
        from .knowledge import _parse_json

        keep = _parse_json(reply, list)
        if keep is None or not all(isinstance(i, int) and 0 <= i < len(candidates) for i in keep):
            return candidates
        return [candidates[i] for i in sorted(set(keep))]
    except Exception as exc:
        log.info("promise filter failed, keeping the regex list: %s", exc)
        return candidates


def build_promises(session_dir, segments: list[dict], cfg, completion=None) -> list[dict]:
    """Find, filter and save promises.json (removed when there are none)."""
    from .cleanup import provider_ready

    path = Path(session_dir) / PROMISES_FILE
    found = find_promises(segments)
    if found and provider_ready(cfg.cleanup_provider):
        if completion is None:
            from .summarise import _complete

            def completion(messages):
                return _complete(messages, cfg.cleanup_provider, cfg.cleanup_model, timeout=30)
        found = ai_filter(found, completion)
    if found:
        pending = path.with_suffix(".json.tmp")
        pending.write_text(json.dumps(found, indent=2), encoding="utf-8")
        pending.replace(path)
    else:
        path.unlink(missing_ok=True)
    return found


def load_promises(session_dir) -> list[dict]:
    try:
        data = json.loads((Path(session_dir) / PROMISES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [p for p in data if isinstance(p, dict) and p.get("text")] if isinstance(data, list) else []
