import json
import pathlib
import sys
import tempfile
import types
import wave
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import meeting


def write_wav(path):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 160)


def write_session(path, *, mic_offset=10.0, desktop_offset=10.0, mic_error=None):
    write_wav(path / "mic.wav")
    write_wav(path / "desktop.wav")
    meta = {
        "mic": {
            "file": "mic.wav",
            "first_block_monotonic": mic_offset,
            "error": mic_error,
        },
        "desktop": {
            "file": "desktop.wav",
            "first_block_monotonic": desktop_offset,
            "error": None,
        },
    }
    (path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def cfg():
    return types.SimpleNamespace(
        language="",
        deepgram_model="nova-3",
        local_model_size="base.en",
        local_device="cpu",
        local_compute_type="int8",
    )


def result(*segments):
    return {
        "text": "",
        "language": "en",
        "duration": 1.0,
        "segments": list(segments),
    }


def test_offset_shifting_puts_earlier_stream_first():
    merged = meeting.merge_transcripts(
        [{"start": 0.0, "text": "local"}],
        [{"start": 0.0, "text": "remote", "speaker": 0}],
        mic_offset=10.065,
        desktop_offset=10.0,
    )
    assert merged == [
        {"t": 0.0, "speaker": "Them", "text": "remote"},
        {"t": 0.065, "speaker": "You", "text": "local"},
    ]


def test_interleaving_uses_adjusted_segment_time():
    merged = meeting.merge_transcripts(
        [
            {"start": 0.1, "text": "one"},
            {"start": 0.5, "text": "three"},
        ],
        [
            {"start": 0.3, "text": "two", "speaker": 0},
            {"start": 0.7, "text": "four", "speaker": 0},
        ],
        mic_offset=4.0,
        desktop_offset=4.0,
    )
    assert [segment["text"] for segment in merged] == [
        "one",
        "two",
        "three",
        "four",
    ]


def test_one_usable_stream_still_writes_transcript():
    calls = []

    def fake_local(path, **kwargs):
        calls.append((path, kwargs))
        return result({"start": 0.25, "text": "remote only"})

    with tempfile.TemporaryDirectory() as tmp:
        session = pathlib.Path(tmp)
        write_session(session, mic_error="RuntimeError: mic failed")
        with wave.open(str(session / "mic.wav"), "wb") as audio:
            audio.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
        with patch(
            "wisprlite.engines.transcribe.transcribe_file",
            side_effect=fake_local,
        ):
            transcript = meeting.transcribe_session(session, cfg(), backend="local")

        assert len(calls) == 1
        assert calls[0][0].endswith("desktop.wav")
        assert transcript["segments"] == [
            {"t": 0.25, "speaker": "Them", "text": "remote only"}
        ]
        assert json.loads(
            (session / "transcript.json").read_text(encoding="utf-8")
        ) == transcript
        meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
        assert meta["status"] == "transcribed"


def test_stream_with_frames_is_transcribed_even_if_error_was_flagged():
    calls = []

    def fake_local(path, **_kwargs):
        name = pathlib.Path(path).name
        calls.append(name)
        return result({"start": 0.0, "text": name})

    with tempfile.TemporaryDirectory() as tmp:
        session = pathlib.Path(tmp)
        write_session(session, mic_error="RuntimeError: transient capture warning")
        with patch(
            "wisprlite.engines.transcribe.transcribe_file",
            side_effect=fake_local,
        ):
            transcript = meeting.transcribe_session(session, cfg(), backend="local")

    assert calls == ["desktop.wav", "mic.wav"]
    assert [segment["speaker"] for segment in transcript["segments"]] == [
        "You",
        "Them",
    ]


def test_input_overflow_does_not_drop_good_mic_audio_from_transcript():
    calls = []

    def fake_local(path, **_kwargs):
        name = pathlib.Path(path).name
        calls.append(name)
        return result({"start": 0.0, "text": name})

    with tempfile.TemporaryDirectory() as tmp:
        session = pathlib.Path(tmp)
        recorder = meeting.MeetingRecorder(session)
        recorder.session_dir = session
        recorder._waves = {
            "mic": recorder._open_wave(session / "mic.wav"),
            "desktop": recorder._open_wave(session / "desktop.wav"),
        }
        block = np.full((160, 1), 0.25, dtype=np.float32)
        recorder._on_mic_block(block, 160, None, "input overflow")
        recorder._on_mic_block(block, 160, None, None)
        recorder._write_block("desktop", block)
        recorder._close_waves()
        recorder._write_meta(stopped_at=None, duration=0.02)

        with patch(
            "wisprlite.engines.transcribe.transcribe_file",
            side_effect=fake_local,
        ):
            transcript = meeting.transcribe_session(session, cfg(), backend="local")

    assert calls == ["desktop.wav", "mic.wav"]
    assert any(
        segment["speaker"] == "You" and segment["text"] == "mic.wav"
        for segment in transcript["segments"]
    )


def test_zero_frame_stream_is_skipped():
    calls = []

    def fake_local(path, **_kwargs):
        calls.append(pathlib.Path(path).name)
        return result({"start": 0.0, "text": "desktop"})

    with tempfile.TemporaryDirectory() as tmp:
        session = pathlib.Path(tmp)
        write_session(session)
        with wave.open(str(session / "mic.wav"), "wb") as audio:
            audio.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
        with patch(
            "wisprlite.engines.transcribe.transcribe_file",
            side_effect=fake_local,
        ):
            meeting.transcribe_session(session, cfg(), backend="local")

    assert calls == ["desktop.wav"]


def test_remote_speaker_labels_follow_diarized_speaker_count():
    one_speaker = meeting.merge_transcripts(
        [],
        [
            {"start": 0.0, "text": "hello", "speaker": 7},
            {"start": 1.0, "text": "again", "speaker": 7},
        ],
        mic_offset=None,
        desktop_offset=1.0,
    )
    assert [segment["speaker"] for segment in one_speaker] == ["Them", "Them"]

    two_speakers = meeting.merge_transcripts(
        [],
        [
            {"start": 0.0, "text": "first", "speaker": 7},
            {"start": 1.0, "text": "second", "speaker": 3},
            {"start": 2.0, "text": "first again", "speaker": 7},
        ],
        mic_offset=None,
        desktop_offset=1.0,
    )
    assert [segment["speaker"] for segment in two_speakers] == [
        "Them 1",
        "Them 2",
        "Them 1",
    ]


def test_renderer_merges_consecutive_same_speaker_blocks():
    text = meeting.render_transcript(
        [
            {"t": 0.0, "speaker": "You", "text": "Hello."},
            {"t": 0.5, "speaker": "You", "text": "How are you?"},
            {"t": 1.0, "speaker": "Them 1", "text": "Good."},
            {"t": 1.5, "speaker": "You", "text": "Great."},
        ]
    )
    assert text == (
        "You: Hello. How are you?\n\n"
        "Them 1: Good.\n\n"
        "You: Great."
    )


def test_deepgram_receives_per_stream_diarize_flag():
    calls = []

    def fake_deepgram(path, **kwargs):
        calls.append((pathlib.Path(path).name, kwargs["diarize"]))
        return result({"start": 0.0, "text": pathlib.Path(path).stem})

    with tempfile.TemporaryDirectory() as tmp:
        session = pathlib.Path(tmp)
        write_session(session)
        with (
            patch("wisprlite.config.deepgram_key", return_value="secret"),
            patch(
                "wisprlite.engines.transcribe.transcribe_file_deepgram",
                side_effect=fake_deepgram,
            ),
        ):
            meeting.transcribe_session(session, cfg(), backend="auto")

    assert calls == [("desktop.wav", True), ("mic.wav", False)]


def test_auto_without_deepgram_key_uses_local_backend():
    calls = []

    def fake_local(path, **_kwargs):
        calls.append(pathlib.Path(path).name)
        return result({"start": 0.0, "text": pathlib.Path(path).stem})

    with tempfile.TemporaryDirectory() as tmp:
        session = pathlib.Path(tmp)
        write_session(session)
        with (
            patch("wisprlite.config.deepgram_key", return_value=""),
            patch(
                "wisprlite.engines.transcribe.transcribe_file",
                side_effect=fake_local,
            ),
        ):
            transcript = meeting.transcribe_session(session, cfg(), backend="auto")

    assert transcript["backend"] == "local"
    assert calls == ["desktop.wav", "mic.wav"]


if __name__ == "__main__":
    test_offset_shifting_puts_earlier_stream_first()
    test_interleaving_uses_adjusted_segment_time()
    test_one_usable_stream_still_writes_transcript()
    test_stream_with_frames_is_transcribed_even_if_error_was_flagged()
    test_input_overflow_does_not_drop_good_mic_audio_from_transcript()
    test_zero_frame_stream_is_skipped()
    test_remote_speaker_labels_follow_diarized_speaker_count()
    test_renderer_merges_consecutive_same_speaker_blocks()
    test_deepgram_receives_per_stream_diarize_flag()
    test_auto_without_deepgram_key_uses_local_backend()
    print("OK")
