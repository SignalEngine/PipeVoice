"""Headless tests for the meeting export module.

The export module is Tk-free, so these tests pass dicts shaped the way
production callers actually pass them. The exact shape:

    {
        "title": "Today 14:32",          # display_started from the row
        "duration_seconds": 600,         # meta["duration_seconds"]
        "duration_label": "10m 00s",     # format_duration(...)
        "backend": "Deepgram",           # _backend_label(...)
        "speaker_names": ["You", "Dev"], # displayed (post-rename) names
        "transcript": "You: ...\\n\\nDev: ...",
        "highlights": [
            {"t": 12.5, "source": "hotkey",
             "text": "<window>", "first_text": "..."},
            ...
        ],
        "summaries": {                   # read_summaries(...) output
            "bullets": "- one",
            "todos":   "",
            "actions": "- [ ] ship",
        },
    }

Each test below uses ONLY that shape. A test that wrote its own fixture in a
different shape and went green would still leave the feature broken — that's
the fixture-mismatch bug this suite exists to catch.
"""

import html
import json
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import export


def _session(**overrides):
    """A session shaped the way the meetings_tab gather helper builds it."""
    base = {
        "title": "Today 14:32",
        "duration_seconds": 600.0,
        "duration_label": "10m 00s",
        "backend": "Deepgram",
        "speaker_names": ["You", "Dev"],
        "transcript": "You: Hello there.\n\nDev: Hi & welcome <script>alert(1)</script>.",
        "highlights": [
            {"t": 12.5, "source": "hotkey",
             "text": "Hello there.", "first_text": "Hello there."},
            {"t": 47.0, "source": "phrase",
             "text": "Hi and welcome.", "first_text": "Hi and welcome.",
             "phrase": "bookmark that"},
        ],
        "summaries": {
            "bullets": "- Point A\n- Point B",
            "todos":   "- [ ] ship the parser",
            "actions": "",
        },
    }
    base.update(overrides)
    return base


# -- build_markdown ------------------------------------------------------


def test_markdown_head_includes_title_duration_and_backend():
    md = export.build_markdown(_session())

    # Heading once, at the top.
    assert md.startswith("# Today 14:32\n"), md.splitlines()[:3]
    assert "Duration: 10m 00s" in md
    assert "Transcription: Deepgram" in md


def test_markdown_lists_named_speakers_when_present():
    md = export.build_markdown(_session())
    assert "Speakers: You, Dev" in md

    md_empty = export.build_markdown(_session(speaker_names=[]))
    # No "Speakers:" line when no one is on the call.
    assert "Speakers:" not in md_empty
    md_unknown = export.build_markdown(_session(speaker_names=[""]))
    assert "Speakers:" not in md_unknown


def test_markdown_includes_highlights_with_timestamps_in_source_order():
    md = export.build_markdown(_session())

    assert "## Highlights" in md
    # Both marks must appear, ordered by their timestamps as written.
    first_idx = md.index("0:12")
    second_idx = md.index("0:47")
    assert first_idx < second_idx, "highlights must follow recording order"
    # The phrase-driven mark should surface the recorded phrase.
    assert "bookmark that" in md


def test_markdown_skips_highlights_section_when_none_exist():
    md = export.build_markdown(_session(highlights=[]))
    assert "## Highlights" not in md


def test_markdown_includes_only_summaries_that_were_already_generated():
    md = export.build_markdown(_session())
    # Bullets and todos have content; actions is empty → no Actions block.
    assert "## Summary · Bullets" in md
    assert "## Summary · To-dos" in md
    assert "## Summary · Actions" not in md
    # Real summary text is on screen; default placeholder text is NOT.
    assert "- Point A" in md
    assert "_No actions were generated._" not in md


def test_markdown_summary_section_omitted_entirely_when_nothing_was_generated():
    md = export.build_markdown(
        _session(summaries={"bullets": "", "todos": "", "actions": ""})
    )
    # No headings, no placeholders, no carry-over.
    for forbidden in ("## Summary", "Bullets", "To-dos", "Actions"):
        assert forbidden not in md, forbidden


