"""Incremental microphone + desktop capture for meeting recordings.

The two inputs deliberately use separate audio libraries and own separate WAV
writers. Optional audio dependencies are imported when capture starts so this
module remains importable on non-Windows development machines while missing
packages still fail synchronously.
"""

from __future__ import annotations

import json
import math
import os
import queue
import shutil
import sys
import threading
import tempfile
import time
import wave
import re
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SAMPLE_RATE = 48_000
CHANNELS = 1
SAMPLE_WIDTH = 2
MIC_BLOCKSIZE = 2400  # 50ms of frames at SAMPLE_RATE, unchanged from 16kHz
DESKTOP_BLOCKSIZE = 4_800  # 100ms of frames at SAMPLE_RATE, unchanged from 16kHz
CAPTURE_JOIN_TIMEOUT = 3.0
HEADER_PATCH_INTERVAL = 5.0
# ~30s of audio in hand before we start dropping. Dropping is a last resort,
# but stalling the audio callback loses the recording, which is worse.
PCM_QUEUE_LIMIT = 2_000
DEFAULT_RETENTION_SESSIONS = 20
SPEAKER_MAP_FILE = "speaker_map.json"
CORRECTIONS_FILE = "corrections.json"
BOOKMARKS_FILE = "bookmarks.json"
POLISHED_FILE = "polished.json"


def load_polished(session_dir: str | Path) -> dict[int, str]:
    try:
        value = json.loads((Path(session_dir) / POLISHED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    result = {}
    for index, text in value.items():
        try:
            index = int(index)
        except (TypeError, ValueError):
            continue
        if index >= 0 and str(text).strip():
            result[index] = str(text).strip()
    return result


def save_polished(session_dir: str | Path, polished: dict[int, str]) -> None:
    _write_json(
        Path(session_dir) / POLISHED_FILE,
        {str(index): str(text).strip() for index, text in polished.items()
         if int(index) >= 0 and str(text).strip()},
    )


def apply_polished(segments: list[dict], polished: dict[int, str] | None = None) -> list[dict]:
    overlay = polished or {}
    return [
        {**segment, "text": overlay.get(index, segment.get("text", ""))}
        for index, segment in enumerate(segments)
        if isinstance(segment, dict)
    ]
# "note that" was here and had to go: "note that the deploy is Friday" is
# ordinary speech, so every existing user would have had false highlights fed
# to the summariser as moments they deliberately flagged. A trigger phrase has
# to be one nobody says by accident.
DEFAULT_BOOKMARK_PHRASES = "bookmark that, flag that"


def bookmarks_from_phrases(transcript, phrases: str = DEFAULT_BOOKMARK_PHRASES) -> list[dict]:
    """Find spoken bookmark phrases in a completed transcript.

    ``transcript`` is the same object written by :func:`transcribe_session`:
    ``{"segments": [{"t": ..., "speaker": ..., "text": ...}]}``.  Matching
    is deliberately word-based, so punctuation and casing in speech-to-text
    output do not matter, while a longer word cannot trigger a mark.
    """
    if isinstance(transcript, dict):
        segments = transcript.get("segments")
    else:
        segments = transcript
    if not isinstance(segments, list):
        return []
    configured = []
    for phrase in str(phrases or "").split(","):
        words = re.findall(r"[\w']+", phrase.casefold())
        if words:
            configured.append((phrase.strip(), words))
    found = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "")
        text_words = re.findall(r"[\w']+", text.casefold())
        for label, words in configured:
            width = len(words)
            if any(text_words[index:index + width] == words
                   for index in range(max(0, len(text_words) - width + 1))):
                try:
                    timestamp = max(0.0, float(segment.get("t", segment.get("start", 0)) or 0))
                except (TypeError, ValueError, OverflowError):
                    continue
                found.append({"t": timestamp, "source": "phrase", "phrase": label})
                break
    return found


def load_speaker_map(session_dir: str | Path) -> dict[str, str]:
    """Load the optional display-name overlay, never the raw transcript."""
    path = Path(session_dir) / SPEAKER_MAP_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(name).strip()
        for key, name in value.items()
        if str(key).strip() and str(key).casefold() != "you" and str(name).strip()
    }


def save_speaker_map(session_dir: str | Path, speaker_map: dict[str, str]) -> None:
    """Persist only non-empty names as an atomic session-dir overlay."""
    clean = {
        str(key): str(value).strip()
        for key, value in speaker_map.items()
        if str(key).strip() and str(key).casefold() != "you" and str(value).strip()
    }
    _write_json(Path(session_dir) / SPEAKER_MAP_FILE, clean)


def apply_speaker_map(
    segments: list[dict], speaker_map: dict[str, str] | None = None
) -> list[dict]:
    """Return display copies of segments with remote names overlaid."""
    mapping = speaker_map or {}
    return [
        {
            **segment,
            "speaker": (
                segment.get("speaker")
                if str(segment.get("speaker") or "").casefold() == "you"
                else mapping.get(str(segment.get("speaker") or ""), segment.get("speaker"))
            ),
        }
        for segment in segments
        if isinstance(segment, dict)
    ]


def load_corrections(session_dir: str | Path) -> dict[str, str]:
    """Load the optional wording overlay, never the raw transcript."""
    path = Path(session_dir) / CORRECTIONS_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(replacement).strip()
        for key, replacement in value.items()
        if str(key).strip() and str(replacement).strip()
    }


