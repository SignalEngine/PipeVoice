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
from unittest import mock
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


def test_the_recording_rate_is_48khz_not_the_16khz_speech_rate():
    """16kHz is a speech-recognition rate; a file people LISTEN to needs treble."""
    assert screenrec.AUDIO_RATE == 48_000
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp, fps=10)
        _feed(rec, _frames(10))
        _write_wav(rec.audio_path, seconds=1.0)

        out = rec._mux()

        with av.open(str(out)) as container:
            assert container.streams.audio[0].codec_context.sample_rate == 48_000


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


def test_audio_recorded_before_the_first_frame_is_trimmed():
    """Measured on James's first real clip: 12.46s of audio against 11.42s of
    video, both muxed from zero, so the narration ran a second ahead of the
    picture for the whole recording. The mic opens faster than mss plus x264.
    """
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp)
        samples = np.arange(screenrec.AUDIO_RATE * 5, dtype="<i2")

        rec.first_audio_at, rec.first_frame_at = 100.0, 101.5
        assert rec.lead_seconds() == pytest.approx(1.5)
        trimmed = rec._trim_lead(samples)
        assert trimmed.size == samples.size - int(1.5 * screenrec.AUDIO_RATE)
        assert trimmed[0] == samples[int(1.5 * screenrec.AUDIO_RATE)], \
            "the wrong end was trimmed"

        # Video first (or simultaneous) means nothing to correct.
        rec.first_audio_at, rec.first_frame_at = 101.0, 100.0
        assert rec.lead_seconds() == 0.0
        assert rec._trim_lead(samples) is samples

        # A clip with no frames at all must not be touched.
        rec.first_frame_at = None
        assert rec._trim_lead(samples) is samples


def test_trimming_never_eats_the_whole_narration():
    """A bad clock reading must not silently delete what somebody said."""
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp)
        samples = np.arange(screenrec.AUDIO_RATE * 3, dtype="<i2")
        rec.first_audio_at, rec.first_frame_at = 0.0, 600.0   # absurd
        trimmed = rec._trim_lead(samples)
        assert trimmed.size >= screenrec.AUDIO_RATE, \
            "at least a second of audio must survive any lead correction"


def test_a_failed_rename_puts_every_file_back():
    """All three files or none.

    A half-rename splits one recording across two names, and video_path is
    derived from stem, so it would point at a file that is not there.
    """
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp)
        for suffix in (".mp4", ".wav", ".txt"):
            (rec.out_dir / f"{rec.stem}{suffix}").write_bytes(b"x")
        original = rec.stem
        real_rename = pathlib.Path.rename

        def fail_on_the_wav(self, target):
            if self.suffix == ".wav":
                raise OSError("locked by another process")
            return real_rename(self, target)

        with patch.object(pathlib.Path, "rename", fail_on_the_wav):
            rec.rename("login bug")

        assert rec.stem == original
        for suffix in (".mp4", ".wav", ".txt"):
            assert (rec.out_dir / f"{original}{suffix}").exists()
            assert not (rec.out_dir / f"login bug{suffix}").exists()
        assert rec.video_path.exists()


def test_renaming_will_not_clobber_a_wav_whose_mp4_is_gone():
    """The collision check has to cover every suffix, not just the video."""
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp)
        (rec.out_dir / f"{rec.stem}.mp4").write_bytes(b"new")
        (rec.out_dir / f"{rec.stem}.wav").write_bytes(b"new audio")
        (rec.out_dir / "login bug.wav").write_bytes(b"older narration")

        rec.rename("login bug")

        assert (rec.out_dir / "login bug.wav").read_bytes() == b"older narration"
        assert rec.video_path.exists()


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


@pytest.mark.parametrize("value,expected", [(0, "+0"), (1920, "+1920"), (-1920, "-1920")])
def test_a_negative_monitor_origin_makes_valid_tk_geometry(value, expected):
    """f"+{-1920}" gives "+-1920", which Tk rejects — so the selector would not
    open at all on a monitor placed left of the primary."""
    assert screenrec._offset(value) == expected


def test_shutdown_waits_longer_than_an_upload_can_take():
    from wisprlite import app as app_module

    source = pathlib.Path(app_module.__file__).read_text()
    assert "screenrec.UPLOAD_TIMEOUT + 60.0" in source
    assert screenrec.UPLOAD_TIMEOUT >= 300.0


