"""Cloud text-to-speech for Read Aloud: Deepgram Aura-2 and ElevenLabs.

Both are bring-your-own-key. Deepgram reuses `config.deepgram_key()` — the same
key already configured for dictation, no second place to store it. ElevenLabs
is bring-your-own-key exactly like the transcription engines, via
`config.elevenlabs_key()`.

Every failure (missing key, bad key, no network, a quota wall) raises
CloudTTSError — never a bare urllib exception — so the caller has one thing to
catch and can degrade to the Windows voice. Read Aloud must never go silent.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class CloudTTSError(Exception):
    """A degraded-but-handled cloud TTS failure: no key, network, quota, etc."""


# Curated rather than the full ~40-voice catalogue. draco is the default: the
# closest thing in it to the JARVIS register James asked for.
DEEPGRAM_VOICES = [
    ("aura-2-draco-en", "British baritone, warm, trustworthy"),
    ("aura-2-zeus-en", "American, deep, trustworthy, smooth"),
    ("aura-2-saturn-en", "American, knowledgeable, confident, baritone"),
    ("aura-2-jupiter-en", "American, expressive, knowledgeable, baritone"),
    ("aura-2-asteria-en", "American feminine, clear"),
]
DEFAULT_DEEPGRAM_VOICE = "aura-2-draco-en"


def _post_for_audio(url: str, headers: dict, payload: dict, *, timeout: float, label: str) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise CloudTTSError(f"{label} failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise CloudTTSError(f"{label} failed: {exc.reason}") from exc
    # Deliberately NOT a bare `except Exception` here. A TypeError from a
    # malformed payload is our bug, not a cloud failure, and relabelling it as
    # CloudTTSError hid it behind the friendly "using the Windows voice"
    # message. build_speaker still catches it and still falls back - but it
    # names the exception type, so the bug is visible in the log.


def deepgram_speak(text: str, voice: str, api_key: str, *, timeout: float = 20.0) -> bytes:
    """POST to Deepgram's /v1/speak. Returns WAV bytes (container=wav is
    requested explicitly so playback doesn't have to guess the encoding)."""
    if not (api_key or "").strip():
        raise CloudTTSError("no Deepgram API key configured")
    model = (voice or DEFAULT_DEEPGRAM_VOICE).strip()
    # quoted: a voice string containing & or ? silently rewrites the request -
    # a different container, a different model, or a 400 nobody can explain.
    url = ("https://api.deepgram.com/v1/speak"
           f"?model={urllib.parse.quote(model, safe='')}&encoding=linear16&container=wav")
    return _post_for_audio(
        url,
        {"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        {"text": text},
        timeout=timeout,
        label="Deepgram speak",
    )


def elevenlabs_speak(text: str, voice_id: str, api_key: str, *, timeout: float = 20.0) -> bytes:
    """POST to ElevenLabs' text-to-speech endpoint. Returns MP3 bytes."""
    if not (api_key or "").strip():
        raise CloudTTSError("no ElevenLabs API key configured")
    if not (voice_id or "").strip():
        raise CloudTTSError("no ElevenLabs voice ID configured")
    # quoted: the voice ID is free text the user pastes in. A stray slash or
    # trailing whitespace would otherwise route the request to a different
    # endpoint entirely.
    url = ("https://api.elevenlabs.io/v1/text-to-speech/"
           + urllib.parse.quote(voice_id.strip(), safe=""))
    return _post_for_audio(
        url,
        {"xi-api-key": api_key, "Content-Type": "application/json"},
        {"text": text},
        timeout=timeout,
        label="ElevenLabs speak",
    )