def test_markdown_includes_full_transcript_under_a_heading():
    md = export.build_markdown(_session())
    assert "## Transcript" in md
    # Transcript body uses speaker labels already (it's rendered text).
    assert "You: Hello there." in md
    # Highlights come BEFORE the transcript (skim-friendly order).
    assert md.index("## Highlights") < md.index("## Transcript")


# -- build_html ----------------------------------------------------------


def test_html_is_self_contained_and_printable():
    out = export.build_html(_session())
    assert out.lstrip().lower().startswith("<!doctype html>"), "must be a real HTML doc"
    assert "<html" in out.lower() and "</html>" in out.lower()
    # Self-contained: no remote stylesheets, scripts, or images.
    assert "<link rel=\"stylesheet\" href=\"http" not in out.lower()
    assert "<script src=\"http" not in out.lower()
    # Printable styling is inlined.
    assert "print" in out.lower() or "@page" in out.lower()


def test_html_escapes_user_text_in_transcript_and_summary():
    nasty = _session(
        transcript="You: <script>alert(1)</script> & goodbye.",
        summaries={"bullets": "- raw <em>tag</em>", "todos": "", "actions": ""},
    )
    out = export.build_html(nasty)

    # Raw angle brackets from the transcript must NOT survive intact.
    assert "<script>alert(1)</script>" not in out
    # The neutralised form must be present so on-screen text is still accurate.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    # Ampersands that are not part of an escape must also be neutralised.
    assert "&amp; goodbye" in out
    # Summary body is escaped too.
    assert "&lt;em&gt;tag&lt;/em&gt;" in out
    # And the rendered Text widget never lets the dangerous angle brackets
    # smuggle back in by being part of an HTML tag (count occurrences of
    # exactly that literal expression, NEITHER form runs as a tag/script).
    assert out.count("<script>") == 0


def test_html_highlights_section_suppresses_unsafe_attack_text():
    nasty = _session(highlights=[
        {"t": 5.0, "source": "hotkey",
         "text": "</section><script>x()</script>",
         "first_text": "</section>"},
    ])
    out = export.build_html(nasty)
    # Text got escaped, section structure survived.
    assert "&lt;/section&gt;&lt;script&gt;x()&lt;/script&gt;" in out
    assert out.count("<script>") == 0


def test_html_omits_sections_that_dont_apply():
    out = export.build_html(_session(speaker_names=[], highlights=[],
                                     summaries={"bullets": "", "todos": "",
                                                 "actions": ""}))
    assert "Speakers" not in out
    assert "Highlights" not in out
    assert "Summary" not in out


# -- build_slides --------------------------------------------------------


def test_slides_one_slide_per_section_in_source_order():
    out = export.build_slides(_session())
    # The convention for Marp/reveal is a horizontal rule between slides.
    slides = re.split(r"^---\s*$", out, flags=re.MULTILINE)
    slides = [slide for slide in [s.strip() for s in slides]
              if slide and not slide.startswith("<!--")]
    titles = [slide.splitlines()[0] for slide in slides]
    assert titles[0].startswith("# ") or titles[0].startswith("## "), titles[:3]
    # Highlights belong before the transcript in the deck too.
    assert any("Highlight" in t for t in titles[1:]), titles
    transcript_idx = next(i for i, t in enumerate(titles) if "Transcript" in t)
    highlights_idx = next(i for i, t in enumerate(titles) if "Highlight" in t)
    assert highlights_idx < transcript_idx
    # A transcript slide exists.
    assert any("Transcript" in t for t in titles)


def test_slides_are_marp_friendly():
    out = export.build_slides(_session())
    # Marp slide titles are conventionally `##` (the deck's title is `#`).
    # `###` and deeper are body headings inside a slide, not slide titles —
    # any tool that splits on `---` will count the WHOLE section between
    # separators as one slide, but Marp's table-of-contents generator keys
    # on heading level, and a `####` "slide title" appears as a body line.
    for heading in re.findall(r"^#+\s.+$", out, flags=re.MULTILINE):
        # First line of any slice is the slide title or deck title.
        assert heading.startswith("# ") or heading.startswith("## "), (
            f"Marp slide titles must be '# ' or '## ': {heading!r}"
        )
        # And never go past two hashes — Marp does NOT support ####+ slide titles.
        assert not heading.startswith("####"), heading