def test_the_recordings_list_notices_a_transcript_written_after_the_clip():
    """The listing and its poll signature must change when the .txt lands.

    Scope: this covers list_recordings() and the signature the poll compares,
    not the Tk callback itself — the tab-level swap behaviour is covered in
    tests/test_ui_smoke.py.

    James recorded a clip while the Recordings tab was open. The tab was a
    snapshot from when it opened, so it still read "0 recordings" — the proof
    he sent was a video OF that tab saying "Nothing recorded yet." The
    transcript is written seconds after the mux, so a listing that keys only on
    the video would show "no transcript" for ever.
    """
    from wisprlite import screenrec_tab

    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        assert screenrec_tab.list_recordings(base) == []

        clip = base / "2026-08-12 16-24-42.mp4"
        clip.write_bytes(b"video")
        first = screenrec_tab.list_recordings(base)
        assert len(first) == 1
        assert first[0]["transcript_path"] is None

        (base / "2026-08-12 16-24-42.txt").write_text("okay doing a little test",
                                                     encoding="utf-8")
        second = screenrec_tab.list_recordings(base)
        assert second[0]["transcript_path"] is not None, \
            "a transcript written after the clip must be picked up"
        assert screenrec_tab.read_transcript(second[0]) == "okay doing a little test"

        def signature(items):
            return tuple((i["stem"], i["size"], i["transcript_path"] is not None)
                         for i in items)

        assert signature(first) != signature(second), (
            "the poll signature must change when a transcript appears, or the "
            "tab never re-renders and 'no transcript' sticks")


def test_pausing_drops_both_streams_so_they_stay_aligned():
    """Pause must stop audio AND video, or the paused stretch exists in one
    stream and not the other and everything after it is out of sync."""
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp)
        block = np.full((800, 1), 0.25, dtype=np.float32)

        rec._on_mic_block(block, 800, None, None)
        assert rec._audio_queue.qsize() == 1
        assert rec.first_audio_at is not None

        rec.pause()
        assert rec.paused
        for _ in range(5):
            rec._on_mic_block(block, 800, None, None)
        assert rec._audio_queue.qsize() == 1, "audio kept flowing while paused"

        rec.resume()
        assert not rec.paused
        rec._on_mic_block(block, 800, None, None)
        assert rec._audio_queue.qsize() == 2, "audio did not resume"


def test_elapsed_excludes_time_spent_paused():
    """The pill's clock has to show recorded seconds, not wall clock, or it
    disagrees with the length of the file it produces."""
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp)
        assert rec.elapsed() == 0.0            # nothing captured yet

        now = time.monotonic()
        rec.first_audio_at = now - 10.0
        assert rec.elapsed() == pytest.approx(10.0, abs=0.2)

        rec.pause()
        rec._paused_at = now - 4.0             # as if paused 4 seconds ago
        assert rec.elapsed() == pytest.approx(6.0, abs=0.2), \
            "a paused recording's clock must stop"

        rec.resume()
        assert rec.paused_seconds == pytest.approx(4.0, abs=0.2)
        assert rec.elapsed() == pytest.approx(6.0, abs=0.2), \
            "resuming must not give back the paused time"


def test_the_pill_buttons_are_where_the_clicks_are_caught():
    """One table drives both drawing and hit-testing, in every phase, so this
    proves the geometry a user aims at is the geometry that answers."""
    from wisprlite.overlay import Overlay, WIN_H

    overlay = Overlay(enabled=False)
    cy = WIN_H // 2

    for action, cx in Overlay.SCREENREC_BUTTONS:
        assert overlay._screenrec_hit(cx, cy, "recording") == action
        assert overlay._screenrec_hit(cx, cy - 4, "recording") == action
    # The pill body is not a button - dragging it must stay possible.
    assert overlay._screenrec_hit(30, cy, "recording") == ""
    assert overlay._screenrec_hit(120, cy, "recording") == ""
    hits = [overlay._screenrec_hit(cx, cy, "recording")
            for _a, cx in Overlay.SCREENREC_BUTTONS]
    assert len(set(hits)) == len(hits), "two buttons answer the same click"

    # Naming reuses the last two slots, on the row the entry sits on (y=56).
    save_cx, skip_cx = Overlay.SCREENREC_BUTTONS[1][1], Overlay.SCREENREC_BUTTONS[2][1]
    assert overlay._screenrec_hit(save_cx, 56, "naming") == "save"
    assert overlay._screenrec_hit(skip_cx, 56, "naming") == "skip"
    assert overlay._screenrec_hit(Overlay.SCREENREC_BUTTONS[0][1], 56, "naming") == "", \
        "the resume slot must be dead while naming"

    # Nothing is clickable while it is finishing.
    for _a, cx in Overlay.SCREENREC_BUTTONS:
        assert overlay._screenrec_hit(cx, cy, "working") == ""

    # The finished row: named buttons, and their boxes must not overlap.
    boxes = [overlay._done_button_box(i) for i in range(len(Overlay.SCREENREC_DONE))]
    for index, (action, _label) in enumerate(Overlay.SCREENREC_DONE):
        x1, y1, x2, y2 = boxes[index]
        assert overlay._screenrec_hit((x1 + x2) // 2, (y1 + y2) // 2, "done") == action
        assert x1 >= 0 and x2 <= 380, "a button is off the edge of the pill"
    for left, right in zip(boxes, boxes[1:]):
        assert left[2] < right[0], "two finished-clip buttons overlap"
    assert overlay._screenrec_hit(190, 20, "done") == "", "the title row is not a button"


def test_the_pill_naming_hands_back_what_was_typed_and_never_blocks_for_ever():
    """Naming moved into the pill, so the finish thread waits on an Event rather
    than a modal. It must survive nobody ever answering."""
    from uistub import install_platform_stubs

    install_platform_stubs()          # app.py pulls in sounddevice via audio.py
    from wisprlite.app import ScreenrecUI

    ui = ScreenrecUI()
    assert ui.snapshot()["phase"] == "recording"

    event = ui.expect_name("2026-08-12 17-32-18")
    state = ui.snapshot()
    assert state["phase"] == "naming"
    assert state["name"] == "2026-08-12 17-32-18"
    assert not event.is_set()

    ui.answer_name("  login bug  ")
    assert event.is_set()
    assert ui.take_name() == "login bug", "the typed name must survive, trimmed"
    assert ui.take_name() == "", "the name is consumed once"
    assert ui.snapshot()["phase"] == "working", "answering must move the pill on"

    # Skip is an empty answer, not a missing one.
    ui.expect_name("stamp")
    ui.answer_name("")
    assert ui.take_name() == ""
    assert ui.snapshot()["phase"] == "working"


def test_a_quiet_recording_comes_out_of_the_mux_normalised():
    """The pure normalisation maths being right proves nothing about whether
    _mux actually CALLS it. Sabotaging the call site left the whole suite green,
    which is how a feature ships built-but-not-mounted."""
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp, fps=10)
        _feed(rec, _frames(10))
        # -26 dBFS: quiet enough to correct, inside the 8x cap.
        quiet = (np.sin(np.linspace(0, 220 * 2 * np.pi, screenrec.AUDIO_RATE))
                 * (32767 * 10 ** (-26 / 20))).astype("<i2")
        with wave.open(str(rec.audio_path), "wb") as handle:
            handle.setparams((1, 2, screenrec.AUDIO_RATE, 0, "NONE", "not compressed"))
            handle.writeframes(quiet.tobytes())

        out = rec._mux()

        assert out is not None, rec.errors
        with av.open(str(out)) as container:
            decoded = np.concatenate([
                f.to_ndarray().reshape(-1) for f in container.decode(audio=0)
            ])
        # AAC decodes to float32 in [-1, 1], not int16.
        # AAC is lossy, so assert the LEVEL moved, not the exact sample values.
        peak_dbfs = 20 * np.log10(np.abs(decoded).max())
        assert peak_dbfs > -10, (
            f"quiet narration reached the mp4 at {peak_dbfs:.1f} dBFS — "
            "_mux is not normalising"
        )


