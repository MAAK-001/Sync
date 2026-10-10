# Sync backend

Sync listens to your calls and meetings with you. It nudges you on screen when something is being missed while you
can still act, keeps a shared, editable record of every project, and learns how you work. **Everything runs locally,
built on free, open-source software and open-weight models.**

See [../motive.md](../motive.md) for the why and [../architecture.md](../architecture.md) for the design (the v2.4
note at the top describes the current version).

## What's inside

| Part | What it does | Code |
| --- | --- | --- |
| **Live engine** | **Instant checks** find gaps G1–G16 within about a second (see the table below). A **small model** checks claims against your documents and suggests the next question. A **gate** decides whether a nudge is worth interrupting you | `sync/live/` |
| **Project brain** | Project memory (summary, goal, context, every number on record), document facts, the after-call record, briefs and follow-ups | `sync/brain/` |
| **Guide** | What Sync listens for in every conversation: goals, numbers worth tracking, vague words. There are no roles | `sync/guide/` |
| **Sharing** | Profiles, sign-in keys, project members and invite links; who may see and change what | `sync/auth.py`, `sync/access.py` |
| **Agent** | Chat with Sync, across all projects or inside one project with its complete memory | `sync/agent/` |
| **Learning** | Your own habits and what worked, from your own calls (never from sample calls) | `sync/learning.py` |
| **Speech** | silero VAD plus Parakeet (or Moonshine) through sherpa-onnx, one stream for you and one for them | `sync/stt/` |
| **Desktop agent** | Detects calls (Phone Link, Zoom, Teams, Meet, WhatsApp…) and captures both sides. Shows the countdown, the "which project?" picker and nudges on screen; tray icon and shortcuts | `sync/desktop/` |
| **App** | The web app (served by the backend at /app): workspace, projects, live companion, recap review, commitments, Ask Sync, what Sync learned, settings. Plain HTML/CSS/JS, no build step | `../frontend/` |
| **History** | Every change goes in an append-only `events` table, with who made it and when | `sync/events.py` |

### What Sync nudges about

| Gap | When | Example |
| --- | --- | --- |
| G1 | A question or part of a request went unanswered | "They also asked you to: send your SOC2 whitepaper; introduce an implementation engineer" |
| G2 | A vague word ("soon", "a lot", "secure") | "Ask: what date exactly — and what's driving it?" |
| G3 | A concern was raised and not explored | "Ask: what worries you most about the SAP upgrade?" |
| G4 | A goal of the conversation is still open | "Ask: what's the next step, and who owns it?" |
| G5 | A promise without a date | "Confirm: by when will Priya send the integration list?" |
| G6 | An offer with a condition | "Confirm the condition: “if you sign for two years”" |
| G7 | A contradiction, or a launch before what it depends on is ready | "Check the timeline: campaign Nov 15, but deployed only by Nov 18" |
| G8 | You over-promise against your own documents | "Caution: standard implementation takes 6–8 weeks [Implementation Guide §2]" |
| G9 | You ask for something already known or already said | "Already said: account number EXP-8842 — Casey" |
| G10 | A parked topic, before the call ends | "Ask: revisit “data migration” before we close?" |
| G11 | A meeting agreed for a day with no time | "Confirm: what time next Thursday?" |
| G12 | Their question (or your blank moment) answered from your documents or past calls | "Answer: 15 × $1,200 × 6/12 = $9,000 — Additional seats cost $1,200 per seat annually…" |
| G13 | You skipped past their urgency, emotion or deadline | "Acknowledge first: they're furious — presentation to my board in two hours" |
| G14 | You gave ground on a number with nothing back | "Caution: you moved from $120,000 to $109,000 — ask for something in return" |
| G15 | A task came up and nobody took it | "Ask: who will update the sandbox API keys — and by when?" |
| G16 | A debate about something for later, in a meeting about something else | "Off the agenda: “v2.0 UI overhaul” — park it for another meeting?" |

Near the end of a call, open items are collected on one "Before you hang up" card instead of separate nudges.

## Models

All of these were chosen by measuring on a CPU-only laptop (i7-1355U, 16 GB):

| Use | Model | License | Why |
| --- | --- | --- | --- |
| Live (claim checks, next question, catch-up) | `granite4.1:3b` (Ollama) | Apache 2.0 | 5/5 correct claim checks; 3–7 s per check with a cached prompt |
| Chat agent and follow-up drafts | `granite4.1:3b` | Apache 2.0 | Clean tool calls |
| After-call record (background) | `qwen3:4b` (Ollama) | Apache 2.0 | Most accurate about who said what; a few minutes after a call on CPU |
| Speech to text | Parakeet TDT 0.6B v2 int8 (sherpa-onnx) | CC-BY-4.0 | 1.8% word error rate on your side; 11× faster than real time |
| Embeddings | bge-small-en-v1.5 (fastembed, ONNX) | MIT | about 10 ms per sentence |

