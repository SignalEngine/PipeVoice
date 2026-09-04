"""PipeFocus policy: the feature is only as good as its restraint."""

import json
import pathlib
import threading
import time
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# wisprlite.app pulls in the Windows-only audio libs; the UI harness already
# stubs them so the module can be imported on a headless box.
from uistub import install_platform_stubs

install_platform_stubs()

from wisprlite import focus


def test_analysis_is_driven_by_new_speech_not_the_clock():
    # A wall-clock timer burns API calls on silence and on a meeting that is
    # going perfectly well. Only genuinely new speech should cost anything.
    policy = focus.FocusPolicy()
    assert policy.should_analyse(50, now=1000.0) is False, "too little said yet"
    assert policy.should_analyse(focus.MIN_NEW_WORDS, now=1000.0) is True

    policy.analysed(focus.MIN_NEW_WORDS, now=1000.0)
    # Same words later: nothing new was said, so nothing to analyse.
    assert policy.should_analyse(focus.MIN_NEW_WORDS, now=9999.0) is False
    # Plenty more said, but too soon.
    assert policy.should_analyse(focus.MIN_NEW_WORDS * 3, now=1001.0) is False
    # New speech AND the gap passed.
    assert policy.should_analyse(
        focus.MIN_NEW_WORDS * 3, now=1000.0 + focus.MIN_ANALYSIS_GAP + 1
    ) is True


def test_a_tip_interrupts_at_most_once_per_cooldown():
    policy = focus.FocusPolicy(cooldown=300.0)
    first = "Three action items have been raised and none has an owner yet."
    assert policy.accept(first, now=1000.0) is True
    assert policy.accept("The pricing decision has been deferred twice now.",
                         now=1000.0 + 60) is False, "a second tip a minute later is nagging"
    assert policy.accept("The pricing decision has been deferred twice now.",
                         now=1000.0 + 301) is True


def test_the_same_nudge_is_never_shown_twice():
    # Reworded repeats are the fastest way to make this feel like nagging.
    policy = focus.FocusPolicy(cooldown=0.0)
    assert policy.accept("Three action items have no owner assigned to them", 0.0) is True
    assert policy.accept("Three action items have no owner assigned", 100.0) is False


def test_vague_or_oversized_tips_are_refused():
    policy = focus.FocusPolicy(cooldown=0.0)
    assert policy.accept("Stay focused!", 0.0) is False, "too vague to act on"
    assert policy.accept("", 0.0) is False
    assert policy.accept(None, 0.0) is False
    assert policy.accept("x" * (focus.MAX_TIP_CHARS + 1), 0.0) is False


def test_no_call_is_made_during_a_cooldown():
    # Asking during the cooldown spends money on an answer that cannot be shown.
    policy = focus.FocusPolicy(cooldown=300.0)
    policy.accept("Three action items have been raised with no owner named.", now=1000.0)
    assert policy.should_analyse(100_000, now=1000.0 + 10) is False
    assert policy.should_analyse(100_000, now=1000.0 + 400) is True


def test_declining_to_speak_is_the_normal_answer():
    assert focus.parse_tip(json.dumps({"tip": None})) == (None, "")
    assert focus.parse_tip("") == (None, "")
    assert focus.parse_tip("I think the meeting is going fine!") == (None, "")
    assert focus.parse_tip('{"tip": "not json') == (None, "")
    assert focus.parse_tip(json.dumps({"tip": 42})) == (None, "")


def test_a_real_tip_is_parsed_with_its_evidence():
    tip, because = focus.parse_tip(json.dumps({
        "tip": "Nobody has been named to own the migration.",
        "because": "we should migrate at some point",
    }))
    assert tip == "Nobody has been named to own the migration."
    assert because == "we should migrate at some point"

    # Gemini fences JSON by default; a fenced reply must still parse, or the
    # feature silently never fires for anyone on Gemini.
    fenced = '```json\n{"tip": "Two decisions were deferred again.", "because": "let us park that"}\n```'
    assert focus.parse_tip(fenced)[0] == "Two decisions were deferred again."


def test_the_prompt_only_sends_the_recent_window():
    # Sending a whole meeting every time is cost with no benefit, and on a long
    # call it would eventually exceed the context window.
    transcript = " ".join(f"word{i}" for i in range(5000))
    messages = focus.build_messages(transcript, window_words=900)
    sent = messages[-1]["content"].split()
    assert len(sent) < 950
    assert "word4999" in messages[-1]["content"], "must keep the MOST RECENT speech"
    assert "word0" not in sent


