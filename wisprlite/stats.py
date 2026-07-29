"""Meeting stats: the few numbers worth acting on.

Computed from the transcript that already exists — no LLM, no API key, no
network, and no cost. Runs offline like the rest of the meeting features.

Deliberately NOT included: "interruptions" needs word-level timing this app does
not keep and would be wrong often; engagement or sentiment scores look precise
and mean nothing. A stat nobody would change their behaviour over is noise, and
noise makes the real numbers harder to see.
"""

from __future__ import annotations

QUESTION_OPENERS = (
    "what", "why", "how", "when", "where", "who", "which", "can", "could",
    "would", "should", "do", "does", "did", "is", "are", "was", "were", "shall",
)


def _speaker(segment: dict) -> str:
    return str(segment.get("speaker") or "").strip() or "Unknown"


def _words(segment: dict) -> list[str]:
    return str(segment.get("text") or "").split()


def _questions(text: str) -> int:
    """Count questions, allowing for transcripts with no question marks.

    Deepgram punctuates, local Whisper often does not, so counting "?" alone
    would report zero questions for offline users and look broken.
    """
    total = 0
    for sentence in str(text or "").replace("!", ".").replace("?", "?.").split("."):
        stripped = sentence.strip()
        if not stripped:
            continue
        if stripped.endswith("?"):
            total += 1
        elif stripped.split()[0].lower() in QUESTION_OPENERS:
            total += 1
    return total


def speaking_stats(segments: list[dict]) -> list[dict]:
    """Per-speaker figures, most talkative first.

    Shares are of WORDS rather than seconds: segment end times are not always
    present, and word count is a fair proxy for airtime that never divides by a
    missing duration.
    """
    people: dict[str, dict] = {}
    longest: dict[str, int] = {}
    run_speaker, run_words = None, 0

    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        name = _speaker(segment)
        words = _words(segment)
        if not words:
            continue
        entry = people.setdefault(name, {"speaker": name, "words": 0, "turns": 0, "questions": 0})
        entry["words"] += len(words)
        entry["turns"] += 1
        entry["questions"] += _questions(segment.get("text"))

        # An unbroken stretch is consecutive segments by the SAME person: that is
        # what a monologue actually looks like in a transcript.
        if name == run_speaker:
            run_words += len(words)
        else:
            run_speaker, run_words = name, len(words)
        longest[name] = max(longest.get(name, 0), run_words)

    total = sum(entry["words"] for entry in people.values())
    out = []
    for entry in people.values():
        entry["share"] = round(entry["words"] / total * 100, 1) if total else 0.0
        entry["longest_run_words"] = longest.get(entry["speaker"], 0)
        out.append(entry)
    out.sort(key=lambda e: (-e["words"], e["speaker"]))
    return out


def render_stats(segments: list[dict]) -> str:
    """A short plain-text block, or "" when there is nothing worth showing."""
    rows = speaking_stats(segments)
    if len(rows) < 2:
        # One voice is not a comparison. Reporting "You: 100%" is noise.
        return ""
    lines = ["Who spoke"]
    for row in rows:
        bar = "█" * max(1, round(row["share"] / 5))
        turns = f"{row['turns']} turn{'' if row['turns'] == 1 else 's'}"
        questions = f"{row['questions']} question{'' if row['questions'] == 1 else 's'}"
        lines.append(
            f"  {row['speaker']:<10} {row['share']:>5.1f}%  {bar}   {turns}, {questions}"
        )
    top = rows[0]
    if top["share"] >= 70:
        lines.append(f"\n{top['speaker']} did {top['share']:.0f}% of the talking.")
    longest = max(rows, key=lambda r: r["longest_run_words"])
    if longest["longest_run_words"] >= 250:
        lines.append(
            f"Longest unbroken stretch: {longest['speaker']}, "
            f"about {longest['longest_run_words']} words."
        )
    return "\n".join(lines)
