"""Live answers — when the other side asks something your docs cover, show it.

Pure: no Tk, no network of its own. ``completion``, ``clock`` and ``load`` are
injectable so the policy is testable. Grounding is the after-call code
(`knowledge`): the quote must exist verbatim in the source or there is no card.
Silence is the normal outcome — small talk and unsure answers show nothing.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from . import knowledge

log = logging.getLogger("wisprlite")

# No LLM call unless the question shares a real word with the docs. Not a BM25
# score cut-off: scores shrink with fewer chunks, so a one-file FAQ scored a
# perfect question at 0.99 and never showed a card.
_FILLER = frozenset(
    "about after again also been from just know like make mean okay really right "
    "sorry sure tell than that them then there they think this those very what "
    "with yeah your yours".split()
)
# Deepgram finalises on short pauses, so "Do you guys support" and "SSO on the
# team plan?" can arrive as two lines. A Them line this recent is joined on.
JOIN_SECONDS = 4.0
PREP_WAIT = 600          # seconds _build waits for the prep pop-up's prep.json
_QUESTION_WORDS = frozenset(
    "what how why when where which who do does did can could is are will would "
    "should have has".split()
)


def looks_like_question(text: str) -> bool:
    words = str(text or "").split()
    if len(words) < 3:
        return False
    if str(text).rstrip().endswith("?"):
        return True
    return words[0].lower().strip(",.") in _QUESTION_WORDS


def _norm(text: str) -> str:
    return " ".join(re.findall(r"\w+", str(text or "").lower()))


class LiveAnswerer:
    def __init__(self, folder, url, notes_path, completion, on_card, *,
                 clock=time.monotonic, deadline=8.0, log_path=None,
                 load=knowledge.load_sources, files=None, prep_path=None, on_ready=None):
        self._folder, self._url = folder, url
        self._notes = Path(notes_path) if notes_path else None
        self._files = list(files or [])
        self._prep_path = Path(prep_path) if prep_path else None
        self._on_ready = on_ready
        self._small = False
        self._completion, self._on_card = completion, on_card
        self._clock, self._deadline = clock, float(deadline)
        self._log_path = Path(log_path) if log_path else None
        self._load = load
        self._index = None
        self._docs: list[dict] = []
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._pending: tuple[str, float] | None = None   # 1-slot, latest wins
        self._shown: list[str] = []                      # normalised questions and quotes
        self._log: list[dict] = []
        self._prev: tuple[str, float] | None = None      # last Them line, for joining
        self.last_error = ""

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        threading.Thread(target=self._build, daemon=True).start()
        threading.Thread(target=self._work, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def _wait_prep(self) -> dict | None:
        """prep.json from the pop-up; None if it never shows up or we were stopped."""
        deadline = time.monotonic() + PREP_WAIT
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                data = json.loads(self._prep_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError):
                pass
            self._stop.wait(0.5)
        return None

    def _build(self) -> None:
        try:
            folder, files = self._folder or "", self._files
            if self._prep_path is not None:
                prep = self._wait_prep()
                if self._stop.is_set():
                    return
                if prep is not None:
                    if prep.get("skip"):
                        return                       # stays not ready: no cards
                    files = [str(f) for f in prep.get("files") or []]
                    if not prep.get("use_folder"):
                        folder = ""
            docs, _report = self._load(folder, self._url or "", self._notes, files=files)
            self._docs = docs
            self._index = knowledge.BM25(knowledge.chunk_docs(docs))
            self._small = knowledge.doc_words(docs) <= knowledge.FULL_TEXT_WORDS
            self._ready.set()
            if self._on_ready is not None:
                self._on_ready(sum(1 for d in docs if d["source"] != "Call notes"))
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.info("live answers: index failed: %s", exc)

    # -- lines in (called from the Deepgram thread) ----------------------------

    def on_line(self, speaker: str, text: str) -> None:
        try:
            if speaker != "Them" or not self._ready.is_set():
                return
            now, text = self._clock(), str(text)
            prev, self._prev = self._prev, (text, now)
            if prev is not None and now - prev[1] <= JOIN_SECONDS:
                joined = f"{prev[0]} {text}"
                if looks_like_question(joined) and not looks_like_question(prev[0]):
                    text = joined
            if not looks_like_question(text):
                return
            with self._cond:
                self._pending = (text, now)
                self._cond.notify()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"

    # -- worker ----------------------------------------------------------------

    def _work(self) -> None:
        while not self._stop.is_set():
            with self._cond:
                while self._pending is None and not self._stop.is_set():
                    self._cond.wait(timeout=0.5)
                job, self._pending = self._pending, None
            if job is None or self._stop.is_set():
                continue
            try:
                self._answer(*job)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"

    def _answer(self, question: str, asked_at: float) -> None:
        index = self._index
        if index is None or not index.chunks:
            return
        if not self._small:
            terms = {w for w in knowledge._words(question)
                     if len(w) >= 4 and w not in _QUESTION_WORDS and w not in _FILLER}
            if not terms & index.idf.keys():
                return                               # small talk: no LLM call
        ans = knowledge.answer_live(question, index, self._docs, self._completion)
        if not ans["quote"]:
            self._record(asked_at, question, ans, False)
            return
        late = self._clock() - asked_at > self._deadline
        qn, quote_n = _norm(question), _norm(ans["quote"])
        repeat = qn in self._shown or quote_n in self._shown
        show = not late and not repeat
        if show:
            self._shown += [qn, quote_n]
            self._on_card(ans["headline"], ans["points"], ans["quote"], ans["source"])
        self._record(asked_at, question, ans, show)

    def _record(self, asked_at, question, ans, shown) -> None:
        if self._log_path is None:
            return
        self._log.append({"t_asked": asked_at, "question": question,
                          "headline": ans["headline"], "points": ans["points"], "quote": ans["quote"],
                          "source": ans["source"], "shown": shown})
        try:
            self._log_path.write_text(json.dumps(self._log, indent=2), encoding="utf-8")
        except OSError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
