"""Dev terms must work on install, and must never argue with the user.

James, 2026-09-04, watching it fail live mid-conversation: "the normal polish is
actually just done claw.md. Perfectly well. oh not that time." Same term, two
dictations, two different answers - because `vocabulary` and `replacements` both
shipped EMPTY and whether "CLAUDE.md" survived was down to the polish model's
guess that run.
"""

import pathlib
import sys
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import starter_vocab
from wisprlite.config import Config


def test_the_term_that_started_this_is_covered():
    fixes = starter_vocab.FIXES
    assert {v for v in fixes.values() if v == "CLAUDE.md"}, "CLAUDE.md has no fix"
    assert "claw dot md" in fixes and fixes["claw dot md"] == "CLAUDE.md"


def test_a_users_own_terms_survive_the_merge():
    """Merged, never assigned. Someone's hand-built list must not be replaced."""
    vocab, fixes = starter_vocab.merge_into("Acme, MyProduct", {"foo": "bar"})
    terms = [t.strip() for t in vocab.split(",")]
    assert "Acme" in terms and "MyProduct" in terms
    assert fixes["foo"] == "bar"


def test_a_users_fix_is_never_overwritten():
    """If they already decided 'jason' means a person's name, keep it."""
    _, fixes = starter_vocab.merge_into("", {"jason": "Jason"})
    assert fixes["jason"] == "Jason", "the starter list overwrote a user's own fix"


def test_a_term_the_user_already_has_is_not_duplicated():
    vocab, _ = starter_vocab.merge_into("npm", {})
    terms = [t.strip().lower() for t in vocab.split(",")]
    assert terms.count("npm") == 1, f"npm appears {terms.count('npm')} times"


def test_the_list_stays_a_dev_list_not_a_dictionary():
    """Every term costs prompt budget on EVERY utterance. Growth is the failure."""
    assert len(starter_vocab.TERMS) <= 80, (
        f"{len(starter_vocab.TERMS)} terms - this is becoming a dictionary, "
        "and each one is paid for on every single dictation"
    )


def test_seeding_runs_once_and_respects_a_deletion():
    """Delete a term you do not want; it must not come back at the next launch."""
    cfg = Config()
    assert cfg.apply_starter_vocab() is True
    assert cfg.starter_vocab_seeded is True
    cfg.vocabulary = "OnlyWhatIWant"
    assert cfg.apply_starter_vocab() is False, "the seed ran a second time"
    assert cfg.vocabulary == "OnlyWhatIWant", "a deleted term was restored"


def test_opting_out_means_nothing_is_seeded():
    cfg = Config()
    cfg.starter_vocab = False
    assert cfg.apply_starter_vocab() is False
    assert cfg.vocabulary == ""
    assert cfg.replacements == {}


def test_a_broken_starter_list_cannot_stop_the_app_loading():
    cfg = Config()
    with mock.patch.object(starter_vocab, "merge_into", side_effect=RuntimeError("boom")):
        assert cfg.apply_starter_vocab() is False
    assert cfg.starter_vocab_seeded is False, "a failed seed must not mark itself done"
