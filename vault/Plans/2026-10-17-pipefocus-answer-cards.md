# PipeFocus Answer Cards — recommendation

2026-10-17 · idea-options run `20261017-125017` (5 blind proposers: Tencent, GLM, Gemini, GPT, DeepSeek) · judged by the brain session

**The idea:** on sales, support and internal calls, when the *other side* asks a question your own knowledge can answer, a small card pops up within ~2 s with the answer and the exact sentence from your docs it came from. Silent when unsure.

## The one fact that shapes the design

Jev **cannot write answers**. It only returns fast judgments: yes/no probabilities, "pick one of these", scores. (~0.3 s for 13 judgments in one call, ~$0.0005; docs.typesafe.ai.) So Jev is the **bouncer**, not the speaker:

1. "Was that a real question the other person wants answered?" (vs "you know what I mean?")
2. "Which of these 10 passages from your docs answers it?"
3. "Is the best one actually an answer, or just on-topic?"

All three ask in ONE Jev call. Only if all three pass does a cheap LLM (your existing Gemini key) squeeze the passage into ≤2 sentences.

## Options (5 proposals merged into 2)

| | A. Grounded card (4 of 5 proposers) | B. Verbatim chunk, no LLM (1 proposer) |
|---|---|---|
| How | Keyword search your docs → one LLM call writes ≤2-sentence answer + must quote an exact sentence → card | Keyword search → if score high enough, show the raw ~300-word passage |
| Worth (value / distinct / prior art) | 4 / 2 / 3 = **9** | 3 / 2 / 3 = **8** |
| Feasibility (fit / cost / undo / confidence) | 5 / 3 / 5 / 3 = **16** | 3 / 5 / 5 / 2 = **15** |
| **Total /35** | **25** | 23 |
| Weak spot | LLM can misjudge confidence | 300 words is unreadable mid-call; a keyword score is a bad "is this an answer?" signal |

Prior art: Cluely (live answers, controversial "cheat" positioning), Natively (open-source Cluely clone, adding "live helpers" this month), Gong/Outreach-style sales assist. None is open-source + local-docs + Windows-dictation-app. Distinctiveness comes from execution and positioning, not the idea.

B beats A only if: users' docs are already written as short Q&A pairs (an FAQ file), so the raw chunk *is* the answer.

## Recommendation: A, with Jev as the gate

Proposer 2's shape (most complete), plus grafts:

- **Jev as the 3-question gate** (your ask; fits Jev's "select, don't generate" design). The LLM only runs after Jev says yes, so a wrong card needs Jev *and* the quote check to fail.
- **Exact-quote check** (proposer 4): the LLM must return a sentence that exists word-for-word in your docs, or the card is dropped.
- **No search hit → no call at all** (proposer 3): free on small talk.
- **"Arm" a pack before the call** (proposer 2): pick "Acme demo"; the pill shows *armed: Acme demo*. Nothing armed → feature off.
- **Test box in setup** (proposer 4): type "do you do SSO?" and see the card you'd get. This is the "train it" UI.
- **Hard 3 s deadline**: late card = no card.
- **Shadow mode first**: logs what it *would* have shown for a week before cards go visible.

### v1 (≈1–2 days of build)
- Setup window: docs folder (PDF/MD/TXT), per-meeting brief box, one web page URL, Build button, test box.
- Live: other side's channel only → Jev gate → LLM summary → card with answer, quote, file name + date. Esc / auto-fade 20 s.
- Nudges and cards share the overlay; nudges pause while a card is up.

### Later
Whole-site crawl, smarter search (embeddings), Notion/Drive, "brief me" before a call, per-call-type playbooks.

## Do not build
- **Job-interview mode** — Cluely's backlash; bad look for an open-source app Defender already flags.
- **Website crawling in v1** — breaks on JS sites, goes stale, adds a heavy dependency.
- **Raw-chunk cards (B)** — unreadable in the 2 seconds you have.

## Critique round (idea-panel, 2026-10-17) — and what changes

Skeptic (GLM) + researcher (Gemini) ran; advocate failed. Triage:

| Critique | Verdict | Change |
|---|---|---|
| Quote check proves the sentence *exists*, not that it *answers* the question ("encrypted at rest" passes for "EU data residency?") | **Holds** | Card leads with the verbatim quote; a separate "does this sentence answer this question?" check is required |
| Jev gate can only ADD latency on the yes-path; one Gemini call can pick the passage and judge answerability itself; Jev's cost saving is ~$0.10/hour | **Mostly holds** | Jev moves from gate to *verifier* (the answer check above — Jev's citation-check pattern), kept only if the replay shows it cuts wrong cards |
| Real p95 ≈ ASR endpoint 0.5–1 s + Jev + LLM 1–2 s ≈ 3.9 s, over the 3 s deadline; and reading 2 sentences takes 4–6 s of dead air | **Plausible, unmeasured** | Card headline ≤10 words ("Yes — Enterprise plan only"), quote underneath |
| Keyword search is the real bottleneck: "get our data in" never matches "ingestion pipeline" | **Holds** | Hybrid search: keywords + Gemini embeddings (computed once when the pack is built; ~150 ms per question, same key) |
| Live card is the riskiest slice; "brief me" and a post-call question log carry the same value with no live risk; v1 has no feedback loop | **Holds** | **v1 becomes "After-call answers"** (below); live cards switch on per pack once your own calls prove precision |
| Tiny audience; consent for transcribing the other side | Noted | Same exposure PipeFocus already has; positioning = your own docs, your own calls |

### Revised v1: After-call answers (then live cards)
1. **During the call, nothing pops up.** The pipeline runs silently in the background.
2. **After the call**, a list: every question the other side asked → the answer your docs give (quote + file), or **"Not in your docs"**. Thumbs up/down on each.
3. That list *is* the feedback loop: it shows what to add to your docs, and measures precision on your real calls.
4. **Live cards unlock per pack** once that pack's after-call answers hit ≥90% thumbs-up over 20+ questions.

## Before committing (checkable)
1. Replay 5–10 real call transcripts against a real docs pack: ≥85% of shown cards "I'd read this aloud", ≥50% of answerable questions caught, 0 cards on filler, p95 <3 s.
2. Jev gate vs LLM-only gate on the same replay: keep Jev only if it cuts wrong cards or latency.
3. Bundled Python has SQLite FTS5; pypdf adds no new Defender flag (build + scan).
4. You need a TypeSafe API key (BYO, like every other engine).
