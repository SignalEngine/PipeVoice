"""End-to-end test of transcribe_file_deepgram against a FAKE Deepgram server.

Exercises the real SDK — request construction, option serialisation, upload,
response parsing, _diarized_text — without a key or network. Only the remote
service is faked; everything on our side is the real code path.

Needs the deepgram SDK (absent on the Linux dev box), so it skips cleanly when
missing. Run: python3 tests/test_transcribe_deepgram_e2e.py
"""

import json
import os
import struct
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import deepgram  # noqa: F401
except Exception:
    print("SKIP: deepgram sdk not installed (expected on the Linux dev box)")
    raise SystemExit(0)

from deepgram import DeepgramClient, DeepgramClientOptions  # noqa: E402

from wisprlite.engines import transcribe as T  # noqa: E402

# A real prerecorded response: two speakers, one of them split across two
# paragraphs (so the merge path is exercised).
FAKE_RESPONSE = {
    "metadata": {"transaction_key": "x", "request_id": "r", "sha256": "s",
                 "created": "2026-01-01T00:00:00Z", "duration": 12.5, "channels": 1,
                 "models": ["nova-2"]},
    "results": {
        "channels": [{
            "alternatives": [{
                "transcript": "Hello there. Hi back. Good to see you.",
                "confidence": 0.99,
                "words": [],
                "paragraphs": {
                    "transcript": "Hello there.\n\nHi back. Good to see you.",
                    "paragraphs": [
                        {"sentences": [{"text": "Hello there.", "start": 0.1, "end": 1.2}],
                         "speaker": 0, "num_words": 2, "start": 0.1, "end": 1.2},
                        {"sentences": [{"text": "Hi back.", "start": 1.5, "end": 2.0}],
                         "speaker": 1, "num_words": 2, "start": 1.5, "end": 2.0},
                        {"sentences": [{"text": "Good to see you.", "start": 2.1, "end": 3.0}],
                         "speaker": 1, "num_words": 4, "start": 2.1, "end": 3.0},
                    ],
                },
            }],
        }],
        "utterances": [
            {"start": 0.1, "end": 1.2, "confidence": 0.99, "channel": 0,
             "transcript": "Hello there.", "words": [], "speaker": 0, "id": "u1"},
            {"start": 1.5, "end": 3.0, "confidence": 0.99, "channel": 0,
             "transcript": "Hi back. Good to see you.", "words": [], "speaker": 1, "id": "u2"},
        ],
    },
}

received = {}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = self.headers.get("Content-Length")
        if length is not None:
            body = self.rfile.read(int(length))
        else:  # chunked (streaming upload)
            body = b""
            while True:
                size_line = self.rfile.readline().strip()
                size = int(size_line, 16)
                if size == 0:
                    self.rfile.readline()
                    break
                body += self.rfile.read(size)
                self.rfile.readline()
        received["bytes"] = len(body)
        received["path"] = self.path
        received["chunked"] = length is None
        # Behave like the real API: these sections are only returned when the
        # request asked for them. A permissive fake that always returns them
        # hides a missing query flag (it hid a missing utterances=true once).
        response = json.loads(json.dumps(FAKE_RESPONSE))
        if "utterances=true" not in self.path:
            response["results"].pop("utterances", None)
        if "paragraphs=true" not in self.path and "smart_format=true" not in self.path:
            response["results"]["channels"][0]["alternatives"][0].pop("paragraphs", None)
        payload = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


def _wav(path, seconds=1, rate=16000):
    """Minimal real WAV so the file on disk is plausible audio."""
    n = seconds * rate
    data = b"".join(struct.pack("<h", 0) for _ in range(n))
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt ")
        fh.write(struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16))
        fh.write(b"data" + struct.pack("<I", len(data)) + data)
    return os.path.getsize(path)


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # Point the real SDK at our fake server by swapping the client constructor.
    real_ctor = deepgram.DeepgramClient
    deepgram.DeepgramClient = lambda key: real_ctor(
        key, DeepgramClientOptions(api_key=key, url=f"http://127.0.0.1:{port}"))
    try:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "meeting.wav")
            size = _wav(path, seconds=2)
            out = T.transcribe_file_deepgram(path, api_key="fake-key")
    finally:
        deepgram.DeepgramClient = real_ctor
        server.shutdown()

    assert received.get("bytes") == size, f"uploaded {received.get('bytes')} of {size} bytes"
    print(f"  ok  full file uploaded ({size} bytes, chunked={received['chunked']})")

    assert "/v1/listen" in received["path"], received["path"]
    for flag in ("diarize=true", "paragraphs=true", "smart_format=true",
                 "utterances=true", "model=nova-2"):
        assert flag in received["path"], f"{flag} missing from {received['path']}"
    print("  ok  request carries model + diarize + paragraphs + smart_format + utterances")

    assert out["text"] == (
        "Speaker 0: Hello there.\n\n"
        "Speaker 1: Hi back. Good to see you."), repr(out["text"])
    print("  ok  diarized text parsed, consecutive same-speaker paragraphs merged")

    assert out["duration"] == 12.5, out["duration"]
    assert len(out["segments"]) == 2, out["segments"]
    assert out["segments"][1]["speaker"] == 1, out["segments"][1]
    print("  ok  duration + per-utterance segments with speaker ids")
    print("all passed")


if __name__ == "__main__":
    main()
