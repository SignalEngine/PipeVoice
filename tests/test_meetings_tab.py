"""Headless tests for the pure Meetings-tab helpers."""

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import meetings_tab


def _session(base, name, *, started_at, duration=0, status=None, error=None):
    path = base / name
    path.mkdir()
    meta = {
        "started_at": started_at,
        "duration_seconds": duration,
        "mic": {"file": "mic.wav", "error": error},
        "desktop": {"file": "desktop.wav", "error": None},
    }
    if status:
        meta["status"] = status
    if status == "transcription_failed":
        meta["transcription_error"] = "RuntimeError: failed"
    (path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return path


def test_session_listing_is_newest_first():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        older = _session(
            base,
            "meeting-20260727-090000",
            started_at="2026-07-27T09:00:00+00:00",
            duration=65,
        )
        newer = _session(
            base,
            "meeting-20260727-110000",
            started_at="2026-07-27T11:00:00+00:00",
            duration=30,
        )

        sessions = meetings_tab.list_sessions(base)

        assert [item["path"] for item in sessions] == [newer, older]
        assert sessions[0]["duration"] == "30s"
        assert sessions[1]["duration"] == "1m 05s"


def test_status_derivation_recorded_transcribed_and_error():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        recorded = _session(
            base,
            "meeting-recorded",
            started_at="2026-07-27T09:00:00+00:00",
        )
        transcribed = _session(
            base,
            "meeting-transcribed",
            started_at="2026-07-27T10:00:00+00:00",
            error="RuntimeError: one stream failed",
        )
        (transcribed / "transcript.json").write_text(
            json.dumps(
                {
                    "segments": [
                        {"speaker": "You", "text": "Hello"},
                        {"speaker": "Them", "text": "Hi"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        failed = _session(
            base,
            "meeting-error",
            started_at="2026-07-27T11:00:00+00:00",
            status="transcription_failed",
        )

        assert meetings_tab.derive_status(recorded) == "recorded"
        assert meetings_tab.derive_status(transcribed) == "transcribed"
        assert meetings_tab.derive_status(failed) == "error"
        malformed = base / "meeting-malformed"
        malformed.mkdir()
        (malformed / "meta.json").write_text("{", encoding="utf-8")
        assert meetings_tab.derive_status(malformed) == "error"
        assert {
            item["path"]: item["status"]
            for item in meetings_tab.list_sessions(base)
        }[malformed] == "error"
        listed = {item["path"]: item for item in meetings_tab.list_sessions(base)}
        assert listed[transcribed]["speaker_count"] == 2


def test_duration_formatting():
    assert meetings_tab.format_duration(None) == "0s"
    assert meetings_tab.format_duration(45.9) == "45s"
    assert meetings_tab.format_duration(90) == "1m 30s"
    assert meetings_tab.format_duration(3725) == "1h 02m"


def test_search_match_index_cycles_both_directions():
    assert meetings_tab.cycle_match_index(-1, 0, 1) == -1
    assert meetings_tab.cycle_match_index(-1, 3, 1) == 0
    assert meetings_tab.cycle_match_index(2, 3, 1) == 0
    assert meetings_tab.cycle_match_index(0, 3, -1) == 2
    assert meetings_tab.cycle_match_index(-1, 3, -1) == 2


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
