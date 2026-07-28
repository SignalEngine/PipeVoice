"""Opt-in, per-segment meeting transcript polish."""

from __future__ import annotations

import json

from .cleanup import PROVIDERS, chat_completion, provider_ready

SYSTEM = (
    "Tidy this meeting transcript one segment at a time. Remove only fillers "
    "such as um and uh, false starts, and repeated words; fix punctuation, "
    "capitalisation, and obvious sentence mis-splits. Never reword, summarise, "
    "merge speakers, change meaning, or add anything not said. Return a JSON "
    "array with exactly one string for each input segment, in the same order."
)


def polish_segments(segments: list[dict], provider: str, model: str = "", *, completion=None):
    """Return a same-length text overlay, or ``None`` when it is unsafe to use."""
    source = [str(segment.get("text") or "") for segment in segments
              if isinstance(segment, dict)]
    if len(source) != len(segments):
        return None
    provider = (provider or "").strip().lower()
    if provider not in PROVIDERS or not provider_ready(provider):
        return None
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
    ]
    answer = (completion or chat_completion)(messages, provider, model)
    try:
        value = json.loads(answer or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list) or len(value) != len(source):
        return None
    if not all(isinstance(text, str) for text in value):
        return None
    return {index: text.strip() for index, text in enumerate(value)}
