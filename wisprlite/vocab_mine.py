"""Offline vocabulary candidates extracted from saved meeting material."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping

# Deliberately small: this is a noise filter, not a dictionary or an LLM.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do",
    "for", "from", "get", "go", "has", "have", "he", "her", "here", "him",
    "his", "how", "i", "if", "in", "is", "it", "its", "me", "my", "no",
    "not", "of", "on", "or", "our", "she", "so", "that", "the", "their",
    "them", "there", "they", "this", "to", "us", "was", "we", "were", "what",
    "when", "where", "which", "who", "why", "will", "with", "you", "your",
}
_TOKEN = re.compile(r"[\w]+(?:[’'][\w]+)*(?:[-–][\w]+(?:[’'][\w]+)*)?", re.UNICODE)


def _text_for_session(session: object) -> str:
    if isinstance(session, str):
        return session
    if isinstance(session, Mapping):
        value = session.get("text")
        if isinstance(value, str):
            return value
        value = session.get("transcript")
        if isinstance(value, str):
            return value
        if isinstance(value, Mapping):
            return _text_for_session(value)
        segments = session.get("segments")
        if isinstance(segments, Iterable) and not isinstance(segments, (str, bytes)):
            return " ".join(
                str(segment.get("text") or "")
                for segment in segments
                if isinstance(segment, Mapping)
            )
    return ""


def _session_name(session: object, index: int) -> str:
    if isinstance(session, Mapping):
        return str(session.get("name") or session.get("path") or index)
    return str(index)


def _existing_terms(existing_vocab: object) -> set[str]:
    if isinstance(existing_vocab, str):
        values = existing_vocab.split(",")
    elif isinstance(existing_vocab, Iterable):
        values = existing_vocab
    else:
        values = ()
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def _correction_groups(corrections: object, sessions: list[object]):
    """Yield (session index, mapping) for either one mapping or per-session mappings."""
    if isinstance(corrections, Mapping):
        yield from ((index, corrections) for index in range(len(sessions)))
        return
    if isinstance(corrections, Iterable) and not isinstance(corrections, (str, bytes)):
        for index, value in enumerate(corrections):
            if isinstance(value, Mapping):
                yield index, value
        return
    for index, session in enumerate(sessions):
        if isinstance(session, Mapping) and isinstance(session.get("corrections"), Mapping):
            yield index, session["corrections"]


def mine_candidates(sessions, existing_vocab, corrections) -> list[dict]:
    """Mine ranked, offline vocabulary candidates from meeting text.

    ``sessions`` may contain plain strings or dictionaries with ``text`` (or
    transcript ``segments``) and an optional ``name``. ``corrections`` may be
    one mapping shared by all sessions or one mapping per session. The latter
    is what the Settings UI passes after loading each local corrections.json.
    """
    sessions = list(sessions or [])
    known = _existing_terms(existing_vocab)
    correction_counts = defaultdict(int)
    correction_sessions = defaultdict(set)
    for session_index, mapping in _correction_groups(corrections, sessions):
        for replacement in mapping.values():
            term = str(replacement).strip()
            key = term.casefold()
            if term and key not in known and key not in _STOPWORDS:
                correction_counts[key] += 1
                correction_sessions[key].add(session_index)

    token_counts = defaultdict(int)
    token_sessions = defaultdict(set)
    capitalised = set()
    for session_index, session in enumerate(sessions):
        text = _text_for_session(session)
        if not text.strip():
            continue
        for match in _TOKEN.finditer(text):
            term = match.group(0)
            key = term.casefold()
            if key in known or key in _STOPWORDS or not term[0].isalpha():
                continue
            token_counts[key] += 1
            token_sessions[key].add(session_index)
            before = text[:match.start()]
            stripped = before.rstrip()
            # Transcripts are not prose. render_transcript emits "You: Thanks for
            # joining", and blocks often end with no full stop at all, so treating
            # only ".!?" as a boundary scores the first word of every block as
            # mid-sentence jargon — "Thanks"/"Sure"/"Great" then outrank the real
            # names this feature exists to surface. A colon ends the speaker
            # label; a newline ends the block.
            sentence_initial = (
                not stripped
                or stripped[-1] in ".!?:"
                or "\n" in before[len(stripped):]
            )
            if term[0].isupper() and not sentence_initial:
                capitalised.add(key)

    result = []
    for key, count in correction_counts.items():
        result.append({
            "term": next(
                (str(value).strip() for _, mapping in _correction_groups(corrections, sessions)
                 for value in mapping.values() if str(value).strip().casefold() == key),
                key,
            ),
            "count": count,
            "sessions": len(correction_sessions[key]),
            "source": "correction",
        })
    for key in capitalised:
        if key in correction_counts:
            continue
        result.append({"term": _display_term(key, sessions), "count": token_counts[key],
                       "sessions": len(token_sessions[key]), "source": "capitalised"})
    for key, count in token_counts.items():
        if key in correction_counts or key in capitalised or count < 3 or len(token_sessions[key]) < 2:
            continue
        result.append({"term": _display_term(key, sessions), "count": count,
                       "sessions": len(token_sessions[key]), "source": "repeated"})
    rank = {"correction": 0, "capitalised": 1, "repeated": 2}
    result.sort(key=lambda item: (rank[item["source"]], -item["count"], item["term"].casefold()))
    return result


def _display_term(key: str, sessions: list[object]) -> str:
    for session in sessions:
        text = _text_for_session(session)
        for match in _TOKEN.finditer(text):
            if match.group(0).casefold() == key:
                return match.group(0)
    return key
