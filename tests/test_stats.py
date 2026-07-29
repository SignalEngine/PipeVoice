"""Meeting stats — computed from the transcript, free and offline."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import stats


def _seg(speaker, text, t=0.0):
    # The shape transcribe_session actually writes.
    return {"speaker": speaker, "text": text, "t": t}


def test_talk_share_answers_did_someone_dominate():
    segments = [
        _seg("You", " ".join(["word"] * 90), 0.0),
        _seg("Them", " ".join(["word"] * 10), 30.0),
    ]
    rows = stats.speaking_stats(segments)
    assert [r["speaker"] for r in rows] == ["You", "Them"], "most talkative first"
    assert rows[0]["share"] == 90.0
    assert rows[1]["share"] == 10.0
    assert sum(r["share"] for r in rows) == 100.0


def test_a_monologue_is_consecutive_turns_by_the_same_person():
    # A single long segment is not the only shape a monologue takes; three turns
    # in a row with nobody interjecting is exactly what one looks like.
    segments = [
        _seg("You", " ".join(["a"] * 100), 0.0),
        _seg("You", " ".join(["a"] * 100), 10.0),
        _seg("You", " ".join(["a"] * 100), 20.0),
        _seg("Them", "right", 40.0),
        _seg("You", " ".join(["a"] * 50), 45.0),
    ]
    rows = {r["speaker"]: r for r in stats.speaking_stats(segments)}
    assert rows["You"]["longest_run_words"] == 300, "the run must not reset mid-stretch"
    assert rows["You"]["turns"] == 4


def test_questions_are_counted_without_question_marks():
    # Deepgram punctuates; local Whisper often does not. Counting only "?" would
    # report zero questions for every offline user and look broken.
    punctuated = [_seg("Them", "What is the price? It seems high.")]
    bare = [_seg("Them", "what is the price it seems high")]
    assert stats.speaking_stats(punctuated)[0]["questions"] == 1
    assert stats.speaking_stats(bare)[0]["questions"] == 1


def test_one_voice_is_not_a_comparison():
    # "You: 100%" tells nobody anything. Better to show nothing.
    assert stats.render_stats([_seg("You", "just me talking here alone")]) == ""
    assert stats.render_stats([]) == ""


def test_the_rendered_block_calls_out_domination_only_when_it_happens():
    lopsided = [_seg("You", " ".join(["w"] * 80)), _seg("Them", " ".join(["w"] * 20))]
    text = stats.render_stats(lopsided)
    assert "80.0%" in text
    assert "did 80% of the talking" in text

    balanced = [_seg("You", " ".join(["w"] * 50)), _seg("Them", " ".join(["w"] * 50))]
    assert "of the talking" not in stats.render_stats(balanced), (
        "an even meeting should not be editorialised"
    )


def test_malformed_segments_do_not_crash_it():
    segments = [_seg("You", "hello there everyone"), None, {"speaker": "Them"},
                {"text": "no speaker at all"}, _seg("Them", "hi")]
    rows = stats.speaking_stats(segments)
    assert rows, "valid segments must still be counted"
    assert stats.render_stats(segments)


def test_the_stats_panel_is_actually_wired_into_the_meetings_tab():
    # Proving the function works is not proving it is SHOWN. A component that is
    # never mounted has shipped in this project before.
    import inspect
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from uistub import install_platform_stubs
    install_platform_stubs()

    from wisprlite import meetings_tab

    source = inspect.getsource(meetings_tab.build)
    assert "render_stats(raw_segments)" in source, "the stats must be computed"
    assert "stats_panel.pack(" in source, "and actually packed"
    assert "stats_panel.pack_forget()" in source, "and cleared when there is no session"
