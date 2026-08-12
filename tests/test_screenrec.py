"""Screen recorder: the mp4 it produces, and the scp that delivers it.

No display is needed for any of this. Frame grabbing is the one part that needs
a real screen, so these tests hand the muxer frames directly — which is also the
part that would silently produce an unplayable file.
"""

import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import wave
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import screenrec

av = pytest.importorskip("av", reason="PyAV ships with faster-whisper")

REGION = (0, 0, 320, 240)


def _recording(tmp, **kwargs):
    return screenrec.ScreenRecording(REGION, pathlib.Path(tmp), **kwargs)


def _frames(count, width=320, height=240):
    """Frames that actually differ, so a broken encoder cannot look fine."""
    out = []
    for index in range(count):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, :, index % 3] = 40 + (index * 7) % 200
        out.append(frame)
    return out


def _feed(rec, frames):
    """Push frames through the real streaming encoder, as the grab loop does."""
    for frame in frames:
        with rec._encode_lock:
            if rec._container is None:
                rec._open_container(frame.shape[1], frame.shape[0])
            rec._encode_frame(np.ascontiguousarray(frame))
        rec.frames_written += 1


def _write_wav(path, seconds=1.0):
    samples = (np.sin(np.linspace(0, 220 * 2 * np.pi, int(screenrec.AUDIO_RATE * seconds)))
               * 12000).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, screenrec.AUDIO_RATE, 0, "NONE", "not compressed"))
        handle.writeframes(samples.tobytes())


# -- the mp4 ----------------------------------------------------------------

def test_it_produces_an_mp4_that_decodes_back_to_the_frames_put_in():
    """"It wrote a file" is not the bar. It has to decode."""
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp, fps=10)
        _feed(rec, _frames(20))
        _write_wav(rec.audio_path, seconds=2.0)

        out = rec._mux()

        assert out is not None and out.exists(), rec.errors
        with av.open(str(out)) as container:
            video = container.streams.video[0]
            assert (video.codec_context.width, video.codec_context.height) == (320, 240)
            decoded = sum(1 for _ in container.decode(video=0))
        assert decoded == 20, f"put in 20 frames, got {decoded} back"


def test_the_narration_survives_into_the_mp4():
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp, fps=10)
        _feed(rec, _frames(10))
        _write_wav(rec.audio_path, seconds=1.0)

        out = rec._mux()

        with av.open(str(out)) as container:
            assert container.streams.audio, "no audio stream — the narration is the feedback"
            samples = sum(f.samples for f in container.decode(audio=0))
        # AAC pads, so this is "roughly a second of audio", not an exact count.
        assert samples > screenrec.AUDIO_RATE * 0.8, samples


def test_a_recording_with_no_audio_still_produces_a_playable_video():
    """A muted mic must not cost you the recording."""
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp, fps=10)
        _feed(rec, _frames(8))

        out = rec._mux()

        assert out is not None and out.exists(), rec.errors
        with av.open(str(out)) as container:
            assert sum(1 for _ in container.decode(video=0)) == 8


def test_an_odd_sized_region_is_rounded_down_not_up():
    """yuv420p needs even dimensions, and rounding UP would capture pixels
    outside the box the user drew."""
    with tempfile.TemporaryDirectory() as tmp:
        rec = screenrec.ScreenRecording((10, 20, 321, 241), pathlib.Path(tmp))
        assert rec.region == (10, 20, 320, 240)


def test_capturing_nothing_reports_it_instead_of_writing_a_broken_file():
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp)
        rec._stop.set()
        assert rec.stop() is None
        assert any("no frames" in e for e in rec.errors), rec.errors
        assert not rec.video_path.exists()


# -- the audio callback -----------------------------------------------------

def test_the_mic_callback_does_not_block_on_the_held_wave_lock():
    """Same rule as meeting capture: whatever the callback does not finish in
    time is audio Windows throws away."""
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp)
        block = np.full((800, 1), 0.25, dtype=np.float32)

        holding, release = threading.Event(), threading.Event()

        def hold():
            with rec._wave_lock:
                holding.set()
                release.wait(2.0)

        holder = threading.Thread(target=hold, daemon=True)
        holder.start()
        assert holding.wait(2.0)

        started = time.monotonic()
        for _ in range(20):
            rec._on_mic_block(block, 800, None, None)
        elapsed = time.monotonic() - started

        release.set()
        holder.join(timeout=2.0)
        assert elapsed < 0.25, f"callback blocked for {elapsed:.3f}s"


def test_the_mic_callback_copies_the_buffer_it_is_handed():
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp)
        rec.start_wave_for_test = None
        rec._wave = wave.open(str(rec.audio_path), "wb")
        rec._wave.setparams((1, 2, screenrec.AUDIO_RATE, 0, "NONE", "not compressed"))

        reused = np.full((800, 1), 0.5, dtype=np.float32)
        rec._on_mic_block(reused, 800, None, None)
        reused[:] = -0.5                     # PortAudio refilling its buffer
        rec._drain_audio()
        rec._wave.close()

        with wave.open(str(rec.audio_path), "rb") as handle:
            first = np.frombuffer(handle.readframes(1), dtype="<i2")[0]
        assert first > 0, f"queued a live view, not a copy (got {first})"


