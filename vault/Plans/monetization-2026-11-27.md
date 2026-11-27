# PipeVoice: can it make £200 a month?

*27 Nov 2026 · research by Claude for James*

## Decisions (James, 27 Nov)

- **Supporter licence: dropped.** People will not pay for a badge.
- **Build PipeVoice Cloud.** £3/month or £25/year.
- **Cloud v1 includes:** no-key setup (our key), settings and vocabulary sync across PCs, early-access (beta) updates.
- **Speech-to-text for Cloud:** Groq Whisper first (~$1/month per heavy user), Deepgram as backup.
- **Not now:** Mac version (2–4 days + Apple account). Claude Artifacts cannot replace the app: a web page cannot type into other apps.
- **First step, in progress:** an anonymous daily install count, on by default with an off switch. Every "zero telemetry" claim is rewritten to say exactly what is sent.

## Short answer (research, before the decisions)

- **Not yet.** £200/month needs about 1,000–2,000 active users. PipeVoice has about **300–400** today (estimate, see below).
- With ~300 active users, a paid tier makes about **£30–75 a month**.
- A **one-off launch deal** this weekend could bring **£100–300 one time**.
- **Do not hide features that are free today.** Add new paid features on top.

## 1. How many users? (what we know)

| Measure | Number | Source |
|---|---|---|
| Installer downloads, all time (since 19 Jun) | **8,699** | GitHub release asset counts |
| Downloads of the latest release (v2.49.29, 9 Nov) | 597 | GitHub |
| Auto-update fetches of that release | **334** | GitHub `.sha256` downloads |
| GitHub stars | 46 | GitHub |
| Repo visitors, last 14 days | 77 unique | GitHub traffic |
| Where repo visitors come from (14 d) | pipevoice.app 20, Google 11, GitHub 5, Bing 2, Doubao 2 | GitHub referrers |

**How to read it (estimate, not a measurement):**
- The app's auto-updater downloads the `.sha256` file. People in a browser almost never do. So `.sha256` downloads per release ≈ installs that are still running and updating. Big releases since September each show **~200–330**.
- Exe downloads minus updater fetches ≈ new manual downloads. For the latest release that is ~260 in 18 days, about **15 new downloads a day**.
- Most of the 8,699 are the same installs updating again and again. It is **not** 8,699 people.
- The numbers per release have been flat since September. Downloads are not clearly growing.

**What we do NOT know:** who the users are, what they use it for, how often, or which country. The app sends nothing home, which is part of its promise.

**Not checked:** the website has its own private visitor counter (pipevoice.app/admin). I could not see if it is switched on. You can open that page with your admin token.

## 2. Is £200 a month possible?

| Plan | Payers needed | Active users needed (2–5% pay) |
|---|---|---|
| £5/month | 40 | 800–2,000 |
| £3/month | 67 | 1,300–3,300 |

- The 2–5% figure is a common rule of thumb for free-to-paid apps. It is unverified for PipeVoice. Donation-only open-source projects often get under 1%.
- **Today, about 300 users × 2–5% = 6–15 payers = £30–75/month.**
- £200/month becomes possible at about 3–5× today's users. Growth matters more than price.

## 3. Hide features, or add new ones?

**Add new ones. Do not hide existing features.**
- The code is MIT open source. Anyone can turn a hidden feature back on, or fork the app. Taking away free features usually causes anger and a free fork.
- The README says "free forever, no subscription, ever". Keep that true for everything that exists today. New paid extras do not break it.

## 4. Options, ranked

| # | Option | What the user gets | Build effort | Running cost |
|---|---|---|---|---|
| **1** | **Supporter licence** (recommended first) | "Supporter" badge, early-access update channel, extra themes. Mostly: they support a free tool they use every day | **2–3 hours** (Lemon Squeezy or Stripe checkout + licence key box in Settings) | none |
| **2** | **Meeting mode (Pro)** | Record a meeting (mic + PC sound), get a transcript and an AI summary. You already use PipeVoice for meetings | **1–2 days** | none if it uses the user's own key or Gemini free tier |
| **3** | **PipeVoice Cloud** | No API key setup; vocabulary and settings synced across PCs | **1–2 days**, plus upkeep | **real**: speech-to-text is charged per minute, so a heavy user can cost more than they pay. Needs a monthly minutes cap |
| — | anyIP proxy SDK | — | — | Rejected: it rents out users' internet connections |

Suggested prices: **£4/month or £29 lifetime.** Launch deal: **£15 lifetime "founding supporter"**.

## 5. Black Friday is today

Black Friday 2026 is today (27 Nov). Cyber Monday is 30 Nov.
- If option 1 is built today (2–3 hours), a **"founding supporter, £15 lifetime, ends Monday"** offer can go out through the in-app update note, the website and the email list.
- Realistic result: 10–20 buyers = **£150–300 one time** (guess, not measured).
- It also answers the real question: **will PipeVoice users pay at all?**

## 6. Measure first, honestly

To know real user numbers, count **update checks** on the PipeVoice website, not on GitHub:
- The app asks pipevoice.app "is there a new version?" instead of asking GitHub directly.
- The website counts unique installs per day the same way the site counter already does: a hashed value that changes daily, with no IP address stored and no audio or text.
- Say this plainly on the privacy page. Nothing else leaves the PC.
- Effort: **1–2 hours.** Result: a true "daily active installs" number within a week.

## External check (what proves this right)

- **Pass:** within 14 days of the launch deal, **10+ paying supporters** and a daily-installs number from step 6. Then £200/month is a growth problem, and option 2 is worth building.
- **Fail:** fewer than 5 buyers in 14 days. Then stop building paid features and keep only the donation link.