def save_corrections(session_dir: str | Path, corrections: dict[str, str]) -> None:
    """Persist only non-empty wording fixes as an atomic session overlay."""
    clean = {
        str(key): str(value).strip()
        for key, value in corrections.items()
        if str(key).strip() and str(value).strip()
    }
    _write_json(Path(session_dir) / CORRECTIONS_FILE, clean)


def apply_corrections(
    segments: list[dict], corrections: dict[str, str] | None = None
) -> list[dict]:
    """Return display copies of segments with wording overlaid."""
    from .typer import apply_replacements

    mapping = corrections or {}
    return [
        {
            **segment,
            "text": apply_replacements(str(segment.get("text") or ""), mapping),
        }
        for segment in segments
        if isinstance(segment, dict)
    ]


def smooth_level(current: float, rms: float) -> float:
    """Apply the recorder's fast-attack, slow-release level smoothing."""
    return rms if rms > current else current * 0.7


def meetings_dir() -> Path:
    """Return the recording directory: the user's choice, else machine-local.

    Recordings are large, so people reasonably want them on another drive. A
    configured path wins, but it must still be USABLE — an unplugged external
    disk or a path that cannot be created would otherwise lose a recording
    mid-meeting, so fall back to the default rather than fail.
    """
    from .config import APP_NAME, Config

    try:
        chosen = str(Config.load().meetings_dir or "").strip()
    except Exception:
        chosen = ""
    if chosen:
        candidate = Path(chosen).expanduser()
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".pipevoice-write-test"
            probe.touch()
            probe.unlink()
            return candidate
        except OSError:
            pass          # unwritable or unplugged — use the default below

    return default_meetings_dir()


