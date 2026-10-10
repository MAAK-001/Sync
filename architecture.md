# Sync: Architecture & Workflows

> **What this document is:** how Sync is built. It covers where Sync can listen, how the live engine decides what to say, how projects remember everything, how the agent and the learning loop work, and the plan for the 18-hour build.
> It is the companion to [motive.md](motive.md), which explains *why* Sync exists and *what* it is.
>
> Version 2.0 · 9 October 2026 · **Free and open-source stack, local-first**

> **What changed from v1 of this document**
> - All paid services are removed. Claude → **open-weight models on Ollama**; Deepgram → **local speech recognition** (Moonshine / faster-whisper / sherpa-onnx); Twilio → **Sync Call Link** (self-hosted WebRTC, later).
> - The live engine is now **three-tiered**: instant *signals* (rules and embeddings), a *small model* as editor, and a *deep model* in the background. This design comes from measured CPU speed on the dev laptop (§5.3).
> - **ElevenLabs** (10k free credits) is used only to generate **fictional demo and eval audio**. It never processes real conversations (§5.6).
> - **v2.1, auto-listen:** a **Sync Desktop Agent** detects every call and meeting and starts listening automatically. That includes ordinary **phone calls taken on the laptop through Windows Phone Link** (Mac Continuity later). Every call has a skip countdown and an off switch, and the user can set rules (§3.1).

> **v2.2: implemented and measured** (backend in [`backend/`](backend/README.md)). Decisions made from on-device tests:
> - **Models, from bake-offs on this laptop:**
>   - **Live, chat and follow-ups:** `granite4.1:3b`. 5/5 correct claim checks at 3–7 s with a cached prompt, and clean tool calls.
>   - **Post-call record:** `qwen3:4b`. It attributed who said what correctly where granite didn't (runs about 4–5 min in the background).
>   - **Rejected:** Qwen3.5, because it can't reuse Ollama's prompt cache (40–60 s per call).
> - **Speech:** Parakeet TDT 0.6B (1.8% word error rate on your side; hears "25 lakh" correctly; 11× faster than real time). Moonshine base is the lighter option. Remote speakers are told apart with CAM++ voice embeddings that are kept for the call only.
> - **No live "ledger sweep".** Small models made poor live extractors (truncated JSON, items invented from memory, 40–70 s per call). The live ledger comes from instant signals, and the post-call pass makes it final with two focused calls on one cached prompt.
> - **A wrap-up card at the close.** Unanswered questions, open goals, missing dates and parked topics are folded into one card instead of a burst of nudges.
> - **Data lives in `~/.sync`**, outside OneDrive and outside AppData (which packaged apps virtualise).
> - **Golden-gap eval:** with instant signals alone, 100% recall / 100% precision in Active mode and 95% / 100% under Normal mode's nudge budget, across sales, support, team and negotiation calls. With the live model on (Thursday call in real time), 100% / 100%.

---

> **v2.4: what changed after hands-on testing.** Where this note disagrees with the sections below, this note wins.
> - **No roles.** The Role Lens is removed. Every conversation uses one general guide (`backend/sync/guide/guide.yaml`) with these goals: the goal, the timeline, who decides and the next step. It also includes the shared number slots and vague words.
> - **The user chooses the project; Sync never guesses.** Automatic project matching is removed. When Sync starts listening, a window in the middle of the screen lists the user's projects, with a search box, "+ New project" and "Decide later". A new project is named after the call, and the user can rename it. The live page in the app offers the same choice.
> - **The live model's memory** carries the project's name, goal, summary and context, its open conflicts, and every number on record (amounts, quantities, dates, days, times). That is in addition to the other items (capped at 24) and the people. Numbers people type into a project are tracked like numbers heard on a call.
> - **Shared projects.** Sync has user profiles and signs people in with keys; the database stores only hashes of the keys. Projects have members, and a teammate joins with an invite link. Everything in a project is common: calls filed there, items, people, documents and conflicts. Every change is logged with the person's name and time. A conversation belongs to whoever had it: only they control it live, and calls not filed under a project stay private. Teammates on other laptops reach a shared Sync over the network (`SYNC_HOST=0.0.0.0`) and run their own desktop agent with their own key.
> - **Editable project records.** Name, goal, summary, context and status can all be edited, and so can items, people, documents and members. Each project has its own chat with its complete memory.
> - **New nudge types, from the final test scripts:**
>   - G12, answer card: answers the other side's question, or the moment you stall, from your documents and past calls, including per-seat or per-user sums.
>   - G13, acknowledge: flags urgency, emotion or a deadline you skipped past.
>   - G14, concession: flags giving ground on a number with nothing back.
>   - G15, ownerless task: flags "someone needs to…" when nobody takes it.
>   - G16, off-agenda: flags a debate about a later version while the meeting is about something else.
>   - Extended existing checks: G9 now covers identifiers already given (account and order numbers); G7 now covers a launch scheduled before what it depends on is ready; G1 now covers compound questions and partly confirmed requests.

## 0. At a glance

```
┌──────────────── CAPTURE: SYNC DESKTOP AGENT (Windows MVP, tray app) ─────┐
│ Auto-detects calls & meetings: Phone Link (phone calls), Zoom, Teams,    │
│ Meet in Chrome/Edge/Brave, WhatsApp… → auto-listen (skip·pause·stop·rules)│
│ Captures system audio (them) + mic (you) · floating overlay window       │
│ Also: in-person mode · browser-capture fallback · script & demo players  │
│ Later: macOS agent (Continuity) · mobile PWA · Sync Call Link            │
└────────────────┬─────────────────────────────────────────────────────────┘
                 │ WebSocket: PCM16 16 kHz, 2 channels (you | them) + control JSON
┌────────────────▼─────────────────────────────────────────────────────────┐
│ REALTIME GATEWAY (FastAPI)  session · notify user · off-the-record · stop│
└───────┬────────────────────────────────────────────────┬─────────────────┘
        │ audio                                          │ events → client
┌───────▼──────────────┐ final utterances ┌──────────────┴───────────────────┐
│ LOCAL STT            │─────────────────▶│ LIVE ENGINE (per session)        │
│ silero-vad + Moonshine│ + redaction     │ T0 signals: rules + embeddings   │
│ / faster-whisper     │                  │ T1 small LLM: verify / sharpen   │
│ (sherpa-onnx option) │                  │ gate → one nudge                 │
└──────────────────────┘                  └──────────────┬───────────────────┘
                                                         │ session ledger
┌────────────────────────────────────────────────────────▼─────────────────┐
│ PROJECT BRAIN (T2 deep model, background): match · consolidate ·         │
│ conflicts · recap · brief · follow-up                                    │
├──────────────────────────────────────────────────────────────────────────┤
│ AGENT (chat any time, tools)        LEARNING (signals → playbook)        │
├──────────────────────────────────────────────────────────────────────────┤
│ STORAGE  SQLite: users · lenses · projects · people · sessions ·         │
│          utterances · items · facts · conflicts · nudges · docs ·        │
│          embeddings · playbook · APPEND-ONLY EVENT LOG                   │
└──────────────────────────────────────────────────────────────────────────┘
   LLM runtime: Ollama (local) · Embeddings: fastembed (local) · $0 to run
```

---

## 1. Principles that shape the architecture

1. **Local-first, free and open.** Every component is open source and runs on the user's own machine. The confidentiality story is simple: the conversation never needs to leave the device.
2. **The AI is the editor, not the engine.** Fast, explainable *signals* (rules plus embeddings) find most gaps instantly. A small model verifies and sharpens them, and a deeper model does the heavy thinking after the call. This is what lets Sync run live on an ordinary laptop CPU.
3. **The engine is text-first.** Everything after speech recognition works on text events. We build and test with scripted transcripts, then plug in audio. Every capture surface feeds the same pipeline.
4. **Two channels when possible.** With your mic and the other side's audio captured separately, Sync knows exactly what *you* said, with no voice fingerprinting.
5. **The KV cache gets the best seat.** Prompts are laid out *stable prefix first, then append-only*, so the local runtime reuses its cache and only processes new tokens. We measured this: prefill dropped from 25 s to 1.5 s (§5.3).
6. **Everything is an event.** Every change is appended to an event log, and current state is derived from it.
7. **Every part is swappable.** STT, LLM, embeddings and capture sit behind adapters. Moving from laptop to GPU box, or from English to multilingual STT, is a config change.

---

## 2. Where Sync can listen: the platform research

Phones and browsers deliberately limit who can hear a call. Sync **works with these protections, never around them.**

