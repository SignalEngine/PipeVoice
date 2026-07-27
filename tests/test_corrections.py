"""Pure tests for meeting wording overlays and one-pass replacements."""

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import meeting
from wisprlite.typer import apply_replacements


def test_apply_corrections_copies_segments_and_preserves_speakers():
    segments = [{"speaker": "You", "text": "Dave met José"}]
    result = meeting.apply_corrections(segments, {"Dave": "Dev", "José": "Jose"})

    assert result == [{"speaker": "You", "text": "Dev met Jose"}]
    assert segments == [{"speaker": "You", "text": "Dave met José"}]
    assert result[0] is not segments[0]


def test_apply_replacements_is_single_pass():
    assert apply_replacements("a b", {"a": "b", "b": "c"}) == "b c"


def test_apply_replacements_prefers_longest_match():
    assert apply_replacements(
        "new york city and New York", {"new york": "NY", "new york city": "NYC"}
    ) == "NYC and NY"


def test_replacements_keep_boundaries_and_case_insensitivity():
    assert apply_replacements("Dave dave DAVE davenport", {"dave": "Dev"}) == (
        "Dev Dev Dev davenport"
    )


def test_corrections_round_trip_does_not_mutate_transcript():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp)
        raw = b'{"segments":[{"speaker":"You","text":"Dave Dave"}]}'
        (path / "transcript.json").write_bytes(raw)
        meeting.save_corrections(path, {"Dave": "Dev", "empty": "  "})

        assert meeting.load_corrections(path) == {"Dave": "Dev"}
        assert meeting.render_transcript(
            json.loads(raw)["segments"], corrections=meeting.load_corrections(path)
        ) == "You: Dev Dev"
        assert (path / "transcript.json").read_bytes() == raw

        (path / meeting.CORRECTIONS_FILE).unlink()
        assert meeting.render_transcript(json.loads(raw)["segments"]) == "You: Dave Dave"


def test_load_corrections_guards_corrupt_and_non_dict_json():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp)
        corrections = path / meeting.CORRECTIONS_FILE
        corrections.write_text("{not-json", encoding="utf-8")
        assert meeting.load_corrections(path) == {}
        corrections.write_text("[]", encoding="utf-8")
        assert meeting.load_corrections(path) == {}


def test_you_is_a_structural_speaker_label_not_a_correction_target():
    segments = [{"speaker": "You", "text": "Hello"}]
    corrected = meeting.apply_corrections(segments, {"You": "Them"})
    assert corrected[0]["speaker"] == "You"
    assert corrected[0]["text"] == "Hello"

