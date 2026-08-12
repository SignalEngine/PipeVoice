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
# Shutdown waits longer than this, so quitting never kills a live upload.
UPLOAD_TIMEOUT = 300.0


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
        self._container = None
        self._video_stream = None
        self._audio_stream = None
        self._encode_lock = threading.Lock()
        self.errors: list[str] = []
        self.dropped_audio_blocks = 0
        self.frames_written = 0
        # The mic opens faster than mss + the x264 container, so audio starts
        # first. Measured on a real 12s clip: 12.46s of audio against 11.42s of
        # video, both starting at 0 — the narration ran a second ahead of the
        # picture for the whole recording. Recording both instants lets the mux
        # drop that leading second instead of guessing.
        self.first_frame_at: float | None = None
        self.first_audio_at: float | None = None

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

    def rename(self, stem: str) -> None:
        """Give the finished files a new stem, keeping every extension."""
        stem = (stem or "").strip()
        if not stem or stem == self.stem:
            return
        suffixes = (".mp4", ".wav", ".txt")
        # Any of the three colliding means the name is taken. Checking only the
        # .mp4 would silently overwrite an earlier recording's wav or transcript.
        if any((self.out_dir / f"{stem}{suffix}").exists() for suffix in suffixes):
            stem = f"{stem} ({self.stem})"
        done = []
        for suffix in suffixes:
            source = self.out_dir / f"{self.stem}{suffix}"
            if not source.exists():
                continue
            target = self.out_dir / f"{stem}{suffix}"
            try:
                source.rename(target)
            except OSError as exc:
                # All three files or none. A half-rename splits one recording
                # across two names and leaves video_path — derived from stem —
                # pointing at a file that is not there.
                self._record_error(exc)
                for moved_target, moved_source in reversed(done):
                    try:
                        moved_target.rename(moved_source)
                    except OSError:
                        pass
                return
            done.append((target, source))
        self.stem = stem

    def _record_error(self, exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}"
        self.errors.append(message)
        log.info("screenrec: %s", message)

    # -- video ---------------------------------------------------------------

    def _open_container(self, width: int, height: int) -> None:
        """Open the mp4 and its video stream, ready to take frames one at a time."""
        import av

        self._container = av.open(str(self.video_path), mode="w")
        stream = self._container.add_stream("libx264", rate=self.fps)
        stream.width, stream.height = width, height
        stream.pix_fmt = "yuv420p"
        # Small enough to send over a phone tether, still readable text.
        stream.options = {"crf": "28", "preset": "veryfast"}
        self._video_stream = stream
        # Both streams MUST be declared before the first packet is muxed — the
        # header is written then, and adding a stream afterwards fails with
        # "Cannot rebase to zero time". The mic is always recorded, so the
        # audio track is always declared, even if it ends up empty.
        audio = self._container.add_stream("aac", rate=AUDIO_RATE)
        audio.layout = "mono"
        self._audio_stream = audio

    def _encode_frame(self, frame) -> None:
        import av

        picture = av.VideoFrame.from_ndarray(frame, format="rgb24")
        for packet in self._video_stream.encode(picture):
            self._container.mux(packet)

    def _grab_loop(self) -> None:
        """Grab, encode, mux — one frame at a time.

        Frames are NEVER accumulated. A 1080p frame is 6 MB of raw RGB, so a
        one-minute recording at 12 fps would be about 4.5 GB held in memory
        waiting to be encoded, and the recording would die before it produced
        anything. Encoding as they arrive keeps memory flat.
        """
        try:
            import mss
            import numpy as np

            left, top, width, height = self.region
            box = {"left": left, "top": top, "width": width, "height": height}
            interval = 1.0 / self.fps
            with mss.mss() as sct:
                next_at = time.monotonic()
                while not self._stop.is_set():
                    shot = sct.grab(box)
                    # BGRA -> RGB, dropping alpha.
                    frame = np.array(shot, dtype=np.uint8)[:, :, :3][:, :, ::-1]
                    with self._encode_lock:
                        if self._container is None:
                            self._open_container(frame.shape[1], frame.shape[0])
                        if self.first_frame_at is None:
                            self.first_frame_at = time.monotonic()
                        self._encode_frame(np.ascontiguousarray(frame))
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
            if not self.frames_written:
                # The container was opened but never took a frame, so stop()
                # skips the mux and leaves an unplayable stub sitting in the
                # user's Videos folder looking like a recording.
                with self._encode_lock:
                    container, self._container = self._container, None
                    self._video_stream = self._audio_stream = None
                try:
                    if container is not None:
                        container.close()
                except Exception:
                    pass
                try:
                    self.video_path.unlink()
                except OSError:
                    pass

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

            if self.first_audio_at is None:
                self.first_audio_at = time.monotonic()
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
        """Flush the video, add the narration, close the file."""
        try:
            import av

            with self._encode_lock:
                container, self._container = self._container, None
                video, self._video_stream = self._video_stream, None
                audio, self._audio_stream = self._audio_stream, None
            if container is None:
                return None
            try:
                for packet in video.encode():          # flush the encoder
                    container.mux(packet)

                samples = self._trim_lead(self._read_wav())
                if audio is not None and samples is not None and samples.size:
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

    def lead_seconds(self) -> float:
        """How long the microphone ran before the first frame was encoded."""
        if self.first_frame_at is None or self.first_audio_at is None:
            return 0.0
        return max(0.0, self.first_frame_at - self.first_audio_at)

    def _trim_lead(self, samples):
        """Drop the audio captured before there was any picture.

        Both streams are muxed starting at zero, so without this the whole
        narration plays ahead of what it is describing — you hear "click this
        button" a second before the cursor moves. Trimming the head is right
        rather than padding the video: the missing picture never existed.

        Only the startup gap is corrected. A capped, sane bound keeps a bad
        clock reading from eating the start of what someone said.
        """
        lead = self.lead_seconds()
        if samples is None or not lead:
            return samples
        drop = min(int(lead * AUDIO_RATE), max(0, samples.size - AUDIO_RATE))
        if drop <= 0:
            return samples
        log.info("screenrec: trimmed %.2fs of audio that preceded the first frame", lead)
        return samples[drop:]

    def _read_wav(self):
        try:
            import numpy as np

            with wave.open(str(self.audio_path), "rb") as handle:
                raw = handle.readframes(handle.getnframes())
            return np.frombuffer(raw, dtype="<i2")
        except Exception:
            return None