def test_a_full_queue_drops_a_block_rather_than_stalling_the_callback():
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp)
        block = np.full((800, 1), 0.25, dtype=np.float32)
        for _ in range(screenrec.AUDIO_QUEUE_LIMIT + 3):
            rec._on_mic_block(block, 800, None, None)
        assert rec.dropped_audio_blocks == 3


# -- delivery ---------------------------------------------------------------

def _touch(tmp, name):
    path = pathlib.Path(tmp) / name
    path.write_bytes(b"x")
    return path


def test_it_refuses_to_send_with_no_destination_configured():
    with tempfile.TemporaryDirectory() as tmp:
        ok, message = screenrec.send([_touch(tmp, "a.mp4")], "")
        assert not ok and "destination" in message


def test_it_reports_a_failed_upload_instead_of_claiming_success():
    """The recording must never be presented as delivered when it was not."""
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.CompletedProcess(
            [], 255, stdout="", stderr="ssh: connect to host x port 22: No route to host")
        with patch.object(screenrec.shutil, "which", return_value="/usr/bin/scp"), \
             patch.object(screenrec.subprocess, "run", return_value=result):
            ok, message = screenrec.send([_touch(tmp, "a.mp4")], "root@x:/inbox/")
        assert not ok
        assert "No route to host" in message


def test_a_hung_upload_gives_up_rather_than_blocking_forever():
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(screenrec.shutil, "which", return_value="/usr/bin/scp"), \
             patch.object(screenrec.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("scp", 300)):
            ok, message = screenrec.send([_touch(tmp, "a.mp4")], "root@x:/inbox/", timeout=300)
        assert not ok and "timed out" in message


def test_it_sends_batch_mode_so_a_missing_key_fails_instead_of_hanging():
    """Without -B, scp prompts for a password that a tray app cannot show."""
    with tempfile.TemporaryDirectory() as tmp:
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(screenrec.shutil, "which", return_value="/usr/bin/scp"), \
             patch.object(screenrec.subprocess, "run", side_effect=fake_run):
            ok, _ = screenrec.send([_touch(tmp, "a.mp4")], "root@x:/inbox/")

        assert ok
        assert "-B" in seen["cmd"], seen["cmd"]
        assert seen["cmd"][-1] == "root@x:/inbox/"


def test_it_sends_the_transcript_alongside_the_video():
    with tempfile.TemporaryDirectory() as tmp:
        sent = {}

        def fake_run(cmd, **kwargs):
            sent["files"] = [c for c in cmd if c.endswith((".mp4", ".txt"))]
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(screenrec.shutil, "which", return_value="/usr/bin/scp"), \
             patch.object(screenrec.subprocess, "run", side_effect=fake_run):
            screenrec.send(
                [_touch(tmp, "a.mp4"), _touch(tmp, "a.txt")], "root@x:/inbox/")

        assert len(sent["files"]) == 2


def test_the_colours_that_go_in_are_the_colours_that_come_out():
    """A BGR/RGB mix-up still decodes 20 frames and still passes a frame count.
    So check the pixels: red in must not come back blue."""
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp, fps=10)
        red = np.zeros((240, 320, 3), dtype=np.uint8); red[:, :, 0] = 220
        green = np.zeros((240, 320, 3), dtype=np.uint8); green[:, :, 1] = 220
        _feed(rec, [red] * 5 + [green] * 5)

        out = rec._mux()

        with av.open(str(out)) as container:
            decoded = [f.to_ndarray(format="rgb24") for f in container.decode(video=0)]
        first = decoded[0].reshape(-1, 3).mean(axis=0)
        last = decoded[-1].reshape(-1, 3).mean(axis=0)
        assert first[0] > 200 and first[1] < 20, f"red came back as {first}"
        assert last[1] > 200 and last[0] < 20, f"green came back as {last}"


# -- region selection (needs a display) --------------------------------------

import uistub  # noqa: E402


@pytest.mark.skipif(not uistub.have_display(), reason="no display")
def test_dragging_a_box_returns_that_box():
    import tkinter as tk

    root = tk.Tk()
    root.geometry("800x600")
    root.update_idletasks()

    result = {}

    def drive():
        top = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
        canvas = top.winfo_children()[0]
        canvas.event_generate("<Button-1>", x=100, y=80)
        canvas.event_generate("<B1-Motion>", x=340, y=260)
        canvas.event_generate("<ButtonRelease-1>", x=340, y=260)

    root.after(300, drive)
    result["box"] = screenrec.select_region(root)
    root.destroy()

    assert result["box"] == (100, 80, 240, 180)


@pytest.mark.skipif(not uistub.have_display(), reason="no display")
def test_escape_records_nothing():
    import tkinter as tk

    root = tk.Tk()
    root.update_idletasks()

    def drive():
        top = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
        top.event_generate("<Escape>")

    root.after(300, drive)
    box = screenrec.select_region(root)
    root.destroy()
    assert box is None


