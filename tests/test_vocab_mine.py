"""Offline vocabulary mining tests."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite.vocab_mine import mine_candidates


def test_corrections_rank_first_and_count_saved_replacements():
    sessions = [
        {"name": "one", "text": "I asked Dave about the launch."},
        {"name": "two", "text": "Dave will send it."},
    ]
    result = mine_candidates(sessions, "", [{"Dave": "Dev"}, {"Dave": "Dev"}])

    assert result[0] == {
        "term": "Dev", "count": 2, "sessions": 2, "source": "correction"
    }


def test_capitalised_mid_sentence_beats_repeated_and_sentence_initial_is_not_enough():
    sessions = [
        {"text": "Mira joined the call. We discussed Atlas and Atlas."},
        {"text": "Atlas shipped today. I thanked the team."},
    ]
    result = mine_candidates(sessions, [], None)

    assert result[0]["term"] == "Atlas"
    assert result[0]["source"] == "capitalised"
    assert not any(item["term"] == "Mira" for item in result)


def test_repeated_terms_need_three_occurrences_across_two_sessions():
    sessions = [
        {"text": "kubernetes kubernetes signal"},
        {"text": "kubernetes signal signal"},
    ]
    result = mine_candidates(sessions, "", {})

    kubernetes = next(item for item in result if item["term"] == "kubernetes")
    assert kubernetes == {
        "term": "kubernetes", "count": 3, "sessions": 2, "source": "repeated"
    }


def test_known_vocabulary_is_case_insensitive_and_empty_sessions_are_safe():
    sessions = [None, {"text": "Acme acme Acme"}, {"text": "Acme"}]
    assert mine_candidates(sessions, "ACME", None) == []
    assert mine_candidates([{}], [], None) == []


def test_accented_and_non_ascii_tokens_survive():
    result = mine_candidates(
        [{"text": "Wir trafen José. José sprach mit José."}], [], None
    )
    jose = next(item for item in result if item["term"] == "José")
    assert jose["count"] == 3
    assert jose["source"] == "capitalised"


def test_no_api_key_or_network_is_needed():
    assert mine_candidates([{"text": "Offline works."}], [], None) == []


def test_speaker_labels_do_not_manufacture_jargon():
    # Each segment is a separate utterance, so its first word is sentence-initial.
    # This MUST use the shape settings.py actually passes -- {"segments": [...]} --
    # not a pre-rendered string. The first version of this test used a rendered
    # string, a form the UI never produces, so it passed while the shipped code
    # returned filler words ("ok, before, today, should, review") on real data.
    session = {
        "name": "m1",
        "segments": [
            {"speaker": "You", "text": "Thanks for joining"},
            {"speaker": "You", "text": "Dave will send the Kubernetes config"},
            {"speaker": "Them 1", "text": "Sure"},
            {"speaker": "Them 1", "text": "Anthropic uses Kubernetes too"},
            {"speaker": "You", "text": "Great"},
            {"speaker": "You", "text": "Can you ping Anthropic about Kubernetes"},
        ],
    }
    terms = [c["term"] for c in mine_candidates([session, session], "", [{}, {}])]
    for noise in ("Thanks", "Sure", "Great", "Can"):
        assert noise not in terms, f"{noise} should not be a vocabulary candidate"
    assert "Kubernetes" in terms
    assert "Anthropic" in terms


def test_segments_are_joined_on_a_boundary_not_a_space():
    # Joining segments with " " erases the utterance boundary, so the next
    # segment's opening word scores as mid-sentence jargon.
    session = {"segments": [
        {"text": "we should ship it"},
        {"text": "Before the call"},
    ]}
    terms = [c["term"] for c in mine_candidates([session] * 2, "", [{}, {}])]
    assert "Before" not in terms
