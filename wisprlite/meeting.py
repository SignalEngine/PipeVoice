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
import shutil
import sys
import threading
import tempfile
import time
import wave
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2
MIC_BLOCKSIZE = 800
DESKTOP_BLOCKSIZE = 1_600
CAPTURE_JOIN_TIMEOUT = 3.0
HEADER_PATCH_INTERVAL = 5.0
DEFAULT_RETENTION_SESSIONS = 20
SPEAKER_MAP_FILE = "speaker_map.json"


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


def smooth_level(current: float, rms: float) -> float:
    """Apply the recorder's fast-attack, slow-release level smoothing."""
    return rms if rms > current else current * 0.7


def meetings_dir() -> Path:
    """Return a machine-local recording directory, never the roaming profile."""
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
) -> str:
    """Render consecutive same-speaker segments as plain-text blocks."""
    blocks: list[dict] = []
    for segment in apply_speaker_map(segments, speaker_map):
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


def _write_json(path: Path, value: dict) -> None:
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
        on_auto_stop: Callable[[str], None] | None = None,
    ) -> None:
        if base_dir is None:
            base_dir = meetings_dir()
        self.base_dir = Path(base_dir)
        self.device = device
        self.max_minutes = max_minutes
        self.retention_sessions = max(1, int(retention_sessions))
        self._on_auto_stop = on_auto_stop
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
        self._mic_stream = None
        self._limit_timer: threading.Timer | None = None
        self._stop_reason: str | None = None
        self._state_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._meta_lock = threading.Lock()
        self._heartbeat_lock = threading.Lock()
        self._last_meta_heartbeat = float("-inf")

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
    def levels(self) -> dict[str, float]:
        with self._state_lock:
            return dict(self._levels)

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
            self._errors = {"mic": None, "desktop": None}
            self._fatal_errors = {"mic": None, "desktop": None}
            self._levels = {"mic": 0.0, "desktop": 0.0}
            self._stop_reason = None
            self._stop.clear()
            self._active = True

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

        self._close_waves()
        stopped_at = datetime.now(timezone.utc)
        duration = max(0.0, time.monotonic() - started) if started is not None else 0.0
        self._write_meta(stopped_at.isoformat(), duration)
        if not any(thread.is_alive() for thread in self._threads):
            self._threads = []
        self._mic_stream = None
        return self.session_dir

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
        self._write_block("mic", indata)

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
                    self._write_block("desktop", block)
        except Exception as exc:
            self._record_error("desktop", exc)
        finally:
            if com_initialized:
                ctypes.windll.ole32.CoUninitialize()

    def _write_block(self, label: str, block) -> None:
        arrived_at = time.monotonic()
        checkpointed = False
        try:
            import numpy as np

            data = np.asarray(block, dtype=np.float32).reshape(-1)
            if not data.size:
                return
            rms = float(np.sqrt(np.mean(data ** 2)))
            with self._state_lock:
                self._levels[label] = smooth_level(self._levels[label], rms)
            pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            with self._wave_locks[label]:
                output = self._waves.get(label)
                if output is None:
                    return
                if self._first_blocks[label] is None:
                    self._first_blocks[label] = arrived_at
                output.writeframesraw(pcm)
                if (
                    arrived_at - self._last_header_patches[label]
                    >= HEADER_PATCH_INTERVAL
                ):
                    output._patchheader()
                    output._file.flush()
                    self._last_header_patches[label] = arrived_at
                    checkpointed = True
        except Exception as exc:
            self._record_error(label, exc)
        # Either stream can be the one still alive after the other fails. Keep
        # JSON I/O out of the realtime callback and share one heartbeat budget
        # so two healthy streams do not double the metadata writes.
        if checkpointed and not self._stop.is_set():
            with self._heartbeat_lock:
                if arrived_at - self._last_meta_heartbeat >= HEADER_PATCH_INTERVAL:
                    self._last_meta_heartbeat = arrived_at
                    self._write_meta(stopped_at=None, duration=self.elapsed)

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
