"""After-call answers: find the questions the other side asked and answer them
from the user's own docs. Tk-free; the LLM and Jev calls are injectable."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

from .cleanup import chat_completion
from .summarise import _chunks

log = logging.getLogger("wisprlite")

CHUNK_WORDS = 120
TOP_K = 6
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_THRESHOLD = 0.5
QUESTION_CHUNK = 120
QUESTION_OVERLAP = 10
_TEXT_SUFFIXES = {".md", ".txt"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text or "").lower())


# --- 1. load sources ---------------------------------------------------------

def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader  # lazy: a missing dep disables PDFs only

    return "\n\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "head"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in {"p", "br", "li", "div", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _fetch_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "WisprLite"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read(5_000_000)
        charset = resp.headers.get_content_charset() or "utf-8"
    parser = _TextExtractor()
    parser.feed(raw.decode(charset, errors="replace"))
    return "".join(parser.parts)


def load_sources(
    folder: str = "",
    url: str = "",
    notes_path: Path | None = None,
    *,
    pdf_reader: Callable[[Path], str] = _pdf_text,
    fetch: Callable[[str], str] = _fetch_url,
) -> tuple[list[dict], dict]:
    """Return ([{source, text}], {"ok": [...], "failed": [...]}). Never raises."""
    docs: list[dict] = []
    ok: list[str] = []
    failed: list[str] = []

    def add(name: str, text: str, why: str = "") -> None:
        if len(_words(text)) == 0:
            failed.append(f"{name}{why or ' (no text found)'}")
            return
        docs.append({"source": name, "text": text})
        ok.append(name)

    if folder.strip():
        root = Path(folder.strip())
        try:
            files = sorted(p for p in root.iterdir() if p.is_file())
        except OSError as exc:
            failed.append(f"{folder} ({exc.strerror or exc})")
            files = []
        for path in files:
            suffix = path.suffix.lower()
            try:
                if suffix in _TEXT_SUFFIXES:
                    add(path.name, path.read_text(encoding="utf-8", errors="replace"))
                elif suffix == ".pdf":
                    add(path.name, pdf_reader(path), " (scanned?)")
            except Exception as exc:  # one bad file must not hide the rest
                failed.append(f"{path.name} ({exc})")
    if url.strip():
        try:
            add(url.strip(), fetch(url.strip()))
        except Exception as exc:
            failed.append(f"{url.strip()} ({exc})")
    if notes_path is not None and notes_path.is_file():
        try:
            text = notes_path.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                add("Call notes", text)
        except OSError as exc:
            failed.append(f"Call notes ({exc})")
    return docs, {"ok": ok, "failed": failed}


# --- 2. chunk ----------------------------------------------------------------

def chunk_docs(docs: list[dict], target: int = CHUNK_WORDS) -> list[dict]:
    """~target-word passages, split on paragraph then sentence boundaries."""
    chunks: list[dict] = []
    for doc in docs:
        pieces: list[str] = []
        for para in re.split(r"\n\s*\n", doc["text"]):
            para = para.strip()
            if not para:
                continue
            if len(para.split()) <= target:
                pieces.append(para)
            else:
                pieces.extend(s for s in re.split(r"(?<=[.!?])\s+", para) if s.strip())
        current: list[str] = []
        count = 0
        for piece in pieces:
            n = len(piece.split())
            if current and count + n > target:
                chunks.append({"source": doc["source"], "text": " ".join(current)})
                current, count = [], 0
            current.append(piece)
            count += n
        if current:
            chunks.append({"source": doc["source"], "text": " ".join(current)})
    return chunks


# --- 3. BM25 -----------------------------------------------------------------

# ponytail: rebuilt each run, persist if folders get large
class BM25:
    def __init__(self, chunks: list[dict], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.tokens = [_words(c["text"]) for c in chunks]
        self.avg = (sum(map(len, self.tokens)) / len(chunks)) if chunks else 0.0
        df: dict[str, int] = {}
        for toks in self.tokens:
            for term in set(toks):
                df[term] = df.get(term, 0) + 1
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def score(self, query: str, i: int) -> float:
        toks = self.tokens[i]
        if not toks:
            return 0.0
        total = 0.0
        for term in set(_words(query)):
            f = toks.count(term)
            if f:
                norm = self.k1 * (1 - self.b + self.b * len(toks) / (self.avg or 1))
                total += self.idf.get(term, 0.0) * f * (self.k1 + 1) / (f + norm)
        return total

    def top(self, query: str, k: int = TOP_K) -> list[dict]:
        scored = [(self.score(query, i), i) for i in range(len(self.chunks))]
        scored = [s for s in scored if s[0] > 0]
        scored.sort(key=lambda s: (-s[0], s[1]))
        return [self.chunks[i] for _s, i in scored[:k]]


# --- LLM helpers -------------------------------------------------------------

def _parse_json(answer: object, want: type):
    """First valid JSON value of type `want` in a model reply, else None."""
    text = str(answer or "")
    decoder = json.JSONDecoder()
    opener = "[" if want is list else "{"
    index = text.find(opener)
    while index != -1:
        try:
            value, _end = decoder.raw_decode(text[index:])
        except ValueError:
            value = None
        if isinstance(value, want):
            return value
        index = text.find(opener, index + 1)
    return None


def _default_completion(provider: str, model: str):
    def call(messages):
        return chat_completion(messages, provider, model, allow_empty=True, raise_errors=True) or ""
    return call


# --- 4. extract questions ----------------------------------------------------

_Q_PROMPT = (
    "Below is part of a call transcript, one line per remark as '[seconds] Speaker: text'. "
    "List ONLY the sincere questions the remote speakers (Them) want answered. Skip rhetorical "
    "questions and filler ('you know?', 'right?', 'make sense?'). Rewrite each into a standalone "
    "question that makes sense without the transcript. For each give 3-8 search keywords, "
    "INCLUDING synonyms and alternate wording. Reply with JSON only: "
    '[{"t": <seconds>, "question": "...", "keywords": ["..."]}]. Reply [] if there are none.'
)


def extract_questions(segments: list[dict], completion: Callable) -> list[dict]:
    """Only `Them*` segments are sent to the model."""
    them = [s for s in segments if str(s.get("speaker", "")).startswith("Them")]
    found: list[dict] = []
    seen: set[str] = set()
    for chunk in _chunks(them, QUESTION_CHUNK, QUESTION_OVERLAP):
        lines = "\n".join(
            f"[{int(s.get('t') or 0)}] {s.get('speaker')}: {s.get('text', '')}" for s in chunk
        )
        reply = completion([
            {"role": "system", "content": _Q_PROMPT},
            {"role": "user", "content": lines},
        ])
        items = _parse_json(reply, list) or []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("question"), str):
                continue
            key = _norm(item["question"])
            if not key or key in seen:
                continue
            seen.add(key)
            kws = item.get("keywords")
            found.append({
                "t": item.get("t") if isinstance(item.get("t"), (int, float)) else 0,
                "question": item["question"].strip(),
                "keywords": [k for k in kws if isinstance(k, str)] if isinstance(kws, list) else [],
            })
    return found


# --- 5-6. answer + verify ----------------------------------------------------

_A_PROMPT = (
    "Answer the question using ONLY the excerpts. Reply with JSON only: "
    '{"headline": "<answer in 10 words or fewer>", "quote": "<the exact sentence copied '
    'word for word from one excerpt>", "source": "<that excerpt\'s file name>"}. '
    'If the excerpts do not answer it, reply {"headline": null, "quote": null, "source": null}.'
)


def answer_question(q: dict, index: BM25, docs: list[dict], completion: Callable) -> dict:
    out = {"headline": None, "quote": None, "source": None}
    hits = index.top(" ".join([q["question"], *q.get("keywords", [])]))
    if not hits:
        return out
    excerpts = "\n\n".join(f"[{h['source']}]\n{h['text']}" for h in hits)
    reply = completion([
        {"role": "system", "content": _A_PROMPT},
        {"role": "user", "content": f"Question: {q['question']}\n\nExcerpts:\n{excerpts}"},
    ])
    data = _parse_json(reply, dict)
    if not data:
        return out
    headline, quote, source = data.get("headline"), data.get("quote"), data.get("source")
    if not all(isinstance(v, str) and v.strip() for v in (headline, quote, source)):
        return out
    text = next((d["text"] for d in docs if d["source"] == source), None)
    if text is None or _norm(quote) not in _norm(text):  # existence check only
        return out
    return {"headline": headline.strip(), "quote": quote.strip(), "source": source}


# --- 7. Jev answer check -----------------------------------------------------

def jev_check(question: str, quote: str, *, api_key: str | None = None, post=None) -> float | None:
    """P(quote directly answers question) from Jev; None when skipped or failed."""
    key = os.environ.get("TYPESAFE_API_KEY", "").strip() if api_key is None else api_key
    if not key:
        return None
    body = json.dumps({
        "state": {"question": question, "quote": quote},
        "model": "jev-latest",
        "questions": {"answers": {
            "type": "noul",
            "instructions": "Does `quote` directly answer `question`?",
        }},
    }).encode("utf-8")
    try:
        if post is not None:
            data = post(body)
        else:
            req = urllib.request.Request(
                JEV_URL, data=body, method="POST",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        return float(data["answers"]["answers"]["noul"])
    except Exception as exc:  # optional step: never fail the run
        log.warning("Jev check failed: %s", exc)
        return None


# --- 8. persistence ----------------------------------------------------------

def _write_json(path: Path, value) -> None:
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, indent=2), encoding="utf-8")
    pending.replace(path)


def load_answers(session_dir: str | Path) -> dict | None:
    try:
        data = json.loads((Path(session_dir) / "answers.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def set_vote(session_dir: str | Path, index: int, vote: str | None) -> bool:
    data = load_answers(session_dir)
    if not data or not 0 <= index < len(data.get("items", [])) or vote not in (None, "up", "down"):
        return False
    data["items"][index]["vote"] = vote
    _write_json(Path(session_dir) / "answers.json", data)
    return True


# --- orchestration -----------------------------------------------------------

def build_answers(
    session_dir: str | Path,
    folder: str,
    url: str,
    provider: str,
    model: str,
    *,
    completion: Callable | None = None,
    jev: Callable[[str, str], float | None] = jev_check,
    pdf_reader: Callable[[Path], str] = _pdf_text,
    fetch: Callable[[str], str] = _fetch_url,
) -> dict:
    session_dir = Path(session_dir)
    completion = completion or _default_completion(provider, model)
    try:
        segments = json.loads((session_dir / "transcript.json").read_text(encoding="utf-8")).get("segments") or []
    except (OSError, ValueError):
        segments = []
    docs, sources = load_sources(
        folder, url, session_dir / "call_notes.md", pdf_reader=pdf_reader, fetch=fetch
    )
    index = BM25(chunk_docs(docs))
    items = []
    for q in extract_questions(segments, completion):
        ans = answer_question(q, index, docs, completion)
        jev_p = jev(q["question"], ans["quote"]) if ans["quote"] else None
        if not ans["quote"]:
            status = "not_found"
        elif jev_p is not None and jev_p < JEV_THRESHOLD:
            status = "doubtful"
        else:
            status = "answered"
        items.append({"t": q["t"], "question": q["question"], **ans,
                      "jev_p": jev_p, "status": status, "vote": None})
    result = {"built_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sources": sources, "items": items}
    _write_json(session_dir / "answers.json", result)
    return result
