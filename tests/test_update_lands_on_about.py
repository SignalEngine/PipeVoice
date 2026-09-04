"""After an update the app must SHOW what changed, not just change.

James, 2026-09-04: "I clicked update, and they opened in the minimised toolbar
thing, but it didn't open fully. When updating, it should probably update the
full thing and then land on the about page, so I can see what's just been
updated."

The installer relaunches with /RESTARTAPPLICATIONS, which restores the app
exactly as it was — a tray icon and nothing else — so an update the user asked
for completed with no visible sign it had happened.
"""

import json
import pathlib
import sys
import time
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import updater


def _redirect(tmp_path):
    return mock.patch.object(updater.config, "config_dir", return_value=tmp_path)


def test_the_first_start_after_our_own_update_reports_it_once(tmp_path):
    with _redirect(tmp_path):
        updater.mark_pending("2.41.0")
        assert updater.take_pending("2.41.1") is True
        # Once. A marker that survived would open About on every start.
        assert updater.take_pending("2.41.1") is False


def test_a_failed_install_does_not_claim_an_update_happened(tmp_path):
    """The installer ran but the version did not move — nothing was updated,
    and the marker must still be cleared rather than firing forever."""
    with _redirect(tmp_path):
        updater.mark_pending("2.41.1")
        assert updater.take_pending("2.41.1") is False
        assert not (tmp_path / "update" / updater.PENDING).exists(), \
            "a failed update left its marker behind"


def test_a_hand_reinstall_is_not_treated_as_our_update(tmp_path):
    """No marker means we did not do it. A tray app that starts at boot must
    not open a window because someone reinstalled or restored a backup."""
    with _redirect(tmp_path):
        assert updater.take_pending("2.41.1") is False


def test_a_stale_marker_does_not_ambush_the_user_days_later(tmp_path):
    with _redirect(tmp_path):
        updater.mark_pending("2.40.0")
        path = tmp_path / "update" / updater.PENDING
        data = json.loads(path.read_text(encoding="utf-8"))
        data["at"] = time.time() - (updater.PENDING_MAX_AGE + 60)
        path.write_text(json.dumps(data), encoding="utf-8")

        assert updater.take_pending("2.41.1") is False


def test_a_corrupt_marker_is_swallowed_not_raised(tmp_path):
    """This runs during startup. It must never be able to stop the app."""
    with _redirect(tmp_path):
        path = tmp_path / "update" / updater.PENDING
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")

        assert updater.take_pending("2.41.1") is False
        assert not path.exists()


def test_download_and_run_marks_the_update_before_handing_over():
    """The marker has to be written BEFORE the installer starts: it force-closes
    this process, so anything written afterwards never runs."""
    order = []
    info = {"url": "https://example.test/Setup.exe", "sha256": ""}

    with mock.patch.object(updater, "mark_pending", side_effect=lambda v: order.append("mark")), \
         mock.patch.object(updater.subprocess, "Popen", side_effect=lambda *a, **k: order.append("spawn")), \
         mock.patch.object(updater.urllib.request, "urlopen") as urlopen, \
         mock.patch("builtins.open", mock.mock_open()), \
         mock.patch.object(updater.config, "config_dir", return_value=pathlib.Path("/tmp")):
        urlopen.return_value.__enter__.return_value.read.side_effect = [b"data", b""]
        updater.download_and_run(info)

    assert order == ["mark", "spawn"], \
        f"the marker must be written before the installer is spawned, got {order}"


def test_a_marker_that_cannot_be_deleted_is_emptied_not_trusted(tmp_path):
    """If unlink fails and the marker survives, claiming "we updated" turns a
    one-shot window into a pop-up on every boot that nobody can switch off."""
    with _redirect(tmp_path):
        updater.mark_pending("2.41.0")
        path = tmp_path / "update" / updater.PENDING

        with mock.patch.object(pathlib.Path, "unlink", side_effect=PermissionError("held")):
            first = updater.take_pending("2.41.1")

        assert first is True, "an emptied marker still counts as consumed once"
        assert path.read_text(encoding="utf-8") == "", "the marker must be neutered"
        assert updater.take_pending("2.41.1") is False, "it must never fire twice"


def test_an_unclearable_marker_stays_silent(tmp_path):
    """Neither delete nor write works — read-only folder. Say nothing rather
    than open About on every start for ever."""
    with _redirect(tmp_path):
        updater.mark_pending("2.41.0")
        with mock.patch.object(pathlib.Path, "unlink", side_effect=PermissionError("held")), \
             mock.patch.object(pathlib.Path, "write_text", side_effect=PermissionError("ro")):
            assert updater.take_pending("2.41.1") is False


def test_a_non_string_from_field_is_not_a_version(tmp_path):
    """`1 != "2.41.1"` is True, so an unvalidated field fires About on a marker
    that means nothing."""
    with _redirect(tmp_path):
        path = tmp_path / "update" / updater.PENDING
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"from": 1, "at": time.time()}), encoding="utf-8")

        assert updater.take_pending("2.41.1") is False