@pytest.mark.skipif(not uistub.have_display(), reason="no display")
def test_a_stray_click_is_a_cancel_not_a_tiny_recording():
    import tkinter as tk

    root = tk.Tk()
    root.update_idletasks()

    def drive():
        top = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
        canvas = top.winfo_children()[0]
        canvas.event_generate("<Button-1>", x=200, y=200)
        canvas.event_generate("<ButtonRelease-1>", x=203, y=201)

    root.after(300, drive)
    box = screenrec.select_region(root)
    root.destroy()
    assert box is None


# -- naming ------------------------------------------------------------------

def test_the_timestamp_always_leads_so_an_inbox_sorts():
    assert screenrec.stamped_stem("2026-08-12 10-33-25", "login bug") == \
        "2026-08-12 10-33-25 login bug"


def test_no_name_still_gives_a_timestamped_file():
    assert screenrec.stamped_stem("2026-08-12 10-33-25", "") == "2026-08-12 10-33-25"
    assert screenrec.stamped_stem("2026-08-12 10-33-25", "   ") == "2026-08-12 10-33-25"


@pytest.mark.parametrize("typed", ['a/b', 'a\\b', 'a:b', 'a*b', 'a?b', 'a"b', 'a<b>', 'a|b'])
def test_characters_windows_and_scp_reject_are_stripped(typed):
    cleaned = screenrec.safe_name(typed)
    assert not (set(cleaned) & set('<>:"/\\|?*')), cleaned
    assert cleaned


def test_a_name_that_windows_would_silently_mangle_is_cleaned():
    """A trailing dot or space is dropped by Windows, making the file
    unfindable by the name you typed."""
    assert screenrec.safe_name("report. ") == "report"
    assert screenrec.safe_name("...") == ""
    assert screenrec.safe_name("a\tb\nc") == "a b c"


def test_a_very_long_name_is_capped():
    assert len(screenrec.safe_name("x" * 500)) <= 60


def test_renaming_moves_every_file_of_the_recording():
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp)
        for suffix in (".mp4", ".wav", ".txt"):
            (rec.out_dir / f"{rec.stem}{suffix}").write_bytes(b"x")

        rec.rename("2026-08-12 10-33-25 login bug")

        assert rec.video_path.name == "2026-08-12 10-33-25 login bug.mp4"
        for suffix in (".mp4", ".wav", ".txt"):
            assert (rec.out_dir / f"2026-08-12 10-33-25 login bug{suffix}").exists()


def test_renaming_never_overwrites_an_earlier_recording():
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp)
        (rec.out_dir / f"{rec.stem}.mp4").write_bytes(b"new")
        (rec.out_dir / "login bug.mp4").write_bytes(b"older recording")

        rec.rename("login bug")

        assert (rec.out_dir / "login bug.mp4").read_bytes() == b"older recording"
        assert rec.video_path.read_bytes() == b"new"


def _click(parent, label):
    """Press the button a user would press."""
    import tkinter as tk

    def walk(widget):
        for child in widget.winfo_children():
            if isinstance(child, tk.Button) and child.cget("text") == label:
                child.invoke()
                return True
            if walk(child):
                return True
        return False

    assert walk(parent), f"no {label!r} button"


@pytest.mark.skipif(not uistub.have_display(), reason="no display")
@pytest.mark.parametrize("typed,expected", [("log", "log"), (None, "")])
def test_the_name_dialog_returns_what_was_typed(typed, expected):
    """A failsafe closes the dialog no matter what, so a broken driver fails
    this test instead of hanging the whole run."""
    import tkinter as tk

    root = tk.Tk()
    root.update_idletasks()

    def dialogs():
        return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]

    def drive():
        top = dialogs()[0]
        if typed is None:
            _click(top, "Skip")
            return
        entries = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Entry):
                    entries.append(child)
                walk(child)

        walk(top)
        entries[0].insert("end", typed)
        _click(top, "Save & send")

    def failsafe():
        for top in dialogs():
            top.destroy()

    root.after(300, drive)
    root.after(4000, failsafe)
    got = screenrec.ask_name("2026-08-12 10-33-25", root)
    root.destroy()

    assert got == expected


def test_a_long_recording_does_not_hold_every_frame_in_memory():
    """A 1080p raw frame is 6 MB. Buffering a minute at 12 fps is ~4.5 GB, so
    the recording dies before producing anything. Memory must stay flat."""
    import tracemalloc

    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp, fps=12)
        frame = np.random.randint(0, 255, (480, 854, 3), dtype=np.uint8)

        _feed(rec, [frame] * 12)          # warm the encoder
        tracemalloc.start()
        base = tracemalloc.get_traced_memory()[0]
        _feed(rec, [frame] * 240)         # 20 more seconds
        peak_growth = tracemalloc.get_traced_memory()[0] - base
        tracemalloc.stop()

        raw_size = frame.nbytes * 240     # what buffering them would have cost
        assert peak_growth < raw_size / 10, (
            f"grew {peak_growth/1e6:.1f} MB over 240 frames; "
            f"buffering them all would be {raw_size/1e6:.1f} MB")
        rec._mux()
