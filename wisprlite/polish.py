"""Opt-in, per-segment meeting transcript polish."""

from __future__ import annotations

import json

from .cleanup import PROVIDERS, chat_completion, provider_ready


class ProviderNotReady(RuntimeError):
    """The chosen AI provider has no key / is not configured."""

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


def _strip_fence(answer: object) -> str:
    """Unwrap a ```json ... ``` block.

    Gemini in particular returns JSON inside a markdown fence by default, which
    json.loads rejects — the whole reply was being discarded as "unsafe" when it
    was perfectly good. Nothing else in the codebase asks a model for JSON, so
    this had no prior art to copy.
    """
    text = str(answer or "").strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    newline = body.find("\n")
    if newline != -1 and body[:newline].strip().isalpha():
        body = body[newline + 1:]          # drop a language tag such as "json"
    end = body.rfind("```")
    return (body[:end] if end != -1 else body).strip()


class PolishFailed(RuntimeError):
    """Carries WHY polishing failed, so the UI can show something actionable."""


def _extract_array(text: str) -> str:
    """Pull the first JSON array out of a reply that may be wrapped in prose."""
    start = text.find("[")
    end = text.rfind("]")
    return text[start:end + 1] if 0 <= start < end else text


def _polish_chunk(source: list[str], provider: str, model: str, completion):
    """Polish one chunk. Raises PolishFailed with the real reason."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
    ]
    if completion is not None:
        answer = completion(messages, provider, model)
    else:
        # raise_errors=True, or an API failure (rate limit, token cap, bad model
        # name) comes back as a bare None and gets reported as "the model replied
        # with something unusable" — blaming the reply for a transport error and
        # hiding the one detail that would fix it.
        try:
            answer = chat_completion(messages, provider, model, raise_errors=True)
        except Exception as exc:
            raise PolishFailed(f"{type(exc).__name__}: {exc}") from exc

    if not str(answer or "").strip():
        raise PolishFailed(f"{provider} returned an empty response")

    text = _extract_array(_strip_fence(answer))
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        snippet = " ".join(str(answer).split())[:110]
        raise PolishFailed(f"could not read the reply as JSON ({exc}). It began: {snippet}")
    if not isinstance(value, list):
        raise PolishFailed(f"expected a list of {len(source)} lines, got {type(value).__name__}")
    if len(value) != len(source):
        raise PolishFailed(
            f"got {len(value)} lines back for {len(source)} segments — discarded "
            "rather than risk putting one person's words under another's name"
        )
    if not all(isinstance(item, str) for item in value):
        raise PolishFailed("the reply contained something that was not text")
    return value


def polish_segments(segments: list[dict], provider: str, model: str = "", *, completion=None):
    """Return a same-length text overlay, or ``None`` when it is unsafe to use."""
    source = [str(segment.get("text") or "") for segment in segments
              if isinstance(segment, dict)]
    if len(source) != len(segments):
        return None
    provider = (provider or "").strip().lower()
    if provider not in PROVIDERS or not provider_ready(provider):
        # Distinct from a bad reply: this is "no key configured", which the user
        # fixes somewhere completely different. Blending the two into one
        # message left James unable to tell which had happened.
        raise ProviderNotReady(provider or "none")

    polished: list[str] = []
    for start in range(0, len(source), CHUNK_SEGMENTS):
        # One bad chunk discards the WHOLE result. Keeping the good chunks would
        # leave a transcript half tidied and half raw with no way to tell which,
        # and this is a record of what people actually said.
        value = _polish_chunk(source[start:start + CHUNK_SEGMENTS],
                              provider, model, completion)
        polished.extend(value)
    if len(polished) != len(source):
        return None
    return {index: text.strip() for index, text in enumerate(polished)}
