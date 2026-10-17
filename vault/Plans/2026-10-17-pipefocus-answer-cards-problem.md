# Problem: PipeFocus should surface helpful knowledge during live calls, not just hygiene nudges

## Today (wisprlite/focus.py, shipped)
PipeFocus = opt-in, Deepgram-only live meeting mode. A general LLM watches the transcript and, at most once per 5 min (after >=220 new words, >=90 s between analyses), shows ONE nudge: action item with no owner, deferred decision, circled topic, unanswered question. Design doctrine: RESTRAINT - "a tip that fires often, or states the obvious, is worse than no feature". Policy is pure + tested. Mic = channel 0, desktop audio = channel 1 (so we know who said what).

## What the owner wants
During SALES/DEMO, SUPPORT/CLIENT, and INTERNAL calls: when the OTHER side asks a question that the user's own knowledge can answer (pricing, a feature, a competitor, "what did we decide about X"), an on-screen card appears fast with the answer. Explicitly NOT a job-interview cheating tool (Cluely positioning risk).
- Trigger: AUTO on questions, STRICT - only when a real question was asked AND the knowledge base has a confident answer; silent otherwise.
- Knowledge sources: a local folder of docs (PDF/MD/TXT), a per-meeting brief (pasted notes: who, company, goals), a website URL.
- Needs a UI to give it the knowledge + meeting parameters ("train it").
- Wants it fast and cheap - proposes TypeSafe Jev.

## Hard facts about Jev (docs.typesafe.ai, read 2026-10-17)
- Jev does NOT generate text. Only typed judgments: Noul (P(yes)), Choice (one of <=255 options), Score (2-10 ordered levels), Box (image).
- Many questions per request run in parallel; a 13-question call took 0.27 s and cost $0.0005 (cookbook). Price $0.042 per M input tokens, output free.
- Context 64k tokens/request, 32k per question.
- Rerank cookbook: BM25 shortlist of 30 -> Jev Noul per candidate improved top-1 5%->18%, top-5 15%->35% (legal citations, hard domain).
- Python SDK `typesafe-sdk`; user would need their own TYPESAFE_API_KEY (PipeVoice is BYO-key for every engine already).

## Constraints
- Windows desktop Python/Tk app, open source, local-first, BYO API keys, PyInstaller-packaged (bundle size + AV false positives matter - a new heavy dependency is a cost).
- Existing pieces: live Deepgram multichannel transcript, overlay pill (Tk), Voices/profiles, cleanup LLM providers (OpenAI/Gemini/OpenRouter/Ollama), local faster-whisper.
- A wrong or irrelevant card mid-sales-call is WORSE than no card (user may read it out). Latency budget: card must land within ~2-3 s of the question ending to be usable.
- One-person team; first version should ship in 1-2 days of agent build time.

## Evidence on the market (thin)
/last30days 2026-10-17: Reddit unavailable; HN/GitHub show open-source Cluely clones (e.g. Natively) adding "live helpers"/"brief me" features. No hard demand data gathered.

## The question
What is the best design for this feature - architecture (where Jev fits, whether an LLM writes the answer text or we show source text verbatim, retrieval approach), the knowledge/meeting-setup UI, the on-screen card UX, and how it coexists with existing PipeFocus nudges? And what should v1 be vs later?
