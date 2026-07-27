"""_diarized_text: Deepgram diarized paragraphs -> "Speaker N:" blocks.

Plain asserts, no framework (see ARCHITECTURE.md): python3 tests/test_transcribe_diarize.py
Uses stand-in objects because the real SDK response types need the deepgram package.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wisprlite.engines.transcribe import _diarized_text  # noqa: E402


class Sentence:
    def __init__(self, text):
        self.text = text


class Paragraph:
    def __init__(self, speaker, *sentences):
        self.speaker = speaker
        self.sentences = [Sentence(s) for s in sentences]


class Alt:
    """Mimics results.channels[0].alternatives[0]."""

    def __init__(self, transcript="", paragraphs=None):
        self.transcript = transcript
        self.paragraphs = type("P", (), {"paragraphs": paragraphs})() if paragraphs is not None else None


def test_labels_each_speaker():
    out = _diarized_text(Alt("flat", [Paragraph(0, "Hello there."), Paragraph(1, "Hi back.")]))
    assert out == "Speaker 0: Hello there.\n\nSpeaker 1: Hi back.", out


def test_merges_consecutive_same_speaker():
    """Deepgram splits one speaker's turn across paragraphs; they must not
    produce a repeated 'Speaker 0:' header."""
    out = _diarized_text(Alt("flat", [
        Paragraph(0, "One."), Paragraph(0, "Two."), Paragraph(1, "Three."),
    ]))
    assert out == "Speaker 0: One. Two.\n\nSpeaker 1: Three.", out
    assert out.count("Speaker 0:") == 1, out


def test_speaker_zero_is_labelled_not_dropped():
    """Regression: speaker 0 is falsy — a truthiness check would drop its label."""
    out = _diarized_text(Alt("flat", [Paragraph(0, "Only me.")]))
    assert out == "Speaker 0: Only me.", out


def test_falls_back_to_flat_transcript():
    assert _diarized_text(Alt("just words", None)) == "just words"
    assert _diarized_text(Alt("just words", [])) == "just words"


def test_unlabelled_paragraphs_have_no_header():
    """diarize=False still returns paragraphs, but with speaker=None."""
    out = _diarized_text(Alt("flat", [Paragraph(None, "No speakers here.")]))
    assert out == "No speakers here.", out


def test_skips_empty_paragraphs_without_breaking_merge():
    out = _diarized_text(Alt("flat", [
        Paragraph(0, "Start."), Paragraph(0, "  "), Paragraph(0, "End."),
    ]))
    assert out == "Speaker 0: Start. End.", out


def test_empty_everything_is_empty_string():
    assert _diarized_text(Alt("", [])) == ""


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