Every model is configurable in `sync/config.py` (or with `SYNC_<NAME>` environment variables / `backend/.env`).

## Setup (Windows)

1. Create a virtual environment and install the dependencies:
   ```bash
   python -m venv .venv
   ```
   ```bash
   .venv/Scripts/python -m pip install -e ".[desktop,dev]"
   ```
2. Pull the language models (about 2 GB and 2.5 GB):
   ```bash
   ollama pull granite4.1:3b
   ```
   ```bash
   ollama pull qwen3:4b
   ```
3. Download the speech models into `~/.sync/models/stt` (about 650 MB); see "Speech models" below.

Data (the database and model files) lives in `~/.sync`. Set `SYNC_HOME` to move it.

## Run

Start the backend and the desktop agent, each in its own terminal:

```bash
.venv/Scripts/python -m sync.main
```

```bash
.venv/Scripts/python -m sync.desktop.agent
```

Open **http://127.0.0.1:8765** and create your profile; the desktop agent on this laptop signs itself in as you.

### Using Sync

- **Calls are picked up automatically.** The agent only watches which app is using the microphone, never the audio.
  When a call starts, a short countdown appears at the top of the screen; skip it and nothing is kept.
- **You choose the project.** When Sync starts listening, a small window in the middle of the screen lists your
  projects, with a search box, "+ New project" and "Decide later". Sync never guesses. A new project is named after the
  call; rename it on the project page.
- **Nudges appear on screen, over any app**, in a see-through strip at the top centre. Clicks pass through it.
- **Projects are an editable record.** On a project page you can edit details, items and people, add documents,
  manage members and chat with the project's full memory. **History** shows every change with who made it and when.
- **The live companion** (Conversations → the live call) shows the same nudges, the transcript, the live ledger,
  goal coverage, private Ask and "Close the loop". After the call, **review the recap**: confirm, edit or reject each
  proposed update, then "Finish review".
- **No call handy?** Home → "Play a sample call".
- **Only one desktop agent runs at a time.** Troubleshooting logs: `~/.sync/agent.log` and `~/.sync/server.log`.

| Shortcut | What it does |
| --- | --- |
| `Ctrl+Alt+N` | Hide or show nudges (the countdown and "Sync is on/off" notices always show) |
| `Ctrl+Alt+S` | Skip the countdown (nothing is kept), or stop listening |
| `Ctrl+Alt+L` | Listen to this call, when auto-listen is set to "Ask me every time" |

### Sharing projects with teammates

1. Start Sync so other laptops on your network can reach it:
   ```bash
   SYNC_HOST=0.0.0.0 .venv/Scripts/python -m sync.main
   ```
2. On a project's **Members** tab, create an invite link and send it. The teammate opens it, enters their name, and
   the project is shared: its calls, items, people, documents and history.
3. For their own calls, the teammate runs the desktop agent on their laptop with a key from
   **Settings → Desktop & runtime → Create key**.

A conversation belongs to whoever had it: only they control it live and choose its project. Once it is filed under a
shared project, every member sees it. Calls not filed under a project stay private.

## Test and evaluate

```bash
.venv/Scripts/python -m pytest -q
```

The golden-gap eval replays scripted calls with planted gaps and scores what Sync caught:

```bash
.venv/Scripts/python -m demo.eval_gaps --mode normal
```

The test scenarios (sales call, support escalation, renewal negotiation, group launch sync) play line by line and
print every nudge right after the line that triggered it, next to what was expected:

```bash
.venv/Scripts/python -m demo.run_scenarios --t2
```

| Test | Result |
| --- | --- |
| 4 test scenarios (13 expected moments), live model on, Normal nudges | 13 / 13 caught, no wrong nudges |
| Golden-gap eval, instant checks, Active / Normal | 100% / 96% recall, 100% precision |

Your data is never touched by these: each run uses its own throwaway database. To wipe Sync and start fresh:

```bash
.venv/Scripts/python -m demo.seed --reset
```

## Speech models

Download from `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/` and extract into
`~/.sync/models/stt/`: `silero_vad.onnx` and `sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2` (or the lighter
`sherpa-onnx-moonshine-base-en-quantized-2026-02-27.tar.bz2`). For telling remote speakers apart, save
`https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx`
as `~/.sync/models/stt/campplus_en.onnx`.
