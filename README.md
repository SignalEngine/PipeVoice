<div align="center">

<!--
  SEO / discovery keywords (not visible copy, no stuffing):
  free voice typing windows, wispr flow alternative, open source dictation, offline speech to text,
  push to talk dictation, voice coding, dictate into claude code, cursor voice input, deepgram windows,
  whisper dictation, local speech to text windows, no-gpu voice typing, voicebox alternative, free dictation app
-->

# PipeVoice

### Talk faster than you type.

**Free, open-source, push-to-talk voice typing for Windows that lands in any app — your terminal, editor, browser, chat box.**
Cloud with your own key, free AI polish with Gemini, or go 100% offline so nothing leaves your PC.

**No account. No subscription. Ever.**

<p>
  <a href="https://github.com/Powleads/PipeVoice/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/Powleads/PipeVoice?style=for-the-badge&logo=github&color=ff5fa2"></a>
  <a href="https://github.com/Powleads/PipeVoice/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/Powleads/PipeVoice?style=for-the-badge&color=ff5fa2"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge"></a>
  <img alt="Platform: Windows 10 & 11" src="https://img.shields.io/badge/platform-Windows%2010%20%26%2011-0078D6?style=for-the-badge&logo=windows">
  <a href="https://github.com/Powleads/PipeVoice/pulls"><img alt="PRs welcome" src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=for-the-badge"></a>
</p>

**Windows 10 & 11 · free forever · open source**

