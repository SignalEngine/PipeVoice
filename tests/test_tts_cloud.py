"""Deepgram/ElevenLabs TTS for Read Aloud: bring-your-own-key, and every
failure (bad key, network, quota) degrades to the Windows voice instead of
going silent. Runs on Linux with the HTTP layer mocked — never hits a real API.
"""

from __future__ import annotations

import pathlib
import sys
import urllib.error
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import tts_cloud


class _Resp:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


# ---- gate 3: a mocked 200 returns audio bytes ------------------------------

def test_deepgram_speak_returns_audio_bytes_on_a_200():
    with mock.patch.object(tts_cloud.urllib.request, "urlopen",
                            return_value=_Resp(b"RIFF....WAVEfmt ")) as urlopen:
        audio = tts_cloud.deepgram_speak("hello world", "aura-2-draco-en", "fake-key")
    assert audio == b"RIFF....WAVEfmt "
    req = urlopen.call_args[0][0]
    assert "aura-2-draco-en" in req.full_url
    assert req.headers.get("Authorization") == "Token fake-key"


def test_elevenlabs_speak_returns_audio_bytes_on_a_200():
    with mock.patch.object(tts_cloud.urllib.request, "urlopen",
                            return_value=_Resp(b"ID3-mp3-bytes")) as urlopen:
        audio = tts_cloud.elevenlabs_speak("hello world", "voice123", "fake-key")
    assert audio == b"ID3-mp3-bytes"
    req = urlopen.call_args[0][0]
    assert "voice123" in req.full_url
    assert req.headers.get("Xi-api-key") == "fake-key"


# ---- gate 3: a 401 and a network error each raise CloudTTSError -----------

def test_deepgram_speak_raises_on_401():
    err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
    with mock.patch.object(tts_cloud.urllib.request, "urlopen", side_effect=err):
        try:
            tts_cloud.deepgram_speak("hi", "aura-2-draco-en", "bad-key")
        except tts_cloud.CloudTTSError as exc:
            assert "401" in str(exc)
        else:
            raise AssertionError("expected CloudTTSError")


def test_deepgram_speak_raises_on_a_network_error():
    err = urllib.error.URLError("no route to host")
    with mock.patch.object(tts_cloud.urllib.request, "urlopen", side_effect=err):
        try:
            tts_cloud.deepgram_speak("hi", "aura-2-draco-en", "a-key")
        except tts_cloud.CloudTTSError as exc:
            assert "no route to host" in str(exc)
        else:
            raise AssertionError("expected CloudTTSError")


def test_elevenlabs_speak_raises_on_401():
    err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
    with mock.patch.object(tts_cloud.urllib.request, "urlopen", side_effect=err):
        try:
            tts_cloud.elevenlabs_speak("hi", "voice123", "bad-key")
        except tts_cloud.CloudTTSError as exc:
            assert "401" in str(exc)
        else:
            raise AssertionError("expected CloudTTSError")


def test_deepgram_speak_without_a_key_raises_before_any_http_call():
    with mock.patch.object(tts_cloud.urllib.request, "urlopen") as urlopen:
        try:
            tts_cloud.deepgram_speak("hi", "aura-2-draco-en", "")
        except tts_cloud.CloudTTSError as exc:
            assert "key" in str(exc)
        else:
            raise AssertionError("expected CloudTTSError")
    assert not urlopen.called


def test_elevenlabs_speak_without_a_voice_id_raises_before_any_http_call():
    with mock.patch.object(tts_cloud.urllib.request, "urlopen") as urlopen:
        try:
            tts_cloud.elevenlabs_speak("hi", "", "a-key")
        except tts_cloud.CloudTTSError as exc:
            assert "voice ID" in str(exc)
        else:
            raise AssertionError("expected CloudTTSError")
    assert not urlopen.called


# ---- gate 4: config.deepgram_key() is the only reader of the env var ------

def test_deepgram_env_var_has_exactly_one_reader():
    """A second os.getenv("DEEPGRAM_API_KEY") anywhere would be a second place
    to store/read the key, which the plan explicitly forbids."""
    import re

    wisprlite_dir = pathlib.Path(__file__).resolve().parent.parent / "wisprlite"
    readers = []
    for path in wisprlite_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'os\.getenv\(\s*["\']DEEPGRAM_API_KEY["\']', text):
            readers.append(f"{path.name}:{text.count(chr(10), 0, m.start()) + 1}")
    assert readers, "sabotage check: os.getenv(\"DEEPGRAM_API_KEY\") was not found anywhere"
    assert all(r.startswith("config.py:") for r in readers), (
        f"DEEPGRAM_API_KEY is read outside config.py: {readers}"
    )
    # tts_cloud itself must not read the env var directly.
    tts_cloud_src = (wisprlite_dir / "tts_cloud.py").read_text(encoding="utf-8")
    assert "os.getenv" not in tts_cloud_src, \
        "tts_cloud.py must go through config.deepgram_key(), not read the env directly"