def _decoded_duration(path):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        last = 0.0
        for frame in container.decode(video=0):
            if frame.pts is not None:
                last = float(frame.pts * stream.time_base)
    return last


def test_the_video_lasts_as_long_as_the_recording_actually_took():
    """James, 2026-09-05, on a 32-minute take: the picture ran 1.79x fast and
    ended at 17:58 against 32:06 of audio.

    Cause: add_stream(rate=self.fps) stamps the REQUESTED rate, and frames got
    implicit sequential PTS - so a capture that really managed 16.8fps was
    written as if every frame were 1/30s apart. Pure-Python mss at 1080p never
    hits a high requested rate, so this fired on every long recording.
    """
    with tempfile.TemporaryDirectory() as tmp:
        # Ask for 30fps and deliver 10 frames over ~1s of recording: exactly the
        # shape of the bug. Correct output is ~1s of video, not 10/30 = 0.33s.
        rec = _recording(tmp, fps=30)
        rec.first_frame_at = time.monotonic()
        rec.first_audio_at = rec.first_frame_at
        for index, frame in enumerate(_frames(10)):
            with mock.patch.object(type(rec), "elapsed", lambda self, i=index: i * 0.1):
                with rec._encode_lock:
                    if rec._container is None:
                        rec._open_container(frame.shape[1], frame.shape[0])
                    rec._encode_frame(np.ascontiguousarray(frame))
                rec.frames_written += 1
        _write_wav(rec.audio_path, seconds=1.0)

        out = rec._mux()

        assert out is not None, rec.errors
        duration = _decoded_duration(out)
        assert 0.8 <= duration <= 1.2, (
            f"10 frames spanning 0.9s of real time became {duration:.2f}s of "
            "video - the requested fps was stamped instead of the real timing"
        )


def test_two_frames_in_the_same_millisecond_get_distinct_timestamps():
    """libx264 rejects a duplicate PTS and the recording ends there. Two grabs
    inside one millisecond is ordinary on a fast machine, and elapsed() is
    milliseconds. Asserts the ENCODE path, not a whole mux: three frames
    spanning 2ms is not a file any encoder should be asked to produce."""
    with tempfile.TemporaryDirectory() as tmp:
        rec = _recording(tmp, fps=30)
        seen = []
        with mock.patch.object(type(rec), "elapsed", lambda self: 0.5):
            for frame in _frames(3):
                with rec._encode_lock:
                    if rec._container is None:
                        rec._open_container(frame.shape[1], frame.shape[0])
                    rec._encode_frame(np.ascontiguousarray(frame))
                seen.append(rec._last_pts)

    assert seen == sorted(set(seen)), \
        f"timestamps were not strictly increasing: {seen}"
    assert len(set(seen)) == 3, f"a duplicate PTS was emitted: {seen}"
