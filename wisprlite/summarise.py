"""Tk-free meeting summarisation using the cleanup provider machinery."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable

from .cleanup import PROVIDERS, chat_completion, provider_ready
from .meeting import render_transcript

log = logging.getLogger("wisprlite")

CHUNK_SEGMENTS = 120
CHUNK_OVERLAP = 10
MODES = ("bullets", "todos", "actions")

_COMMON_RAILS = (
    "Use only facts explicitly present in the transcript. Never invent tasks, "
    "owners, dates, deadlines, decisions, or agreements. Speaker labels are "
    "reliable: 'You' is the local user and 'Them', 'Them 1', 'Them 2', etc. are "
    "remote speakers. Preserve those labels when naming an owner. If ownership "
    "is not stated, write 'unassigned' instead of guessing. It is correct to "
    "return an empty section when nothing matching the request was agreed."
)

PROMPTS = {
    "bullets": (
        "Summarise the key points of this meeting as concise Markdown bullet "
        "points, grouped by topic rather than by speaker. Use short topic "
        "headings only when useful. Do not add a preamble and do not say "
        "'In this meeting'. "
    ),
    "todos": (
        "Return a Markdown to-do list containing everything explicitly agreed "
        "to be done. Use '- [ ] ' for every item. Include no preamble. "
    ),
    "actions": (
        "Return only explicitly agreed actionable tasks, each with an owner and "
        "a due date only where one was stated. Format every item exactly as "
        "'- [ ] <task> — <owner>[ — <when>]'. Include no preamble. "
    ),
}

EMPTY_RESULTS = {
    "bullets": "_No key points were identified._",
    "todos": "_No to-dos were agreed._",
    "actions": "_No owned actions were agreed._",
}


def _chunks(
    segments: list[dict],
    chunk_size: int,
    overlap: int,
) -> list[list[dict]]:
    """Split without dropping the tail, overlapping adjacent chunks."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be between 0 and chunk_size - 1")
    if not segments:
        return []
    if len(segments) <= chunk_size:
        return [segments]
    step = chunk_size - overlap
    chunks = []
    start = 0
    while start < len(segments):
        chunks.append(segments[start:start + chunk_size])
        if start + chunk_size >= len(segments):
            break
        start += step
    return chunks


def _complete(
    messages: list[dict[str, str]],
    provider: str,
    model: str,
) -> str:
    result = chat_completion(
        messages,
        provider,
        model,
        allow_empty=True,
        raise_errors=True,
    )
    return result or ""


def _messages(mode: str, content: str, *, reducing: bool = False) -> list[dict[str, str]]:
    task = PROMPTS[mode] + _COMMON_RAILS
    if reducing:
        user = (
            "Combine the partial summaries below into one final result. Remove "
            "duplicates caused by chunk overlap. Apply the requested format and "
            "all non-invention rules again.\n\nPARTIAL SUMMARIES:\n" + content
        )
    else:
        user = "MEETING TRANSCRIPT:\n" + content
    return [
        {"role": "system", "content": task},
        {"role": "user", "content": user},
    ]


def _normalise_segments(segments: list[dict]) -> list[dict]:
    return [
        segment
        for segment in segments
        if isinstance(segment, dict) and str(segment.get("text") or "").strip()
    ]


def _section_pattern(mode: str) -> re.Pattern[str]:
    return re.compile(
        rf"<!-- pipevoice-summary:{re.escape(mode)}:start -->.*?"
        rf"<!-- pipevoice-summary:{re.escape(mode)}:end -->\n?",
        re.DOTALL,
    )