# ---- readaloud.build_speaker: tier dispatch + fallback ---------------------

def test_build_speaker_windows_tier_never_touches_the_network():
    from wisprlite import config, readaloud

    cfg = config.Config(read_aloud_tts="windows", read_aloud_voice="")
    with mock.patch.object(tts_cloud.urllib.request, "urlopen") as urlopen:
        speaker, reason = readaloud.build_speaker("hello", cfg)
    assert not urlopen.called
    assert reason == ""
    assert isinstance(speaker, readaloud.Speaker)


def test_build_speaker_falls_back_to_windows_on_a_deepgram_failure():
    from wisprlite import config, readaloud

    cfg = config.Config(read_aloud_tts="deepgram", read_aloud_voice="aura-2-draco-en")
    with mock.patch.object(config, "deepgram_key", return_value="a-key"), \
         mock.patch.object(tts_cloud, "deepgram_speak",
                            side_effect=tts_cloud.CloudTTSError("Deepgram speak failed: HTTP 401")):
        speaker, reason = readaloud.build_speaker("hello", cfg)
    assert isinstance(speaker, readaloud.Speaker)
    assert speaker.voice == ""
    assert "HTTP 401" in reason
    assert "Windows" in reason


def test_build_speaker_falls_back_to_windows_on_a_network_error():
    from wisprlite import config, readaloud

    cfg = config.Config(read_aloud_tts="deepgram", read_aloud_voice="aura-2-draco-en")
    with mock.patch.object(config, "deepgram_key", return_value="a-key"), \
         mock.patch.object(tts_cloud, "deepgram_speak",
                            side_effect=tts_cloud.CloudTTSError("Deepgram speak failed: timed out")):
        speaker, reason = readaloud.build_speaker("hello", cfg)
    assert isinstance(speaker, readaloud.Speaker)
    assert "timed out" in reason


def test_build_speaker_uses_the_cloud_audio_on_success():
    from wisprlite import config, readaloud

    cfg = config.Config(read_aloud_tts="deepgram", read_aloud_voice="aura-2-draco-en")
    fake_player = object()
    with mock.patch.object(config, "deepgram_key", return_value="a-key"), \
         mock.patch.object(tts_cloud, "deepgram_speak", return_value=b"wav-bytes"), \
         mock.patch.object(readaloud, "_winrt_player_from_bytes", return_value=fake_player) as builder:
        speaker, reason = readaloud.build_speaker("hello", cfg)
    assert reason == ""
    assert isinstance(speaker, readaloud.Speaker)
    builder.assert_called_once_with(b"wav-bytes", "audio/wav")
    # the factory hands back the pre-built player rather than re-synthesizing
    assert speaker._build_player("hello") is fake_player


def test_a_cloud_call_that_succeeds_but_cannot_play_still_falls_back():
    """The player was built OUTSIDE the try, so a cloud response that arrived
    and then failed to become a player raised out of build_speaker - no
    fallback, and silence, which is the one thing this path must never do."""
    import types
    from unittest import mock
    from wisprlite import readaloud, tts_cloud
    from wisprlite import config as _config

    cfg = types.SimpleNamespace(read_aloud_tts="deepgram", read_aloud_rate=1.0,
                                read_aloud_voice="aura-2-draco-en")

    with mock.patch.object(tts_cloud, "deepgram_speak", return_value=b"RIFFfake"), \
         mock.patch.object(_config, "deepgram_key", return_value="k"), \
         mock.patch.object(readaloud, "_winrt_player_from_bytes",
                           side_effect=RuntimeError("no MediaPlayer here")):
        speaker, note = readaloud.build_speaker("hello", cfg)

    assert speaker is not None, "it raised instead of falling back"
    assert "Windows voice" in note, f"the fallback was silent about why: {note!r}"


def test_a_dead_key_falls_back_and_says_why():
    import types
    from unittest import mock
    from wisprlite import readaloud, tts_cloud
    from wisprlite import config as _config

    cfg = types.SimpleNamespace(read_aloud_tts="deepgram", read_aloud_rate=1.0,
                                read_aloud_voice="aura-2-draco-en")
    with mock.patch.object(tts_cloud, "deepgram_speak",
                           side_effect=tts_cloud.CloudTTSError("HTTP 401")), \
         mock.patch.object(_config, "deepgram_key", return_value="bad"):
        speaker, note = readaloud.build_speaker("hello", cfg)

    assert speaker is not None
    assert "401" in note and "Windows voice" in note
