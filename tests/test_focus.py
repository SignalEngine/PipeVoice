"""PipeFocus policy: the feature is only as good as its restraint."""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

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