def read_summaries(session_dir: str | Path) -> dict[str, str]:
    """Read mode results from a PipeVoice ``summary.md`` file."""
    try:
        source = (Path(session_dir) / "summary.md").read_text(encoding="utf-8")
    except OSError:
        return {}
    results = {}
    for mode in MODES:
        match = _section_pattern(mode).search(source)
        if not match:
            continue
        section = match.group(0)
        body = re.sub(r"^.*?\n", "", section, count=1)
        body = re.sub(
            rf"\n<!-- pipevoice-summary:{re.escape(mode)}:end -->\n?$",
            "",
            body,
        )
        lines = body.splitlines()
        if lines and lines[0].startswith("## "):
            lines.pop(0)
        if lines and lines[0].startswith("_Mode: "):
            lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
        results[mode] = "\n".join(lines).strip()
    return results


def _persist(
    session_dir: str | Path,
    mode: str,
    provider: str,
    model: str,
    markdown: str,
) -> None:
    path = Path(session_dir) / "summary.md"
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        existing = "# Meeting summaries\n\n"
    heading = {"bullets": "Bullet points", "todos": "To-dos", "actions": "Actions"}[mode]
    section = (
        f"<!-- pipevoice-summary:{mode}:start -->\n"
        f"## {heading}\n"
        f"_Mode: {mode} · Provider: {provider} · Model: {model}_\n\n"
        f"{markdown.strip()}\n"
        f"<!-- pipevoice-summary:{mode}:end -->\n"
    )
    pattern = _section_pattern(mode)
    if pattern.search(existing):
        # lambda, NOT the string directly: re.sub treats a replacement STRING's
        # backslashes as group references, and `section` is model output. A summary
        # mentioning "\d{4}" or a Windows path like C:\Users\me\.pipevoice raised
        # `bad escape \d`, which surfaced as "Summarising failed", discarded the
        # paid LLM call, and left the previous summary in place — every retry
        # failing the same way.
        updated = pattern.sub(lambda _m: section, existing, count=1)
    else:
        updated = existing.rstrip() + "\n\n" + section
    pending = path.with_suffix(".md.tmp")
    pending.write_text(updated, encoding="utf-8")
    pending.replace(path)


def summarise(
    segments: list[dict],
    mode: str,
    provider: str = "",
    model: str = "",
    *,
    session_dir: str | Path | None = None,
    speaker_map: dict[str, str] | None = None,
    chunk_size: int = CHUNK_SEGMENTS,
    overlap: int = CHUNK_OVERLAP,
    completion: Callable[[list[dict[str, str]], str, str], str] | None = None,
) -> str:
    """Summarise meeting segments into Markdown, using map-reduce when needed."""
    mode = (mode or "").strip().lower()
    if mode not in PROMPTS:
        raise ValueError(f"unsupported summary mode: {mode}")
    provider = (provider or "gemini").strip().lower()
    if provider not in PROVIDERS:
        provider = "openai"
    if not provider_ready(provider):
        log.warning("Meeting summary: provider %s is not ready", provider)
        return ""

    clean_segments = _normalise_segments(segments)
    if not clean_segments:
        return ""
    _, _, default_model = PROVIDERS[provider]
    resolved_model = (model or "").strip() or default_model
    call = completion or _complete
    parts = _chunks(clean_segments, chunk_size, overlap)

    if len(parts) == 1:
        result = call(
            _messages(mode, render_transcript(parts[0], timestamps=True, speaker_map=speaker_map)),
            provider,
            resolved_model,
        ).strip()
        if not result:
            result = EMPTY_RESULTS[mode]
    else:
        partials = []
        for index, part in enumerate(parts, 1):
            partial = call(
                _messages(mode, render_transcript(part, timestamps=True, speaker_map=speaker_map)),
                provider,
                resolved_model,
            ).strip()
            if partial:
                partials.append(f"PART {index}:\n{partial}")
        if partials:
            result = call(
                _messages(mode, "\n\n".join(partials), reducing=True),
                provider,
                resolved_model,
            ).strip()
        else:
            result = ""
        result = result or EMPTY_RESULTS[mode]
        result += f"\n\n_(summarised in {len(parts)} parts)_"

    if result and session_dir is not None:
        _persist(session_dir, mode, provider, resolved_model, result)
    return result
