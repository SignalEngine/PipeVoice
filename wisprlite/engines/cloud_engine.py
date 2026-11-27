"""PipeVoice Cloud (batch): POSTs the clip to pipevoice.app with the licence key.

Every failure raises, so app._fallback runs the same audio through local Whisper.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

import numpy as np

from .. import cloud
from .base import Engine, OnPartial, Session
from .openai_engine import _wav_bytes

MAX_WAV_BYTES = 4_400_000
MAX_PROMPT_CHARS = 800

_ERRORS = {401: "Cloud licence not valid", 402: "Cloud monthly limit reached",
           413: "Clip too long for Cloud"}


class _CloudSession(Session):
    def __init__(self, engine: "CloudEngine") -> None:
        self._engine = engine

    def finish(self, audio: np.ndarray) -> str:
        if audio is None or audio.size == 0:
            return ""
        wav = _wav_bytes(audio)
        if len(wav) > MAX_WAV_BYTES:
            raise RuntimeError("Clip too long for Cloud")
        key = cloud.load().get("key")
        if not key:  # removed during this utterance: upload nothing
            raise RuntimeError("Cloud licence not valid")
        url = cloud.stt_url()
        if self._engine.language:
            url += "?" + urllib.parse.urlencode({"lang": self._engine.language})
        headers = {"Authorization": "Bearer " + key,
                   "Content-Type": "audio/wav", "User-Agent": cloud.USER_AGENT}
        if self._engine.prompt:
            headers["X-Prompt"] = urllib.parse.quote(self._engine.prompt[:MAX_PROMPT_CHARS], safe="")
        try:
            req = urllib.request.Request(url, data=wav, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = json.loads(resp.read())["text"]
            return text.strip()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(_ERRORS.get(exc.code, "Cloud unavailable")) from exc
        except Exception as exc:
            raise RuntimeError("Cloud unavailable") from exc


class CloudEngine(Engine):
    name = "cloud"
    streaming = False

    def __init__(self, language: Optional[str] = None, prompt: str = "") -> None:
        self.language = language or None
        self.prompt = (prompt or "").strip()

    def start_session(self, on_partial: Optional[OnPartial] = None) -> Session:
        return _CloudSession(self)