[**↓ Download for Windows**](https://github.com/Powleads/PipeVoice/releases/latest) · [**View source**](https://github.com/Powleads/PipeVoice) · [**pipevoice.app**](https://pipevoice.app)

<br/>

<!-- Video hero: clickable YouTube thumbnail (README can't iframe) -->
[![Watch PipeVoice in action](https://img.youtube.com/vi/3DZJbwTmcGU/maxresdefault.jpg)](https://youtu.be/3DZJbwTmcGU)

*▶ Watch the 3-minute demo — dictating a full Remotion video build into Claude Code, by voice.*

</div>

---

## What is PipeVoice?

PipeVoice is a tiny tray app for Windows. **Hold a hotkey, talk, release** — your speech is transcribed and typed straight into whatever window is focused: a terminal, a code editor, a browser, a chat box. It was built for one thing above all: **dictating to AI coding agents** like Claude Code and Cursor without lifting your hands off the flow.

It's the **free, open-source, Windows-first alternative to paid dictation SaaS like Wispr Flow** — but with a twist most tools force you to choose between: you pick the trade-off, per moment.

- **Bring your own key** (Deepgram / OpenAI) for fast, accurate cloud transcription at pennies a day, **or**
- **Polish free with Gemini** — clean up filler words and punctuation with Google's free tier, **or**
- **Run 100% offline** with local Whisper + Ollama, so nothing ever leaves your PC.

> *"The tool hears what I'm saying exactly, but the AI will take over in a sec and make sense of it."* — from the demo

One-click install · auto-updates itself · bring your own key, polish free with Gemini, or run 100% offline.

---

## ⚡ Why PipeVoice?

- 🪶 **No GPU. No gigabyte downloads.** The cloud path needs no GPU and downloads nothing. The local path is a single ~150 MB Whisper model on CPU. Most "local AI voice" tools make Windows users fight a CUDA/GPU gauntlet first — PipeVoice doesn't.
- ⚡ **Words appear live as you speak.** With Deepgram, the transcript forms word-by-word in the overlay in real time — true streaming, not "wait until you stop talking."
- 🎯 **Focused, not a kitchen sink.** A quiet tray app that does *one thing* well: talk and type. No voice cloning, no DAW, no studio you didn't ask for.
- 🪟 **Windows-native, today.** First-class Windows 10 & 11 support, end to end — installer, tray, auto-updates.
- 🤖 **Built for voice coding.** Dictate prompts straight into Claude Code, Cursor, or any terminal. Per-app profiles mean *"Terminal: raw + Enter. Chat: polished + auto-send. Editor: no AI cleanup."*
- 🔒 **Your keys, your machine.** API keys are stored locally in your `.env`, never uploaded. Or go fully offline and skip the cloud entirely.

---

## ✨ Features

- 🎙️ **Push-to-talk voice typing into any app** — terminal, editor, browser, chat box.
- 🎧 **Record a meeting** — captures your mic *and* your computer's audio as two separate streams, so what you said and what they said never get mixed up. Transcribe, name the speakers, and turn it into bullet points, to-dos or action items with an owner. See [Record a meeting](#-record-a-meeting).
- 🔀 **Three transcription engines, switchable at runtime:** Deepgram (live, fastest), OpenAI Whisper (accurate), Local Whisper (private/offline).
- ⌨️ **Two capture modes:** Push-to-talk (hold) or Toggle (tap on / tap off).
- 📋 **Two output modes:** Type keystrokes, or Clipboard + Ctrl+V (paste).
- 🎛️ **Configurable hotkeys** — a dedicated push-to-talk key (`ctrl+\`) *and* a separate clipboard hotkey (`right ctrl+right shift`).
- 🌏 **Accent / language selection** for better accuracy — *pick yours, including non-native accents* (US / GB / AU / IN / NZ).
- 🧩 **Per-engine model picker** — OpenAI `whisper-1`, Deepgram `nova-3`, Local `base.en` (bigger = more accurate but slower).
- ✨ **AI Polish (Flow mode)** — *"Tidies filler words, punctuation and casing after transcription."*
- 🧠 **Cleanup providers:** OpenAI, **free Google Gemini**, OpenRouter (free models), or **fully offline Ollama**.
- 🗣️ **Spoken commands:** say *"new line"*, *"scratch that"*, or end with *"send it"*.
- ↵ **Press Enter after typing (auto-send)** — handy for chat apps, leave off for editors.
- 📚 **Vocabulary list** — teach it names and jargon for correct recognition and spelling.
- 🔧 **Word fixes** — auto-corrections as `wrong=right`, comma-separated.
- 🩹 **Speech notes** — *"Describe your accent, stutter or fillers to guide AI cleanup."*
- 🟢 **Live overlay HUD** — a pill that shows it's listening, with a real-time VU meter and live transcript.
- 🔔 **Start/stop sounds** and **Start on Windows login**.
- 🔄 **Automatic silent updates** on startup (SHA-256 verified GitHub Releases).
- 🕘 **Local dictation history** (last 50, saved on your PC, opened from the tray) with Copy per entry.
- 🗂️ **Per-app profiles** — give a specific app its own engine, cleanup, output and Enter behaviour. *Terminal: raw + Enter. Chat: polished + auto-send. Editor: no AI cleanup.*
- 🎚️ **Advanced tuning** — Min seconds, Deepgram wait, Paste speed.
- 🔓 **Open source (MIT).** No account, no telemetry, no lock-in.

---

## 🎚️ Transcription engines

| Engine | Latency | Cost | Internet | Best for |
|---|---|---|---|---|
| **Deepgram** | Live (streaming) | ~$0.0043/min | Required | Words appear as you talk. Fastest. Needs `DEEPGRAM_API_KEY`. |
| **OpenAI Whisper** | ~1–2s after release | ~$0.006/min | Required | Highest accuracy. Needs `OPENAI_API_KEY`. |
| **Local Whisper** | ~1–4s (CPU) | Free | None | 100% private/offline. ~150 MB model, no GPU needed (auto-uses CUDA if present). |

> *"Deepgram streams live (fastest). Local and OpenAI transcribe after your release."* — and if a cloud engine fails (no internet), PipeVoice automatically retries that utterance on Local Whisper.

---

## 🚀 Quickstart (Windows)

**The easy way:** [**Download the installer**](https://github.com/Powleads/PipeVoice/releases/latest), run it (per-user, no admin), and you're done — it auto-updates itself from then on.

**From source:**

1. Install [Python 3.10+](https://www.python.org/downloads/) — tick **"Add Python to PATH"**.
2. Clone the repo: `git clone https://github.com/Powleads/PipeVoice.git`
3. Copy `.env.example` to `.env` and add a key (`DEEPGRAM_API_KEY` or `OPENAI_API_KEY`) — *or skip keys entirely and use Local Whisper offline.*
4. Double-click **`run.bat`**. First run builds a venv and installs everything, then starts listening. A pink mic icon appears in your system tray.
5. **Hold `Ctrl+\`, speak, release.** Text appears at your cursor.

Right-click the tray icon for **Settings** — engine, hotkeys, AI polish, per-app profiles, history. No JSON to edit.

### 🤖 The dev hook: talk into Claude Code, Cursor, or any app

Open your AI coding agent, hold the hotkey, and dictate:

> *"Hey Claude, make me a new video using Remotion based around how PipeVoice works, add some motion graphics in, then send it to the PWA app..."*

PipeVoice transcribes exactly what you said; turn on **Flow mode** and the AI cleans it into a tidy prompt before it lands. Set a **per-app profile** for your terminal (raw + Enter) and your chat app (polished + auto-send), and each window behaves the way it should.

**Going further — an MCP server for agents (opt-in).** Enable *Agent MCP* in the tray and PipeVoice exposes two local tools so an agent can use your voice directly: **`listen`** (the agent asks a question, you answer by voice) and **`transcribe`** (hand it a local audio/video file → text with word/segment timestamps, or `srt`/`vtt` captions). No API key, runs on your machine.

---

## 🎧 Record a meeting

PipeVoice can record a whole call — **both sides** — and turn it into a transcript, then into
notes and action items. Recordings stay on your machine; nothing is uploaded unless you pick a
cloud transcription engine.

The trick is that it captures **two separate streams**: your microphone, and your computer's own
audio (everyone else on the call). Because they're separate, *you* are identified with certainty
rather than guessed at — no diarization can mistake you for someone else.

**1 · Set a meeting hotkey.** Settings → Hotkeys → *Meeting hotkey* → Capture. Tap once to start,
tap again to stop. It's off until you set one.

**2 · Record.** A REC bar shows the elapsed time and a level meter for **each** side, so you can
see at a glance that both are alive. Your normal dictation hotkey is disabled while a meeting
records, so a stray press can't interrupt it.

**3 · Transcribe.** Settings → **Meetings**, pick the session, press *Transcribe*.
Deepgram is fast and separates the remote speakers; Local Whisper works fully offline but labels
everyone the same. The tab shows which engine produced each transcript.

**4 · Name the people.** Click a `Them 1` label in the transcript. PipeVoice plays the **clearest
two seconds** of that person talking — not their first "um" — so you can recognise them, then you
type their name. Names are per-meeting and never overwrite the original transcript, so a mistake
is one edit away from undone.

**5 · Summarise.** Choose **Bullets**, **To-dos** or **Actions**. Actions lists tasks with an
owner, which is why naming people first is worth the ten seconds:

```
- [ ] Send the deck and the pricing sheet — You — tonight
- [ ] Review the deck together — Sarah — next Tuesday
- [ ] Add the roadmap slide — unassigned
```

It won't invent owners or deadlines that weren't said; anything unclear comes back `unassigned`.
Summaries use the same provider as AI polish — the **free Gemini tier** works out of the box, and
**Ollama** keeps them entirely offline.

**6 · Bookmark the bits that matter.** Say **"bookmark that"** out loud during the call and it is
marked. Nothing listens live — the phrase is found when the recording is transcribed, so it costs
nothing, needs no key, and works completely offline. You can change the phrases, or set a
**Bookmark hotkey**, under Settings → Hotkeys.

A bookmark marks the **half-minute before it**, not the instant — you always react after the
interesting bit, and you say "bookmark that" after it too. Marked moments appear as **Highlights**
above the transcript; click one to jump to it. Summaries are told which moments you flagged, so
they get priority without dropping anything else important.

There is also a hands-free **double clap or snap**, off by default. Be warned: Windows Audio
enhancements — *Studio Effects* / *Voice Focus* on Copilot+ PCs — delete claps, snaps and keyboard
noise **on purpose** and ship switched on, so on many laptops no amount of clapping will register
until you turn them off in Windows Sound settings. The spoken phrase has no such problem, because
it is speech. Settings → Hotkeys → *Test* tells you which is happening.

**7 · Tidy the wording (optional).** Press **Polish** on a transcribed meeting to strip "um", "uh"
and false starts and fix punctuation. It only tidies — it never rewords anyone or changes what was
said — and the raw transcript stays one click away.

**Fix a mis-heard word.** Right-click any word in a transcript → *Fix wording…*. It corrects every
occurrence in that meeting, and if you tick "Also fix this in future dictation" the same fix is
applied to your normal typing from then on. Corrections are stored separately, so the original
transcript is never altered.

> **Two things worth knowing.** Your computer's audio is captured as one mix, so a podcast or
> Spotify ends up in the transcript alongside the meeting — pause it first. And recording other
> people may require their consent where you live.

Recordings and transcripts live in `%LOCALAPPDATA%\Pipevoice\meetings\`.

---

## 🆚 PipeVoice vs the alternatives

An honest comparison. Different tools win for different people — here's where each one shines.

| | **PipeVoice** | **Wispr Flow** | **Voicebox** |
|---|---|---|---|
| **What it is** | Focused Windows voice typing | Paid dictation SaaS | Full AI voice studio (cloning + TTS + dictation) |
| **Price** | **Free, open source (MIT)** | Paid subscription | Free, open source (MIT) |
| **Account required** | **No** | Yes | No |
| **Platform** | **Windows-native, first-class** | macOS / Windows | macOS + Windows (Linux = build from source) |
| **GPU required** | **No** — cloud needs none; local runs on CPU | N/A (cloud) | Built around a local GPU; CPU fallback is slow |
| **Downloads to start** | **Nothing** (cloud) / ~150 MB (local) | — | Whisper models + TTS models (GBs) |
| **Live streaming transcript** | **Yes (Deepgram, word-by-word)** | Yes | No — batch only |
| **Engine choice** | **Cloud OR offline, switchable** | Cloud only | Local Whisper only |
| **AI cleanup** | **OpenAI / free Gemini / OpenRouter / offline Ollama** | Built-in | On-device LLM |
| **Per-app profiles** | **Yes** | Limited | No |
| **Voice commands** | **Yes** ("new line", "send it", "scratch that") | Some | No |
| **Offline option** | **Yes — 100% local** | No | Yes (local-only) |

**Bottom line:** want a beautiful AI voice *studio* (cloning, TTS) and have a GPU? Voicebox is excellent. Want a paid, polished cross-platform SaaS? Wispr Flow. Want **the simplest, lightest way to talk and type on any Windows PC — no GPU, no gigabyte downloads, cloud-or-offline, words live as you speak, straight into any app including your terminal**? That's PipeVoice.

---

## ❓ FAQ

**Is PipeVoice really free?**
Yes — free forever, MIT-licensed, open source. No account, no subscription, no trial wall. If you use the Deepgram or OpenAI engines you pay those providers directly (pennies a day) with your own key. Use Local Whisper and it's free end-to-end.

**Is this a free dictation app for Windows / a free Wispr Flow alternative?**
Yes. PipeVoice is a free, open-source voice typing tool for Windows that types into any app. It's a strong alternative if you want dictation without a subscription or account.

**Does it work offline?**
Yes — choose the **Local Whisper** engine and (optionally) **Ollama** for AI cleanup, and nothing leaves your PC. No internet, no cloud, no API key.

**Do I need a GPU?**
No. The cloud engines need no GPU at all and download nothing. Local Whisper runs on CPU out of the box (and auto-uses CUDA if you happen to have it).

**Can I dictate into Claude Code / Cursor / a terminal? (voice coding)**
That's exactly what it was built for. PipeVoice injects real keystrokes into the focused window — terminals included — so you can talk your prompts into AI coding agents. Per-app profiles let your terminal get raw text + Enter while your chat app gets polished, auto-sent text.

**Does it send Enter automatically?**
Only if you want it to. By default text lands at your cursor so you can review/edit before submitting. Turn on **auto-send** (globally or per app) for chat-style apps.

**What about accents and non-native speakers?**
Pick your accent/language for better accuracy, and use **Speech notes** to describe your accent, stutter or fillers — the AI cleanup uses it to make sense of your speech. *If you talk like the creator: always use AI cleanup. 😄*

**Is my data private?**
API keys are stored locally in your `.env` and never uploaded. Dictation history is saved only on your PC. Go fully offline with Local Whisper + Ollama for zero cloud involvement.

**Which Windows versions?**
Windows 10 and Windows 11.

---

## ⭐ Support PipeVoice

PipeVoice is free, open source, and has zero telemetry — so it grows entirely by word of mouth. If it saves your wrists (or your sleep), three things help, in order:

1. **[Star the repo](https://github.com/Powleads/PipeVoice)** — the single biggest signal that helps other people find a free, no-account voice typing tool for Windows.
2. **[Leave an honest review on Product Hunt](https://www.producthunt.com/products/pipevoice/reviews)** — it tells people weighing the download whether it's worth it.
3. **Tell one person** who types all day — or who talks to Claude Code / Cursor more than they type.

[![Star on GitHub](https://img.shields.io/github/stars/Powleads/PipeVoice?style=social)](https://github.com/Powleads/PipeVoice/stargazers)

---

## 🤝 Contributing

PRs welcome — this is built in public and shipped fast.

- 🐛 Found a bug or have an idea? [Open an issue](https://github.com/Powleads/PipeVoice/issues).
- 🔧 Want to add an engine or feature? Every transcription engine implements the same small `start_session(on_partial) -> Session` interface, so adding one is a single new file plus a branch. See the architecture notes in the repo.
- 📦 Local dev: `run.bat` (source), `build_exe.bat` (single .exe), `build_installer.bat` (full installer).

There's no required test suite, linter, or formatter — keep changes focused and the tray app light.

---

## 🔗 Links

- 🌐 **Website:** [pipevoice.app](https://pipevoice.app) — *voice typing for Windows that types into any app.*
- ⬇️ **Download:** [latest release](https://github.com/Powleads/PipeVoice/releases/latest)
- ▶️ **Demo video:** [youtu.be/3DZJbwTmcGU](https://youtu.be/3DZJbwTmcGU)
- 💬 **Issues & ideas:** [github.com/Powleads/PipeVoice/issues](https://github.com/Powleads/PipeVoice/issues)
- 📰 **Blog:** [pipevoice.app/blog](https://pipevoice.app/blog)

---

<div align="center">

**Talk faster than you type.** · Free forever · Open source · Windows 10 & 11

Made with ☕ and not much sleep. **MIT licensed.**

</div>