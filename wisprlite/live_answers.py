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

# Below this BM25 top score the docs have nothing to say: no LLM call.
MIN_SCORE = 1.0
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
                 load=knowledge.load_sources):
        self._folder, self._url, self._notes = folder, url, notes_path
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
        self.last_error = ""

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        threading.Thread(target=self._build, daemon=True).start()
        threading.Thread(target=self._work, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def _build(self) -> None:
        try:
            docs, _report = self._load(self._folder or "", self._url or "", self._notes)
            self._docs = docs
            self._index = knowledge.BM25(knowledge.chunk_docs(docs))
            self._ready.set()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.info("live answers: index failed: %s", exc)

    # -- lines in (called from the Deepgram thread) ----------------------------

    def on_line(self, speaker: str, text: str) -> None:
        try:
            if speaker != "Them" or not self._ready.is_set():
                return
            if not looks_like_question(text):
                return
            with self._cond:
                self._pending = (str(text), self._clock())
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
        if max(index.score(question, i) for i in range(len(index.chunks))) < MIN_SCORE:
            return                                   # small talk: no LLM call
        ans = knowledge.answer_question(
            {"question": question, "keywords": []}, index, self._docs, self._completion
        )
        if not ans["quote"]:
            self._record(asked_at, question, ans, False)
            return
        late = self._clock() - asked_at > self._deadline
        qn, quote_n = _norm(question), _norm(ans["quote"])
        repeat = qn in self._shown or quote_n in self._shown
        show = not late and not repeat
        if show:
            self._shown += [qn, quote_n]
            self._on_card(ans["headline"], ans["quote"], ans["source"])
        self._record(asked_at, question, ans, show)

    def _record(self, asked_at, question, ans, shown) -> None:
        if self._log_path is None:
            return
        self._log.append({"t_asked": asked_at, "question": question,
                          "headline": ans["headline"], "quote": ans["quote"],
                          "source": ans["source"], "shown": shown})
        try:
            self._log_path.write_text(json.dumps(self._log, indent=2), encoding="utf-8")
        except OSError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