def test_the_rolling_transcript_cannot_grow_without_bound():
    chunks = [f"chunk{i} " * 20 for i in range(500)]
    text = focus.rolling_transcript(chunks, max_words=4000)
    assert len(text.split()) <= 4000
    assert "chunk499" in text, "the newest speech must survive trimming"


def test_a_tip_is_found_whatever_the_model_wraps_it_in():
    # Slicing from the first "{" to the last "}" breaks on prose containing a
    # brace, or a remark after the JSON. Both are ordinary model behaviour.
    cases = [
        '```json\n{"tip": "Two decisions were deferred again.", "because": "park that"}\n```',
        'Here you go: {"tip": "Two decisions were deferred again.", "because": "park that"}',
        '{"tip": "Two decisions were deferred again.", "because": "park that"}\nHope that helps! }',
        'Note: {not json} then {"tip": "Two decisions were deferred again.", "because": "park that"}',
    ]
    for reply in cases:
        tip, _because = focus.parse_tip(reply)
        assert tip == "Two decisions were deferred again.", f"failed on {reply[:40]!r}"


def test_interleaving_keeps_both_sides_aligned():
    # Deepgram reports which channel a phrase came from, so interleaving mic as
    # channel 0 and desktop as channel 1 gives both sides WITH attribution over
    # one socket — better than summing (which loses who spoke) and cheaper than
    # two connections.
    import struct

    interleaver = focus.StreamInterleaver()
    mic = struct.pack("<4h", 100, 101, 102, 103)
    desktop = struct.pack("<4h", 200, 201, 202, 203)

    # One side alone must emit NOTHING; emitting early would slide the channels
    # against each other for the rest of the meeting.
    assert interleaver.add("mic", mic) == b""
    out = interleaver.add("desktop", desktop)
    assert struct.unpack("<8h", out) == (100, 200, 101, 201, 102, 202, 103, 203)


def test_interleaver_emits_only_what_both_sides_cover():
    import struct

    interleaver = focus.StreamInterleaver()
    interleaver.add("mic", struct.pack("<4h", 1, 2, 3, 4))
    out = interleaver.add("desktop", struct.pack("<2h", 9, 9))
    assert struct.unpack("<4h", out) == (1, 9, 2, 9), "only the covered part"
    # The rest of the mic audio is still held, not discarded.
    out2 = interleaver.add("desktop", struct.pack("<2h", 8, 8))
    assert struct.unpack("<4h", out2) == (3, 8, 4, 8)


def test_one_silent_stream_cannot_grow_memory_forever():
    # A solo meeting, or a device that drops, must not accumulate the other
    # side's audio for the whole call.
    interleaver = focus.StreamInterleaver(max_pending_frames=100)
    for _ in range(500):
        interleaver.add("mic", b"\x00\x00" * 100)
    assert interleaver.pending_bytes() <= 100 * 2 + 2


def test_channel_labels_and_the_mono_default():
    # PipeFocus sends stereo but asks Deepgram NOT to treat it as multichannel,
    # because multichannel bills PER CHANNEL — a 10-minute stereo call would be
    # billed as 20. So the live stream is unattributed by design.
    assert focus.channel_speaker(None) == "-", "mono stream carries no speaker"
    assert focus.channel_speaker(0) == "You"
    assert focus.channel_speaker(1) == "Them"


def test_the_focus_stream_is_not_billed_per_channel():
    # The single most expensive mistake available here: multichannel=True
    # silently doubles the bill for every meeting.
    import inspect

    from wisprlite.engines import deepgram_engine

    source = inspect.getsource(deepgram_engine.focus_stream)
    assert "multichannel=False" in source, (
        "multichannel bills PER CHANNEL — leaving it on doubles the cost"
    )


def test_focus_stream_takes_the_meetings_own_sample_rate():
    # meeting.py now records at 48kHz. If focus_stream keeps a second hardcoded
    # 16_000 literal, every PipeFocus transcript decodes as noise: the server
    # is told the wrong rate for the PCM it is actually being fed.
    import inspect

    from wisprlite.engines import deepgram_engine

    sig = inspect.signature(deepgram_engine.focus_stream)
    assert sig.parameters["sample_rate"].default == 16_000, (
        "keep the old default so no other caller changes"
    )
    source = inspect.getsource(deepgram_engine.focus_stream)
    assert "sample_rate=sample_rate" in source, (
        "focus_stream must forward its own sample_rate, not a literal, into LiveOptions"
    )


