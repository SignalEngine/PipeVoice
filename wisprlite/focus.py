"""PipeFocus — quiet, occasional nudges to keep a meeting on track.

The whole design problem here is not transcription, it is RESTRAINT. A tip that
fires often, or states the obvious, is worse than no feature at all: people turn
it off after one meeting and never turn it back on. So the rules are:

- Rare. At most one tip per COOLDOWN_SECONDS, whatever the model suggests.
- Earned. Analysis runs on NEW SPEECH, not on a wall clock, so a quiet meeting
  costs nothing and a silent one never fires.
- Specific. A tip has to name what triggered it, or it reads as a horoscope.
- Never repeated. The same nudge twice in one meeting is nagging.
- Off by default, and Deepgram-only, because it needs live transcription.

Everything in this module is pure: no audio, no network, no Tk. The policy that
decides WHETHER to spend a call, and whether a returned tip is worth showing, is
the part most likely to be wrong, so it is the part that must be testable.
"""

from __future__ import annotations

import json
import re

# Spend a call only after this much genuinely new speech. Wall-clock timers burn
# money on silence and on a meeting that is going fine.
MIN_NEW_WORDS = 220
# ...and never more often than this, even in a fast-moving conversation.
MIN_ANALYSIS_GAP = 90.0
# A tip is an interruption. One every few minutes at most.
COOLDOWN_SECONDS = 300.0
# Below this, a "tip" is too vague to act on and reads as filler.
MIN_TIP_CHARS = 25
MAX_TIP_CHARS = 180

SYSTEM = (
    "You are watching a live meeting transcript. Most of the time the right "
    "answer is to say NOTHING. Only speak up when something is concretely "
    "going wrong that the people in the room would thank you for noticing: an "
    "action item with no owner, a decision that keeps being deferred, a topic "
    "circled back to repeatedly, or someone's question left unanswered. "
    "Never comment on tone, never encourage, never summarise, never praise. "
    "Reply with a JSON object: {\"tip\": \"...\", \"because\": \"...\"} where "
    "'because' quotes the few words from the transcript that prompted it. If "
    "nothing is wrong, reply exactly {\"tip\": null}. Prefer null."
)


class FocusPolicy:
    """Decides when to analyse, and whether a returned tip is worth showing."""

    def __init__(self, *, cooldown: float = COOLDOWN_SECONDS):
        self.cooldown = float(cooldown)
        self._words_at_last_analysis = 0
        self._last_analysis_at = -1e9
        self._last_tip_at = -1e9
        self._shown: list[str] = []

    # -- when to spend a call -------------------------------------------------

    def should_analyse(self, transcript_words: int, now: float) -> bool:
        """True when enough NEW speech has accumulated and the gap has passed."""
        if transcript_words - self._words_at_last_analysis < MIN_NEW_WORDS:
            return False
        if now - self._last_analysis_at < MIN_ANALYSIS_GAP:
            return False
        # No point asking during the cooldown — nothing could be shown anyway,
        # so it would be a call whose answer is discarded.
        if now - self._last_tip_at < self.cooldown:
            return False
        return True

    def analysed(self, transcript_words: int, now: float) -> None:
        self._words_at_last_analysis = transcript_words
        self._last_analysis_at = now

    # -- whether to show what came back --------------------------------------

    def accept(self, tip: str | None, now: float) -> bool:
        """Whether this tip may be shown now. Records it if so."""
        text = " ".join(str(tip or "").split())
        if not text:
            return False
        if not (MIN_TIP_CHARS <= len(text) <= MAX_TIP_CHARS):
            return False
        if now - self._last_tip_at < self.cooldown:
            return False
        if any(_similar(text, seen) for seen in self._shown):
            return False
        self._shown.append(text)
        self._last_tip_at = now
        return True


def _similar(a: str, b: str) -> bool:
    """Same nudge in different words? Word overlap, not string equality."""
    wa, wb = set(a.lower().split()), set(b.lower().split())
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.6


def parse_tip(answer: object) -> tuple[str | None, str]:
    """Return (tip, because) from a model reply. (None, "") when it declines.

    Silence is the expected answer, so anything unparseable is treated as
    silence rather than surfaced — a malformed reply must never become a
    popup during someone's meeting.
    """
    # Scan for the first VALID object with raw_decode rather than slicing on
    # the first "{" and last "}". Slicing looked fine and was in fact dead
    # weight — find("{") already skipped a ```json fence — but it breaks the
    # moment the model writes prose containing a brace before the JSON, or adds
    # a remark after it. raw_decode consumes exactly one value and ignores the
    # rest, which is what polish.py settled on for the same reason.
    text = str(answer or "").strip()
    decoder = json.JSONDecoder()
    data = None
    index = text.find("{")
    while index != -1:
        try:
            value, _end = decoder.raw_decode(text[index:])
        except ValueError:
            value = None
        if isinstance(value, dict):
            data = value
            break
        index = text.find("{", index + 1)
    if data is None:
        return None, ""
    tip = data.get("tip")
    if not isinstance(tip, str):
        return None, ""
    because = data.get("because")
    return tip.strip() or None, (because.strip() if isinstance(because, str) else "")


def build_messages(transcript: str, *, window_words: int = 900) -> list[dict]:
    """Prompt from the RECENT window only — a whole meeting is cost with no gain."""
    words = str(transcript or "").split()
    recent = " ".join(words[-window_words:])
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"MEETING SO FAR (most recent part):\n{recent}"},
    ]


_SENTENCE_END = re.compile(r"[.!?]\s")


def rolling_transcript(chunks: list[str], *, max_words: int = 4000) -> str:
    """Join live chunks into one transcript, bounded so memory cannot grow."""
    joined = " ".join(str(c or "").strip() for c in chunks if str(c or "").strip())
    words = joined.split()
    return " ".join(words[-max_words:])
