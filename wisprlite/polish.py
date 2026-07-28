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


# An hour-long meeting is roughly 700 segments. Sent as one request the reply
# exceeds any sensible output-token cap, so the JSON comes back truncated,
# parsing fails, and Polish returns None — the feature would be dead on exactly
# the meetings worth polishing. summarise.py chunks at 120 for the same reason.
CHUNK_SEGMENTS = 80


def _polish_chunk(source: list[str], provider: str, model: str, completion):
    """Polish one chunk, or return None when the reply cannot be trusted."""
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
    return value


def polish_segments(segments: list[dict], provider: str, model: str = "", *, completion=None):
    """Return a same-length text overlay, or ``None`` when it is unsafe to use."""
    source = [str(segment.get("text") or "") for segment in segments
              if isinstance(segment, dict)]
    if len(source) != len(segments):
        return None
    provider = (provider or "").strip().lower()
    if provider not in PROVIDERS or not provider_ready(provider):
        return None

    polished: list[str] = []
    for start in range(0, len(source), CHUNK_SEGMENTS):
        value = _polish_chunk(source[start:start + CHUNK_SEGMENTS],
                              provider, model, completion)
        # One bad chunk discards the WHOLE result. Keeping the good chunks would
        # leave a transcript half tidied and half raw with no way to tell which,
        # and this is a record of what people actually said.
        if value is None:
            return None
        polished.extend(value)
    if len(polished) != len(source):
        return None
    return {index: text.strip() for index, text in enumerate(polished)}