def test_pipefocus_connects_at_the_meetings_sample_rate():
    import inspect

    from wisprlite import app

    source = inspect.getsource(app.App._start_pipefocus)
    assert "sample_rate=meeting.SAMPLE_RATE" in source, (
        "PipeFocus must stream at the rate meeting.py actually records, not a stale literal"
    )


class _FakeConn:
    """A live connection that can be told to die, like the real one does."""

    def __init__(self, on_text, die_after=None):
        self.on_text = on_text
        self.fed = 0
        self.closed = False
        self._die_after = die_after

    def feed(self, pcm):
        self.fed += 1
        if self._die_after is not None and self.fed > self._die_after:
            raise ConnectionResetError("socket closed")

    def close(self):
        self.closed = True


def _pcm(n=80):
    return b"\x01\x02" * n


def test_the_audio_callback_never_blocks_even_when_the_queue_is_full():
    # Stalling the capture callback would lose the RECORDING — the thing the
    # user actually came for. Focus is best-effort on top of it, so it drops.
    session = focus.FocusSession(connect=lambda on_text: _FakeConn(on_text))
    for _ in range(focus.QUEUE_LIMIT + 60):
        session.feed("mic", _pcm())
        session.feed("desktop", _pcm())
    assert session.dropped_blocks > 0, "it must drop rather than block"
    assert session.last_error == "", "dropping is not an error condition"


def test_a_dropped_socket_reconnects_and_keeps_the_transcript():
    # Deepgram closes a live socket after about an hour, and meetings run long.
    # A reconnect that lost the transcript would reset the policy and re-fire
    # tips the user has already dismissed.
    conns = []

    def connect(on_text):
        conn = _FakeConn(on_text, die_after=2 if not conns else None)
        conns.append(conn)
        return conn

    session = focus.FocusSession(connect=connect)
    session.on_text("we should ship on Friday", channel=1)
    session.start()
    try:
        for _ in range(12):
            session.feed("mic", _pcm())
            session.feed("desktop", _pcm())
        deadline = time.time() + 5
        while session.reconnects == 0 and time.time() < deadline:
            time.sleep(0.05)
    finally:
        session.stop()

    assert session.reconnects >= 1, "the dead socket must be rebuilt"
    assert len(conns) >= 2, "a new connection must actually be opened"
    assert "we should ship on Friday" in session.transcript(), (
        "the transcript must survive a reconnect"
    )
    assert conns[0].closed, "the dead connection must be closed, not leaked"


def test_a_connection_that_never_comes_back_is_recorded_not_swallowed():
    # Silent death is the whole risk with this feature: it would simply stop
    # working mid-meeting with no sign.
    def connect(on_text):
        raise OSError("deepgram unreachable")

    session = focus.FocusSession(connect=connect)
    session.start()
    time.sleep(0.4)
    session.stop()
    assert "deepgram unreachable" in session.last_error


def test_transcript_is_attributed_by_channel():
    session = focus.FocusSession(connect=lambda on_text: _FakeConn(on_text))
    session.on_text("what is the number", channel=0)
    session.on_text("about twelve thousand", channel=1)
    text = session.transcript()
    assert "You: what is the number" in text
    assert "Them: about twelve thousand" in text


def test_a_tip_reaches_the_callback_and_the_model_runs_off_the_audio_thread():
    seen = {}
    calls = []

    def completion(messages):
        calls.append(threading.current_thread().name)
        return json.dumps({"tip": "Three action items still have no owner named.",
                           "because": "someone should pick that up"})

    session = focus.FocusSession(
        connect=lambda on_text: _FakeConn(on_text),
        completion=completion,
        on_tip=lambda tip, because: seen.update(tip=tip, because=because),
        policy=focus.FocusPolicy(cooldown=0.0),
    )
    session.on_text(" ".join(f"word{i}" for i in range(focus.MIN_NEW_WORDS + 40)), channel=1)
    session.start()
    try:
        deadline = time.time() + 5
        while "tip" not in seen and time.time() < deadline:
            session.feed("mic", _pcm())
            session.feed("desktop", _pcm())
            time.sleep(0.05)
    finally:
        session.stop()

    assert seen.get("tip") == "Three action items still have no owner named."
    assert calls, "the model must actually be consulted"
    # The meaningful guarantee is not "not MainThread" — _maybe_analyse already
    # runs on the worker, so that passed even when the call was made inline.
    # What matters is that a slow model call cannot stall the thread feeding the
    # socket, i.e. it runs on a DIFFERENT thread from the audio pump.
    assert session._worker is not None
    assert all(name != session._worker.name for name in calls), (
        "the model call must not run on the thread feeding audio to the socket"
    )


