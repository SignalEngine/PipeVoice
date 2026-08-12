"""Record a region of the screen with narration, then hand it to an agent.

The point is a bug report you can SHOW: drag a box round the thing, say what is
wrong, and have the clip land in the working directory of whatever coding agent
is on the other end of your SSH session.

Nothing here runs unless the user sets a hotkey.

Two writers, muxed at the end, deliberately: one container fed by a frame
grabber and an audio callback on two different clocks is where this class of
feature goes wrong, and a crash mid-record still leaves both raw files on disk.

Spec: docs/specs/2026-08-12-screen-recorder.md
"""

from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

log = logging.getLogger("wisprlite")

AUDIO_RATE = 16_000
AUDIO_CHANNELS = 1
AUDIO_WIDTH = 2
DEFAULT_FPS = 12
# ~30s of audio in hand before dropping. The mic callback must never block —
# see feedback_audio_callback_must_not_touch_disk.
AUDIO_QUEUE_LIMIT = 2_000


def default_output_dir() -> Path:
    return Path.home() / "Videos" / "PipeVoice"


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H-%M-%S")


class ScreenRecording:
    """One recording: grabs frames, records the mic, muxes an mp4 at stop.

    `region` is (left, top, width, height) in virtual-desktop coordinates.
    """

    def __init__(self, region: tuple[int, int, int, int], out_dir: Path,
                 *, fps: int = DEFAULT_FPS, device=None) -> None:
        left, top, width, height = region
        # x264 requires even dimensions for yuv420p. Round DOWN so the capture
        # never reaches outside the box the user actually drew.
        self.region = (left, top, width - (width % 2), height - (height % 2))
        self.fps = max(1, int(fps))
        self.device = device
        self.out_dir = Path(out_dir)
        self.stem = _stamp()

        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._audio_queue: queue.Queue = queue.Queue(maxsize=AUDIO_QUEUE_LIMIT)
        self._wave: wave.Wave_write | None = None
        self._wave_lock = threading.Lock()
        self._mic_stream = None
        self.errors: list[str] = []
        self.dropped_audio_blocks = 0
        self.frames_written = 0

    # -- paths ---------------------------------------------------------------

    @property
    def video_path(self) -> Path:
        return self.out_dir / f"{self.stem}.mp4"

    @property
    def audio_path(self) -> Path:
        return self.out_dir / f"{self.stem}.wav"

    @property
    def transcript_path(self) -> Path:
        return self.out_dir / f"{self.stem}.txt"

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._wave = wave.open(str(self.audio_path), "wb")
        self._wave.setparams(
            (AUDIO_CHANNELS, AUDIO_WIDTH, AUDIO_RATE, 0, "NONE", "not compressed")
        )
        self._threads = [
            threading.Thread(target=self._grab_loop, name="screenrec-video", daemon=True),
            threading.Thread(target=self._audio_writer, name="screenrec-audio", daemon=True),
            threading.Thread(target=self._capture_mic, name="screenrec-mic", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self, timeout: float = 5.0) -> Path | None:
        """Stop, mux, and return the finished mp4 (None if nothing was captured)."""
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._close_mic()
        self._drain_audio()
        with self._wave_lock:
            if self._wave is not None:
                try:
                    self._wave.close()
                except Exception as exc:
                    self._record_error(exc)
                self._wave = None
        if not self.frames_written:
            self._record_error(RuntimeError("no frames captured"))
            return None
        return self._mux()

    def _record_error(self, exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}"
        self.errors.append(message)
        log.info("screenrec: %s", message)

    # -- video ---------------------------------------------------------------

    def _grab_loop(self) -> None:
        try:
            import mss
            import numpy as np

            left, top, width, height = self.region
            box = {"left": left, "top": top, "width": width, "height": height}
            interval = 1.0 / self.fps
            with mss.mss() as sct:
                self._frames: list = []
                next_at = time.monotonic()
                while not self._stop.is_set():
                    shot = sct.grab(box)
                    # BGRA -> RGB, dropping alpha. np.asarray on the raw buffer
                    # is a view, so copy: the next grab reuses it.
                    frame = np.array(shot, dtype=np.uint8)[:, :, :3][:, :, ::-1]
                    self._frames.append(frame.copy())
                    self.frames_written += 1
                    next_at += interval
                    sleep_for = next_at - time.monotonic()
                    if sleep_for > 0:
                        if self._stop.wait(sleep_for):
                            return
                    else:
                        # Falling behind: give up the missed slots rather than
                        # sprinting to catch up and stealing the CPU from the
                        # thing being demonstrated.
                        next_at = time.monotonic()
        except Exception as exc:
            self._record_error(exc)

    # -- audio ---------------------------------------------------------------

    def _capture_mic(self) -> None:
        stream = None
        try:
            import sounddevice as sd

            stream = sd.InputStream(
                samplerate=AUDIO_RATE,
                channels=AUDIO_CHANNELS,
                dtype="float32",
                blocksize=800,
                callback=self._on_mic_block,
                device=self.device,
            )
            self._mic_stream = stream
            stream.start()
            while not self._stop.wait(0.25):
                if not stream.active:
                    raise RuntimeError("microphone input stream became inactive")
        except Exception as exc:
            self._record_error(exc)
        finally:
            if stream is not None:
                for step in (stream.stop, stream.close):
                    try:
                        step()
                    except Exception:
                        pass
            self._mic_stream = None

    def _on_mic_block(self, indata, _frames, _time_info, _status) -> None:
        """Runs on the PortAudio thread. Copy, enqueue, return — nothing else."""
        try:
            import numpy as np

            self._audio_queue.put_nowait(
                np.array(indata, dtype="float32", copy=True)
            )
        except queue.Full:
            self.dropped_audio_blocks += 1
        except Exception:
            pass                      # never raise inside the audio callback

    def _audio_writer(self) -> None:
        while True:
            try:
                block = self._audio_queue.get(timeout=0.25)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            self._write_audio(block)

    def _drain_audio(self) -> None:
        while True:
            try:
                block = self._audio_queue.get_nowait()
            except queue.Empty:
                return
            self._write_audio(block)

    def _write_audio(self, block) -> None:
        try:
            import numpy as np

            data = np.asarray(block, dtype="float32").reshape(-1)
            pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            with self._wave_lock:
                if self._wave is None:
                    return
                self._wave.writeframesraw(pcm)
        except Exception as exc:
            self._record_error(exc)

    def _close_mic(self) -> None:
        stream, self._mic_stream = self._mic_stream, None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass

    # -- mux -----------------------------------------------------------------

    def _mux(self) -> Path | None:
        """Write the frames and the narration into one mp4."""
        try:
            import av
            import numpy as np

            frames = getattr(self, "_frames", [])
            if not frames:
                return None
            height, width = frames[0].shape[:2]
            container = av.open(str(self.video_path), mode="w")
            try:
                video = container.add_stream("libx264", rate=self.fps)
                video.width, video.height = width, height
                video.pix_fmt = "yuv420p"
                # Small enough to send over a phone tether, still readable text.
                video.options = {"crf": "28", "preset": "veryfast"}

                audio = None
                samples = self._read_wav()
                if samples is not None and samples.size:
                    audio = container.add_stream("aac", rate=AUDIO_RATE)
                    audio.layout = "mono"

                for frame in frames:
                    picture = av.VideoFrame.from_ndarray(frame, format="rgb24")
                    for packet in video.encode(picture):
                        container.mux(packet)
                for packet in video.encode():
                    container.mux(packet)

                if audio is not None:
                    frame = av.AudioFrame.from_ndarray(
                        samples.reshape(1, -1), format="s16", layout="mono"
                    )
                    frame.sample_rate = AUDIO_RATE
                    for packet in audio.encode(frame):
                        container.mux(packet)
                    for packet in audio.encode():
                        container.mux(packet)
            finally:
                container.close()
            return self.video_path
        except Exception as exc:
            self._record_error(exc)
            return None

    def _read_wav(self):
        try:
            import numpy as np

            with wave.open(str(self.audio_path), "rb") as handle:
                raw = handle.readframes(handle.getnframes())
            return np.frombuffer(raw, dtype="<i2")
        except Exception:
            return None


def send(paths, destination: str, *, timeout: float = 300.0) -> tuple[bool, str]:
    """scp the files to `destination`. Returns (ok, message).

    Shells out to the scp that ships with Windows so the user's own keys, agent
    and ~/.ssh/config apply. PipeVoice never reads, stores or transmits a
    private key — there is nothing here for it to leak.

    -B is batch mode: no key means an immediate failure instead of a hang on an
    invisible password prompt that nobody can see or answer from a tray app.
    """
    files = [str(p) for p in paths if p and Path(p).exists()]
    if not files:
        return False, "nothing to send"
    destination = (destination or "").strip()
    if not destination:
        return False, "no destination configured"
    scp = shutil.which("scp")
    if not scp:
        return False, "scp not found — install the Windows OpenSSH client"
    try:
        done = subprocess.run(
            [scp, "-B", *files, destination],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"scp timed out after {timeout:g}s"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return False, detail[-1] if detail else f"scp exited {done.returncode}"
    return True, f"sent {len(files)} file(s)"


def select_region(root=None) -> tuple[int, int, int, int] | None:
    """Dim the screen, let the user drag a box, return it. Esc / a click cancels.

    Sized to the whole VIRTUAL desktop, not one monitor, so a box can be drawn
    on any screen and the coordinates still line up with what mss grabs.
    """
    import tkinter as tk

    owns_root = root is None
    if owns_root:
        root = tk.Tk()
        root.withdraw()

    top = tk.Toplevel(root)
    top.overrideredirect(True)
    top.configure(bg="#000000")
    try:
        top.attributes("-alpha", 0.28)
        top.attributes("-topmost", True)
    except Exception:
        pass
    width = root.winfo_screenwidth()
    height = root.winfo_screenheight()
    top.geometry(f"{width}x{height}+0+0")

    canvas = tk.Canvas(top, cursor="crosshair", bg="#000000", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        width // 2, 40,
        text="Drag over what you want to record  ·  Esc to cancel",
        fill="#e6e8eb", font=("Segoe UI", 15, "bold"),
    )

    state = {"x": 0, "y": 0, "box": None, "result": None}

    def press(event):
        state["x"], state["y"] = event.x, event.y
        if state["box"] is not None:
            canvas.delete(state["box"])
        state["box"] = canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#e06c75", width=2)

    def drag(event):
        if state["box"] is not None:
            canvas.coords(state["box"], state["x"], state["y"], event.x, event.y)

    def release(event):
        left, top_ = min(state["x"], event.x), min(state["y"], event.y)
        w, h = abs(event.x - state["x"]), abs(event.y - state["y"])
        # A stray click is a cancel, not a 3x2 recording.
        state["result"] = (left, top_, w, h) if w >= 16 and h >= 16 else None
        top.destroy()

    def cancel(_event=None):
        state["result"] = None
        top.destroy()

    canvas.bind("<Button-1>", press)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", release)
    top.bind("<Escape>", cancel)
    top.focus_force()
    top.grab_set()
    top.wait_window()
    if owns_root:
        try:
            root.destroy()
        except Exception:
            pass
    return state["result"]