| Surface | Can a third-party app hear both sides live? | What actually happens | Sync's approach |
| --- | --- | --- | --- |
| **Phone call taken on the laptop** (Windows Phone Link with Android 10+ or iPhone on iOS 15+; Mac Continuity with iPhone) | **Yes** | The ordinary cellular call is relayed over Bluetooth (the PC adapter must support HFP). The caller's voice plays on the laptop, and your voice goes through the laptop mic | **MVP path for phone calls.** The Desktop Agent detects the call and captures system audio plus mic |
| **Desktop agent on the laptop** (any meeting app: Zoom, Teams, Webex, WhatsApp, or Meet in a browser) | **Yes** | Windows: WASAPI loopback (what the laptop plays) plus the mic in shared mode, with no picker and no bot. macOS (later): Core Audio process taps (14.2+/14.4+, "System Audio Recording" permission) | **MVP path for every call and meeting**, started automatically (§3.1) |
| **Laptop browser only** (no agent) | **Yes** | `getDisplayMedia` captures a shared tab's audio in Chrome 74+, and system audio when sharing the entire screen on Windows (on macOS reported from Chrome 141 / macOS 14.2+). The user has to pick the source every time | Fallback for machines without the agent |
| **Meeting-platform apps** (later, optional) | Yes, with platform consent | Zoom RTMS is GA (paid). Google Meet Media API is in Developer Preview | Not on the free path. Browser capture covers these platforms anyway |
| **In person** | **Yes** (room mic) | Normal mic capture (Android 14+ needs a microphone-type foreground service) | **MVP:** laptop or phone on the table, with speaker separation |
| **Android phone call** | **No** | `VOICE_CALL` audio is reserved for the preloaded dialer. Play has banned the Accessibility-API workaround for call recording since May 2022. During a call, other apps' mic gets silence, and voice-communication playback can't be captured | Phone paths below |
| **iPhone call** | **No** | iOS interrupts other apps' audio sessions during calls, and there's no API to access call recordings. iOS 18.1+ has a built-in recorder (it announces itself and saves a transcript to Notes) | Phone paths below |

### Phone conversations: the free, legitimate paths