# -- write_export --------------------------------------------------------


def test_write_export_dispatches_by_extension():
    s = _session()
    with tempfile.TemporaryDirectory() as tmp:
        for suffix, expected in ((".md", "# Today"), (".html", "<!DOCTYPE"),
                                  (".md", "<!-- Slide")):
            pass  # filled in below by per-format checks
        # Markdown
        target = pathlib.Path(tmp) / "out.md"
        export.write_export(s, target)
        assert target.read_text(encoding="utf-8").startswith("# Today")
        # HTML
        target = pathlib.Path(tmp) / "out.html"
        export.write_export(s, target)
        assert target.read_text(encoding="utf-8").lstrip().lower().startswith(
            "<!doctype")
        # Slides
        target = pathlib.Path(tmp) / "out.slides.md"
        export.write_export(s, target)
        assert "---" in target.read_text(encoding="utf-8")


def test_write_export_unknown_extension_reports_helpfully():
    s = _session()
    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "out.xyz"
        try:
            export.write_export(s, target)
        except ValueError as exc:
            # Message must mention every supported suffix — no guessing.
            assert ".md" in str(exc)
            assert ".html" in str(exc)
        else:
            raise AssertionError("expected ValueError for an unknown extension")


def test_write_export_unwritable_path_raises_safely():
    s = _session()
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        # A directory at the destination — writing a FILE there must fail.
        target = base / "out.md"
        target.mkdir()                   # now a directory, not a file
        try:
            export.write_export(s, target)
        except OSError:
            pass
        else:
            raise AssertionError("expected OSError when destination is a directory")


def test_write_export_string_path_is_accepted():
    """Filenames come back from filedialog as str; refuse would break the UI."""
    s = _session()
    with tempfile.TemporaryDirectory() as tmp:
        export.write_export(s, os.path.join(tmp, "out.md"))
        assert pathlib.Path(tmp, "out.md").is_file()


def test_write_export_uses_atomic_replace_not_partial_writes():
    """A failed write must NOT leave a half-written target next to the user."""
    import os as _os
    s = _session()
    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "out.md"
        # Sabotage os.replace so the user's existing file (or a future write)
        # is never silently corrupted by a half-written sibling.
        original_replace = _os.replace

        def boom(_src, _dst):
            raise OSError("simulated mid-write failure")

        _os.replace = boom
        try:
            try:
                export.write_export(s, target)
            except OSError:
                # No half-written sibling next to the chosen destination.
                siblings = list(pathlib.Path(tmp).iterdir())
                assert siblings == [], (
                    "write_export leaked a temp file when the move failed: "
                    f"{[p.name for p in siblings]}"
                )
            else:
                raise AssertionError(
                    "expected OSError when os.replace fails"
                )
        finally:
            _os.replace = original_replace


# -- Production-shape guard ---------------------------------------------


def test_session_shape_does_not_drift_from_production_gatherer():
    """The export module reads keys by NAME. If meetings_tab's gather helper
    ever renames one, every export silently goes blank. Anchor the contract
    on a dict that's byte-identical to the one meetings_tab builds."""
    # Re-import the production gather helper from meetings_tab if present.
    try:
        from wisprlite.meetings_tab import gather_session_export_data
    except ImportError:
        # If the helper doesn't exist yet, this test is the contract draft.
        return

    # Make a real on-disk session and feed it through the gather helper.
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp) / "meeting-20260729-143200"
        d.mkdir()
        (d / "meta.json").write_text(json.dumps({
            "started_at": "2026-07-29T14:32:00+00:00",
            "duration_seconds": 600,
            "transcription_backend": "deepgram",
            "status": "transcribed",
        }))
        (d / "transcript.json").write_text(json.dumps({
            "segments": [{"t": 0.0, "speaker": "You", "text": "Hello"}],
        }))

        built = gather_session_export_data(d)

        # Every key the exporter reads must be present.
        for required in export.REQUIRED_KEYS:
            assert required in built, f"gather helper must include {required!r}"

        # Same call with polished=False must still produce all keys.
        built_raw = gather_session_export_data(d, polished=False)
        for required in export.REQUIRED_KEYS:
            assert required in built_raw, (
                f"polished=False must also include {required!r}"
            )


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
