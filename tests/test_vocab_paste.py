from wisprlite import settings


def test_pasted_comma_list_becomes_separate_terms():
    assert settings.split_terms(" OpenRouter, Battle Lifter,LaunchEngine;PWA\nbots, ") == [
        "OpenRouter", "Battle Lifter", "LaunchEngine", "PWA", "bots"]
    assert settings.split_terms("Claude Code") == ["Claude Code"]
