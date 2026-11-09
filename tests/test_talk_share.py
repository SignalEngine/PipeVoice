"""Talk-time hint: rolling mic/call loudness share, and the app tip (once per 10 minutes)."""

import pathlib
import sys
import types
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from uistub import install_platform_stubs

install_platform_stubs()

from wisprlite import meeting
from wisprlite.meeting import BLEED_LIVE_LEVEL, MeetingRecorder

LOUD, QUIET = BLEED_LIVE_LEVEL * 3, 0.0


def _rec(tmp_path):
    return MeetingRecorder(tmp_path)


def _feed(rec, seconds, mic, desktop, t0, step=0.5):
    """Feed `seconds` of blocks; returns the clock after them."""
    t = t0
    for _ in range(int(seconds / step)):
        rec._talk_block(mic, desktop, step, t)
        t += step
    return t


def test_three_minutes_me_and_one_minute_them_is_75_percent(tmp_path):
    rec = _rec(tmp_path)
    t = _feed(rec, 180, LOUD, QUIET, 0.0)
    t = _feed(rec, 60, QUIET, LOUD, t)
    assert abs(rec.talk_share(now=t) - 0.75) <= 0.05


def test_under_two_minutes_of_talk_is_none(tmp_path):
    rec = _rec(tmp_path)
    t = _feed(rec, 60, LOUD, QUIET, 0.0)
    t = _feed(rec, 59, QUIET, LOUD, t)
    assert rec.talk_share(now=t) is None


def test_both_loud_or_both_quiet_counts_for_neither(tmp_path):
    rec = _rec(tmp_path)
    t = _feed(rec, 300, LOUD, LOUD, 0.0)
    t = _feed(rec, 300, QUIET, QUIET, t)
    assert rec.talk_share(now=t) is None


def test_only_the_last_five_minutes_count(tmp_path):
    rec = _rec(tmp_path)
    t = _feed(rec, 300, LOUD, QUIET, 0.0)         # me, then it ages out
    t = _feed(rec, 300, QUIET, LOUD, t)           # them
    assert rec.talk_share(now=t) == 0.0
    assert len(rec._talk) <= 600 + 2              # trimmed from the left, bounded


def test_a_new_recording_starts_with_no_history(tmp_path):
    rec = _rec(tmp_path)
    _feed(rec, 200, LOUD, QUIET, 0.0)
    rec._reset_talk()
    assert rec.talk_share(now=200.0) is None and len(rec._talk) == 0


def test_write_block_feeds_the_share_from_the_mic_timeline(tmp_path):
    np = __import__("numpy")
    rec = _rec(tmp_path)
    sr = meeting.SAMPLE_RATE
    loud = np.full(sr // 2, LOUD, dtype=np.float32)
    quiet = np.zeros(sr // 2, dtype=np.float32)
    rec._waves = {}
    for _ in range(300):                          # 150 s of me talking over a quiet call
        rec._write_block("desktop", quiet)
        rec._write_block("mic", loud)
    assert rec._talk and rec._talk[0][1] == "me"
    assert rec.talk_share(now=rec._talk[-1][0]) == 1.0


# ---- app tip -----------------------------------------------------------------

def _app(share, hint=True):
    from wisprlite import app as app_mod, config

    a = app_mod.App.__new__(app_mod.App)
    a.cfg = config.Config(meeting_talk_hint=hint)
    a._meeting_active = True
    a._meeting = types.SimpleNamespace(talk_share_recent=share)
    a.overlay = mock.Mock()
    return a


def test_hint_shows_once_then_not_again_within_ten_minutes():
    a = _app(0.8)
    a._check_talk_hint(now=100.0)
    a.overlay.show_focus_tip.assert_called_once_with(
        "You've done most of the talking for a few minutes.", "talk share 80%")
    for t in (120.0, 400.0, 699.0):
        a._check_talk_hint(now=t)
    assert a.overlay.show_focus_tip.call_count == 1
    a._check_talk_hint(now=720.0)
    assert a.overlay.show_focus_tip.call_count == 2


def test_hint_is_checked_at_most_every_15_seconds():
    a = _app(0.5)
    a._check_talk_hint(now=100.0)
    a._meeting.talk_share_recent = 0.9
    a._check_talk_hint(now=110.0)                 # too soon: not even read
    assert a.overlay.show_focus_tip.call_count == 0
    a._check_talk_hint(now=116.0)
    assert a.overlay.show_focus_tip.call_count == 1


def test_no_hint_when_off_below_threshold_or_unknown():
    for share, hint in ((0.9, False), (0.69, True), (None, True)):
        a = _app(share, hint)
        a._check_talk_hint(now=100.0)
        assert a.overlay.show_focus_tip.call_count == 0, (share, hint)
    a = _app(0.70)
    a._check_talk_hint(now=100.0)
    assert a.overlay.show_focus_tip.call_count == 1
