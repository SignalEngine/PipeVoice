"""File transcription with word-level timestamps (for the agent MCP).

Separate from the streaming dictation Session: takes a file *path* (not an
audio array), so it needs no numpy and is import-light / unit-testable.
faster-whisper is imported lazily inside the functions.
"""

from __future__ import annotations

import threading as _threading

_TRANSCRIBE_MODEL = None
_TRANSCRIBE_KEY = None
_TRANSCRIBE_LOCK = _threading.Lock()


def _segments_to_dicts(segments) -> list:
    """Shape faster-whisper segments into plain JSON-able dicts. Pure."""
    out = []
    for seg in segments:
        words = []
        for w in (getattr(seg, "words", None) or []):
            words.append({"start": round(float(w.start), 3),
                          "end": round(float(w.end), 3),
                          "word": w.word})
        out.append({"start": round(float(seg.start), 3),
                    "end": round(float(seg.end), 3),
                    "text": (seg.text or "").strip(),
                    "words": words})
    return out


def _get_transcribe_model(model_size: str, device: str = "auto", compute_type: str = "int8"):
    global _TRANSCRIBE_MODEL, _TRANSCRIBE_KEY
    key = (model_size, device, compute_type)
    if _TRANSCRIBE_MODEL is None or _TRANSCRIBE_KEY != key:
        from faster_whisper import WhisperModel
        _TRANSCRIBE_MODEL = WhisperModel(model_size, device=device, compute_type=compute_type)
        _TRANSCRIBE_KEY = key
    return _TRANSCRIBE_MODEL


def transcribe_file(path: str, *, language=None, model_size: str = "base.en",
                    device: str = "auto", compute_type: str = "int8") -> dict:
    """Transcribe an audio/video file to text + word/segment timestamps.

    Serialized behind a lock (one shared warm model). faster-whisper decodes
    audio from many container formats via its bundled PyAV/ffmpeg.
    """
    with _TRANSCRIBE_LOCK:
        model = _get_transcribe_model(model_size, device, compute_type)
        segments, info = model.transcribe(
            path, language=language or None, word_timestamps=True,
            vad_filter=True, beam_size=1,
        )
        seg_dicts = _segments_to_dicts(segments)  # consume the generator inside the lock
    text = " ".join(s["text"] for s in seg_dicts).strip()
    return {"text": text,
            "language": getattr(info, "language", None),
            "duration": round(float(getattr(info, "duration", 0.0) or 0.0), 3),
            "segments": seg_dicts}


def _diarized_text(alt) -> str:
    """Render Deepgram's diarized paragraphs as "Speaker N:" blocks.

    Consecutive paragraphs from the same speaker are merged into one block.
    Falls back to the flat transcript when paragraphs/diarization are absent
    (e.g. diarize=False, or a model that didn't return them).
    """
    paras = getattr(getattr(alt, "paragraphs", None), "paragraphs", None) or []
    blocks: list[str] = []
    current = None  # only read once blocks is non-empty, i.e. after it's been set
    for para in paras:
        sentences = getattr(para, "sentences", None) or []
        text = " ".join((getattr(s, "text", "") or "").strip() for s in sentences).strip()
        if not text:
            continue
        speaker = getattr(para, "speaker", None)
        if speaker == current and blocks:
            blocks[-1] += " " + text
        else:
            blocks.append(f"Speaker {speaker}: {text}" if speaker is not None else text)
            current = speaker
    return "\n\n".join(blocks).strip() or (getattr(alt, "transcript", "") or "").strip()


def transcribe_file_deepgram(path: str, *, api_key: str, model: str = "nova-2",
                             diarize: bool = True, language=None,
                             timeout_s: float = 900.0) -> dict:
    """Cloud transcription with speaker labels, via Deepgram's prerecorded API.

    Same {text, language, duration, segments} shape as `transcribe_file`, so
    callers can swap backends. Unlike local whisper this has no practical
    length cap and returns diarization, but needs a key and network.
    """
    import httpx  # bundled with the deepgram sdk
    from deepgram import DeepgramClient, PrerecordedOptions

    listen = DeepgramClient(api_key).listen
    # v3 renamed .prerecorded -> .rest mid-series; accept either (see deepgram_engine).
    rest = (listen.rest if hasattr(listen, "rest") else listen.prerecorded).v("1")

    opts = {"model": model, "smart_format": True, "punctuate": True,
            "paragraphs": True, "diarize": diarize}
    if language:
        opts["language"] = language
    # ponytail: whole file into RAM (~110 MB for an hour of WAV, ~10 MB for the
    # compressed formats recordings actually use). The SDK also accepts
    # {"stream": fh} for a chunked upload — switch if raw WAV sizes bite.
    with open(path, "rb") as fh:
        resp = rest.transcribe_file(
            {"buffer": fh.read()}, PrerecordedOptions(**opts),
            timeout=httpx.Timeout(timeout_s, connect=15.0),
        )

    alt = resp.results.channels[0].alternatives[0]
    segs = []
    for utt in (getattr(resp.results, "utterances", None) or []):
        segs.append({"start": round(float(getattr(utt, "start", 0.0) or 0.0), 3),
                     "end": round(float(getattr(utt, "end", 0.0) or 0.0), 3),
                     "text": (getattr(utt, "transcript", "") or "").strip(),
                     "speaker": getattr(utt, "speaker", None),
                     "words": []})
    meta = getattr(resp, "metadata", None)
    return {"text": _diarized_text(alt),
            "language": language or None,
            "duration": round(float(getattr(meta, "duration", 0.0) or 0.0), 3),
            "segments": segs}