def test_pipefocus_only_runs_on_deepgram():
    # It needs LIVE transcription, which no other engine provides. Rather than
    # half-working elsewhere it must simply not start — and the recording must
    # be unaffected either way.
    import inspect

    from wisprlite import app

    source = inspect.getsource(app.App._start_pipefocus)
    assert 'cfg.engine != "deepgram"' in source, "the engine gate must exist"
    assert 'getattr(cfg, "pipefocus", False)' in source, "and it must be opt-in"
    # Every path through it must clear the session rather than leave a stale one.
    assert source.count("self._meeting.focus_session = None") >= 2


def test_a_focus_failure_cannot_stop_the_meeting_recording():
    # Losing a recording to a focus problem would be indefensible: the recording
    # is what the user came for.
    import inspect

    from wisprlite import app

    source = inspect.getsource(app.App._start_pipefocus)
    assert "except Exception" in source, "connection failures must be caught"
    body = source.split("except Exception")[1]
    assert "raise" not in body, "a focus failure must never propagate"


def test_the_recorder_feeds_focus_from_the_realtime_path():
    # The bridge is easy to write and easy to leave unwired; a source check
    # catches that, the way the bleed-counter wiring test does.
    import inspect

    from wisprlite.meeting import MeetingRecorder

    source = inspect.getsource(MeetingRecorder._write_block)
    assert "focus_session" in source and "feed(label, pcm)" in source, (
        "the realtime path must actually feed the focus session"
    )


def test_every_path_out_of_a_meeting_releases_the_socket():
    # A leaked Deepgram stream keeps BILLING for the rest of the app's life, so
    # every exit has to release it — not just the obvious one. The review found
    # two that did not: the recorder stopping ITSELF at the max-minutes limit,
    # and _meeting.start() raising after focus was already running.
    import inspect

    from wisprlite import app

    for name in ("toggle_meeting", "_on_meeting_auto_stop", "shutdown"):
        method = getattr(app.App, name, None)
        if method is None:
            continue
        source = inspect.getsource(method)
        if "focus" in source or "_meeting.stop()" in source or "_meeting_active = False" in source:
            assert "_stop_pipefocus()" in source, (
                f"{name} can end a meeting without releasing the focus socket"
            )

    # ...including the path where the recorder fails to start at all.
    toggle = inspect.getsource(app.App.toggle_meeting)
    after_start_failure = toggle.split("except Exception as exc:")[-1]
    assert "_stop_pipefocus()" in after_start_failure, (
        "focus starts BEFORE the recorder; if the recorder throws it must be released"
    )


def test_a_repeating_connect_failure_is_logged_once_not_once_per_retry(caplog):
    """One meeting with no key wrote ~400 identical lines into the log."""
    import logging
    from wisprlite import focus as focus_mod

    session = focus_mod.FocusSession.__new__(focus_mod.FocusSession)
    session._connect = lambda _cb: (_ for _ in ()).throw(
        RuntimeError("no Deepgram API key"))

    with caplog.at_level(logging.INFO, logger="wisprlite"):
        for _ in range(50):
            assert session._open_conn() is False

    same = [r for r in caplog.records if "no Deepgram API key" in r.getMessage()]
    assert len(same) == 1, f"logged {len(same)} times"


def test_a_different_failure_still_gets_logged(caplog):
    """Deduping must not swallow a NEW problem."""
    import logging
    from wisprlite import focus as focus_mod

    session = focus_mod.FocusSession.__new__(focus_mod.FocusSession)
    errors = iter(["no Deepgram API key", "no Deepgram API key",
                   "connection refused", "connection refused"])

    def _connect(_cb):
        raise RuntimeError(next(errors))

    session._connect = _connect
    with caplog.at_level(logging.INFO, logger="wisprlite"):
        for _ in range(4):
            session._open_conn()

    msgs = [r.getMessage() for r in caplog.records if "connect failed" in r.getMessage()]
    assert len(msgs) == 2, msgs
