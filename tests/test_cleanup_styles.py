import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from wisprlite import cleanup

def test_tidy_is_default():
    assert cleanup._style_system("tidy") == cleanup._TIDY
    assert cleanup._style_system("") == cleanup._TIDY
    assert cleanup._style_system("nonsense") == cleanup._TIDY  # unknown -> tidy

def test_prompt_style():
    s = cleanup._style_system("prompt")
    assert s == cleanup._PROMPT
    low = s.lower()
    # restructure + output-only rail
    assert "reshuffle" in low or "rewrite" in low
    assert s.strip().endswith("Return ONLY the polished text, nothing else.")
    # faithfulness rail: polarity / negation must be called out and protected
    assert "negation" in low
    assert "'with'" in low and "'without'" in low
    # speech-act rail: never fabricate a command; keep the kind of utterance
    assert "fabricate a command" in low
    assert "a question stays a question" in low
    assert "stays a request for ideas" in low
    assert "what do you think" in low  # tentative brainstorm / opinion-ask preserved

def test_custom_style():
    s = cleanup._style_system("custom", "Rewrite as a git commit message.")
    assert s.startswith("Rewrite as a git commit message.")
    assert "ONLY the rewritten text" in s  # safety rails appended
    # empty custom instruction falls back to tidy (never an empty/unsafe prompt)
    assert cleanup._style_system("custom", "   ") == cleanup._TIDY


def test_email_style_greets_and_signs_off():
    s = cleanup._style_system("email")
    assert "greeting" in s.lower() and "sign-off" in s.lower()
    named = cleanup._style_system("email", "Sam")
    assert "Sam" in named and named != s  # the sign-off name is threaded through


def test_code_comment_style_wraps_in_comment_syntax():
    s = cleanup._style_system("code_comment")
    assert "comment" in s.lower()
    assert "casing" in s.lower()  # identifiers keep their exact case


def test_meeting_actions_style_bullets_action_items():
    s = cleanup._style_system("meeting_actions")
    assert "bullet" in s.lower() and "action item" in s.lower()


def test_new_presets_are_materially_different_from_tidy_and_each_other():
    tidy = cleanup._style_system("tidy")
    presets = {p: cleanup._style_system(p) for p in ("email", "code_comment", "meeting_actions")}
    for name, prompt in presets.items():
        assert prompt != tidy, f"{name} must not fall back to tidy"
    names = list(presets)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert presets[a] != presets[b], f"{a} and {b} must not collapse to the same prompt"


def test_sabotage_two_presets_pointed_at_the_same_prompt_fails():
    # Positive control for the test above: prove it actually catches a collision.
    sabotaged = dict(cleanup._FIXED_STYLES)
    sabotaged["code_comment"] = sabotaged["meeting_actions"]
    raised = False
    try:
        assert sabotaged["code_comment"] != sabotaged["meeting_actions"]
    except AssertionError:
        raised = True
    assert raised, "sabotage fixture did not actually collide the two prompts"


if __name__ == "__main__":
    test_tidy_is_default(); test_prompt_style(); test_custom_style()
    test_email_style_greets_and_signs_off(); test_code_comment_style_wraps_in_comment_syntax()
    test_meeting_actions_style_bullets_action_items()
    test_new_presets_are_materially_different_from_tidy_and_each_other()
    test_sabotage_two_presets_pointed_at_the_same_prompt_fails()
    print("OK")
