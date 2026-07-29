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


# --- feeding two live streams down ONE socket --------------------------------
#
# Deepgram accepts multichannel audio and reports which channel each phrase came
# from. Interleaving the microphone as channel 0 and the desktop capture as
# channel 1 therefore gives BOTH sides of the call, WITH attribution, over a
# single connection — better than summing them (which would lose who spoke) and
# cheaper than opening two sockets.
#
# The two capture threads deliver blocks independently and never in lockstep, so
# a buffer holds whatever has arrived and only emits frames where BOTH sides are
# present. Emitting early would slide one channel against the other for the rest
# of the meeting.

class StreamInterleaver:
    """Pair mono blocks from two sources into interleaved stereo frames."""

    def __init__(self, *, max_pending_frames: int = 16_000):
        self._pending = {"mic": bytearray(), "desktop": bytearray()}
        self.max_pending_bytes = int(max_pending_frames) * 2   # int16

    def add(self, label: str, pcm: bytes) -> bytes:
        """Add mono PCM for one side; return whatever stereo is now complete."""
        if label not in self._pending:
            return b""
        buf = self._pending[label]
        buf.extend(pcm)
        # A stream that stops (device drop, or a solo meeting with no far end)
        # must not grow the other buffer without bound.
        if len(buf) > self.max_pending_bytes:
            del buf[: len(buf) - self.max_pending_bytes]

        mic, desktop = self._pending["mic"], self._pending["desktop"]
        ready = min(len(mic), len(desktop)) // 2 * 2      # whole int16 samples
        if ready <= 0:
            return b""
        out = bytearray(ready * 2)
        out[0::4] = mic[0:ready:2]
        out[1::4] = mic[1:ready:2]
        out[2::4] = desktop[0:ready:2]
        out[3::4] = desktop[1:ready:2]
        del mic[:ready]
        del desktop[:ready]
        return bytes(out)

    def pending_bytes(self) -> int:
        return sum(len(b) for b in self._pending.values())


def channel_speaker(channel: object) -> str:
    """Map a Deepgram channel index to a speaker label. Channel 0 is the mic."""
    try:
        return "You" if int(channel) == 0 else "Them"
    except (TypeError, ValueError):
        return "Them"


# --- the live session --------------------------------------------------------

import logging
import queue
import threading
import time

log = logging.getLogger("wisprlite")

# Deepgram closes a live socket after about an hour. A meeting can outlast that,
# so the connection is rebuilt — and the rebuild is COUNTED and surfaced,
# because a socket that dies at minute 60 and never comes back would leave the
# feature silently dead for the rest of a long call.
RECONNECT_BACKOFF = (1.0, 2.0, 5.0, 10.0, 20.0)
QUEUE_LIMIT = 400          # ~20s of 50ms blocks; beyond this we drop, never block


class FocusSession:
    """Streams a meeting to Deepgram and emits occasional focus tips.

    ``connect`` is injected so the reconnect logic can be tested without a
    network: it must return an object with ``feed(bytes)`` and ``close()``, and
    push transcript text through the ``on_text`` callback it is given.
    """

    def __init__(self, connect, *, completion=None, on_tip=None,
                 policy: "FocusPolicy | None" = None, clock=time.monotonic):
        self._connect = connect
        self._completion = completion
        self._on_tip = on_tip
        self._policy = policy or FocusPolicy()
        self._clock = clock
        self._interleaver = StreamInterleaver()
        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=QUEUE_LIMIT)
        self._chunks: list[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._conn = None
        # Observability — a silent failure here is the whole risk.
        self.reconnects = 0
        self.dropped_blocks = 0
        self.last_error = ""
        self.analyses = 0

    # -- audio in (called from the REALTIME callback) ------------------------

    def feed(self, label: str, pcm: bytes) -> None:
        """Enqueue audio. NEVER blocks and never raises into the audio thread."""
        try:
            stereo = self._interleaver.add(label, pcm)
            if not stereo:
                return
            try:
                self._queue.put_nowait(stereo)
            except queue.Full:
                # Dropping audio is bad; stalling the capture callback is worse,
                # because that loses the RECORDING, which is the thing the user
                # actually came for. Focus is best-effort on top of it.
                self.dropped_blocks += 1
        except Exception as exc:                      # never escape into audio
            self.last_error = f"{type(exc).__name__}: {exc}"

    # -- transcript in --------------------------------------------------------

    def on_text(self, text: str, channel: object = 1) -> None:
        line = " ".join(str(text or "").split())
        if not line:
            return
        with self._lock:
            self._chunks.append(f"{channel_speaker(channel)}: {line}")

    def transcript(self) -> str:
        with self._lock:
            return rolling_transcript(list(self._chunks))

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=timeout)
        self._close_conn()

    def _close_conn(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _open_conn(self) -> bool:
        try:
            self._conn = self._connect(self.on_text)
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.info("focus: connect failed: %s", exc)
            return False

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            if self._conn is None:
                if not self._open_conn():
                    delay = RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)]
                    attempt += 1
                    if self._stop.wait(delay):
                        return
                    continue
                attempt = 0
            try:
                block = self._queue.get(timeout=0.25)
            except queue.Empty:
                self._maybe_analyse()
                continue
            try:
                self._conn.feed(block)
            except Exception as exc:
                # The socket died — an hour-long cap, a network blip. Rebuild it
                # and keep the transcript: losing it would reset the policy and
                # re-fire tips the user has already seen.
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.reconnects += 1
                log.info("focus: stream dropped (%s), reconnecting", exc)
                self._close_conn()
                continue
            self._maybe_analyse()

    # -- the policy loop ------------------------------------------------------

    def _maybe_analyse(self) -> None:
        text = self.transcript()
        now = self._clock()
        if not self._policy.should_analyse(len(text.split()), now):
            return
        self._policy.analysed(len(text.split()), now)
        self.analyses += 1
        threading.Thread(target=self._analyse, args=(text,), daemon=True).start()

    def _analyse(self, text: str) -> None:
        """Ask for a tip. Runs on its OWN thread: never the audio or Tk thread."""
        try:
            answer = self._completion(build_messages(text)) if self._completion else None
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return
        tip, because = parse_tip(answer)
        if not self._policy.accept(tip, self._clock()):
            return
        if self._on_tip:
            try:
                self._on_tip(tip, because)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
