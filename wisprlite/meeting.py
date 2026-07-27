"""Incremental microphone + desktop capture for meeting recordings.

The two inputs deliberately use separate audio libraries and own separate WAV
writers.  Optional audio dependencies are imported only inside their capture
threads so this module remains importable on non-Windows development machines.
"""

from __future__ import annotations

import json
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2
MIC_BLOCKSIZE = 800
DESKTOP_BLOCKSIZE = 1_600


class MeetingRecorder:
    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            from .config import config_dir

            base_dir = config_dir() / "meetings"
        self.base_dir = Path(base_dir)
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
        self._errors: dict[str, str | None] = {
            "mic": None,
            "desktop": None,
        }
        self._mic_stream = None
        self._state_lock = threading.Lock()

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

    def start(self) -> Path:
        with self._state_lock:
            if self._active:
                if self.session_dir is None:
                    raise RuntimeError("meeting recorder has no session directory")
                return self.session_dir

            self.session_dir = self._create_session_dir()
            self._started_monotonic = time.monotonic()
            self._started_at = datetime.now(timezone.utc).isoformat()
            self._first_blocks = {"mic": None, "desktop": None}
            self._errors = {"mic": None, "desktop": None}
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

        self._threads = [
            threading.Thread(target=self._capture_mic, name="meeting-mic", daemon=True),
            threading.Thread(
                target=self._capture_desktop,
                name="meeting-desktop",
                daemon=True,
            ),
        ]
        for thread in self._threads:
            thread.start()
        return self.session_dir

    def stop(self) -> Path | None:
        with self._state_lock:
            if not self._active:
                return self.session_dir
            self._active = False
            started = self._started_monotonic

        self._stop.set()
        stream = self._mic_stream
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

        for thread in self._threads:
            thread.join(timeout=3.0)
        for label, thread in zip(("mic", "desktop"), self._threads):
            if thread.is_alive() and self._errors[label] is None:
                self._errors[label] = "capture thread did not stop within 3 seconds"

        self._close_waves()
        stopped_at = datetime.now(timezone.utc)
        duration = max(0.0, time.monotonic() - started) if started is not None else 0.0
        self._write_meta(stopped_at.isoformat(), duration)
        self._threads = []
        self._mic_stream = None
        return self.session_dir

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

    @staticmethod
    def _open_wave(path: Path) -> wave.Wave_write:
        output = wave.open(str(path), "wb")
        output.setparams(
            (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE, 0, "NONE", "not compressed")
        )
        return output

    def _capture_mic(self) -> None:
        try:
            import sounddevice as sd

            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=MIC_BLOCKSIZE,
                callback=self._on_mic_block,
            )
            self._mic_stream = stream
            if self._stop.is_set():
                stream.close()
                return
            stream.start()
            self._stop.wait()
        except Exception as exc:
            self._record_error("mic", exc)
        finally:
            stream = self._mic_stream
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            self._mic_stream = None

    def _on_mic_block(self, indata, _frames, _time_info, _status) -> None:
        self._write_block("mic", indata)

    def _capture_desktop(self) -> None:
        try:
            import soundcard as sc

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

    def _write_block(self, label: str, block) -> None:
        arrived_at = time.monotonic()
        try:
            import numpy as np

            data = np.asarray(block, dtype=np.float32).reshape(-1)
            if not data.size:
                return
            pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            with self._wave_locks[label]:
                output = self._waves.get(label)
                if output is None:
                    return
                if self._first_blocks[label] is None:
                    self._first_blocks[label] = arrived_at
                output.writeframesraw(pcm)
        except Exception as exc:
            self._record_error(label, exc)

    def _record_error(self, label: str, exc: Exception) -> None:
        if self._errors[label] is None:
            self._errors[label] = f"{type(exc).__name__}: {exc}"

    def _close_waves(self) -> None:
        for label, output in list(self._waves.items()):
            with self._wave_locks[label]:
                try:
                    output.close()
                except Exception as exc:
                    self._record_error(label, exc)
        self._waves = {}

    def _write_meta(self, stopped_at: str, duration: float) -> None:
        if self.session_dir is None:
            return
        meta = {
            "started_at": self._started_at,
            "stopped_at": stopped_at,
            "duration_seconds": duration,
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "mic": {
                "file": "mic.wav",
                "first_block_monotonic": self._first_blocks["mic"],
                "error": self._errors["mic"],
            },
            "desktop": {
                "file": "desktop.wav",
                "first_block_monotonic": self._first_blocks["desktop"],
                "error": self._errors["desktop"],
            },
        }
        try:
            (self.session_dir / "meta.json").write_text(
                json.dumps(meta, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