| Path | Live? | Both sides? | Cost | When |
| --- | --- | --- | --- | --- |
| **A. Take the phone call on the laptop.** Windows Phone Link (Android or iPhone) or Mac Continuity (iPhone). Sync joins automatically | ✓ | ✓ two channels (system audio + mic) | free | **MVP (Windows)** · macOS Next |
| **B. Speakerphone with a second device.** Phone on speaker, Sync in in-person mode on a laptop. The same phone can't do both, because its mic is reserved during the call | ✓ | ✓ one channel plus speaker separation | free | MVP |
| **C. Sync Call Link.** Send the other person a link to a browser call room hosted by Sync (open-source WebRTC: LiveKit or Jitsi, self-hosted). Both tracks arrive separately | ✓ | ✓ two clean tracks | free (self-hosted) | Later |
| **D. Import afterwards.** Upload the recording or transcript (e.g. from iOS Notes or a dialer's own recording) | post-call | ✓ | free | Next |

> A bridge to a real phone number (PSTN) always needs a paid telephony number, so it's left out of the free stack. Path C replaces it.

> **Decision:** the MVP is a **Windows laptop app**. A **Sync Desktop Agent** (Python tray app) auto-detects and captures every call and meeting, including phone calls through Phone Link, and the **web UI** is served locally at `127.0.0.1`. It also offers in-person mode, browser-capture fallback, a text-script player and an audio demo player. macOS (Continuity) comes next.

---

## 3. Components

### 3.1 Capture: the Sync Desktop Agent (Windows MVP)

The Desktop Agent is a small Python background app with a system-tray icon. It runs on the laptop next to the local backend, and it's what lets Sync **join every call and meeting automatically**, including ordinary phone calls taken on the laptop.

#### Capture

- **Them:** WASAPI **loopback** of the output device, meaning whatever the laptop plays (the caller's or the meeting's voices), via `PyAudioWPatch` or `soundcard`.
- **You:** the default microphone in **shared mode**. Windows lets several apps use the mic at once, so the call app and Sync both hear it.
- Both streams are resampled to 16 kHz and interleaved into **stereo Int16** frames (channel 0 = you, channel 1 = them). They go to the backend as 100 ms frames over the same WebSocket protocol the browser uses.
- There's no meeting bot, no screen-share picker and no browser tab.
- **Limitation:** loopback hears *everything* the laptop plays, including notifications and music.
  - MVP: suggest Focus / Do Not Disturb during calls. The ASR also ignores non-speech.
  - Next: Windows **process loopback** (Windows 10 2004+), which captures only the call app's audio.

#### Phone calls through the laptop

- **One-time setup:** pair the phone with **Phone Link** (Android 10+ with *Link to Windows*, or iPhone on iOS 15+). The laptop's Bluetooth must support **HFP**.
- **Make or answer calls on the laptop**, from the Phone Link notification or the app. The call audio then plays on the laptop and uses its mic, so the agent hears both sides live.
- **If the call is answered on the handset,** the audio stays on the phone. The overlay says *"Take the call on the laptop so Sync can listen."*
- **Audio: the laptop's built-in mic and speakers.** Phone Link doesn't support wireless headsets, so calls run on the laptop's own mic and speakers. Sync takes your voice straight from the laptop mic. The caller's voice leaking from the speakers into the mic is removed by **echo dedupe** (§3.3), plus Windows' communications echo cancellation where the laptop supports it (the agent opens the mic in *communications* mode).
- **macOS (Next):** iPhone calls through **Continuity**, with Core Audio taps for capture and a Swift or pyobjc agent.

#### Auto-listen: how a call or meeting is detected

The agent polls every 1 s:

1. **Mic in use.** Windows records which app is using the microphone under `HKCU\…\CapabilityAccessManager\ConsentStore\microphone`, where `LastUsedTimeStop = 0` means in use. Packaged apps such as Phone Link sit at the top level, and desktop apps sit under `NonPackaged`. *Verified on the dev laptop: Teams, Zoom, WhatsApp, Chrome, Brave and Webex are all recorded.*
2. **Process alive.** `psutil` confirms the app is actually running. A crashed app can leave a stale "in use" entry, and we saw one on the dev laptop (a game that wasn't running).
3. **Known call apps** (configurable): Phone Link (`Microsoft.YourPhone`), Zoom, Teams, Webex, WhatsApp, Slack, Discord and Skype. Browsers (Chrome, Edge, Brave, Firefox) count when the window title shows a meeting (`Meet –`, `Zoom`, `Teams`, `Jitsi`…).
4. **Context.** The window title (`pywin32`) gives the meeting name or, where Phone Link shows it, the caller's name or number. Rules and project matching both use it.
5. **Start and end.** A call **starts** when a known app holds the mic for ≥ 2 s. It **ends** when the app releases the mic for ≥ 5 s, or the window closes, and the session auto-stops.

#### Controls: automatic, but always yours to switch off

| Control | What it does |
| --- | --- |
| **Master switch** (tray and settings) | *Auto* (default) · *Ask every time* · *Off* |
| **Skip this call** | When a call starts, the overlay counts down (5 s by default, 0–10 s configurable). Audio during the countdown stays **in memory only**; *Skip* discards it and nothing is stored |
| **Pause / Off the record** | Stops processing for part of the call, logged as a gap |
| **Stop for this call** | Sync stops for the rest of the call. *Stop and delete* also deletes what was captured |
| **Global hotkey** | `Ctrl+Alt+S` toggles Sync for the current call |
| **Rules** | Per app (*Always / Ask / Never*), per contact or number (e.g. *never for family, doctor*), per project, title keywords (*personal, HR, 1:1*), working hours, and *pause auto-listen for 1 h / until tomorrow* |
| **Always visible** | The tray icon turns red and the overlay shows *● Listening* whenever Sync is processing audio |

- **Only the user is notified.** When Sync turns on, the user sees a notification (*"Sync is on · Phone Link · Priya"*) on the overlay and the tray icon turns red. Sync then hears **both** the call or meeting audio and the user's voice through the laptop mic. It doesn't announce itself to other participants or ask them anything.
- **Skipped calls** leave only a minimal event (time and app), or nothing at all if the user turns that log off.

#### Overlay

*As built (v2.3):* a see-through, always-on-top strip across the top centre of the screen, drawn by the Desktop Agent with Tk (it ships with Python). The window itself is invisible and click-through, so whatever app the user is in stays fully usable. Only cards are drawn on it: the call countdown, *Sync is on/off* notices, up to two nudges (newest first, each vanishing after 20–30 s) and the *Before you hang up* card. The agent gets live events from the backend's app-wide `/ws/events` bus, so nudges from any live conversation appear, including sample calls. Interaction is by global shortcut only, so the strip never steals a click or focus:
- `Ctrl+Alt+N` hides or shows nudges. The countdown and on/off notices always show.
- `Ctrl+Alt+S` skips the countdown or stops listening.
- `Ctrl+Alt+L` accepts when Sync asks first.

No browser tab needs to be open; the web app (`/app`) is for projects, recaps, chat and settings.

- **Optional whisper nudges:** nudges are spoken softly by **local Kokoro TTS**, but **only when wired earphones are in use** (e.g. in-person meetings). They're turned off on laptop-speaker calls such as Phone Link, because the laptop mic would pick them up and the other side would hear them. While Sync's own voice plays, the agent mutes the "them" channel (±200 ms) so loopback doesn't transcribe it.

#### Fallbacks (same backend protocol)

- **Browser capture:**
  - your channel: `getUserMedia`
  - their channel: `getDisplayMedia` (tab audio, or system audio when sharing the entire screen; Chrome requires a video track, which we request at minimal frame rate and ignore)
  - an AudioWorklet produces the same stereo frames
  - the overlay uses Document Picture-in-Picture (Chromium 116+)
  This covers machines without the agent, Linux/ChromeOS and quick tests.
- **In-person mode:** mic only, mono, with speaker separation (§3.3).
- **Demo player:** streams a prepared stereo WAV (generated once with ElevenLabs or Kokoro, §5.6) at real-time speed.
- **Text-script player:** sends `{channel, speaker, text}` utterances straight to the engine, for development, tests and evals.

### 3.2 Realtime gateway

`WS /ws/session/{id}` (FastAPI). Its jobs:

- session lifecycle:
  - auto-started: `detected → countdown → (skipped | live) → wrapping → ended`
  - manual: starts at `live`
- **auto-start:** the agent sends `call.detected{app, title, caller?}`. The gateway applies the user's auto-listen rules (§3.1), opens the countdown on the overlay, and holds audio **in memory** until the countdown passes. If the call is skipped, the buffer is discarded.
- **notify the user**: send `listening{on}` to the overlay and tray the moment a session goes live. Both channels are processed from then on.
- send audio to STT and fan events back to the client
- **off the record**: drop frames and log `session.paused` / `session.resumed`
- **auto-stop**: end the session when the track ends, after 3 minutes of silence, or when the user ends it

### 3.3 Local speech-to-text

**Interface:** `SttEngine.start(session) · feed(channel, pcm) · on_utterance(Utterance)`, where `Utterance = {id, channel, speaker, text, t_start, t_end}`.

```
per channel: PCM ─▶ silero-vad (speech start/end, max segment 12 s) ─▶ ASR ─▶ final utterance
```

Measured on the dev laptop (126 s two-channel call, 3 threads):

| Model (sherpa-onnx, CPU) | WER you / them | Speed | Worst phrase | Verdict |
| --- | --- | --- | --- | --- |
| **Parakeet TDT 0.6B v2 int8** (CC-BY-4.0) | **1.8% / 7.9%** | 11× real time | 0.73 s | **Default.** Hears "25 lakh" correctly |
| Moonshine base 2026 (MIT) | 3.5% / 6.4% | 26× real time | 0.34 s | Light option; heard "lakh" as "lock" |
| Parakeet 110M / Moonshine tiny | 8–11% | 25–45× real time | ~0.2 s | Not accurate enough |

- **Multilingual / Hinglish (Next):** a Whisper-family model through sherpa-onnx (e.g. `whisper-small`).
- **ASR repair:** `textparse.fix_asr` corrects common slips ("25 lock" → "25 lakh", "Goliv" → "go-live").

- **Thread budget on the laptop:** STT 3 threads per active channel at most. LLM and embeddings get the rest (§5.4).
- **Echo dedupe:** a "you" utterance that fuzzy-matches (`rapidfuzz` ≥ 0.8) a "them" utterance within ±1.5 s is dropped, which handles users on speakers.
- **Speaker separation:**
  - *Online:* the channel tells *you* from *them*.
  - *Remote multi-speaker and in-person:* **sherpa-onnx speaker embeddings** per VAD segment, with online cosine clustering, give *Speaker A/B/C*. Names come from introductions (signal or LLM) or one tap from the user.
  - Labels last for the session only; **no voiceprints are stored**.
- **Redaction:** `privacy.redact` runs on every final utterance before storage or any model call.

### 3.4 Live Engine: three tiers

```
final utterance
   │
   ├─▶ T0 SIGNALS (≤ 100 ms, every utterance): rules + embeddings + slot tracker
   │        └─▶ candidate gaps with template text ─────────────┐
   │                                                           ▼
   ├─▶ T1 SMALL LLM (2–6 s, priority queue, 1 in flight):    GATE ─▶ one nudge
   │        verify Promise Guard claims · sharpen wording ·      ▲
   │        "most important missing question?" every ~45 s ──────┘
   │        ledger sweep every ~60–90 s (items, owners, criteria)
   │
   └─▶ T2 DEEP LLM (background, after the session): consolidation, recap, brief
```

**Session state**

```
SessionState
  lens, objective, exit_criteria[{id, text, question, examples[], status, evidence[]}]
  utterances            all stored in the DB; the live prompt keeps them append-only
  live_items[]          {id, type, text, status, speaker, owner, due, condition, evidence[]}
  slots{}               lens slots seen this session, e.g. budget, go_live, volume, region
  open_questions[]      questions awaiting an answer (asker, turn index, embedding)
  parked_threads[]      "let's come back to that" topics
  topic_vec, topic_changed_at
  nudges                active, recent (shown | used | useful | dismissed | expired), budget
  project_snapshot      items, facts, known Q&A, people, slot values, with embeddings
```

#### T0: Signals (instant, deterministic, explainable)

| Signal | How it works | Gap | Nudge (template, no LLM) |
| --- | --- | --- | --- |
| **Question tracker** | A question is detected by `?` (ASR punctuation) or a wh-word / auxiliary-verb opener. It's *answered* when the other side replies with cosine ≥ τ_ans, or fills the slot it asked about. After 3 turns, or a topic shift, with no answer, it's *unanswered* | G1 | *Confirm: "{short question}" — still unanswered* |
| **Vague-term detector** | Lens list plus a general one (*soon, ASAP, a lot, a few, some, cheap, expensive, scalable, secure, big*), classified as time, quantity or quality | G2 | time: *Ask: what date, and what's driving it?* · quantity: *Ask: roughly how many — or how much?* · quality: *Ask: what does "{term}" mean for you, concretely?* |
| **Concern detector** | Lexicon (*worried, concern, risk, issue, problem, can't afford, difficult, afraid*) on "them". It counts as *addressed* if your next turns are semantically close; otherwise it's *dropped* after N turns | G3 | *Ask: what worries you most about {topic}?* |
| **Exit-criteria coverage** | Cosine between utterances and each criterion's description and examples ≥ τ → *touched*. Urgency rises with elapsed time and peaks at wrap-up | G4 | The criterion's own lens question, e.g. *Ask: who else signs off on this?* |
| **Commitment detector** | Patterns (*I'll, we'll, will send, let me, I can get you*) plus `dateparser` for due dates. Owner comes from the speaker; if the owner or date is missing, it's a gap | G5 | *Confirm: who will {action}, and by when?* |
| **Condition detector** | *if, provided, as long as, only if, assuming, subject to* near price, discount or term words | G6 | *Confirm: is that conditional on {condition}?* |
| **Slot tracker / conflict radar** | The lens defines slots (budget, price, discount, go-live, deadline, volume, headcount, region…) with trigger words. A number, money amount, date or entity near a trigger is a slot value. A new value that differs from the project's recorded one is a conflict | G7 | *Confirm: {person} said {old} on {date} — which is current?* |
| **Promise Guard (numbers)** | Your claims with numbers and units (*weeks, days, hours, %, ₹, lakh*) are compared with the **facts table** extracted from your documents at upload time | G8 | *Caution: {doc fact} [{doc §}]* |
| **Already-asked guard** | Your question goes to a nearest-neighbour search over the project's answered questions. Cosine ≥ 0.82 means it's already known | G9 | *Fact: {answer} — {who}, {when}* |
| **Parked threads** | *come back to, park that, take it offline, later* | G10 | At wrap-up: *Ask: revisit {topic} before we close?* |
| **Plan without a time** | A meeting or call is fixed for a day ("training review next Thursday" → "Yes, Thursday works") and nobody says a clock time within two more turns, or by the close | G11 | *Confirm: what time next Thursday?* |
| **Role-focus gate** | Checklist goals (G4 and the model's next-question) are offered only after the call has used the role's own topic words; "next step" goals are always allowed | — | Keeps a technical-sales role quiet on an unrelated customer call |
| **Topic shift** | Cosine between the rolling 30-second window embedding and the previous one drops below τ | — | Boosts urgency for open gaps |
| **Closing language** | *wrap up, anything else, before we go, thanks everyone* | — | Triggers wrap-up |
| **Speaker names** | *I'm {Name} from {Org}*, *this is {Name}* | — | Proposes a speaker-label → name mapping |

`{topic}` comes from noun phrases near the trigger (a regex chunker; spaCy is optional).

#### T1: Small LLM (editor and verifier, one in flight, priority queue)

| Priority | Job | Output | Typical time on the laptop |
| --- | --- | --- | --- |
| 0 | **Promise Guard check** for non-numeric claims (*"we support SAP"*): the top-3 doc chunks plus the claim → `{contradicts: yes/no, source_id}` | ≤ 10 tokens | 2–4 s |
| 1 | **Missing-question pass** every ~45 s, or on a topic shift: given the lens, criteria and recent turns, return *the single most important question not yet asked*, or `NONE` | ≤ 25 tokens | 3–6 s |
| 2 | **Sharpen** a T0 template using context, e.g. *"what worries you about {topic}"* → *"Ask: what breaks if SAP sync is down for a day?"* (optional; only when the queue is free) | ≤ 20 tokens | 2–4 s |
| — | *(Ledger sweep: removed after testing. Small models extracted poorly live; the post-call pass does it well.)* | | |

Every T1 call uses the **append-only live prompt** (§5.2), so only new transcript lines and a short task line are prefilled. Outputs are constrained with Ollama's JSON-schema `format`.

#### Gate (deterministic Python)

```
for c in candidates:                      # from T0 and T1
    if c.kind in {Caution, Fact} and not c.source_ref: drop            # grounded-or-silent
    if similar(c, recent_nudges) or already_known(c, project): drop     # no repeats, no re-asks
    urgency = 1.0
    if topic_changed_since(c.evidence):    urgency *= 1.5               # window is closing
    if wrapping_up:                        urgency *= 1.5
    if c.kind == Caution and age(c) < 20s: urgency *= 2.0
    c.score = (c.importance / 5) * c.confidence * urgency * weight[user][c.gap_type]

best = max(candidates, key=score)
fire if best.score >= THRESHOLD[mode]            # Quiet 0.75 · Normal 0.60 · Active 0.45
     and budget_ok(mode)                         # Quiet ≤1/5min · Normal ≤3/10min · Active ≤1/min
     and not user_speaking(last=1.2s)
     and (them_mid_turn or pause >= 0.8s)
ttl = 20s → "missed" tray
```

- **Importance and confidence for T0 candidates** come from the lens (each criterion and slot has an importance) and from signal strength (keyword versus embedding margin).
- **Whisper mode** (optional): the fired nudge is spoken by local Kokoro TTS at low volume.

#### On-demand live features

- **Ask Sync privately:** embedding search over project items and the session's utterances returns the **top-3 matching items with quotes immediately**. A short T1 synthesis follows if the queue is free.
- **Catch me up:** a T1 job (priority 1) over the last N utterances, returning two lines.
- **Mark moment:** a bookmark event.
- **Wrap-up card:** assembled **without an LLM** from state: untouched criteria, open questions, commitments missing an owner or date, unresolved conflicts, parked threads. A three-line recap is built from templates, and T1 polishes it if time allows.

### 3.5 Project Brain

- **Matcher (no LLM needed):**
  - **Inputs:** session opening (~150 words) plus detected names and organizations, compared with project cards.
  - **Score:** `0.5·cos(opening, project_summary) + 0.3·name_overlap(rapidfuzz) + 0.2·org/keyword_overlap`. A T2 tiebreak is used only when two projects are within 0.05.
  - **Policy:**
    - ≥ 0.85: link automatically (with undo)
    - 0.5–0.85: ask the user once
    - < 0.5: propose a new project
  - It checks again at 3 minutes if still unconfirmed.
- **Progressive recap.** At session end the user gets an **instant draft recap** built from the live ledger. **T2 consolidation** refines it in the background and the UI says *"Recap refined"* when done. On the laptop, expect about 1–5 minutes for a 10–30-minute conversation.
- **Consolidator (T2):**
  - **Input:** session items, transcript chunks and the project snapshot. Map-reduce runs over transcript chunks of about 1.5k tokens.
  - **Output:** a `ProjectDelta` containing new or updated items, question → answer links, supersessions, conflicts, people updates, the recap and the project brief.
  - It's applied in one transaction as events. Items stay `confirmed=false` until the user reviews them.
- **Conflict verification (T2):** slot conflicts and embedding-near contradictory pairs go to a yes/no check before they're recorded.
- **Document facts (T2, at upload):** each uploaded document is chunked, embedded, and run through a **facts table** extraction (`{slot, value, unit, text, section}`). Promise Guard and Fact nudges use this table.
- **Brief / follow-up (T2):** built from the project snapshot, the recap and the playbook's style notes. Sync never sends anything itself.

### 3.6 Agent (chat any time)

- **Ollama tool calling** with the deep model (e.g. `qwen3:4b`), in a small hand-written tool loop. Responses stream over SSE, and at about 6–7 tokens/s on the laptop that's roughly reading speed.
- **Retrieval first:** every question first gets an embedding search over items and utterances, so answers quote sources and the model reads less.
- **Read tools:** `list_projects`, `get_project`, `search(query, project?)`, `get_session`, `list_commitments(scope)`.
- **Write tools:** `create_project`, `add_note`, `create_item`, `update_item`, `add_person`, `link_session_to_project`.
- **Helper tools:** `prepare_brief`, `draft_followup`.
- **Every write is an event** (`actor="agent"`, with the user's message as `reason`). Status-finalizing writes (*Agreed*, *Done*) and deletes come back as **confirmation chips**.
- **During a live session,** the agent is replaced by the retrieval-first *Ask Sync privately* (§3.4) so the CPU stays free for the live tiers.

### 3.7 Learning loop

| Signal | Captured by | Effect |
| --- | --- | --- |
| Nudge **used** | T0 checks whether your next utterances (within 90 s) are semantically close to the nudge (cosine ≥ 0.7) | Strong positive weight; the question goes to the question bank |
| Useful / dismissed | Nudge buttons or a shortcut | ± weight |
| Expired unseen | TTL ran out | Slight negative (possibly bad timing) |
| Recap or item edits | Diff between AI and user versions | Style notes; extraction corrections |
| Confirmed / rejected | Recap review | Calibrates confidence per signal type |
| Outcome | An open item resolved in a later session | Reinforces the gap types that raised it |

- **Weights:** `w[user][gap_type] = clamp(0.5, 1.5, 1 + 0.1·(used + useful − dismissed) / max(n, 5))`, plus per-signal thresholds that tighten when a signal type keeps getting dismissed.
- **Reflection (T2, background):** reads the session's outcomes and the current playbook, and returns playbook diffs. Each entry carries its evidence. The *What Sync learned* page lets you view, edit or delete every entry.
- **Question bank:** questions that drew useful answers are embedded and suggested again in similar contexts.

### 3.8 Role Lens system

A lens is YAML: built-in, or generated for a custom role by T2.

```yaml
id: technical_sales
name: Technical Sales / Solutions Engineer
cares_about: [current systems, integrations, security & compliance, data, scale,
              pilot success criteria, timeline feasibility, technical decision-maker]
leave_to_others: [pricing negotiation, contract terms]        # domain focus
slots:                                                         # used by the slot tracker
  go_live:   {triggers: [go live, launch, live by, rollout], type: duration|date, importance: 5}
  region:    {triggers: [region, data residency, hosted in], type: entity, importance: 4}
  systems:   {triggers: [integrate, sap, crm, erp, legacy], type: entity_list, importance: 5}
conversation_types:
  discovery:
    exit_criteria:
      - id: systems
        text: Current systems and integration points identified
        question: "Ask: which systems must this integrate with?"
        examples: ["we use SAP", "our CRM is", "legacy system"]
        importance: 5
      - id: security
        text: Security / compliance requirements known
        question: "Ask: what security reviews must this pass?"
        examples: ["ISO 27001", "SOC 2", "security team", "compliance"]
        importance: 4
      # … decision-maker, success criteria, next step
vague_terms: [soon, scalable, secure, a few systems, high volume, real-time]
promise_guard_topics: [implementation timelines, feature availability, SLAs, roadmap]
nudge_style: short, technical, no sales language
```

**MVP packs:** `sales`, `technical_sales`, `finance_manager`, `client_buyer`, `support_agent`, `team_lead`, plus a `custom` generator. Lenses feed both T0 (slots, criteria, vague terms) and the stable prompt prefix for T1 and T2.

### 3.9 Storage, embeddings and event log

- **SQLite** (WAL mode) through **SQLAlchemy 2.0**. Postgres works unchanged for team servers.
- **Embeddings:** **fastembed** (Apache 2.0, ONNX, CPU) with `BAAI/bge-small-en-v1.5` (MIT, 384-dim) for English, or `intfloat/multilingual-e5-small` (MIT) in multilingual mode. Vectors are stored as BLOBs. Search is brute-force cosine in **NumPy**, which is fast enough for per-project sets of thousands of items. Add `sqlite-vec` later if needed.
- **Event log:** every change writes an `events` row in the same transaction as the change to the current-state table. That keeps the full history and makes **time travel** (replaying events up to a date) possible later.

---

## 4. Data model

| Table | Key columns |
| --- | --- |
| `users` | id, name, region, languages, nudge_mode, inference_profile, created_at |
| `lenses` | id, user_id (null = built-in), name, yaml, is_default |
| `projects` | id, user_id, name, objective, context, lens_id, status, brief, summary_vec, created_at, updated_at |
| `persons` | id, user_id, name, org, role, notes |
| `project_persons` | project_id, person_id, concerns (json) |
| `sessions` | id, project_id?, mode (online / in_person / import / text), title, objective, exit_criteria (json), started_at, ended_at, notified_at, recap (json), recap_status (draft / refined) |
| `utterances` | id, session_id, channel, speaker_label, person_id?, text (redacted), t_start, t_end, vec |
| `items` | id, project_id, session_id, type, text, status, owner_person_id?, owner_is_me, due, condition, answer, answered_by, slot?, slot_value?, evidence (json), confidence, confirmed, superseded_by, created_by (t0 / t1 / t2 / agent / user), vec |
| `doc_facts` | id, document_id, slot?, value, unit, text, section, vec |
| `doc_chunks` | id, document_id, text, section, vec |
| `conflicts` | id, project_id, item_a, item_b, slot?, explanation, status, resolved_by |
| `nudges` | id, session_id, tier (t0 / t1), kind, gap_type, text, source_ref, score, shown_at, outcome |
| `documents` | id, user_id, project_id?, name, mime, text, uploaded_at |
| `playbook_entries` | id, user_id, text, kind (habit / style / effective_question / avoid), evidence (json), active |
| `gap_weights` | user_id, gap_type, weight, n |
| `events` | id, ts, user_id, project_id?, session_id?, actor (user / agent / t0 / t1 / t2 / system), type, entity, entity_id, before (json), after (json), reason, source_refs (json) |

**Event types:**
- `project.created|updated`
- `session.detected|skipped|started|notified|paused|resumed|stopped|linked|ended`
- `item.created|updated|status_changed|confirmed|rejected|superseded`
- `conflict.detected|resolved`
- `note.added`, `document.added`, `person.added|updated`
- `nudge.shown|used|useful|dismissed|expired`
- `recap.drafted|refined|edited`, `followup.drafted`
- `playbook.updated`, `profile.updated`

---

## 5. AI design (open models, local)

### 5.1 Models and runtime

**Runtime:** **Ollama** (MIT), already installed on the dev laptop (v0.40.1), using its HTTP API. JSON-schema structured outputs go through the `format` parameter, and tool calling through `tools`. The adapter also speaks the **OpenAI-compatible API**, so `llama.cpp server` (MIT) or `vLLM` (Apache 2.0) can stand in on a GPU box.

| Role | Laptop profile (CPU, default) | GPU profile (any NVIDIA GPU, local network) | License |
| --- | --- | --- | --- |
| **T1 live** (claim check, sharpen, next question, catch-up) | **`granite4.1:3b`** | `granite4.1:8b` or `qwen3:8b` | Apache 2.0 |
| **T2 deep** (post-call record, custom lens) | **`qwen3:4b`** (structured JSON only) | `qwen3:14b` or `gpt-oss:20b` | Apache 2.0 |
| **Chat** (agent with tools, follow-up drafts) | **`granite4.1:3b`** | `granite4.1:8b` | Apache 2.0 |
| **Embeddings** | fastembed `bge-small-en-v1.5` | same | MIT |

**Bake-off on the dev laptop** (same Sync prompts, prompt prefix cached):

| Model | Claim checks correct | Median latency | Notes |
| --- | --- | --- | --- |
| **granite4.1:3b** | **5/5** | 7.0 s | Faithful catch-up, good questions |
| qwen3:1.7b | 4/5 | 4.4 s | Copied a prompt example verbatim (hallucination risk) |
| qwen3:4b | 4/5 | 8.2 s | Verbose output |
| qwen3.5:2b / 4b | — | 40–60 s | **No prompt-cache reuse** (hybrid layers): ruled out on CPU |

**Inference profiles** are one setting in `config.py`:
- **`laptop`** (default): the tiers as described, deep model loaded only when no live session is running.
- **`gpu`:** the same code pointed at `OLLAMA_HOST` on a machine with an NVIDIA GPU (e.g. a teammate's gaming laptop). T1 can then run a full per-turn ledger extraction.
- **`free-cloud`** (*optional, off by default, your decision*): an OpenAI-compatible endpoint serving open-weight models on a free tier. It's fast, but **conversation text leaves the device** and the service isn't open source, so it's opt-in only.

### 5.2 Prompt layout for KV-cache reuse

```
[1] system: Sync instructions + output rules             ← stable for all sessions
[2] system: Role Lens + playbook + nudge preferences     ← stable per user
[3] user:   project snapshot (top-k items, facts, people,
            known Q&A — retrieved once at session start)  ← stable for the session
[4] user:   transcript so far, APPEND-ONLY                ← grows; only new lines are prefilled
[5] user:   task line for this call (≤ 60 tokens)         ← the only part that changes
```

- Ollama/llama.cpp reuse the KV cache for the longest common prefix. Because layers [1]–[4] only ever grow at the end, each T1 call prefills only the new utterances plus its task line.
- **Compaction:** for meetings longer than about 45 minutes, layer [4] is rebuilt once in a quiet moment (a summary of older turns plus recent verbatim turns). That costs one cold prefill.
- **One live model, one queue:** all T1 jobs share the same model and prompt prefix through a single priority queue, which avoids cache eviction and CPU contention.

### 5.3 Measured on the dev laptop

The dev laptop has an i7-1355U (10 cores, 12 threads), 16 GB RAM and Intel Iris Xe graphics (no NVIDIA). Measurements were taken with Ollama 0.40.1 on a ~600-token prompt.

| Model | Cold prefill | Prefill with cached prefix | Generation |
| --- | --- | --- | --- |
| phi3 (3.8B) | 27 tok/s (25 s for 681 tok) | **1.5 s** for the same 682-tok prompt | 7.5 tok/s |
| qwen2.5 7B | 19 tok/s (30 s for 591 tok) | **2.1 s** | 4.7 tok/s |

**What this means for the design:**
- **Prefix caching is essential.** It made the same prompt 15× faster, so the append-only layout (§5.2) is required.
- **Output length dominates.** At about 7 tok/s for 4B and an expected ~15 tok/s for 1.7B (*measure*), T1 outputs must stay tiny (≤ 25 tokens). Structured extraction runs as a background sweep, never in the nudge path.
- **Most nudges therefore come from T0**, which is instant and free, while the LLM verifies and sharpens. With a GPU profile, the same code simply runs T1 more often.
- **Hour-1 experiments:** compare `OLLAMA_NUM_GPU=0` (pure CPU) with Ollama's Vulkan path on the Iris Xe. Intel iGPU reports are mixed, so check the output is correct before trusting any speedup.

### 5.4 Latency and resource budget (laptop profile)

| Path | Steps | Expected |
| --- | --- | --- |
| **T0 nudge** | VAD end (~0.5 s silence) → ASR final (~0.1–0.5 s) → signals + embeddings (< 0.1 s) → gate → push | **≈ 0.7–1.2 s** |
| **T1 verified nudge** (Caution, missing question) | T0 path + small-LLM call | **≈ 3–7 s** |
| **Ledger sweep** | Background, every 60–90 s | Ledger lags about 1 minute |
| **Draft recap** | From live state at session end | **instant** |
| **Refined recap** | T2 map-reduce | ≈ 1–5 min (background) |

**RAM (16 GB):**

| Component | RAM |
| --- | --- |
| OS + browser + meeting app | ~6–7 GB |
| `qwen3:1.7b` + KV cache | ~2 GB |
| STT models | ~0.5–1 GB |
| Embeddings | ~0.2 GB |
| Backend | ~0.5 GB |

`qwen3:4b` (~3 GB) is loaded **only when no live session is running** (short `keep_alive`).

**CPU threads (12):**

| Component | Threads |
| --- | --- |
| STT | 3 |
| T1 LLM | 6–7 (`num_thread`) |
| Embeddings and backend | 2 |

### 5.5 Core schemas (Pydantic, used as Ollama `format`)

```python
class GapCandidate(BaseModel):          # produced by T0 signals and T1
    gap_type: Literal["G1","G2","G3","G4","G5","G6","G7","G8","G9","G10"]
    kind: Literal["ask","confirm","caution","fact"]
    text: str                           # ≤ 12 words, shown to the user
    why: str                            # shown on expand
    importance: int                     # 1–5
    confidence: float
    evidence: list[str]                 # utterance ids
    source_ref: str | None              # doc_fact:<id> | item:<id> | session:<id>
    tier: Literal["t0","t1"]

class ClaimCheck(BaseModel):            # T1 priority 0
    contradicts: Literal["yes","no","unsure"]
    source_id: str | None

class MissingQuestion(BaseModel):       # T1 priority 1
    question: str | None                # ≤ 12 words, or null for NONE
    criterion_id: str | None

class SweepItem(BaseModel):             # T1 priority 3
    type: Literal["fact","requirement","question","concern","proposal",
                  "decision","commitment","risk"]
    text: str
    status: Literal["open","proposed","conditional","agreed","done"]
    owner: str | None                   # "me" | name | None
    due: str | None
    condition: str | None
    evidence: list[str]
```

`ProjectDelta`, `Brief`, `PlaybookDiff`, `RoleLens` and `DocFact` live in `backend/app/llm/schemas.py`. Every schema is validated with Pydantic after generation; on failure there's one retry, and then the job is skipped (signals still work).

### 5.6 ElevenLabs: where the 10k free credits help

**The rule:** ElevenLabs only ever sees **fictional scripts we wrote**. It never sees real conversations, nudges derived from real conversations, or user data. Everything in the product runs without it.

| Use | Why ElevenLabs | Approx. credits | Fallback |
| --- | --- | --- | --- |
| **Hero demo conversation audio:** the ~5-minute "Acme Thursday" call with 3–4 distinct, natural voices (Indian English accents from the voice library), rendered once and saved as a stereo WAV for the demo player | Realistic voices make the demo and STT tests convincing | ~3–5k (≈ 4–5k characters of script; the Flash/Turbo models use fewer credits per character, so check current rates) | Kokoro TTS |
| **One or two golden-eval conversations** in audio, to measure real STT + engine latency and accuracy | Natural pacing and disfluencies stress-test the pipeline | ~2–3k | Kokoro TTS, or text mode |
| **Reserve** | Re-renders after script edits | ~2k | — |

- **Not used for:** live nudges, whisper mode or rehearsal voices (all local **Kokoro**, Apache 2.0), or transcription. Scribe costs about 330 credits per minute and would send real audio off the device.
- **Budget guard:** `demo/tts_render.py` counts characters before every request, keeps a running total in `demo/.elevenlabs_usage.json`, and refuses to exceed `ELEVENLABS_MAX_CREDITS` (default 9,000). Rendered files are cached by script hash so they're never generated twice.
- **License note:** the free plan requires **attribution** and gives **no commercial license**. That's fine for a hackathon demo; add *"Demo voices generated with ElevenLabs"* to the README and the demo's credits slide.

---

## 6. Workflows

### W1. Onboarding (about 2 minutes)

1. Name and languages.
2. **Role:** pick packs, or type *"I'm a…"*. T2 drafts a custom lens (about 20–60 s on the laptop, with a progress bar), which the user can edit.
3. Choose a nudge mode, an overlay or panel, and whisper mode on or off.
4. Optionally upload documents. Each is chunked, embedded and run through facts-table extraction in the background.
5. A short privacy summary appears: what Sync hears when it's on, what it stores, and how to switch it off. The user sets the auto-listen defaults (master switch, countdown, rules).
6. **Events:** `profile.updated`, `document.added`.

### W2. Create a project and add context (any time)

1. *New project* → name, objective, lens, people, notes, documents. Or tell the agent: *"Create a project for the Acme CRM deal; first call Monday with Priya (IT)."*
2. Pre-conversation notes become `note` items and **extra exit criteria** for the next session.
3. **Events:** `project.created`, `person.added`, `note.added`, `document.added`.

### W3. A session starts (automatically, or manually)

**Automatic (default):**
1. The Desktop Agent detects a call or meeting. For example, Phone Link takes a call answered on the laptop, Zoom opens the mic, or a `Meet –` tab starts using the mic.
2. **Rules check** (app, contact or number, title keywords, working hours, master switch):
   - *Never* → nothing happens (an optional minimal "skipped" event)
   - *Ask* → the overlay asks *Listen to this call?*
   - *Auto* → continue
3. The **overlay pops up**: *"Sync is joining · Phone Link · Priya (Acme) · 5…4…3"*, with **[Skip this call]**. Audio is buffered in memory only. *Skip* discards it.
4. **Notify the user:** when the countdown ends, the overlay shows *"Sync is on"* and the tray icon turns red. Sync hears **both** the call or meeting audio and the user's voice from the laptop mic. Nobody else on the call is notified.
5. **Live:**
   - the lens default objective and exit criteria load, plus open items if a project is already known
   - the **live prompt prefix is pre-warmed** in T1
   - the **matcher** uses the caller name or meeting title right away, plus the first ~45 s of speech, and asks *"Continue '…'? / New / Choose"*
6. **Controls** for the whole call: pause, off the record, stop, stop and delete, and the hotkey.
7. **Events:** `session.detected`, `session.skipped` | `session.started`, `session.notified`, `session.linked`.

**Manual:** start from a project page or with *Quick start*. Modes: *In person* (mic), *Browser capture*, *Demo* (player) or *Script* (text). The same steps 4–7 then apply.

### W4. The live loop

```
audio ─▶ VAD ─▶ local ASR ─▶ final utterance (redacted, embedded)
                                   │
                ┌──────────────────┼──────────────────────────┐
                ▼                  ▼                          ▼
       T0 signals (instant)   T1 queue (small LLM)      ledger panel
       candidates + templates  verify · sharpen ·        (T0 drafts, T1 sweep)
                │              missing question
                └────────┬─────────┘
                         ▼
                 GATE: grounded? duplicate? already known? score?
                       budget? user speaking? good moment?
                         ▼
               overlay (+ optional whisper) → one nudge
               (used / useful / dismissed / expired → learning)
```

### W5. Wrap-up and end

1. **Trigger:** closing language, the scheduled end minus 5 minutes, or a tap.
2. **The wrap-up card** is assembled instantly from state:
   - untouched criteria
   - open questions
   - commitments missing an owner or date
   - conflicts
   - parked threads
   - a three-line recap to say out loud
3. **End:** a tap or auto-stop. Audio stops, and STT is released.
4. **Events:** `session.ended`.

### W6. After the session

1. The **instant draft recap** appears from live state.
2. **T2 consolidation** runs in the background, showing *"Refining recap · 6 items · 1 conflict"*. The deep model is loaded now, after the live session has ended.
3. **Review screen:** decisions, my and their commitments, open questions, conflicts and new facts. *Confirm all*, or edit and reject individual items.
4. The project updates (merges, links, supersessions, brief).
5. An optional **follow-up draft** is copied by the user.
6. **Reflection** updates the playbook and weights.
7. **Events:** `recap.drafted`, `item.*`, `conflict.detected`, `recap.refined`, `recap.edited`, `followup.drafted`, `playbook.updated`.

### W7. Talk to Sync any time

1. Chat (page or drawer) → retrieval first → deep model with tools → an answer **with citations** (session, date, quote).
2. Writes go into the event log as `actor=agent`. Finalizing changes need a confirmation tap.
3. Pasted emails or chats become `text` sessions, then go through consolidation and review.

### W8. Learning

- **During:** nudge outcomes are recorded.
- **After:** reflection updates the playbook and `gap_weights`.
- **Next session:** the playbook sits in the prompt prefix and the weights sit in the gate.
- **Control:** *What Sync learned* lets the user edit, delete or reset everything.

### W9. Other inputs

- **In person:** mic only. Speaker clustering separates voices, and the user taps *"That's me"* once per session.
- **Phone:** paths A–D (§2).
- **Import (Next):** an audio file goes through batch STT (faster-whisper / Parakeet), then T2, then review. A transcript file goes straight to T2 and review.

### W10. Share and export (Next)

- The user picks recap items and exports Markdown or PDF. Private content (nudges, playbook, private notes) is **never** included.

---

## 7. Privacy and security

| Concern | Mechanism |
| --- | --- |
| Where data goes | **Nowhere.** STT, the models, embeddings and the database all run on the user's machine. No third party processes real conversations (ElevenLabs only touches fictional demo scripts) |
| Who is notified | **Only the user.** A notification on the overlay and a red tray icon whenever Sync turns on. While it's on, Sync hears both the call or meeting audio and the user's mic. It doesn't announce itself to other participants. Following local call-recording laws (some places require everyone's consent) is the user's responsibility |
| Automatic listening | **Master switch** (Auto / Ask / Off) · **skip countdown** with audio held in memory only and discarded on skip · **stop** or **stop and delete** for any call · **rules** by app, contact or number, project, title keyword and working hours · **hotkey** · tray icon and overlay **always show** when Sync is listening |
| Listening after the meeting | The session ends as soon as the call app releases the mic (≥ 5 s), the window closes, after 3 minutes of silence, or on a hard stop event |
| Sensitive moments | **Off the record** drops frames and logs a gap |
| Phone OS protections | No accessibility hacks, root or hidden recording. Only paths A–D |
| Sensitive numbers | `redact.py` masks card numbers (Luhn check), OTP patterns, Aadhaar (12 digits), PAN (`[A-Z]{5}[0-9]{4}[A-Z]`) and "password is …" phrases before storage and before any model |
| Audio | Never written to disk |
| Model training | None. Learning is per-user tables the user can see and delete |
| Voice biometrics | No voiceprints of others. Speaker embeddings live in memory for the session only |
| Emotion inference | Not built. Only what people say is recorded (EU AI Act Art. 5(1)(f) bans workplace emotion recognition) |
| Retention and deletion | Per-user settings: transcripts kept forever, kept for N days, or deleted after recap. Deleting a session or project purges its content and logs the deletion |
| Security basics | Backend bound to `127.0.0.1` by default, a token-checked WebSocket, CORS locked to the app origin, `.env` for the ElevenLabs key (used only by the demo render script). Next: SQLite encryption at rest with SQLCipher, or field-level encryption with `cryptography` (Fernet) |

---

## 8. Tech stack (all free and open source)

| Layer | Choice | License |
| --- | --- | --- |
| Backend | Python 3.12/3.13, **FastAPI**, uvicorn, **Pydantic v2**, **SQLAlchemy 2** (+ aiosqlite), APScheduler (reminders) | MIT / BSD |
| Database | **SQLite** (Postgres optional) | Public domain / PostgreSQL |
| LLM runtime | **Ollama** (llama.cpp / vLLM optional) | MIT / Apache 2.0 |
| LLMs | **Qwen3** 1.7B / 4B (+ Granite 4, SmolLM3 as alternatives) | Apache 2.0 |
| STT | **Moonshine** (English), **faster-whisper** (multilingual), **silero-vad**, **sherpa-onnx** (speaker embeddings, optional ASR runtime) | MIT / Apache 2.0 |
| TTS (whisper mode, rehearsal) | **Kokoro-82M** | Apache 2.0 |
| Embeddings | **fastembed** + bge-small / multilingual-e5-small | Apache 2.0 / MIT |
| Text utilities | **rapidfuzz**, **dateparser**, NumPy, (spaCy optional) | MIT / BSD |
| Desktop agent (Windows) | Python: **PyAudioWPatch** or **soundcard** (WASAPI loopback + mic), **pywin32** (window titles, hotkey), **psutil**, `winreg` (mic-in-use), **pywebview** (overlay on WebView2), **pystray** (tray) | MIT / BSD / PSF / LGPL-3.0 |
| Web | **Vite + React + TypeScript + Tailwind + shadcn/ui**, Zustand | MIT |
| Browser APIs | getUserMedia, getDisplayMedia, AudioWorklet, Document Picture-in-Picture, Notifications | Web standards |
| Later | Tauri (desktop), Expo/PWA (mobile), LiveKit or Jitsi (Sync Call Link), `.ics` calendar import | MIT / Apache 2.0 |
| Only non-OSS, optional | **ElevenLabs** (free credits) for demo audio only | Free tier, attribution |
| Tooling | `uv` or `pip` + venv, `npm`, Git, Docker (optional) | — |

**Install before the clock starts** (about 3–4 GB of downloads):

```bash
ollama pull qwen3:1.7b
```

```bash
ollama pull qwen3:4b
```

```bash
ollama pull granite4:3b
```

Then install the Python dependencies, and run one warm-up script that downloads the Moonshine, faster-whisper `small`, silero-vad, fastembed, Kokoro and sherpa-onnx speaker-embedding models.

**Repo layout**

As built (frontend comes later):

```
Sync/
├── motive.md · architecture.md
└── backend/
    ├── pyproject.toml · README.md
    ├── sync/
    │   ├── main.py · config.py · db.py · models.py · events.py   # app, settings, SQLite, tables, history
    │   ├── llm.py · embed.py · textparse.py · privacy.py · bus.py · autolisten.py · learning.py
    │   ├── live/      signals.py (T0, G1–G10) · gate.py · engine.py (session runtime, T1 queue) · prompts.py
    │   ├── brain/     memory.py · matcher.py · docs.py · consolidate.py (post-call) · brief.py
    │   ├── agent/     chat.py (tool loop) · search.py
    │   ├── lenses/    packs/*.yaml (7 roles + base) · generate.py (custom roles)
    │   ├── stt/       engine.py (VAD + Parakeet/Moonshine + speaker clustering)
    │   ├── desktop/   agent.py (tray, countdown, off switch) · detect.py · capture.py
    │   └── api/       me.py · projects.py · sessions.py · chat.py · ws.py
    ├── demo/          harness.py · eval_gaps.py · play.py · seed.py · render_audio.py · scripts/ · docs/
    └── tests/         textparse · rules (gate, redaction, auto-listen) · signals · integration
```

---

## 9. API surface

**REST**

| Method | Path | Purpose |
| --- | --- | --- |
| GET/PUT | `/me` · `/me/lens` · `/me/playbook` · `/me/privacy` | Profile, lens, playbook, retention |
| GET/PUT | `/me/autolisten` | Master switch, countdown length, rules (apps, contacts, titles, hours), skipped-call log on/off |
| POST | `/lenses/generate` | Custom role → draft lens (T2) |
| GET/POST/PATCH | `/projects` · `/projects/{id}` | Project CRUD |
| GET | `/projects/{id}/timeline` · `/items` · `/people` · `/conflicts` · `/events` | Views and history |
| PATCH | `/items/{id}` | Edit or confirm an item (event) |
| POST | `/projects/{id}/notes` · `/documents` | Add context (documents → facts extraction) |
| POST | `/sessions` · `/sessions/paste` · `/sessions/import` | Create a session; paste text; import a file |
| GET | `/sessions/{id}` · `/sessions/{id}/recap` | Detail; recap (draft or refined) |
| POST | `/sessions/{id}/recap/confirm` | Confirm or edit recap items |
| POST (SSE) | `/chat` | Agent stream |
| POST | `/projects/{id}/brief` · `/sessions/{id}/followup` | Brief; follow-up draft |
| GET | `/system/health` | Ollama, STT and model status; inference profile |

**WebSocket `/ws/session/{id}`**

| Direction | Message |
| --- | --- |
| agent → server | `call.detected{app, title, caller?}` · `call.ended` · *binary* stereo PCM16 frames · `tts.playing{start, end}` (mutes the "them" channel) |
| server → agent/overlay | `countdown{session_id, seconds, app, title, caller?}` · `session.skipped` · `listening{on}` (tray/indicator state) |
| client → server | `session.skip` · `session.stop{delete: bool}` · *binary* stereo PCM16 frames (browser fallback) · `session.start{mode, objective}` · `project.confirm{project_id \| "new", name?}` · `nudge.feedback{id, action}` · `pause` · `resume` · `bookmark` · `ask{text}` · `catchup` · `wrapup` · `session.end` · `script.utterance{channel, speaker, text}` (dev) |
| server → client | `transcript{id, channel, speaker, text}` · `item.upsert{item}` · `criteria.update` · `nudge{id, kind, tier, text, why, source_ref, ttl}` · `nudge.expire{id}` · `project.match{candidates}` · `wrapup{...}` · `answer{items, text?}` · `tts{nudge_id, audio}` (whisper mode) · `session.state` · `recap.progress` · `error` |

---

## 10. The 18-hour build plan

### 10.1 Scope

| Must | Should | Could | Won't (in 18h) |
| --- | --- | --- | --- |
| Text-script player → T0 signals → gate → nudges | Brief me, follow-up draft | Catch me up | Native desktop or mobile apps |
| Lens packs (6) incl. slots, criteria and templates | In-person mode + speaker clustering | Whisper mode (Kokoro) | Sync Call Link |
| **Desktop agent:** loopback + mic capture → VAD → Moonshine | T1 "sharpen" job | Bookmarks | Meeting-platform apps |
| **Auto-listen:** detect Phone Link, Zoom, Teams, Meet, WhatsApp calls → countdown, skip, stop and delete, master switch, per-app rules, tray indicator | Rules by contact, title and hours; hotkey | Browser-capture fallback | macOS agent |
| T1 queue: Promise Guard check + missing question + sweep | Custom lens generator | Time travel | Calendar, team projects |
| Already-asked guard + conflict radar (seeded project) | Paste-text sessions | Export recap | Encryption at rest |
| Projects CRUD, timeline, items board, **history log** | Multilingual (faster-whisper) mode | GPU profile demo | — |
| Matcher (embeddings) + confirm prompt | | | |
| Wrap-up card, end, auto-stop, off the record, "Sync is on" notification | | | |
| Draft recap + T2 refinement + review + event writes | | | |
| Agent chat with tools (retrieval first) | | | |
| Learning v0: feedback, used detection, *What Sync learned* | | | |
| Overlay window (pywebview) with countdown, nudges and controls | | | |
| ElevenLabs-rendered hero demo audio | | | |

### 10.2 Build order

The tracks can run in parallel with 3–4 people. Working solo, go top to bottom.

| Hours | Live engine (backend) | Brain / agent (backend) | Web + demo |
| --- | --- | --- | --- |
| **Before** | Pull models and warm STT/embedding downloads (§8). **Pair the demo phone with Phone Link and make one test call on the laptop** | ← | Write the demo scripts; render the hero audio with ElevenLabs (credit guard) |
| **0–1 (extra)** | **Phone Link check:** loopback hears the caller, the mic hears you, Phone Link appears in the mic registry while a call is live, and the window title shows the caller | ← | ← |
| **0–1** | Repo, FastAPI skeleton, config, **bake-off: qwen3:1.7b / granite4:1b / qwen3:4b on T1 and T2 schemas; Moonshine vs whisper-small on the hero WAV** | DB models + event writer, seed script | Vite + Tailwind + shadcn scaffold, routing, API client |
| **1–4** | Signals (questions, vague, concerns, commitments, conditions, criteria, parked, closing) + templates + gate + WebSocket + **script player** | Lens YAML packs + composer, embeddings store, project snapshot | Live page: NudgeCard, LedgerPanel, CriteriaList |
| **4–6** | **Agent capture:** WASAPI loopback + mic → stereo frames → WebSocket; VAD + Moonshine per channel, echo dedupe, redaction | Slots + facts table extraction from docs, Promise Guard (numbers), already-asked guard, conflict radar | Overlay page (`/overlay`) + pywebview window; demo player |
| **6–8** | T1 queue: append-only prompt, Promise Guard check, missing question, sweep | **Auto-listen:** detector (registry + psutil + titles), countdown, skip, stop, rules, tray; matcher + confirm; wrap-up assembler | Projects list/detail: timeline, items board, history log, match prompt; Auto-listen settings |
| **8–10** | Ask Sync privately (retrieval), nudge feedback + used detection | Draft recap + T2 consolidation (map-reduce) + review apply | Wrap-up card, recap review screen |
| **10–12** | In-person mode: speaker clustering | Agent chat (tools, SSE), brief, follow-up | Chat drawer, onboarding (lens pick + custom) |
| **12–14** | Golden-gap runner + metrics; rules by contact, title and hours, plus hotkey | Reflection + weights + playbook | *What Sync learned*, overlay polish, "Sync is on" notification |
| **14–16** | **Eval and tune:** thresholds, templates, T1 cadence, thread budget | Seed the demo world (Acme + Monday session + docs) | Polish, empty and loading states |
| **16–18** | Bug bash, **two demo rehearsals on the actual laptop**, record a backup video | ← | ← |

> **Rule:** at hour 4 the full loop must run end to end in **text mode**, with T0 only. Audio and T1 make it better, but the core loop must work without them.

### 10.3 Demo script (about 5 minutes)

1. **(0:00) The problem in one line.** Then: *"Everything you'll see runs on this laptop: free, open source, offline-capable."*
2. **(0:20) Onboarding.** Meera picks *Technical Sales*; show the lens (*leave to others: pricing*).
3. **(0:40) The project.** *Acme · CRM integration* already holds Monday's session: ₹40L budget from Priya, EU region.
4. **(1:00) Live.** A teammate **calls the demo phone**, and Meera answers on the laptop through Phone Link. The overlay pops up (*"Sync is joining · Phone Link · Priya (Acme) · 5…"*) and Sync joins on its own. (Backup: a Meet role-play, or the ElevenLabs audio through the demo player.) Sync:
   - auto-detects Acme
   - shows **Fact** (EU region: no re-ask)
   - shows **Confirm** (₹25L vs ₹40L)
   - shows **Caution** ("four weeks" vs the guide's 6–8)
   - shows **Ask** (*"what date, and what's driving it?"* on "soon")
5. **(2:45) Wrap-up card** → end → instant draft recap → *refined* recap arrives → review → the **history log** shows each change and its actor.
6. **(3:30) Chat.** *"What's still open with Acme?"*, then *"Add Neha, CFO, approves > ₹10L"*. The item appears in the history.
7. **(4:00) Same call, two lenses.** Switch to *Client/Buyer* and replay 30 s; different nudges appear.
8. **(4:15) The off switch.** A second call arrives from a contact with a *Never* rule ("Mom"): Sync stays off. A third call comes in and the user taps **Skip this call**: nothing is stored.
9. **(4:30) Learns you, and privacy.** *What Sync learned*. Turn Wi-Fi off and show that Sync still works.
9. **(5:00) Close** with the motive line.

### 10.4 Golden-gap eval

- **Scripts:** five text conversations (sales discovery, support, negotiation, team meeting, client-side vendor call), each with 4–6 planted gaps `{gap_type, window_start, window_end, acceptable_kinds}`. One or two are also rendered to audio.
- **Runner:** `python -m tests.golden_gaps.run --speed 4 [--audio]`.
- **Metrics:**
  - recall in window
  - precision
  - nudges per 10 minutes
  - p50 and p95 latency, split by tier
  - already-known violations (target 0)
- **Targets:** recall ≥ 70%, precision ≥ 80%, ≤ 3 nudges per 10 minutes (*Normal*), T0 p95 ≤ 1.5 s, T1 p95 ≤ 7 s.

### 10.5 Risks and fallbacks

| Risk | Fallback |
| --- | --- |
| The laptop CPU is overloaded during the demo (meeting app + STT + LLM) | Use the demo player instead of a live Meet call; close other apps; lower T1 cadence; run T2 only after the session |
| The small model's JSON is weak | Schema `format` + Pydantic validation + one retry; T0 still works on its own |
| T1 latency over budget | Shorter outputs; `granite4:1b`; skip "sharpen"; GPU profile on a teammate's machine |
| Moonshine accuracy on accents | Switch to faster-whisper `small` for the demo; the hero audio is pre-tested |
| macOS system-audio quirks | Meet in a Chrome tab (tab audio works on every OS) |
| Speaker echo (laptop speakers leaking into the laptop mic) | Echo dedupe; mic opened in communications mode for Windows' echo cancellation |
| Too many nudges | *Quiet* mode for the demo; raise thresholds |
| Wrong project match | Confirmation prompt; one-click relink |
| ElevenLabs credits run out | Credit guard + cache; Kokoro renders the rest |
| Venue Wi-Fi | Not needed. Everything is local after the downloads |
| Phone Link call audio stays on the phone, or the laptop's Bluetooth lacks HFP | Answer on the laptop; test before the clock starts. Fallbacks: speakerphone + in-person mode, or a Meet call |
| Phone Link doesn't support wireless headsets | By design, calls use the laptop's built-in mic and speakers; echo is handled as above |
| Stale "mic in use" registry entries | Process-alive check plus a mic-held-for-2-s rule |
| Loopback picks up notifications or music | Focus / Do Not Disturb; the ASR ignores non-speech; process loopback later |
| Sync's own whisper voice gets transcribed | The agent mutes the "them" channel while TTS plays |
| False auto-start (e.g. voice-memo apps) | Known-apps list plus *Ask* as the default for unknown apps |

---

## 11. After the hackathon

1. **macOS agent:** iPhone calls through Continuity, Core Audio process taps and mic-in-use detection. **Windows process loopback** to capture only the call app's audio. A packaged installer (PyInstaller, or Tauri with a Python sidecar) that runs at sign-in.
2. **Chrome extension:** one-click meeting-tab capture on machines without the agent.
3. **Mobile PWA/app:** in-person mode, chat, reminders, imports.
4. **Sync Call Link:** a self-hosted LiveKit/Jitsi room for live phone-style conversations with two clean tracks.
5. **Calendar via `.ics`:** auto-start, briefs, better matching.
6. **Team server:** the same stack on a shared machine with Postgres, team projects and handoff packets.
7. **Rehearsal mode** (local LLM + Kokoro), multilingual nudges.
8. **GPU profile** as standard for teams: a full per-turn ledger extraction and larger models.
9. **Integrations** (user-approved): email drafts, CRM, Notion, Jira through their open APIs.

---

### Sources

**Capture and platforms**
- [Microsoft: setting up calls in Phone Link](https://support.microsoft.com/help/4531605)
- [ElevenForum: making phone calls in Phone Link on Windows 11](https://www.elevenforum.com/t/make-phone-calls-in-phone-link-app-on-windows-11-pc.14049/)
- [ElevenForum: Phone Link call audio problems (headset / routing reports)](https://www.elevenforum.com/t/phone-link-call-audio-problems.21420)
- [Microsoft: WASAPI loopback recording](https://learn.microsoft.com/is-is/Windows/win32/coreaudio/loopback-recording)
- [Velociraptor: CapabilityAccessManager registry (mic/camera usage)](https://docs.velociraptor.app/exchange/artifacts/pages/windows.registry.capabilityaccessmanager)
- [How to find out what application uses the microphone in Windows](https://dev.to/hetmansoftware/how-to-find-out-what-application-uses-microphone-in-windows-10-2809)
- [Google Play policy bans third-party call recording via Accessibility API (The Register, 2022)](https://www.theregister.com/2022/04/22/google_banning_thirdparty_callrecording_apps/)
- [Digit: how call recording works on Android after the ban](https://www.digit.in/news/apps/how-to-record-phone-calls-after-google-ban-call-recording-apps-solutions-63697.html)
- [Android: sharing audio input](https://developer.android.com/media/platform/sharing-audio-input)
- [Android: AudioPlaybackCaptureConfiguration](https://developer.android.com/reference/kotlin/android/media/AudioPlaybackCaptureConfiguration)
- [Hedy: recording phone calls on iPhone](https://hedy.ai/help/recording-phone-calls-on-iphone/)
- [Apple Developer Forums: no API for call recordings](https://developer.apple.com/forums/thread/848299)
- [Tom's Guide: iOS 18 call recording](https://www.tomsguide.com/phones/iphones/ios-18-getting-the-ability-to-record-calls-and-create-transcripts-how-it-works)
- [AudioCap: Core Audio taps (macOS)](https://github.com/insidegui/AudioCap)
- [Apple Developer Forums: System Audio Recording permission](https://developer.apple.com/forums/thread/771864)
- [addpipe: getDisplayMedia system audio on Chrome for macOS](https://blog.addpipe.com/getdisplaymedia-allows-capturing-the-screen-with-system-sounds-on-chrome-on-macos/)
- [addpipe: getDisplayMedia demo](https://addpipe.com/getdisplaymedia-demo/)
- [Zoom RTMS GA changelog](https://developers.zoom.us/changelog/rtms/june-25-2025/)
- [Google Meet Media API overview](https://developers.google.com/meet/media-api/guides/overview)

**Open models and speech**
- [Ollama: structured outputs](https://registry.ollama.ai/blog/structured-outputs)
- [Ollama: granite4 models](https://registry.ollama.ai/library/granite4)
- [SmolLM3-3B model card (BFCL / IFEval comparison incl. Qwen3)](https://huggingface.co/HuggingFaceTB/SmolLM3-3B)
- [Ollama models with tool calling (2026)](https://www.betterclaw.io/blog/ollama-models-tool-calling-support)
- [Moonshine vs Whisper (benchmarks)](https://moonshine-voice.readthedocs.io/en/latest/moonshine-vs-whisper/)
- [sherpa-onnx README](https://cdn.jsdelivr.net/gh/k2-fsa/sherpa-onnx@master/README.md)
- [Hosting Nemotron ASR streaming on a CPU (sherpa-onnx)](https://softcery.com/lab/hosting-nemotron-asr-streaming-on-a-cpu.md)
- [Parakeet vs Whisper vs Nemotron (2026)](https://openwhispr.com/blog/parakeet-vs-whisper-vs-nemotron)
- [llama.cpp issue: Intel iGPU under Vulkan](https://github.com/ggml-org/llama.cpp/issues/19327)

**ElevenLabs free tier**
- [ElevenLabs pricing breakdown (2026)](https://techjacksolutions.com/ai-tools/elevenlabs/elevenlabs-pricing/)
- [ElevenLabs API pricing (Jun 2026)](https://developer.puter.com/tutorials/elevenlabs-api-pricing/)
- [ElevenLabs Scribe free plan (2026)](https://costbench.com/software/ai-transcription-apis/elevenlabs-scribe/free-plan/)

**Legal and privacy**
- [AI notetaking: wiretapping, discovery and ethical pitfalls (Bloomberg Law)](https://news.bloomberglaw.com/safety/ai-notetaking-poses-wiretapping-discovery-and-ethical-pitfalls)
- [FPF: EU AI Act prohibition of emotion recognition in the workplace](https://fpf.org/blog/red-lines-under-eu-ai-act-unpacking-the-prohibition-of-emotion-recognition-in-the-workplace-and-education-institutions/)
- [AZB & Partners: DPDP Act phased rollout](https://www.azbpartners.com/bank/indias-digital-personal-data-protection-act-phased-rollout-and-key-compliance-milestones/)
- [DPDP Wiki: Rule 3 consent notice](https://dpdp.myndsolution.com/wiki/rules/rule-3/)

*Items marked "verify" or "measure" are confirmed in hour 1. Several sources are vendor or secondary reports. Legal points are orientation, not legal advice.*
