"""Pure tests for meeting wording overlays and one-pass replacements."""

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import meeting
from wisprlite.meetings_tab import (
    _correction_parts,
    _joined_correction_parts,
    _replacement_key_allowed,
    _selection_contains_click,
)
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


def test_replacements_do_not_raise_on_turkish_casefold_mismatch():
    assert apply_replacements("I went to Istanbul", {"istanbul": "İstanbul"}) == (
        "I went to İstanbul"
    )
    assert apply_replacements("I went to İstanbul", {"istanbul": "İstanbul"}) == (
        "I went to İstanbul"
    )


def test_correction_parts_keep_saved_key_for_case_variant_undo():
    assert _correction_parts("Dave and DAVE spoke", {"Dave": "Dev"}) == [
        ("Dev", True, "Dave"),
        (" and ", False, None),
        ("Dev", True, "Dave"),
        (" spoke", False, None),
    ]


def test_correction_parts_fall_back_to_original_on_turkish_casefold_mismatch():
    assert _correction_parts("İstanbul", {"istanbul": "Istanbul"}) == [
        ("İstanbul", False, None)
    ]


def test_correction_keys_with_settings_delimiters_are_rejected():
    assert _replacement_key_allowed("Dave")
    assert not _replacement_key_allowed("1,000")
    assert not _replacement_key_allowed("C=64")


def test_stale_selection_does_not_contain_right_click_target():
    assert _selection_contains_click("2.3", "2.5", "2.8")
    assert not _selection_contains_click("2.3", "4.1", "2.8")


def test_widget_correction_parts_match_per_segment_joining():
    corrections = {"New York": "NYC"}
    parts = _joined_correction_parts(["New", "York is big"], corrections)
    assert "".join(piece for piece, _corrected, _find in parts) == "New York is big"
    assert meeting.render_transcript(
        [{"speaker": "You", "text": "New"}, {"speaker": "You", "text": "York is big"}],
        corrections=corrections,
    ) == "You: New York is big"


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
