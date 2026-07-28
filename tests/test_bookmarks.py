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



def _burst(signal, start, amp, decay_s, rng, rate=16_000):
    n = max(1, int(decay_s * rate))
    end = min(len(signal), start + n)
    shape = np.exp(-np.linspace(0, 9, end - start))
    signal[start:end] += (rng.standard_normal(end - start) * shape * amp).astype(np.float32)


def _fires(signal, sensitivity=0.5, rate=16_000, block=800):
    detector = SnapDetector(rate, sensitivity=sensitivity)
    return sum(
        bool(detector.feed(signal[i:i + block]))
        for i in range(0, len(signal) - block, block)
    )


def test_a_double_clap_works_as_well_as_a_snap():
    # Not everyone can snap their fingers, so a clap has to work too. A clap is
    # louder but decays far slower than a snap (15-45ms vs ~3ms), which lowers
    # its crest factor — the exact quantity the detector keys on. Cover the
    # quiet, cupped end of the range, not just a sharp one.
    rng = np.random.default_rng(9)
    for name, amp, decay, gap in (
        ("finger snap", 0.9, 0.003, 0.20),
        ("sharp clap", 1.0, 0.015, 0.25),
        ("normal clap", 0.8, 0.030, 0.30),
        ("cupped clap", 0.45, 0.045, 0.30),
        ("quiet clap", 0.25, 0.040, 0.30),
    ):
        signal = (rng.standard_normal(4 * 16_000) * 0.01).astype(np.float32)
        _burst(signal, 16_000, amp, decay, rng)
        _burst(signal, int((1.0 + gap) * 16_000), amp, decay, rng)
        assert _fires(signal) == 1, f"{name} should bookmark once"


def test_sustained_applause_does_not_fire():
    # Applause is a train of claps. The isolation rule must treat it like typing.
    rng = np.random.default_rng(9)
    signal = (rng.standard_normal(5 * 16_000) * 0.01).astype(np.float32)
    for i in range(25):
        _burst(signal, int((1.0 + i * 0.14) * 16_000), 0.7, 0.03, rng)
    assert _fires(signal) == 0


def test_polish_unwraps_a_fenced_json_reply():
    # Gemini returns JSON inside a ```json fence by default, which json.loads
    # rejects — the whole reply was discarded as "unsafe" when it was fine.
    # This is what made Polish fail on a correctly-configured Gemini setup.
    import json as _json
    from wisprlite import polish

    original_ready = polish.provider_ready
    polish.provider_ready = lambda provider: True
    try:
        segments = [{"speaker": "You", "text": "Um, hello there everyone"}]
        for reply in (
            '```json\n["Hello there everyone"]\n```',
            '```\n["Hello there everyone"]\n```',
            _json.dumps(["Hello there everyone"]),
        ):
            overlay = polish.polish_segments(
                segments, "gemini", "", completion=lambda *a, **k: reply
            )
            assert overlay == {0: "Hello there everyone"}, f"failed on {reply!r}"
    finally:
        polish.provider_ready = original_ready


def test_polish_names_a_missing_key_rather_than_blaming_the_model():
    from wisprlite import polish

    original_ready = polish.provider_ready
    polish.provider_ready = lambda provider: False
    try:
        segments = [{"speaker": "You", "text": "Um, hello there everyone"}]
        raised = False
        try:
            polish.polish_segments(segments, "gemini", "", completion=lambda *a, **k: "[]")
        except polish.ProviderNotReady:
            raised = True
        assert raised, "an unconfigured provider must be distinguishable from a bad reply"
    finally:
        polish.provider_ready = original_ready


def test_speaker_bleed_is_detected_but_never_deleted():
    # Two review rounds proved text similarity CANNOT separate an echo from a
    # similar-but-different sentence — the false positive scores HIGHER than the
    # true positive (0.857 vs 0.789). So this only ever counts; the transcript
    # keeps every word and the user is told to wear headphones.
    bleed = [
        {"t": 29.0, "speaker": "Them", "text": "At Manchester's Nordic Muse, they love "
         "the hand picked stuff. A weird sight that would be if you were just going out "
         "to do the red run at the top with a bunch of trout."},
        {"t": 30.0, "speaker": "You", "text": "Manchester's Nordic muse. They love the "
         "handpicked stuff. The weird sight that would be if you were just going up to "
         "do the red run at the top with a bunch of trout."},
    ]
    assert meeting.count_speaker_bleed(bleed) == 1
    assert len(meeting.merge_transcripts(
        [{"start": 30.0, "text": bleed[1]["text"]}],
        [{"start": 29.0, "speaker": 0, "text": bleed[0]["text"]}],
        mic_offset=0.0, desktop_offset=0.0,
    )) == 2, "detection must not remove anything from the transcript"


def test_bleed_detection_is_fuzzy_but_can_only_cost_a_banner():
    # Honest about the limit: "deploy Friday" vs "deploy Monday" scores 0.857,
    # HIGHER than a real echo at 0.789, so detection cannot exclude it. That is
    # exactly why this counts instead of deleting — a false positive costs a
    # warning banner, never a word of the transcript.
    pair = [
        {"t": 10.0, "speaker": "Them", "text": "I think we should deploy Friday morning please"},
        {"t": 12.0, "speaker": "You", "text": "I think we should deploy Monday morning please"},
    ]
    assert meeting.count_speaker_bleed(pair) <= 1, "one coincidence at most"
    # The UI only warns at 2+, so a single coincidence stays silent.
    assert meeting.count_speaker_bleed(pair) < 2

    # And whatever detection thinks, merge_transcripts keeps both lines.
    merged = meeting.merge_transcripts(
        [{"start": 12.0, "text": pair[1]["text"]}],
        [{"start": 10.0, "speaker": 0, "text": pair[0]["text"]}],
        mic_offset=0.0, desktop_offset=0.0,
    )
    assert len(merged) == 2
    assert any("Monday" in seg["text"] for seg in merged), "no word may be lost"


def test_bleed_detection_respects_direction():
    # Echo travels desktop -> mic only, so a local line spoken BEFORE the remote
    # one can never be an echo of it.
    text = "the quarterly numbers came in well above what we forecast last month"
    assert meeting.count_speaker_bleed([
        {"t": 10.0, "speaker": "You", "text": text},
        {"t": 12.0, "speaker": "Them", "text": text},
    ]) == 0