def default_meetings_dir() -> Path:
    """The machine-local folder, ignoring any configured override.

    Needed so the browser can still show recordings made before the user moved
    the save location — changing the folder moves new recordings, not old ones.
    """
    from .config import APP_NAME

    if sys.platform == "win32":
        base = os.getenv("LOCALAPPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Local")
    else:
        base = os.getenv("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME / "meetings"


def merge_transcripts(
    mic_segments: list[dict],
    desktop_segments: list[dict],
    *,
    mic_offset: float | None,
    desktop_offset: float | None,
) -> list[dict]:
    """Merge mic and desktop segments on their first-frame timeline."""
    streams = []
    if mic_segments:
        streams.append(("mic", mic_segments, mic_offset))
    if desktop_segments:
        streams.append(("desktop", desktop_segments, desktop_offset))
    if not streams:
        return []

    known_offsets = [float(offset) for _, _, offset in streams if offset is not None]
    timeline_start = min(known_offsets) if known_offsets else 0.0

    desktop_speakers = []
    for segment in desktop_segments:
        speaker = segment.get("speaker")
        if speaker is not None and speaker not in desktop_speakers:
            desktop_speakers.append(speaker)
    speaker_numbers = {
        speaker: index + 1 for index, speaker in enumerate(desktop_speakers)
    }
    multiple_remote_speakers = len(desktop_speakers) > 1

    merged = []
    for stream, segments, offset in streams:
        shift = float(offset) - timeline_start if offset is not None else 0.0
        for segment in segments:
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            if stream == "mic":
                speaker = "You"
            elif multiple_remote_speakers:
                speaker_id = segment.get("speaker")
                number = speaker_numbers.get(speaker_id)
                speaker = f"Them {number}" if number is not None else "Them"
            else:
                speaker = "Them"
            merged.append(
                {
                    "t": round(float(segment.get("start") or 0.0) + shift, 3),
                    "speaker": speaker,
                    "text": text,
                }
            )

    merged.sort(key=lambda segment: segment["t"])
    return merged


# How far apart the same words may land on the two streams and still be one
# sound. Speaker -> air -> mic is near-instant; the slack is for the two engines
# segmenting the audio differently.
BLEED_WINDOW = 6.0
# Compare WORDS, not characters. Measured on a real bleed pair against real
# two-sided conversation: word-sequence similarity scores 0.814 vs 0.087, which
# separates cleanly, while character similarity gives 0.341 vs 0.180 — ranges
# that overlap, so no threshold can split them. The engines transcribe the same
# audio slightly differently ("hand picked" / "handpicked", a leading "At"), and
# a single inserted word shifts every later character while leaving the word
# sequence almost intact.
BLEED_SIMILARITY = 0.55
BLEED_MIN_WORDS = 6
# An echo may be transcribed a little longer than the original, but not
# half as long again — beyond that the local speaker said something extra.
BLEED_LENGTH_TOLERANCE = 1.3
# Live detection while recording.
BLEED_LIVE_LEVEL = 0.02        # RMS that counts as "someone is talking"
BLEED_LIVE_MIN_SAMPLES = 120   # ~6s of remote speech before judging
BLEED_LIVE_RATIO = 0.75        # mic loud this often DURING remote speech = speakers


def count_speaker_bleed(segments: list[dict]) -> int:
    """Count how many local segments look like an echo of the remote audio.

    DETECTS ONLY — it must never delete. Two review rounds proved text
    similarity cannot tell an echo from a similar-but-different sentence:

        real echo                        -> 0.789
        "deploy Friday" / "deploy Monday" -> 0.857

    The false positive scores HIGHER than the true positive, so no threshold
    exists that removes echoes without sometimes deleting a materially
    different statement — and one word ("Friday" vs "Monday") can invert the
    meaning of a record of what someone said. The user can fix the cause in
    seconds by wearing headphones; the app cannot safely fix it afterwards.
    So we tell them, and leave every word intact.
    """
    from difflib import SequenceMatcher

    remote = [s for s in segments if str(s.get("speaker") or "") != "You"]
    if not remote:
        return 0

    def words(text: object) -> list[str]:
        return str(text or "").lower().split()

    hits = 0
    for segment in segments:
        if str(segment.get("speaker") or "") != "You":
            continue
        mine = words(segment.get("text"))
        if len(mine) < BLEED_MIN_WORDS:
            continue
        for other in remote:
            try:
                delay = float(segment.get("t", 0.0)) - float(other.get("t", 0.0))
            except (TypeError, ValueError):
                continue
            # Echo travels desktop -> mic only, so the mic copy always comes
            # AFTER. A local line spoken BEFORE the remote one cannot be an echo.
            if delay < 0 or delay > BLEED_WINDOW:
                continue
            theirs = words(other.get("text"))
            if len(theirs) < BLEED_MIN_WORDS:
                continue
            if len(mine) > len(theirs) * BLEED_LENGTH_TOLERANCE:
                continue
            if SequenceMatcher(None, mine, theirs).ratio() >= BLEED_SIMILARITY:
                hits += 1
                break
    return hits


def _format_elapsed(value: object) -> str:
    try:
        elapsed = max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        elapsed = 0
    if elapsed >= 3600:
        return (
            f"{elapsed // 3600}:"
            f"{(elapsed % 3600) // 60:02d}:"
            f"{elapsed % 60:02d}"
        )
    return f"{elapsed // 60}:{elapsed % 60:02d}"


def render_transcript(
    segments: list[dict],
    *,
    timestamps: bool = False,
    speaker_map: dict[str, str] | None = None,
    corrections: dict[str, str] | None = None,
) -> str:
    """Render consecutive same-speaker segments as plain-text blocks."""
    blocks: list[dict] = []
    for segment in apply_speaker_map(
        apply_corrections(segments, corrections), speaker_map
    ):
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        speaker = str(segment.get("speaker") or "").strip()
        if blocks and blocks[-1]["speaker"] == speaker:
            blocks[-1]["text"] += " " + text
        else:
            blocks.append(
                {
                    "speaker": speaker,
                    "text": text,
                    "time": segment.get("t", segment.get("start", 0)),
                }
            )
    return "\n\n".join(
        (
            f"[{_format_elapsed(block['time'])}] "
            if timestamps
            else ""
        )
        + f"{block['speaker']}: {block['text']}"
        for block in blocks
    )


def find_loudest_speaker_window(
    wav_path: str | Path,
    segments: list[dict],
    speaker: str,
    *,
    window_seconds: float = 2.0,
    stream_shift: float = 0.0,
) -> tuple[float, float] | None:
    """Find the highest-RMS two-second window among one speaker's segments.

    Audio is read in bounded chunks for each candidate segment. The function is
    deliberately Tk-free and does not retain the recording in memory.
    """
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    candidates = []
    for segment in segments:
        if not isinstance(segment, dict) or str(segment.get("speaker") or "") != speaker:
            continue
        try:
            start = max(
                0.0,
                float(segment.get("t", segment.get("start", 0)) or 0)
                - float(stream_shift),
            )
        except (TypeError, ValueError, OverflowError):
            continue
        candidates.append(start)
    if not candidates:
        return None
    try:
        with wave.open(str(wav_path), "rb") as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                return None
            rate = audio.getframerate()
            total = audio.getnframes()
            length = max(1, int(round(window_seconds * rate)))
            best = None
            for start in candidates:
                first = min(max(0, int(round(start * rate))), max(0, total - length))
                audio.setpos(first)
                remaining = min(length, total - first)
                squares = 0
                count = 0
                while remaining:
                    raw = audio.readframes(min(4096, remaining))
                    if not raw:
                        break
                    samples = array("h")
                    samples.frombytes(raw)
                    if sys.byteorder != "little":
                        samples.byteswap()
                    squares += sum(sample * sample for sample in samples)
                    count += len(samples)
                    remaining -= len(samples)
                if count:
                    rms = math.sqrt(squares / count)
                    if best is None or rms > best[0]:
                        best = (rms, first / rate, min(length, total - first) / rate)
            return (best[1], best[2]) if best else None
    except (OSError, EOFError, wave.Error):
        return None


def write_wav_window(
    wav_path: str | Path, start: float, duration: float, *, directory: str | Path | None = None
) -> Path:
    """Copy a bounded audio window to a temporary WAV for playback."""
    with wave.open(str(wav_path), "rb") as source:
        rate = source.getframerate()
        first = max(0, int(round(start * rate)))
        count = max(1, int(round(duration * rate)))
        source.setpos(min(first, source.getnframes()))
        frames = source.readframes(count)
        fd, name = tempfile.mkstemp(prefix="pipevoice-speaker-", suffix=".wav", dir=directory)
        os.close(fd)
        target = Path(name)
        with wave.open(str(target), "wb") as output:
            output.setnchannels(source.getnchannels())
            output.setsampwidth(source.getsampwidth())
            output.setframerate(rate)
            output.writeframes(frames)
    return target


def _wav_has_frames(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with wave.open(str(path), "rb") as audio:
            return audio.getnframes() > 0
    except (OSError, EOFError, wave.Error):
        return False


def load_bookmarks(session_dir: str | Path) -> list[dict]:
    """Load the optional timestamp overlay; malformed files fail closed."""
    try:
        value = json.loads((Path(session_dir) / BOOKMARKS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(value, list):
        return []
    clean = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("t"), (int, float)):
            return []
        try:
            timestamp = max(0.0, float(item["t"]))
        except (TypeError, ValueError, OverflowError):
            return []
        source = item.get("source")
        if source not in {"hotkey", "acoustic", "phrase"}:
            return []
        entry = {"t": timestamp, "source": source}
        if source == "phrase" and str(item.get("phrase") or "").strip():
            entry["phrase"] = str(item["phrase"]).strip()
        clean.append(entry)
    return clean


def save_bookmarks(session_dir: str | Path, bookmarks: list[dict]) -> None:
    """Atomically save sorted bookmarks, collapsing marks within half a second."""
    clean = []
    for item in bookmarks:
        if not isinstance(item, dict) or item.get("source") not in {"hotkey", "acoustic", "phrase"}:
            continue
        try:
            timestamp = max(0.0, float(item.get("t", 0)))
        except (TypeError, ValueError, OverflowError):
            continue
        entry = {"t": timestamp, "source": item["source"]}
        if item["source"] == "phrase" and str(item.get("phrase") or "").strip():
            entry["phrase"] = str(item["phrase"]).strip()
        clean.append(entry)
    clean.sort(key=lambda item: item["t"])
    deduped = []
    for item in clean:
        duplicate = next(
            (existing for existing in deduped
             if (round(existing["t"], 3) == round(item["t"], 3)
                 and existing["source"] == item["source"])
             or (item["source"] != "phrase"
                 and existing["source"] != "phrase"
                 and item["t"] - existing["t"] < 0.5)),
            None,
        )
        if duplicate is None:
            deduped.append(item)
    _write_json(Path(session_dir) / BOOKMARKS_FILE, deduped)


def _write_json(path: Path, value) -> None:
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, indent=2), encoding="utf-8")
    pending.replace(path)


def transcribe_session(
    session_dir: str | Path,
    cfg,
    backend: str = "auto",
) -> dict:
    """Transcribe and merge one captured session.

    This function is synchronous and has no UI dependencies, so callers can
    invoke it directly from their worker thread.
    """
    from . import config
    from .engines.transcribe import transcribe_file, transcribe_file_deepgram

    session_dir = Path(session_dir)
    meta_path = session_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    api_key = config.deepgram_key()
    selected_backend = "deepgram" if backend == "auto" and api_key else backend
    if selected_backend == "auto":
        selected_backend = "local"
    if selected_backend not in {"deepgram", "local"}:
        raise ValueError(f"unsupported meeting transcription backend: {backend}")
    if selected_backend == "deepgram" and not api_key:
        raise ValueError("Deepgram transcription requires DEEPGRAM_API_KEY")

    meta["status"] = "transcribing"
    meta["transcription_backend"] = selected_backend
    meta.pop("transcription_error", None)
    _write_json(meta_path, meta)

    results: dict[str, dict] = {}
    try:
        for stream in ("desktop", "mic"):
            stream_meta = meta.get(stream) or {}
            path = session_dir / stream_meta.get("file", f"{stream}.wav")
            if not _wav_has_frames(path):
                continue
            if selected_backend == "deepgram":
                results[stream] = transcribe_file_deepgram(
                    str(path),
                    api_key=api_key,
                    model=cfg.deepgram_model,
                    diarize=stream == "desktop",
                    language=cfg.language or None,
                )
            else:
                results[stream] = transcribe_file(
                    str(path),
                    language=cfg.language or None,
                    model_size=cfg.local_model_size,
                    device=cfg.local_device,
                    compute_type=cfg.local_compute_type,
                )

        if not results:
            raise ValueError("meeting session has no usable audio streams")

        mic_meta = meta.get("mic") or {}
        desktop_meta = meta.get("desktop") or {}
        segments = merge_transcripts(
            (results.get("mic") or {}).get("segments") or [],
            (results.get("desktop") or {}).get("segments") or [],
            mic_offset=mic_meta.get("first_block_monotonic"),
            desktop_offset=desktop_meta.get("first_block_monotonic"),
        )
        transcript = {
            "backend": selected_backend,
            "text": render_transcript(segments),
            "segments": segments,
        }
        _write_json(session_dir / "transcript.json", transcript)
        # Phrase marks are an overlay on top of all existing user marks.  The
        # existing file is intentionally read after transcription so a repeat
    # Re-running with the SAME backend will not duplicate: dedup keys on
    # (round(t, 3), source). A different backend that shifts a timestamp by a
    # millisecond would add a second mark — unreachable from the UI, which
    # blocks re-transcribing while transcript.json exists.
        phrase_marks = bookmarks_from_phrases(transcript, getattr(cfg, "bookmark_phrases", ""))
        save_bookmarks(session_dir, load_bookmarks(session_dir) + phrase_marks)
    except Exception as exc:
        meta["status"] = "transcription_failed"
        meta["transcription_error"] = f"{type(exc).__name__}: {exc}"
        _write_json(meta_path, meta)
        raise

    meta["status"] = "transcribed"
    meta["transcript_file"] = "transcript.json"
    _write_json(meta_path, meta)
    return transcript


class MeetingRecorder:
    def __init__(
        self,
        base_dir: Path | None = None,
        *,
        device=None,
        max_minutes: int = 240,
        retention_sessions: int = DEFAULT_RETENTION_SESSIONS,
        bookmark_hotkey: str = "",
        bookmark_acoustic: bool = False,
        bookmark_sensitivity: float = 0.5,
        on_auto_stop: Callable[[str], None] | None = None,
    ) -> None:
        if base_dir is None:
            base_dir = meetings_dir()
        self.base_dir = Path(base_dir)
        self.device = device
        self.max_minutes = max_minutes
        self.retention_sessions = max(1, int(retention_sessions))
        self._on_auto_stop = on_auto_stop
        self.bookmark_hotkey = bookmark_hotkey
        self.bookmark_acoustic = bool(bookmark_acoustic)
        self.bookmark_sensitivity = max(0.0, min(1.0, float(bookmark_sensitivity)))
        self.session_dir: Path | None = None

        self._active = False
        self._started_monotonic: float | None = None
        self._started_at = ""
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._waves: dict[str, wave.Wave_write] = {}
        self._wave_locks = {
            "mic": threading.Lock(),
            "desktop": threading.Lock(),
        }
        self._first_blocks: dict[str, float | None] = {
            "mic": None,
            "desktop": None,
        }
        self._last_header_patches = {
            "mic": float("-inf"),
            "desktop": float("-inf"),
        }
        self._errors: dict[str, str | None] = {
            "mic": None,
            "desktop": None,
        }
        self._fatal_errors: dict[str, str | None] = {
            "mic": None,
            "desktop": None,
        }
        self._levels = {"mic": 0.0, "desktop": 0.0}
        self._bleed_desktop_loud = 0
        self._bleed_both_loud = 0
        self.focus_session = None      # set by the app when PipeFocus is on
        self._mic_stream = None
        self._limit_timer: threading.Timer | None = None
        self._stop_reason: str | None = None
        self._state_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._meta_lock = threading.Lock()
        self._heartbeat_lock = threading.Lock()
        self._last_meta_heartbeat = float("-inf")
        self._bookmarks: list[dict] = []
        self._bookmark_lock = threading.Lock()
        self._checkpoint_wakeup = threading.Event()
        self._checkpoint_thread: threading.Thread | None = None
        self._snap_detector = None
        self._pending_acoustic_t: float | None = None
        self._pcm_queue: queue.Queue = queue.Queue(maxsize=PCM_QUEUE_LIMIT)
        self._writer_thread: threading.Thread | None = None
        self.dropped_blocks = 0

    @property
    def active(self) -> bool:
        with self._state_lock:
            return self._active

    @property
    def elapsed(self) -> float:
        with self._state_lock:
            if not self._active or self._started_monotonic is None:
                return 0.0
            started = self._started_monotonic
        return max(0.0, time.monotonic() - started)

    @property
    def errors(self) -> dict[str, str | None]:
        with self._state_lock:
            return dict(self._errors)

    @property
    def fatal_errors(self) -> dict[str, str | None]:
        with self._state_lock:
            return dict(self._fatal_errors)

    @property
    def bleed_suspected(self) -> bool:
        """True when the mic appears to be hearing the speakers, live."""
        with self._state_lock:
            loud = self._bleed_desktop_loud
            both = self._bleed_both_loud
        if loud < BLEED_LIVE_MIN_SAMPLES:
            return False
        return (both / loud) >= BLEED_LIVE_RATIO

    @property
    def levels(self) -> dict[str, float]:
        with self._state_lock:
            return dict(self._levels)

    @property
    def bookmarks(self) -> list[dict]:
        with self._bookmark_lock:
            return [dict(item) for item in self._bookmarks]

    def mark_bookmark(self, source: str = "hotkey") -> bool:
        """Queue a bookmark while recording; disk persistence is checkpointed."""
        if source not in {"hotkey", "acoustic"} or not self.active:
            return False
        return self._append_bookmark(max(0.0, self.elapsed), source)

    def _append_bookmark(self, timestamp: float, source: str) -> bool:
        with self._bookmark_lock:
            if self._bookmarks and timestamp - self._bookmarks[-1]["t"] < 0.5:
                return False
            self._bookmarks.append({"t": timestamp, "source": source})
        self._checkpoint_wakeup.set()
        return True

    def start(self) -> Path:
        with self._lifecycle_lock:
            return self._start()

    def _start(self) -> Path:
        with self._state_lock:
            if self._active:
                if self.session_dir is None:
                    raise RuntimeError("meeting recorder has no session directory")
                return self.session_dir
            if any(thread.is_alive() for thread in self._threads):
                raise RuntimeError("previous meeting capture thread is still stopping")

        # Import both optional dependencies on the caller's thread so App can
        # surface a missing install instead of silently recording empty WAVs.
        import soundcard as sc
        import sounddevice as sd

        self._prune_sessions(reserve=1)
        with self._state_lock:
            self.session_dir = self._create_session_dir()
            self._started_monotonic = time.monotonic()
            self._started_at = datetime.now(timezone.utc).isoformat()
            self._first_blocks = {"mic": None, "desktop": None}
            self._last_header_patches = {
                "mic": float("-inf"),
                "desktop": float("-inf"),
            }
            self._last_meta_heartbeat = time.monotonic()
            self._bookmarks = []
            self._pending_acoustic_t = None
            self._errors = {"mic": None, "desktop": None}
            self._fatal_errors = {"mic": None, "desktop": None}
            self._levels = {"mic": 0.0, "desktop": 0.0}
            # Per RECORDING, not per process: leftover counts from a session
            # on speakers would otherwise condemn the next one on headphones.
            self._bleed_desktop_loud = 0
            self._bleed_both_loud = 0
            self._stop_reason = None
            self._stop.clear()
            self._active = True
        if self.bookmark_acoustic:
            from .snap import SnapDetector
            self._snap_detector = SnapDetector(
                SAMPLE_RATE, sensitivity=self.bookmark_sensitivity
            )
        else:
            self._snap_detector = None

        try:
            self._waves = {
                "mic": self._open_wave(self.session_dir / "mic.wav"),
                "desktop": self._open_wave(self.session_dir / "desktop.wav"),
            }
        except Exception:
            self._close_waves()
            with self._state_lock:
                self._active = False
            raise
        self._write_meta(stopped_at=None, duration=0.0)
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="meeting-writer", daemon=True
        )
        self._writer_thread.start()
        self._checkpoint_thread = threading.Thread(
            target=self._checkpoint_loop, name="meeting-checkpoint", daemon=True
        )
        self._checkpoint_thread.start()

        try:
            self._threads = [
                threading.Thread(
                    target=self._capture_mic,
                    args=(sd,),
                    name="meeting-mic",
                    daemon=True,
                ),
                threading.Thread(
                    target=self._capture_desktop,
                    args=(sc,),
                    name="meeting-desktop",
                    daemon=True,
                ),
            ]
            for thread in self._threads:
                thread.start()
            if self.max_minutes > 0:
                session_dir = self.session_dir
                max_minutes = self.max_minutes
                self._limit_timer = threading.Timer(
                    max_minutes * 60,
                    lambda: self._stop_at_limit(session_dir, max_minutes),
                )
                self._limit_timer.daemon = True
                self._limit_timer.start()
        except Exception:
            self._stop.set()
            timer = self._limit_timer
            self._limit_timer = None
            if timer is not None:
                timer.cancel()
            for thread in self._threads:
                if thread.ident is not None:
                    thread.join(timeout=CAPTURE_JOIN_TIMEOUT)
            self._close_waves()
            with self._state_lock:
                self._active = False
            if not any(thread.is_alive() for thread in self._threads):
                self._threads = []
            raise
        return self.session_dir

    def stop(self, reason: str = "stopped by user") -> Path | None:
        with self._lifecycle_lock:
            return self._stop_recording(reason)

    def _stop_recording(self, reason: str) -> Path | None:
        with self._state_lock:
            if not self._active:
                return self.session_dir
            self._active = False
            started = self._started_monotonic
            self._stop_reason = reason

        self._stop.set()
        self._checkpoint_wakeup.set()
        timer = self._limit_timer
        self._limit_timer = None
        if timer is not None:
            timer.cancel()

        for thread in self._threads:
            thread.join(timeout=CAPTURE_JOIN_TIMEOUT)
        for label, thread in zip(("mic", "desktop"), self._threads):
            if thread.is_alive():
                self._record_error(
                    label,
                    RuntimeError(
                        "capture thread did not stop within "
                        f"{CAPTURE_JOIN_TIMEOUT:g} seconds"
                    ),
                    recoverable=True,
                )

        if self._checkpoint_thread is not None:
            self._checkpoint_thread.join(timeout=1.0)
            self._checkpoint_thread = None

        self._finish_writer()

        self._close_waves()
        if self.session_dir is not None:
            from . import loudness
            loudness.normalize_wav_file(self.session_dir / "mic.wav")
            loudness.normalize_wav_file(self.session_dir / "desktop.wav")
        stopped_at = datetime.now(timezone.utc)
        duration = max(0.0, time.monotonic() - started) if started is not None else 0.0
        self._write_meta(stopped_at.isoformat(), duration)
        pending = self._pending_acoustic_t
        self._pending_acoustic_t = None
        if pending is not None:
            self._append_bookmark(pending, "acoustic")
        with self._bookmark_lock:
            bookmarks = list(self._bookmarks)
        if self.session_dir is not None:
            save_bookmarks(self.session_dir, bookmarks)
        if not any(thread.is_alive() for thread in self._threads):
            self._threads = []
        self._mic_stream = None
        return self.session_dir

    def _checkpoint_loop(self) -> None:
        while not self._stop.is_set():
            self._checkpoint_wakeup.wait(HEADER_PATCH_INTERVAL)
            self._checkpoint_wakeup.clear()
            self._checkpoint_once()

    def _checkpoint_once(self) -> None:
        """One checkpoint: playable WAV headers, a fresh meta, saved bookmarks.

        Split out from the loop so a test can drive the real thing instead of
        the write path — the header patch and the meta heartbeat both used to
        hang off _write_block, which is the audio callback.
        """
        self._patch_headers()
        if self.session_dir is None:
            return
        self._write_meta(stopped_at=None, duration=self.elapsed)
        with self._bookmark_lock:
            bookmarks = list(self._bookmarks)
        pending = self._pending_acoustic_t
        self._pending_acoustic_t = None
        if pending is not None:
            self._append_bookmark(pending, "acoustic")
            with self._bookmark_lock:
                bookmarks = list(self._bookmarks)
        save_bookmarks(self.session_dir, bookmarks)

    def _stop_at_limit(
        self,
        session_dir: Path | None,
        max_minutes: int,
    ) -> None:
        with self._state_lock:
            if not self._active or self.session_dir != session_dir:
                return
        reason = f"maximum session length reached ({max_minutes} minutes)"
        self.stop(reason=reason)
        with self._state_lock:
            stopped_for_limit = self._stop_reason == reason
        if stopped_for_limit and self._on_auto_stop is not None:
            self._on_auto_stop(reason)

    def _create_session_dir(self) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        stem = datetime.now().strftime("meeting-%Y%m%d-%H%M%S")
        path = self.base_dir / stem
        suffix = 2
        while True:
            try:
                path.mkdir()
                return path
            except FileExistsError:
                path = self.base_dir / f"{stem}-{suffix}"
                suffix += 1

    def _prune_sessions(self, reserve: int = 0) -> None:
        """Keep room for the configured number of newest meeting sessions."""
        try:
            sessions = [
                path
                for path in self.base_dir.glob("meeting-*")
                if path.is_dir()
            ]
            def recording_time(path: Path) -> tuple[float, str]:
                try:
                    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
                    started_at = meta.get("started_at") if isinstance(meta, dict) else None
                    if started_at:
                        return (
                            datetime.fromisoformat(
                                str(started_at).replace("Z", "+00:00")
                            ).timestamp(),
                            path.name,
                        )
                except (OSError, ValueError, TypeError, OverflowError):
                    pass
                return (path.stat().st_mtime, path.name)

            sessions.sort(key=recording_time, reverse=True)
            keep = max(0, self.retention_sessions - reserve)
            for path in sessions[keep:]:
                try:
                    shutil.rmtree(path)
                except Exception:
                    # One locked session must not block pruning the rest.
                    pass
        except Exception:
            # Retention must never prevent recording.
            pass

    @staticmethod
    def _open_wave(path: Path) -> wave.Wave_write:
        output = wave.open(str(path), "wb")
        output.setparams(
            (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE, 0, "NONE", "not compressed")
        )
        return output

    def _capture_mic(self, sd) -> None:
        stream = None
        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=MIC_BLOCKSIZE,
                callback=self._on_mic_block,
                device=self.device,
            )
            self._mic_stream = stream
            if self._stop.is_set():
                return
            stream.start()
            while not self._stop.wait(0.25):
                if not stream.active:
                    raise RuntimeError("microphone input stream became inactive")
        except Exception as exc:
            self._record_error("mic", exc)
        finally:
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            if self._mic_stream is stream:
                self._mic_stream = None

    def _on_mic_block(self, indata, _frames, _time_info, _status) -> None:
        if _status:
            self._record_error(
                "mic",
                RuntimeError(str(_status)),
                recoverable=True,
            )
        detector = self._snap_detector
        if detector is not None and detector.feed(indata):
            # Only hand a scalar to the checkpoint thread. In particular, this
            # callback never allocates a bookmark dict or performs JSON I/O.
            self._pending_acoustic_t = max(0.0, self.elapsed)
            self._checkpoint_wakeup.set()
        self._enqueue("mic", indata)

    def _capture_desktop(self, sc) -> None:
        com_initialized = False
        try:
            if sys.platform == "win32":
                import ctypes

                result = ctypes.windll.ole32.CoInitializeEx(None, 0)
                if result not in (0, 1):
                    raise OSError(
                        "CoInitializeEx failed with HRESULT "
                        f"0x{result & 0xffffffff:08x}"
                    )
                com_initialized = True
            speaker = sc.default_speaker()
            if speaker is None:
                raise RuntimeError("Windows has no default speaker")
            loopbacks = [
                device
                for device in sc.all_microphones(include_loopback=True)
                if getattr(device, "isloopback", False)
            ]
            device = next(
                (item for item in loopbacks if item.id == speaker.id),
                None,
            )
            if device is None:
                raise RuntimeError("no loopback endpoint matches the default speaker")

            # Never access device.name or call soundcard.get_microphone(): both
            # walk an unsafe name lookup in soundcard 0.4.6. IDs are plain data.
            with device.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS) as recorder:
                while not self._stop.is_set():
                    block = recorder.record(numframes=DESKTOP_BLOCKSIZE)
                    self._enqueue("desktop", block)
        except Exception as exc:
            self._record_error("desktop", exc)
        finally:
            if com_initialized:
                ctypes.windll.ole32.CoUninitialize()

    def _enqueue(self, label: str, block) -> None:
        """Hand a block to the writer thread. Safe from an audio callback.

        This is all the mic's PortAudio callback is allowed to do. It must not
        write, seek, flush, or wait on a lock that a slow thing holds — the
        work the callback does not finish in time is audio PortAudio throws
        away, and it says so as `input overflow`.

        The copy is mandatory: sounddevice hands out a VIEW over a buffer it
        reuses for the next block, so queueing it uncopied would give the
        writer audio that changes underneath it.
        """
        try:
            import numpy as np

            self._pcm_queue.put_nowait(
                (label, np.array(block, dtype=np.float32, copy=True))
            )
        except queue.Full:
            # Dropping a block is bad. Blocking the callback is worse: that is
            # how the stream goes inactive and takes the whole capture with it.
            self.dropped_blocks += 1
        except Exception as exc:
            self._record_error(label, exc, recoverable=True)

    def _writer_loop(self) -> None:
        """Sole owner of the WAV files. Everything slow happens here."""
        while True:
            try:
                label, block = self._pcm_queue.get(timeout=0.25)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            self._write_block(label, block)

    def _finish_writer(self) -> None:
        """Stop the writer, then write the tail — never both at the same time.

        The tail of the recording is still queued at stop, so it has to be
        written before the files close or every meeting loses its last second.
        But draining here while the writer is STILL GOING puts two threads on
        one WAV: the wave lock keeps each write intact and does nothing about
        the order they land in, so blocks would interleave. If the writer will
        not stop, the tail is its problem, not ours.
        """
        writer, self._writer_thread = self._writer_thread, None
        if writer is not None:
            writer.join(timeout=CAPTURE_JOIN_TIMEOUT + 1.0)
            if writer.is_alive():
                self._record_error(
                    "mic",
                    RuntimeError(
                        "writer thread did not stop within "
                        f"{CAPTURE_JOIN_TIMEOUT + 1.0:g} seconds"
                    ),
                    recoverable=True,
                )
                return
        self._drain_queue()

    def _drain_queue(self) -> None:
        """Write what is still in hand, before the files get closed."""
        while True:
            try:
                label, block = self._pcm_queue.get_nowait()
            except queue.Empty:
                return
            self._write_block(label, block)

    def _write_block(self, label: str, block, *, realtime: bool = False) -> None:
        """Append one block. Runs on the writer thread, never on an audio
        callback — see _enqueue()."""
        arrived_at = time.monotonic()
        try:
            import numpy as np

            data = np.asarray(block, dtype=np.float32).reshape(-1)
            if not data.size:
                return
            rms = float(np.sqrt(np.mean(data ** 2)))
            with self._state_lock:
                self._levels[label] = smooth_level(self._levels[label], rms)
                # Live speaker-bleed detection. On headphones the two streams go
                # loud at DIFFERENT times, because people take turns. Through
                # speakers the mic is loud WHENEVER the desktop is, since it is
                # hearing the far end. Counting that overlap catches it in the
                # first half-minute, while putting headphones on can still save
                # the recording — telling the user afterwards is too late.
                if label == "desktop" and rms >= BLEED_LIVE_LEVEL:
                    self._bleed_desktop_loud += 1
                    if self._levels.get("mic", 0.0) >= BLEED_LIVE_LEVEL:
                        self._bleed_both_loud += 1
            pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            # PipeFocus, if running. feed() only ENQUEUES and swallows its own
            # errors, so nothing here can stall or break the capture callback —
            # losing the recording to a focus problem would be indefensible.
            focus_session = self.focus_session
            if focus_session is not None:
                focus_session.feed(label, pcm)
            first_block = False
            with self._wave_locks[label]:
                output = self._waves.get(label)
                if output is None:
                    return
                if self._first_blocks[label] is None:
                    self._first_blocks[label] = arrived_at
                    first_block = True
                output.writeframesraw(pcm)
        except Exception as exc:
            self._record_error(label, exc)
            return
        # Patch as soon as there IS something to patch, not up to five seconds
        # later. The old code got this for free by patching from the first
        # write; waiting for the checkpoint tick would leave a crash in the
        # first few seconds with real PCM behind a zero-frame header. Outside
        # the lock above — _patch_headers takes it again.
        if first_block:
            self._patch_headers()

    def _patch_headers(self) -> None:
        """Make the in-progress WAVs playable, from the checkpoint thread.

        This used to run inside the mic's PortAudio callback: every 5 seconds it
        seeked to byte 0, rewrote the RIFF header, seeked back to the end and
        flushed. A blocking disk seek in a real-time audio callback is the
        textbook cause of `input overflow` — PortAudio drops the input it could
        not hand over in time — and James's log shows exactly that on
        2026-07-30, twice, the second one followed two seconds later by the
        stream going inactive and killing the meeting capture.

        The crash-safety this buys is unchanged: the same patch, at the same
        5-second cadence, just on the thread that was already waking up for it.
        """
        for label in tuple(self._waves):
            try:
                with self._wave_locks[label]:
                    output = self._waves.get(label)
                    # Nothing written yet means no header to patch, and
                    # _patchheader() asserts rather than tolerating that. The
                    # checkpoint thread starts with the capture, so it can and
                    # does arrive before the first block.
                    if output is None or self._first_blocks[label] is None:
                        continue
                    output._patchheader()
                    output._file.flush()
                    self._last_header_patches[label] = time.monotonic()
            except Exception as exc:
                self._record_error(label, exc)

    def _record_error(
        self,
        label: str,
        exc: Exception,
        *,
        recoverable: bool = False,
    ) -> None:
        message = f"{type(exc).__name__}: {exc}"
        with self._state_lock:
            if recoverable:
                if self._errors[label] is None:
                    self._errors[label] = message
            elif self._fatal_errors[label] is None:
                # A fatal failure supersedes an earlier recoverable warning.
                self._fatal_errors[label] = message
                self._errors[label] = message

    def _close_waves(self) -> None:
        for label in tuple(self._waves):
            with self._wave_locks[label]:
                output = self._waves.pop(label, None)
                if output is None:
                    continue
                try:
                    output.close()
                except Exception as exc:
                    self._record_error(label, exc)

    def _write_meta(self, stopped_at: str | None, duration: float) -> None:
        if self.session_dir is None:
            return
        recorder_meta = {
            "started_at": self._started_at,
            "stopped_at": stopped_at,
            "duration_seconds": duration,
            "stop_reason": self._stop_reason,
            "recording_pid": os.getpid(),
            # Blocks the writer could not keep up with. 0 is the whole point:
            # without it a wav that ends a second short of the clock cannot be
            # told apart from a second of device-open latency, and "did the
            # overflow fix hold?" stays an inference instead of a fact.
            "dropped_blocks": self.dropped_blocks,
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "mic": {
                "file": "mic.wav",
                "first_block_monotonic": self._first_blocks["mic"],
                "error": self._fatal_errors["mic"],
            },
            "desktop": {
                "file": "desktop.wav",
                "first_block_monotonic": self._first_blocks["desktop"],
                "error": self._fatal_errors["desktop"],
            },
        }
        try:
            with self._meta_lock:
                path = self.session_dir / "meta.json"
                pending = self.session_dir / "meta.json.tmp"
                try:
                    meta = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(meta, dict):
                        meta = {}
                except (OSError, ValueError, TypeError):
                    meta = {}
                if stopped_at is None and meta.get("stopped_at"):
                    return
                meta.update(recorder_meta)
                pending.write_text(json.dumps(meta, indent=2), encoding="utf-8")
                pending.replace(path)
        except Exception:
            pass
