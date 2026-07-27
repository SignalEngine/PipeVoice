"""Incremental microphone + desktop capture for meeting recordings.

The two inputs deliberately use separate audio libraries and own separate WAV
writers. Optional audio dependencies are imported when capture starts so this
module remains importable on non-Windows development machines while missing
packages still fail synchronously.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2
MIC_BLOCKSIZE = 800
DESKTOP_BLOCKSIZE = 1_600
CAPTURE_JOIN_TIMEOUT = 3.0


class MeetingRecorder:
    def __init__(
        self,
        base_dir: Path | None = None,
        *,
        device=None,
        max_minutes: int = 240,
        on_auto_stop: Callable[[str], None] | None = None,
    ) -> None:
        if base_dir is None:
            from .config import config_dir

            base_dir = config_dir() / "meetings"
        self.base_dir = Path(base_dir)
        self.device = device
        self.max_minutes = max_minutes
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
        self._errors: dict[str, str | None] = {
            "mic": None,
            "desktop": None,
        }
        self._mic_stream = None
        self._limit_timer: threading.Timer | None = None
        self._stop_reason: str | None = None
        self._state_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()

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

        with self._state_lock:
            self.session_dir = self._create_session_dir()
            self._started_monotonic = time.monotonic()
            self._started_at = datetime.now(timezone.utc).isoformat()
            self._first_blocks = {"mic": None, "desktop": None}
            self._errors = {"mic": None, "desktop": None}
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
        with self._state_lock:
            if self._errors[label] is None:
                self._errors[label] = f"{type(exc).__name__}: {exc}"

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

    def _write_meta(self, stopped_at: str, duration: float) -> None:
        if self.session_dir is None:
            return
        meta = {
            "started_at": self._started_at,
            "stopped_at": stopped_at,
            "duration_seconds": duration,
            "stop_reason": self._stop_reason,
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
