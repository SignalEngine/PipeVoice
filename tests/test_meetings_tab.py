"""Headless tests for the pure Meetings-tab helpers."""

import json
import os
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import meetings_tab


def _session(
    base,
    name,
    *,
    started_at,
    stopped_at="2026-07-27T12:00:00+00:00",
    duration=0,
    status=None,
    error=None,
    backend=None,
):
    path = base / name
    path.mkdir()
    meta = {
        "started_at": started_at,
        "stopped_at": stopped_at,
        "duration_seconds": duration,
        "mic": {"file": "mic.wav", "error": error},
        "desktop": {"file": "desktop.wav", "error": None},
    }
    if status:
        meta["status"] = status
    if backend:
        meta["transcription_backend"] = backend
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
            backend="deepgram",
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
        assert listed[transcribed]["speaker_names"] == ["You", "Them"]
        assert listed[transcribed]["transcription_backend"] == "Deepgram"


def test_live_session_is_recording_and_not_transcribable():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        live = _session(
            base,
            "meeting-live",
            started_at="2026-07-27T12:00:00+00:00",
            stopped_at=None,
            duration=30,
        )
        meta_path = live / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["recording_pid"] = os.getpid()
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        (live / "desktop.wav").write_bytes(b"R" * 45)

        [session] = meetings_tab.list_sessions(base)

        assert session["status"] == "recording"
        assert session["can_transcribe"] is False


def test_orphaned_session_is_recovered_and_transcribable():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        orphan = _session(
            base,
            "meeting-orphan",
            started_at="2026-07-27T12:00:00+00:00",
            stopped_at=None,
            duration=42,
        )
        meta_path = orphan / "meta.json"
        (orphan / "desktop.wav").write_bytes(b"R" * 45)
        # Simulate the crash the way a crash actually presents: the process died,
        # so meta.json stopped being checkpointed and its mtime went stale.
        # (This previously faked a dead pid, but liveness must NOT be probed with
        # os.kill — on Windows that terminates the target rather than testing it.)
        stale = time.time() - 120
        os.utime(meta_path, (stale, stale))

        [session] = meetings_tab.list_sessions(base)

        assert session["status"] == "recorded"
        assert session["can_transcribe"] is True
        assert json.loads(meta_path.read_text(encoding="utf-8"))["stopped_at"]


def test_duration_formatting():
    assert meetings_tab.format_duration(None) == "0s"
    assert meetings_tab.format_duration(45.9) == "45s"
    assert meetings_tab.format_duration(90) == "1m 30s"
    assert meetings_tab.format_duration(3725) == "1h 02m"


def test_meetings_signature_tracks_dirs_and_meta_mtime_only():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        session = base / "meeting-one"
        session.mkdir()
        meta = session / "meta.json"
        meta.write_text("{}", encoding="utf-8")
        first = meetings_tab.meetings_signature(base)

        (session / "mic.wav").write_bytes(b"audio")
        assert meetings_tab.meetings_signature(base) == first

        stat = meta.stat()
        meta.write_text('{"stopped_at": true}', encoding="utf-8")
        meta.touch()
        if meta.stat().st_mtime_ns == stat.st_mtime_ns:
            time.sleep(0.002)
            meta.touch()
        second = meetings_tab.meetings_signature(base)
        assert second != first

        (base / "meeting-two").mkdir()
        assert meetings_tab.meetings_signature(base) != second


def test_live_checkpoint_metadata_does_not_churn_signature():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        session = _session(
            base,
            "meeting-live",
            started_at="2026-07-27T12:00:00+00:00",
            stopped_at=None,
        )
        first = meetings_tab.meetings_signature(base)
        meta_path = session / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["duration_seconds"] = 17
        meta["recording_pid"] = os.getpid()
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        assert meetings_tab.meetings_signature(base) == first


def test_search_match_index_cycles_both_directions():
    assert meetings_tab.cycle_match_index(-1, 0, 1) == -1
    assert meetings_tab.cycle_match_index(-1, 3, 1) == 0
    assert meetings_tab.cycle_match_index(2, 3, 1) == 0
    assert meetings_tab.cycle_match_index(0, 3, -1) == 2
    assert meetings_tab.cycle_match_index(-1, 3, -1) == 2


def test_a_crashed_session_is_recoverable_not_orphaned_forever():
    """A crash leaves stopped_at unset. Treating that alone as "live" orphaned the
    session forever: not transcribable, not deletable, duration ticking up. The WAV
    headers are patched at checkpoints precisely so a crashed capture survives."""
    import json, os, tempfile, time
    from wisprlite.meetings_tab import derive_status

    with tempfile.TemporaryDirectory() as d:
        s = os.path.join(d, "meeting-20260727-2100")
        os.makedirs(s)
        meta_path = os.path.join(s, "meta.json")
        json.dump({"started_at": "2026-07-27T21:00:00+00:00", "stopped_at": None,
                   "duration_seconds": 60}, open(meta_path, "w"))
        assert derive_status(s) == "recording", "a fresh heartbeat is genuinely live"
        stale = time.time() - 120
        os.utime(meta_path, (stale, stale))
        assert derive_status(s) == "recorded", "a stale heartbeat is an orphan, not live"


def test_liveness_never_signals_a_pid():
    """os.kill(pid, 0) is the POSIX liveness idiom and is CATASTROPHIC here: on
    Windows, the only platform this app ships on, any signal other than
    CTRL_C_EVENT/CTRL_BREAK_EVENT unconditionally terminates the target via
    TerminateProcess. Probing the recorder would have killed it mid-meeting, just
    by opening the Meetings tab."""
    import inspect
    from wisprlite import meetings_tab

    source = inspect.getsource(meetings_tab.derive_status)
    code = "\n".join(line.split("#")[0] for line in source.splitlines())
    assert "os.kill" not in code, "derive_status must not signal a pid to test liveness"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all passed")