def send(paths, destination: str, *, timeout: float = UPLOAD_TIMEOUT) -> tuple[bool, str]:
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


def _offset(value: int) -> str:
    """Tk geometry wants `-1920`, not `+-1920`.

    A monitor positioned left of the primary gives a negative origin, and the
    naive f"+{x}" produces `+-1920`, which raises TclError — so the selector
    would not open at all on exactly the setup the virtual-desktop handling
    was added for.
    """
    return f"+{value}" if value >= 0 else str(value)


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
    # mss reports the union of every monitor as monitors[0]. Tk only knows the
    # primary screen and origin 0,0, so a display positioned LEFT of the primary
    # (negative x) could not be covered or selected at all.
    origin_x, origin_y = 0, 0
    width = root.winfo_screenwidth()
    height = root.winfo_screenheight()
    try:
        import mss

        with mss.mss() as sct:
            desktop = sct.monitors[0]
        origin_x, origin_y = desktop["left"], desktop["top"]
        width, height = desktop["width"], desktop["height"]
    except Exception:
        pass
    top.geometry(f"{width}x{height}{_offset(origin_x)}{_offset(origin_y)}")

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
        left = min(state["x"], event.x) + origin_x
        top_ = min(state["y"], event.y) + origin_y
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


# Windows forbids these outright; a remote inbox is no place for them either.
_ILLEGAL = '<>:"/\\|?*'


def safe_name(text: str, *, limit: int = 60) -> str:
    """Turn what the user typed into something both Windows and scp accept."""
    cleaned = "".join(
        " " if ch in _ILLEGAL or ord(ch) < 32 else ch
        for ch in str(text or "")
    )
    cleaned = " ".join(cleaned.split())          # collapse runs of whitespace
    # A name that is only dots is a directory reference, and a trailing dot or
    # space is silently dropped by Windows, which makes the file unfindable.
    cleaned = cleaned.strip(". ")
    return cleaned[:limit].strip()


def stamped_stem(stamp: str, name: str = "") -> str:
    """`<timestamp> <name>` — the timestamp always leads, so the inbox sorts."""
    clean = safe_name(name)
    return f"{stamp} {clean}" if clean else stamp


def ask_name(default_stamp: str, root=None) -> str | None:
    """Ask what to call this recording. Returns "" for no name, None to cancel.

    Shown after recording stops, so naming never delays hitting record — the
    moment you want to capture something is the wrong moment for a form.
    """
    import tkinter as tk

    owns_root = root is None
    if owns_root:
        root = tk.Tk()
        root.withdraw()

    top = tk.Toplevel(root)
    top.title("Name this recording")
    top.configure(bg="#13151d")
    top.resizable(False, False)

    wrap = tk.Frame(top, bg="#13151d", padx=24, pady=20)
    wrap.pack()
    tk.Label(wrap, text="Name this recording", bg="#13151d", fg="#e5e7eb",
             font=("Segoe UI", 13, "bold")).pack(anchor="w")
    tk.Label(wrap, text=f"Saved as  {default_stamp} <name>", bg="#13151d",
             fg="#94a3b8", font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 12))

    var = tk.StringVar()
    field = tk.Entry(wrap, textvariable=var, width=38, bg="#1b1e29", fg="#e5e7eb",
                     insertbackground="#e5e7eb", relief="flat", font=("Segoe UI", 11))
    field.pack(ipady=6, fill="x")
    field.focus_set()

    state = {"result": None}

    def save(_event=None):
        state["result"] = safe_name(var.get())
        top.destroy()

    def skip(_event=None):
        state["result"] = ""          # no name, keep the timestamp
        top.destroy()

    buttons = tk.Frame(wrap, bg="#13151d")
    buttons.pack(fill="x", pady=(14, 0))
    tk.Button(buttons, text="Skip", command=skip, bg="#1b1e29", fg="#e5e7eb",
              relief="flat", padx=14, pady=6, font=("Segoe UI", 9)).pack(side="left")
    tk.Button(buttons, text="Save & send", command=save, bg="#e06c75", fg="#1a0c0d",
              relief="flat", padx=16, pady=6,
              font=("Segoe UI", 9, "bold")).pack(side="right")
    field.bind("<Return>", save)
    top.bind("<Escape>", skip)
    top.protocol("WM_DELETE_WINDOW", skip)

    top.update_idletasks()
    width, height = top.winfo_width(), top.winfo_height()
    top.geometry(f"+{(top.winfo_screenwidth() - width) // 2}"
                 f"+{(top.winfo_screenheight() - height) // 3}")
    try:
        top.attributes("-topmost", True)
    except Exception:
        pass
    top.grab_set()
    top.wait_window()
    if owns_root:
        try:
            root.destroy()
        except Exception:
            pass
    return state["result"]
