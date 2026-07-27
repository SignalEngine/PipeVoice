"""Headless tests for meeting summary prompts, chunking, and persistence."""

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import summarise as summary


def segments(count):
    return [
        {"t": index * 10, "speaker": "You", "text": f"segment-{index}"}
        for index in range(count)
    ]


def test_chunk_boundaries_and_overlap_keep_the_tail():
    chunks = summary._chunks(segments(10), chunk_size=4, overlap=1)
    assert [[item["text"] for item in chunk] for chunk in chunks] == [
        ["segment-0", "segment-1", "segment-2", "segment-3"],
        ["segment-3", "segment-4", "segment-5", "segment-6"],
        ["segment-6", "segment-7", "segment-8", "segment-9"],
    ]


def test_map_reduce_triggers_only_above_threshold():
    calls = []

    def stub(messages, provider, model):
        calls.append((messages, provider, model))
        return "- result"

    result = summary.summarise(
        segments(4),
        "bullets",
        "ollama",
        chunk_size=4,
        overlap=1,
        completion=stub,
    )
    assert result == "- result"
    assert len(calls) == 1
    assert "PARTIAL SUMMARIES:" not in calls[0][0][1]["content"]

    calls.clear()
    result = summary.summarise(
        segments(5),
        "bullets",
        "ollama",
        chunk_size=4,
        overlap=1,
        completion=stub,
    )
    assert len(calls) == 3  # two map calls, then one reduce call
    assert "PARTIAL SUMMARIES:" in calls[-1][0][1]["content"]
    assert result.endswith("_(summarised in 2 parts)_")


def test_chunk_prompts_include_overlap_timestamps_and_speaker_labels():
    calls = []

    def stub(messages, _provider, _model):
        calls.append(messages)
        return "- result"

    summary.summarise(
        segments(5),
        "todos",
        "ollama",
        chunk_size=4,
        overlap=1,
        completion=stub,
    )
    first = calls[0][1]["content"]
    second = calls[1][1]["content"]
    assert "[0:00] You:" in first
    assert "segment-3" in first
    assert "[0:30] You: segment-3" in second
    assert "segment-4" in second


def test_prompt_selection_per_mode():
    prompts = {}

    def stub(messages, _provider, _model):
        prompts[current_mode] = messages[0]["content"]
        return "- result"

    for current_mode in summary.MODES:
        summary.summarise(
            segments(1),
            current_mode,
            "ollama",
            completion=stub,
        )

    assert "grouped by topic" in prompts["bullets"]
    assert "Markdown to-do list" in prompts["todos"]
    assert "'- [ ] <task> — <owner>[ — <when>]'" in prompts["actions"]
    for prompt in prompts.values():
        assert "Never invent tasks" in prompt
        assert "unassigned" in prompt


def test_no_ready_provider_returns_empty_without_calling_llm():
    original = summary.provider_ready
    called = []
    summary.provider_ready = lambda _provider: False
    try:
        result = summary.summarise(
            segments(1),
            "bullets",
            "gemini",
            completion=lambda *_args: called.append(True) or "- result",
        )
    finally:
        summary.provider_ready = original
    assert result == ""
    assert called == []


def test_empty_success_is_a_valid_empty_section():
    result = summary.summarise(
        segments(1),
        "actions",
        "ollama",
        completion=lambda *_args: "",
    )
    assert result == "_No owned actions were agreed._"


def test_rerunning_mode_replaces_only_that_persisted_section():
    answers = iter(("- first bullets", "- first actions", "- new bullets"))

    def stub(_messages, _provider, _model):
        return next(answers)

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp)
        summary.summarise(
            segments(1), "bullets", "ollama", session_dir=path, completion=stub
        )
        summary.summarise(
            segments(1), "actions", "ollama", session_dir=path, completion=stub
        )
        summary.summarise(
            segments(1), "bullets", "ollama", session_dir=path, completion=stub
        )
        source = (path / "summary.md").read_text(encoding="utf-8")
        loaded = summary.read_summaries(path)

    assert source.count("pipevoice-summary:bullets:start") == 1
    assert "Mode: bullets · Provider: ollama · Model: llama3.2:3b" in source
    assert loaded["bullets"] == "- new bullets"
    assert loaded["actions"] == "- first actions"


def test_rewriting_a_section_survives_backslashes_in_model_output():
    """re.sub treats a replacement STRING's backslashes as group references, so a
    summary mentioning a regex or a Windows path used to raise `bad escape \\d`,
    surface as "Summarising failed", discard the paid LLM call and keep the stale
    section — with every retry failing identically."""
    import tempfile
    from pathlib import Path
    from wisprlite import summarise as S

    with tempfile.TemporaryDirectory() as d:
        S._persist(d, "bullets", "gemini", "m", "- first pass, nothing unusual")
        nasty = r"- Ship the log parser for \d{4} dates; config in C:\Users\james\.pipevoice"
        S._persist(d, "bullets", "gemini", "m", nasty)          # must not raise
        saved = S.read_summaries(d)
        assert nasty in saved["bullets"], saved
        assert "first pass" not in saved["bullets"], "old section should be replaced"
        assert (Path(d) / "summary.md").read_text(encoding="utf-8").count(
            "pipevoice-summary:bullets:start") == 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
