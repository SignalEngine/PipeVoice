import json
import inspect
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import meeting, polish, summarise
from wisprlite.meetings_tab import resolve_bookmarks


def test_phrase_matching_uses_the_transcription_writer_shape_and_tolerates_punctuation():
    transcript = {"segments": [
        {"t": 7.5, "speaker": "You", "text": "We should bookmark, THAT."},
        {"t": 12.0, "speaker": "Them", "text": "ordinary discussion"},
    ]}
    assert meeting.bookmarks_from_phrases(transcript, "bookmark that") == [
        {"t": 7.5, "source": "phrase", "phrase": "bookmark that"}
    ]


def test_phrase_bookmarks_are_idempotent_and_preserve_user_marks():
    with tempfile.TemporaryDirectory() as tmp:
        transcript = {"segments": [{"t": 4.0, "text": "Flag that!"}]}
        marks = [{"t": 4.0, "source": "hotkey"}]
        marks += meeting.bookmarks_from_phrases(transcript, "flag that")
        meeting.save_bookmarks(tmp, marks + meeting.bookmarks_from_phrases(transcript, "flag that"))
        assert meeting.load_bookmarks(tmp) == [
            {"t": 4.0, "source": "hotkey"},
            {"t": 4.0, "source": "phrase", "phrase": "flag that"},
        ]


def test_bookmark_window_clamps_and_trailing_marks_have_no_text():
    segments = [
        {"t": 0.0, "text": "Opening"},
        {"t": 10.0, "text": "The price is 42"},
    ]
    result = resolve_bookmarks([
        {"t": 1.0, "source": "phrase"},
        {"t": 12.0, "source": "hotkey"},
    ], segments)
    assert result[0]["window_start"] == 0.0
    assert result[0]["text"] == "Opening"
    assert result[1]["text"] == "Opening The price is 42"
    trailing = resolve_bookmarks([{"t": 99.0, "source": "hotkey"}], segments)
    assert trailing[0]["text"] == ""


def test_summary_without_bookmarks_has_no_flagged_block():
    calls = []
    summarise.summarise(
        [{"t": 0, "speaker": "You", "text": "A point"}], "bullets", "ollama",
        completion=lambda messages, provider, model: calls.append(messages) or "ok",
    )
    assert "FLAGGED MOMENTS" not in calls[0][1]["content"]
    assert calls[0] == summarise._messages("bullets", "[0:00] You: A point")


def test_summary_with_bookmarks_includes_the_flagged_window():
    calls = []
    summarise.summarise(
        [{"t": 0, "speaker": "You", "text": "The price is 42"}], "bullets", "ollama",
        bookmarks=[{"t": 0, "text": "The price is 42"}],
        completion=lambda messages, provider, model: calls.append(messages) or "ok",
    )
    assert "FLAGGED MOMENTS" in calls[0][1]["content"]
    assert "The price is 42" in calls[0][1]["content"]


def test_both_summary_entry_points_pass_bookmark_windows():
    from wisprlite import meetings_tab
    source = inspect.getsource(meetings_tab.build)
    assert source.count("bookmarks=bookmark_windows") >= 2


def test_polish_wrong_segment_count_is_discarded():
    segments = [{"t": 0, "text": "um hello"}, {"t": 1, "text": "bye"}]
    result = polish.polish_segments(
        segments, "ollama", completion=lambda *_: json.dumps(["Hello"])
    )
    assert result is None


def test_polish_overlay_deletion_restores_raw_text():
    raw = [{"t": 0, "speaker": "You", "text": "um hello"}]
    with tempfile.TemporaryDirectory() as tmp:
        meeting.save_polished(tmp, {0: "Hello."})
        assert meeting.apply_polished(raw, meeting.load_polished(tmp))[0]["text"] == "Hello."
        (pathlib.Path(tmp) / meeting.POLISHED_FILE).unlink()
        assert meeting.apply_polished(raw, meeting.load_polished(tmp))[0]["text"] == "um hello"
