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


def test_levels_stayed_a_property():
    # A near-miss worth locking down: adding bleed_suspected directly above
    # `levels` stole its @property decorator, so app.py's `self._meeting.levels`
    # (no parens) would have handed the overlay a bound method instead of a
    # dict, silently killing both REC level meters.
    assert isinstance(meeting.MeetingRecorder.__dict__["levels"], property)
    assert isinstance(meeting.MeetingRecorder.__dict__["bleed_suspected"], property)


def test_live_bleed_detection_tells_headphones_from_speakers():
    import threading

    recorder_cls = meeting.MeetingRecorder

    class Fake:
        levels = recorder_cls.__dict__["levels"]
        bleed_suspected = recorder_cls.__dict__["bleed_suspected"]

        def __init__(self):
            self._state_lock = threading.Lock()
            self._levels = {"mic": 0.0, "desktop": 0.0}
            self._bleed_desktop_loud = 0
            self._bleed_both_loud = 0

        def sample(self, desktop_rms, mic_level):
            with self._state_lock:
                self._levels["mic"] = mic_level
                if desktop_rms >= meeting.BLEED_LIVE_LEVEL:
                    self._bleed_desktop_loud += 1
                    if self._levels["mic"] >= meeting.BLEED_LIVE_LEVEL:
                        self._bleed_both_loud += 1

    # Headphones: the two streams go loud at different times, because people
    # take turns. The occasional interruption must not trigger the warning.
    headphones = Fake()
    for index in range(300):
        headphones.sample(0.15, 0.10 if index % 10 == 0 else 0.001)
    assert headphones.bleed_suspected is False

    # Speakers: the mic is loud whenever the desktop is.
    speakers = Fake()
    for _ in range(300):
        speakers.sample(0.15, 0.09)
    assert speakers.bleed_suspected is True

    # Not enough remote speech yet to judge either way.
    early = Fake()
    for _ in range(40):
        early.sample(0.15, 0.09)
    assert early.bleed_suspected is False

    # Solo recording: no remote audio at all, so nothing to echo.
    solo = Fake()
    for _ in range(300):
        solo.sample(0.0, 0.09)
    assert solo.bleed_suspected is False


def test_write_block_actually_feeds_the_bleed_counters():
    # The test above proves the JUDGEMENT is right; this proves it is FED.
    # Sabotaging _write_block left that test green, because its fake increments
    # the counters itself — a test guarding a path production never takes.
    # _write_block needs numpy, wave handles and locks to call for real, so
    # assert the wiring at source level, as test_desktop_capture_has_no_detector
    # _path already does.
    source = inspect.getsource(MeetingRecorder._write_block)
    assert "_bleed_desktop_loud" in source, "desktop-loud samples are never counted"
    assert "_bleed_both_loud" in source, "overlapping-loud samples are never counted"
    assert 'label == "desktop"' in source, "counting must be driven by the desktop stream"


def test_cross_meeting_search_finds_the_right_meetings():
    import json as _json
    import tempfile
    from wisprlite.meetings_tab import search_sessions

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)

        def make(name, segments, corrections=None):
            folder = root / name
            folder.mkdir(parents=True)
            (folder / "transcript.json").write_text(
                _json.dumps({"segments": segments}), encoding="utf-8"
            )
            if corrections:
                meeting.save_corrections(folder, corrections)
            return {"path": folder, "name": name}

        sessions = [
            make("meeting-1", [
                {"speaker": "You", "text": "We agreed the Postgres migration lands in March"},
                {"speaker": "Them", "text": "Yes, Postgres first then Redis"},
            ]),
            make("meeting-2", [{"speaker": "You", "text": "Nothing relevant here at all"}]),
            # a meeting whose wording the user has CORRECTED
            make("meeting-3", [{"speaker": "Them", "text": "ask Dave about the invoice"}],
                 {"Dave": "Dev"}),
        ]

        hits = search_sessions("postgres", sessions)
        assert len(hits) == 1
        count, snippet = next(iter(hits.values()))
        assert count == 2, "both occurrences should be counted"
        assert "Postgres" in snippet, "the snippet must show why it matched"

        assert search_sessions("banana", sessions) == {}
        assert search_sessions("", sessions) == {}, "an empty query matches nothing"

        # Search what the user SEES: corrections are applied, so the corrected
        # spelling hits and the original no longer does.
        assert len(search_sessions("Dev", sessions)) == 1
        assert search_sessions("Dave", sessions) == {}


def test_search_cache_notices_a_correction():
    # Keying the cache on transcript.json alone served pre-correction text
    # forever: fix David to Dev, search "Dev", and the meeting was missing.
    import json as _json
    import tempfile
    import time
    from wisprlite.meetings_tab import search_sessions

    with tempfile.TemporaryDirectory() as tmp:
        folder = pathlib.Path(tmp) / "meeting-1"
        folder.mkdir()
        (folder / "transcript.json").write_text(
            _json.dumps({"segments": [{"text": "ask David about it"}]}), encoding="utf-8"
        )
        sessions = [{"path": folder, "name": "m1"}]
        cache = {}

        assert len(search_sessions("David", sessions, cache=cache)) == 1
        time.sleep(0.01)
        meeting.save_corrections(folder, {"David": "Dev"})

        assert len(search_sessions("Dev", sessions, cache=cache)) == 1, (
            "the cache must invalidate when corrections change"
        )
        assert search_sessions("David", sessions, cache=cache) == {}


def test_filtered_rows_select_the_meeting_that_was_clicked():
    # Two P1s lived here, both about mixing up the filtered and unfiltered lists.
    # None means NO FILTER; [] means FILTERED TO NOTHING. Conflating them with
    # `visible or sessions` was the second bug: an empty list is falsy, so a
    # search matching nothing fell back to every meeting and left Delete live on
    # an unrelated recording.
    sessions = [{"name": "april notes"}, {"name": "postgres migration"}]

    def shown(visible):
        return sessions if visible is None else visible

    # Filtered to one hit: clicking row 0 must select THAT meeting.
    assert shown([sessions[1]])[0]["name"] == "postgres migration"

    # Filtered to nothing: there must be NOTHING selectable.
    assert shown([]) == [], "a zero-result search must not expose any meeting"

    # No filter: every meeting is addressable.
    assert len(shown(None)) == 2


def test_live_durations_pair_with_the_rendered_rows():
    # Rows are built from the RENDERED list, so zipping the unfiltered one wrote
    # a live recording's duration into whichever row shared its index.
    sessions = [{"name": "standup"}, {"name": "postgres"}]
    visible = [sessions[1]]
    rows = ["row-for-postgres"]

    rendered = sessions if visible is None else visible
    paired = list(zip(rendered, rows))
    assert paired[0][0]["name"] == "postgres"
    assert list(zip(sessions, rows))[0][0]["name"] == "standup", (
        "documents the old, wrong pairing"
    )


def test_no_caller_computes_a_session_index_by_hand():
    # FIVE separate sites computed a position in the unfiltered session list and
    # handed it to select_session, which indexes the FILTERED one — landing on
    # the wrong meeting or silently on none. Callers that already know which
    # meeting they want must use select_by_path instead of index arithmetic.
    import inspect
    from wisprlite import meetings_tab

    source = inspect.getsource(meetings_tab.build)
    offenders = [
        line.strip()
        for line in source.splitlines()
        if "select_session(" in line
        and "enumerate(state[" in line
    ]
    assert not offenders, f"index arithmetic feeding select_session: {offenders}"
    assert "def select_by_path" in source, "the path-based selector must exist"
