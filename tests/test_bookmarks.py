import inspect
import json
import pathlib
import sys
import tempfile

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import meeting
from wisprlite.meeting import MeetingRecorder
from wisprlite.snap import SnapDetector


def _events(points, blocks=100):
    detector = SnapDetector(16_000)
    fired = []
    for index in range(blocks):
        block = np.zeros(800, dtype=np.float32)
        for point in points:
            if index * 800 <= point < (index + 1) * 800:
                block[point - index * 800] = 1.0
        if detector.feed(block):
            fired.append(index)
    return fired


def test_double_snap_fires_once_after_the_confirm_window():
    # The verdict is deliberately held: a pair only counts once nothing follows
    # it, which is what separates a gesture from a train of keystrokes. Firing
    # is therefore late, and mark_time backdates to the FIRST snap.
    fired = _events([1600, 4800])
    assert len(fired) == 1
    assert fired[0] > 6, "should fire after the confirm window, not immediately"


def test_typing_does_not_fire():
    # THE false positive that matters: people type notes during meetings, and
    # keystrokes land 0.15-0.25s apart, squarely inside the double-snap window.
    # Measured before the isolation rule: 8 keystrokes produced 2 bookmarks.
    fast = [1600 + int(i * 0.19 * 16_000) for i in range(8)]
    assert _events(fast, blocks=140) == []
    slow = [1600 + int(i * 0.25 * 16_000) for i in range(12)]
    assert _events(slow, blocks=200) == []


def test_a_pair_needs_quiet_before_it():
    # A snap-like pair riding at the end of a keystroke run is still a train.
    run = [1600 + int(i * 0.20 * 16_000) for i in range(5)]
    assert _events(run, blocks=140) == []


def test_single_snap_does_not_fire():
    assert _events([1600]) == []


def test_sustained_loud_speech_like_signal_does_not_fire():
    detector = SnapDetector(16_000)
    signal = np.full(800, 0.35, dtype=np.float32)
    assert sum(detector.feed(signal) for _ in range(100)) == 0


def test_constant_white_noise_does_not_fire():
    detector = SnapDetector(16_000)
    rng = np.random.default_rng(12)
    assert sum(detector.feed(rng.normal(0, 0.2, 800)) for _ in range(200)) == 0


def test_three_transients_do_not_fire():
    # A fumbled snap or an echoed one is three transients, not two. Rejecting it
    # is the deliberate trade: a false bookmark is worse than a missed one, and
    # the hotkey is always available.
    assert _events([1600, 4800, 6400]) == []


def test_desktop_capture_has_no_detector_path():
    mic = inspect.getsource(MeetingRecorder._on_mic_block)
    desktop = inspect.getsource(MeetingRecorder._capture_desktop)
    assert "detector.feed" in mic
    assert "_snap_detector" not in desktop


def test_bookmark_overlay_is_sorted_deduped_and_atomic():
    with tempfile.TemporaryDirectory() as tmp:
        meeting.save_bookmarks(tmp, [
            {"t": 3.0, "source": "hotkey"},
            {"t": 1.0, "source": "acoustic"},
            {"t": 1.2, "source": "hotkey"},
        ])
        assert meeting.load_bookmarks(tmp) == [
            {"t": 1.0, "source": "acoustic"},
            {"t": 3.0, "source": "hotkey"},
        ]
        assert not (pathlib.Path(tmp) / "bookmarks.json.tmp").exists()


def test_corrupt_bookmarks_fail_closed_without_touching_transcript():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp)
        transcript = path / "transcript.json"
        transcript.write_text('{"segments": [{"text": "kept"}]}', encoding="utf-8")
        (path / meeting.BOOKMARKS_FILE).write_text('{"t": 1}', encoding="utf-8")
        assert meeting.load_bookmarks(path) == []
        assert json.loads(transcript.read_text(encoding="utf-8"))["segments"][0]["text"] == "kept"


def test_highlight_resolves_to_production_transcript_segments():
    from wisprlite.meetings_tab import resolve_bookmarks
    segments = [
        {"t": 0.0, "speaker": "You", "text": "Opening"},
        {"t": 2.0, "speaker": "Them", "text": "The number is 42"},
    ]
    result = resolve_bookmarks([{"t": 2.2, "source": "hotkey"}], segments)
    assert result[0]["text"] == "Opening The number is 42"
