# Sync: Motive

> **What this document is:** why Sync exists, what it is, and the reasoning behind its core choices. Its companion, [architecture.md](architecture.md), explains how Sync is built and how every workflow runs.
>
> Version 2.0 · 9 October 2026 · Problem statement PR 007 (General)

> **v2.4 update (after hands-on testing):** Sync no longer has roles: one general guide fits every conversation. The user always chooses which project a call belongs to; Sync never guesses. Projects can now be **shared**: teammates see the same calls and records, and every change carries their name. See the v2.4 note in [architecture.md](architecture.md). Mentions of roles below describe the earlier design.

> **What changed from v1**
> - Sync is now **personal**. It works only for the person using it, from that person's perspective. v1 proposed one shared AI for everyone in the room.
> - It adapts to the user's **role** (sales, technical sales, finance, client, support, team lead, or any custom role).
> - Every conversation is filed into a **project**, which keeps a complete, append-only history across every call, meeting and person.
> - It helps **from the very first call**, with no setup, and gets better as it learns the user.
> - It's **available any time**, not only during calls. You can chat with it, give it new details and ask it to prepare you.
> - Privacy moved from "forget everything" to **"you own a complete record"**. Audio is never kept, nothing is used for training, and you can delete anything.
> - **v2.1:** the whole stack is **free and open source, and runs locally**: open-weight models through Ollama, local speech recognition, SQLite. ElevenLabs' free credits are used only for fictional demo audio.

---

## 0. The motive in one breath

Every day, people walk out of conversations that weren't finished. A question went unasked, a concern went unanswered, a condition was forgotten, or a decision ended up with no owner. Sometimes a client got asked the same question for the third time.

**Sync is your personal conversation twin.** It sits on *your* side of every conversation (a call, a meeting, a negotiation, a talk at a client's office) and does five things:

1. **It thinks from your seat.** It knows your role and listens for what *you* need. A finance manager and a solutions engineer in the same meeting get different help.
2. **It remembers everything, by project.** Every conversation with anyone about the same piece of work lands in one project: what was asked, answered, decided, promised and contradicted, with a full history of every change.
3. **It notices what's missing while you can still act.** Even on a first call with nothing set up, it quietly suggests the question that hasn't been asked, the answer that was left hanging, or the commitment that has no owner.
4. **It's always there.** It **joins every call and meeting automatically**, including ordinary phone calls taken on your laptop, and you can switch it off for any one of them. Before the conversation it briefs you. During it, it nudges you and answers private questions. Afterwards it writes the recap and drafts follow-ups. In between, you can chat with it, add details and get reminders.
5. **It gets better at being you.** It learns which suggestions you use, what you tend to miss and how you like to work.

> **Sync doesn't help you talk. It helps you finish, and it never forgets what you started.**

---

## 1. The problem, in human terms

The problem statement: *"people often have to listen, think, and respond at the same time, causing important points and decisions to be missed."*

Look closely at sales pitches, support calls, negotiations and team discussions, and three root causes keep showing up.

### 1.1 Working memory is full, so things get omitted

The costliest failures in a conversation are **omissions, not mistakes**.

| What people *worry* about | What actually *hurts* |
| --- | --- |
| Saying something wrong | Not asking the question that mattered |
| Not having a clever reply | An objection left unanswered because the topic moved on |
| Forgetting a fact | Forgetting the **condition** attached to an offer |
| Talking too little | A decision that **nobody owns** |

A mistake shows up straight away, because someone corrects you. An omission stays hidden until days later, when the deal stalls, the customer calls back or the team reopens the decision.

### 1.2 Context is scattered, so people repeat and contradict each other

Real work is rarely one conversation. A deal, a project, a hiring decision or a vendor evaluation runs over **many conversations with many people over weeks**, and nobody holds the whole picture:

- the client gets asked the same question by three people from the same company
- one side says "we agreed on the 15th" and the other says "I thought we said the 22nd"
- a concern the CFO raised two weeks ago never reaches today's call
- notes live in five places, so preparing for a call means digging

### 1.3 Every role listens for different things, so generic help is noise

A finance manager listens for cost, payment terms and approvals. A technical lead listens for integration, risk and effort. A buyer listens for hidden costs and vendor promises. An assistant that suggests everything to everyone is noise to all of them.

### Three causes, three answers

| Root cause | Sync's answer |
| --- | --- |
| Working memory overload | **Live gap detection.** Sync holds the checklist so you can hold the conversation |
| Scattered context | **Projects.** One living memory for each piece of work, across every conversation and person |
| Role-specific attention | **Role Lens.** Help shaped by your role, so you can focus on your own domain |

---

## 2. Who Sync is for

**Anyone whose conversations matter.** Sync isn't tied to one industry or one company. The Role Lens adapts it to whoever is using it.

| Role | Listens for | Example nudge |
| --- | --- | --- |
| Salesperson | Buying process, needs, objections, next step | *Ask: who else signs off on this?* |
| Technical sales / solutions engineer | Systems, integrations, security, feasibility | *Ask: which systems must this integrate with?* |
| Finance manager | Total cost, payment terms, ROI, approvals, penalties | *Confirm: is the 8% discount conditional on volume?* |
| **Client / buyer** | Hidden costs, SLAs, exit terms, what the vendor promised | *Ask: what's the total cost including implementation?* |
| Support agent | Issue, impact, steps already tried, correct resolution | *Caution: refund cap is 50% after 30 days [Policy]* |
| Project manager / team lead | Decisions, owners, deadlines, blockers | *Confirm: launch on the 15th or the 22nd?* |
| Founder / executive | Strategy, commitments, partner and investor asks | *Fact: you promised them a pilot by November (2 Oct call)* |
| Negotiator / procurement | Offers, conditions, concessions, walk-away points | *Confirm: does the price hold without the 2-year term?* |
| Consultant / freelancer | Scope, deliverables, revisions, payment terms | *Ask: how many revision rounds are included?* |
| Recruiter / hiring manager | Competencies covered, the candidate's own questions | *Ask: how did they handle a stakeholder conflict?* |
| Individuals (patient, tenant, home buyer…) | The questions they'd regret not asking | *Ask: what side effects should make me call back?* |
| **Custom** | Describe your role in a sentence and Sync drafts a lens for you | — |

A person can have **more than one role** (a founder who also sells, for example) and choose the lens for each project.

---

## 3. Five seats at the table, revisited

The five perspectives from v1 still hold, with one important change: **each seat now gets its own Sync instead of sharing one.**

- **The entrepreneur.** By 2026, live in-call suggestions are a common feature. What's still missing is **continuity** (memory across every conversation in a project) and **personal fit** (help shaped by role and learned habits). Both compound: the longer someone uses Sync, the more it knows their projects and patterns, and the harder it is to replace.
- **The salesperson.** Wants never to re-ask a client something, never to forget a promise, and to get the one question that matters right now. Doesn't want scripts or a screen full of text.
- **The startup CEO.** Wants commitments and decisions across all of the team's conversations kept consistent. Trusts Sync because the data belongs to the user, audio is never kept and nothing trains a model.
- **The client-facing professional.** Wants to be right (answers grounded in sources, the Promise Guard) and to walk into every call already knowing the customer's full history.
- **The client.** Can be a Sync user too. In **Client mode**, Sync helps them ask due-diligence questions and keeps a running list of everything vendors promised. Each side has its own twin, and nobody shares one.

---

## 4. The Sync idea: five building blocks

```
            ┌──────────────┐
            │  ROLE LENS   │  who you are and what you listen for
            └──────┬───────┘
                   ▼
 ┌────────────┐  ┌──────────────┐  ┌────────────┐
 │  ALWAYS-ON │◀▶│   PROJECTS   │◀▶│    LIVE    │
 │   AGENT    │  │ memory of    │  │  COPILOT   │
 │ chat, add, │  │ everything,  │  │ notices    │
 │ brief, draft│ │ with history │  │ what's     │
 └────────────┘  └──────┬───────┘  │ missing    │
                        ▲          └────────────┘
                 ┌──────┴───────┐
                 │  LEARNS YOU  │  your playbook gets sharper over time
                 └──────────────┘
```

### 4.1 Role Lens: help shaped by who you are

**Onboarding** asks for:
- your role or roles
- your industry or domain
- the conversations you usually have, and what success looks like in them
- how much help you want: *Quiet*, *Normal* or *Active*
- your languages

Optionally, you can upload your own material (price list, product sheet, policy, contract) so Sync can ground its facts.

**A Role Lens defines:**
- **what you care about** and listen for
- **exit criteria** for each type of conversation (discovery, review, negotiation, support, team sync…): what you need to leave with
- **question frameworks** that suit your role
- **what to leave to others.** This keeps you in your own domain. For example, a solutions engineer's Sync won't push pricing questions that the account executive on the same call will handle.
- **Promise Guard topics:** the things you must not over-promise

**Custom roles:** describe your role in a sentence, and Sync drafts a lens you can edit.

### 4.2 Projects: one living memory per piece of work

- **Automatic filing.** As a conversation starts, Sync works out whether it belongs to an existing project, using the people, organizations and topics it hears or the meeting title. It asks once: *"Continue 'Acme · CRM integration'?"* If the conversation is new, Sync creates the project.
- **Manual creation.** You can create a project at any time and give it an objective, context, people, documents, and notes about an upcoming conversation.
- **What a project holds:**
  - every **session**: calls, meetings, in-person talks, imported recordings, pasted notes and chats with Sync
  - every **item**, each with a type and a status. Types: Fact, Requirement, Question (with its answer), Concern, Proposal, Decision, Commitment, Risk, Note. Statuses: *Open, Proposed, Conditional, Agreed, Done, Superseded, Disputed*.
  - **evidence** for each item: the exact quote, who said it and when
  - **people cards**: each person's role, concerns, the questions they asked, what they promised, and how their positions changed over time
  - **decision history**, showing which decision replaced which
  - **conflicts**, plus an **open-items board** split into *mine* and *theirs*
  - an always-current **project brief**: "where we are"
- **Everything is recorded.** Every item, edit, merge, status change and note is written to an **append-only history**, whether it came from you, from Sync's agent or from the live engine. You can see who changed what, when and why, and view the project as it stood on any date.
- **Conflict radar.** When something contradicts the record (*"budget ₹25L"* today versus *"₹40L"* from Priya on 3 Oct), Sync flags it. If it comes up during a call, the flag appears live; otherwise it appears in the project.

### 4.3 Live Copilot: notices what's missing

**Automatic, with an off switch you control.** Sync notices on its own when a call or meeting starts and joins it. That covers ordinary phone calls answered on your laptop through Windows Phone Link (or Mac Continuity), plus Zoom, Teams, Google Meet, WhatsApp and more. You stay in control:
- a short countdown lets you **skip** any call, and nothing is kept if you do
- **pause**, **stop** or **stop and delete** at any time
- a hotkey
- rules such as *never for these contacts*, *never for meetings titled "personal"*, or *only during work hours*
- a visible indicator whenever Sync is listening

The copilot works **from the very first call**, with no project and no setup, because it draws on three sources at once:

1. **The live conversation.** It spots questions left unanswered, vague claims worth pinning down (*"soon"*, *"a lot"*, *"some issues"*), concerns raised and then dropped, commitments with no owner or date, dangling threads (*"let's come back to that"*) and contradictions.
2. **Your Role Lens.** It knows what someone in your role needs to leave with.
3. **Your history.** It uses project memory (what's already known, so you never re-ask) and your personal playbook (what you tend to miss and which questions work for you).

**Nudges** are one line, about ten words at most, and come in four kinds:

| Kind | When | Example |
| --- | --- | --- |
| **Ask** | A question would close a gap | *Ask: what date, and what's driving it?* |
| **Confirm** | Something is ambiguous, conditional, unowned or contradictory | *Confirm: Priya said ₹40L on Monday — which is current?* |
| **Caution** | You're about to promise more than your sources allow | *Caution: standard go-live is 6–8 weeks [Implementation Guide §2]* |
| **Fact** | A grounded fact from your documents or your project history | *Fact: EU data region required — Priya, Monday call* |

**Also available during the call:**
- **Promise Guard.** Sync checks what *you* say against your documents and past commitments.
- **Already-asked guard.** Sync never suggests a question that has already been answered in the project. If you start to re-ask one, it shows you the answer.
- **Catch me up.** A two-line summary of the last few minutes, for when you join late or lose the thread.
- **Ask Sync privately.** Type a question mid-call, such as *"what did they say about delivery last time?"*
- **Mark moment.** Bookmark the last 30 seconds.
- **Off the record.** Pause listening with one tap.
- **Wrap-up check.** Before the end, Sync shows something like *"2 open · 1 action without an owner"* along with a three-line recap you can read out to confirm with everyone.

**Group discussions.** Sync tells the speakers apart, tracks decisions, owners and deadlines, and pulls out **what's expected of you**: *"You: send the API spec by Friday — asked by Arjun."*

**Restraint is a feature, not a limitation:**
- an **attention budget**: few nudges, never two at once, and each fades on its own
- **turn-aware timing**: never while you're speaking
- **grounded-or-silent**: a *Fact* or *Caution* must cite a source, otherwise it doesn't appear
- **questions, not scripts**: your voice stays your own

### 4.4 Always-on Agent: talk to Sync any time

Outside your conversations, Sync is a chat agent (voice later) that can see all your projects:

- *"What's still open with Acme?"* or *"What did the client say about delivery?"* The answers cite the session and quote what was said.
- *"Add: Neha is the CFO; she approves anything above ₹10L."* Sync adds this to the project and records it in the history.
- *"Brief me for tomorrow's call."* You get the objective, open items, the people and their concerns, conflicts to settle and suggested questions.
- *"Draft a follow-up for today's call."* Sync writes it in your style, and you send it yourself.
- Paste an email, a WhatsApp thread or meeting notes, and Sync files it in the right project.
- **Reminders** for commitments coming due, both yours and theirs.

### 4.5 Learns you

- **Signals.** Sync notices which nudges you **use** (it can tell when you actually ask a suggested question), which you dismiss and which you mark useful. It also learns from your edits to items and recaps, what you confirm or reject, and what happened afterwards (did the open item get resolved?).
- **What it learns.** Your **personal playbook**, with entries such as *"tends to skip the timeline question on first calls"*, *"prefers short technical nudges"* or *"these three questions produced the most useful answers"*. It also learns how often to nudge you, and with which kinds.
- **How it learns.** Learning is stored as **per-user memory**, not by retraining a shared model. You can see everything Sync has learned about you, and edit or delete any of it.

> **A twin of your perspective and memory, not of your voice.** Sync thinks from your seat and remembers what you'd want remembered. It never speaks as you, never sends anything without your tap, and never presents itself as you to anyone.

---

## 5. A week with Sync

**Meera** is a solutions engineer at a software company, using the *Technical Sales* lens. **Ravi** is a procurement manager at Acme and uses his own Sync in *Client* mode.

**Monday: the first call, with no setup.** Priya from Acme calls Meera's mobile number, and Meera answers on her laptop through Phone Link. Sync joins on its own after a five-second skip countdown. Nothing is configured yet. About a minute in, Sync asks: *"Looks new — create project 'Acme · CRM integration'?"* Meera taps yes.
- Priya from Acme IT says *"we need it soon."* Sync: *Ask: what date, and what's driving it?*
- Priya mentions *"a couple of legacy systems."* Sync: *Ask: which systems, and who owns them?*
- Sync says nothing about pricing, because the account executive on the call handles that.

**Monday evening: between calls.** Meera tells Sync: *"Add: their SAP instance is on-prem; Priya sends the architecture diagram by Thursday."* Sync records a *Fact* and a *Commitment* (owner Priya, due Thursday), and both appear in the project history.

**Thursday: a second call with more people.** Sync recognizes the project from the names and the topic.
- Arjun from Acme finance says the budget is ₹25L. Sync: *Confirm: Priya said ₹40L on Monday — which is current?* Meera asks, and Arjun clarifies: ₹25L is phase 1, and ₹40L is the total. Sync records both figures and links them.
- Meera starts to ask about data residency. Sync: *Fact: EU region required — Priya, Monday.* She skips the repeat question.
- Meera says *"we can be live in four weeks."* Sync: *Caution: standard go-live is 6–8 weeks [Implementation Guide §2].* She corrects herself on the spot.
- Near the end, Sync shows: *Wrap-up: architecture diagram not received; the security review has no owner.* Meera settles both before anyone hangs up.

**After the call.** The recap and a follow-up draft are ready. Meera edits one line and sends it herself. The project timeline now shows both sessions, the budget clarification, the overdue diagram and a newly owned security review, with every change in the history.

**On Ravi's side of the same call**, his Sync suggested *Ask: what's included in support after go-live?* It also added *"6–8 week go-live — Meera, Thursday"* to his **vendor-promises** list. It was the same conversation, with two people, two twins and different help for each.

**A few weeks later.** Sync has noticed that Meera tends to skip the success-criteria question on first calls, so it now nudges her about it earlier. She rarely uses pricing facts, so it has stopped showing them.

---

## 6. Feature map: the complete idea

Legend: **MVP** = in the 18-hour build · **Next** = right after · **Later** = roadmap

| Area | Feature | When |
| --- | --- | --- |
| **Onboarding** | Role Lens: built-in packs plus custom roles generated from a description | MVP |
| | Upload your material (product sheet, policy, price list) | MVP |
| | Notification to the user whenever Sync turns on (overlay + tray icon) | MVP |
| | Calendar connection (auto-start, briefs, project matching) | Next |
| **Projects** | Create and edit projects, add context, people and documents | MVP |
| | Automatic project detection and filing at session start | MVP |
| | Timeline, typed items with statuses, evidence quotes | MVP |
| | Append-only history of every change | MVP |
| | Conflict radar | MVP |
| | People cards (basic in MVP) | MVP → Next |
| | Decision history and "project as of any date" | Next |
| | Team-shared projects, so nobody on the team re-asks the client | Later |
| **Before** | Brief me | MVP |
| | Objective and exit criteria for the session | MVP |
| | Rehearsal mode: practise with Sync playing the other side, using real project history | Later |
| **During** | Nudges: Ask, Confirm, Caution, Fact | MVP |
| | Promise Guard and already-asked guard | MVP |
| | Wrap-up check | MVP |
| | Ask Sync privately, off the record | MVP |
| | Speaker separation, "what's expected of you" | MVP |
| | Floating overlay above every call and meeting window (countdown, nudges, controls) | MVP |
| | Catch me up, mark moment | Next |
| | Whisper nudges in an earbud, haptic nudges on a watch | Later |
| **After** | Recap with one-tap confirmation, project update and merge | MVP |
| | Follow-up draft in your style | MVP |
| | Session reflection for learning | MVP |
| **Anytime** | Chat agent that can read and write all your projects | MVP |
| | Paste text (email, chat, notes) into a project | MVP |
| | Import a recording or transcript after a call | Next |
| | Reminders for commitments | Next |
| **Learning** | Feedback, used-nudge detection, editable playbook | MVP |
| | Adaptive nudge weights, question bank | Next |
| **Where it listens** | **Auto-listen** on the laptop for every call and meeting: phone calls through Windows Phone Link, Zoom, Teams, Meet, WhatsApp… | MVP (Windows) |
| | Skip countdown, pause, stop and delete, master switch, rules by app | MVP |
| | Rules by contact, title and working hours; global hotkey | MVP → Next |
| | In-person mode (room microphone) | MVP |
| | Browser-only capture (no agent installed) | Next |
| | macOS agent (iPhone calls through Continuity) | Next |
| | Mobile app (in-person mode, chat, reminders) | Next |
| | *Sync Call Link* (self-hosted browser call room), meeting-platform apps | Later |
| **Sharing** | Export or share a recap | Next |
| | Read-only project link | Later |

---

## 7. Ideas we added beyond the brief

1. **Already-asked guard.** No question is repeated to a client once it's answered anywhere in the project.
2. **Conflict radar.** Contradictions are caught across conversations and people, not only within one call.
3. **"What's expected of me."** In group meetings, Sync pulls out your own action items.
4. **Domain focus.** "Leave to others" keeps each person in their own lane on a shared call.
5. **Commitment radar.** Across all projects: what I promised, what was promised to me, and when each is due.
6. **Client mode and a vendor-promise ledger.** Buyers get a copilot too.
7. **Catch me up.** A two-line summary for when you lose the thread.
8. **Rehearsal mode.** Practise a negotiation with Sync playing the other side, using that side's real concerns from project history.
9. **Handoff packet.** When a project changes hands (sales to onboarding, or agent to agent), the next person gets the full context, so the customer never has to explain again.
10. **Time travel.** See the project exactly as it stood on any date, thanks to the event history.
11. **Off the record and auto-stop.** Sync stops listening the moment the conversation ends.
12. **Fair-play mode.** In interviews, exams or assessments where live help isn't permitted, Sync offers preparation and review only.
13. **Multilingual and code-switching** (e.g. Hinglish), with nudges in your preferred language.
14. **Whisper and haptic nudges** for in-person meetings, where looking at a screen would be rude.
15. **Same call, two lenses.** Teammates on one call each get help for their own domain, so nobody duplicates anyone else.

---

## 8. How Sync answers the problem statement

| The problem statement asks for… | Sync's answer |
| --- | --- |
| Helping people stay focused while they listen, think and respond | Sync holds the checklist and the memory; nudges are rare, one line long and well timed |
| Understanding **the discussion** | A live ledger of questions, answers, concerns, proposals, decisions and commitments, with speakers |
| …and **the user's objective** | Role Lens plus session objective, turned into exit criteria and tracked live |
| Relevant information | *Fact* nudges from your documents and project history, always cited |
| Response suggestions | *Ask*, *Confirm* and *Caution* nudges: questions and checks, not scripts |
| Reminders | Gap nudges, the wrap-up check, carried-forward open items, commitment reminders |
| Next steps | Commitments with owner and date, confirmed in the recap, plus follow-up drafts |
| A shared view of the conversation | The **project**: one unified view of every conversation with everyone, which you can share or export when you choose |
| Individual **and** group conversations | One-to-one calls, plus multi-speaker tracking, decisions and owners, and "what's expected of you" in groups |
| Sales, support, negotiation, team discussions | Role Lenses for each, plus custom roles for anyone else |
| Simple and unobtrusive | Attention budget, turn-aware timing, a floating one-line overlay, silent by default |
| Suitable for confidential conversations | Runs entirely on your laptop, notifies you whenever it's on, never stores audio, never trains on your data, lets you delete anything |

---

## 9. Privacy and trust

The full mechanisms are in [architecture.md §7](architecture.md). The principles:

1. **Sync works for you, and you always know when it's on.** Whenever Sync turns on, you get a notification and the tray icon turns red. While it's on, it hears both the call or meeting audio and your voice through the laptop mic. It doesn't announce itself to the other people on the call; you decide when and whether to tell them. *(Some places require everyone's consent to record a call, and following local law is the user's responsibility.)*
   - **Automatic, but always yours to switch off.** Sync joins calls and meetings by itself, but you get a skip countdown, a master switch, pause, stop and delete, a hotkey and rules. Skipped calls leave nothing behind, and an indicator is always visible while Sync is listening.
2. **It respects the phone's protections.** Android and iOS deliberately block third-party apps from phone-call audio, and Sync doesn't try to get around that. It works through legitimate paths instead: a laptop for online meetings, a second device on speakerphone, a *Sync Call Link* that the other person joins in their browser (later), or importing the recording afterwards.
3. **You own the record.** Transcripts and items live in your projects. You set retention and can delete anything at any time.
4. **Audio is never stored.** Card numbers, OTPs, government IDs and passwords are masked before storage and before any AI model sees them.
5. **Your data is never used for training.** Your personal learning is yours alone, visible and deletable.
6. **No voiceprints of other people, and no emotion reading.** Speakers are told apart per session, and Sync never infers feelings from voice or face. (The EU AI Act already bans emotion recognition at work.)
7. **It stops when the conversation stops,** and you can go off the record at any time.
8. **It runs on your own computer.** Sync is built entirely on free, open-source software and open-weight AI models: speech recognition, the AI models, the database and the app all run locally. Conversation data never needs to leave the device. Teams can run the same stack on their own server.

---

## 10. What makes Sync different

*These are general characterizations of product categories as of October 2026, not product-by-product claims.*

| Capability | Meeting recorders | Live suggestion overlays | Platform copilots | **Sync** |
| --- | --- | --- | --- | --- |
| Help shaped by your role and domain | ✗ | ◐ templates | ✗ | ✓ Role Lens, custom roles |
| Memory across every conversation in a project, any platform or in person | ◐ per-meeting notes | ✗ | ◐ inside one platform | ✓ Projects with full history |
| Notices what's **missing**, not only what was said | ✗ | ◐ reacts to prompts | ◐ when asked | ✓ gap detection against exit criteria |
| Never re-asks; catches contradictions across calls | ✗ | ✗ | ✗ | ✓ already-asked guard, conflict radar |
| Learns you over time, transparently | ✗ | ✗ | ✗ | ✓ editable playbook |
| Useful outside meetings (chat, add details, brief) | ◐ search | ✗ | ◐ | ✓ always-on agent |
| Joins every call and meeting automatically, with a per-call off switch | ✗ | ◐ manual start | ◐ inside one platform | ✓ auto-listen with skip, stop and rules |

Each ingredient exists somewhere. **What's new is putting them together around one person: their role, their projects, their gaps and their habits.**

---

## 11. Non-goals

- **Not an audio recorder.** Sync keeps a clean text record and never keeps audio.
- **Not a teleprompter.** It doesn't produce lines to recite or tell you what to say to win.
- **Not hidden from you.** You're always notified when Sync is on, and you can switch it off for any call. It never runs secretly in the background without the tray indicator.
- **Not an impersonator.** Sync never speaks as you, never clones your voice, and never sends anything without your tap.
- **Not an emotion reader.** It records concerns people actually **say**, and never infers feelings from voice or face.
- **Not a surveillance tool.** It works for the person using it, and nobody can use it to watch that person.
- **Not trained on your data.** Learning is per-user memory that you can see and delete.

---

## 12. What we must prove

| # | Assumption | How we test it | Initial target |
| --- | --- | --- | --- |
| A1 | Sync finds real gaps at a low nudge rate | Scripted conversations with planted gaps ("golden gaps") | ≥ 70% of planted gaps surfaced in time; ≥ 80% of nudges judged useful |
| A2 | Nudges arrive while they're still useful | Time from the trigger moment to the nudge appearing, on a normal laptop CPU | Signal nudges ≤ 1.5 s; AI-checked nudges ≤ 6 s |
| A3 | It stays unobtrusive | Nudges per 10 minutes; users' distraction rating | ≤ 3 per 10 min in *Normal* mode |
| A4 | Projects are filed correctly | Correct project matches in multi-session demos | ≥ 90% correct after one confirmation tap |
| A5 | The project record is trustworthy | Item type, status and owner correctness after recap review | ≥ 90% |
| A6 | Role Lenses change the help in useful ways | Same call replayed with two lenses; users rate relevance | Clear preference for their own lens |
| A7 | Learning improves things | Nudge use rate in later sessions versus the first | Rising use rate, falling dismissals |

**The proof experiment:** run the same scripted conversations with users who have Sync and users who don't, and compare what each group remembers, which critical questions they ask, and how accurately they capture commitments and open issues.

---

## 13. The question behind every decision

> **When someone is in the middle of a conversation that matters, what is the one thing Sync can help them notice, recall or confirm, which they would otherwise miss, while there is still time to act on it?**

If a feature doesn't answer that question, or doesn't make the user's project memory more complete and trustworthy, it doesn't ship.

### Sync's motive

> **Everyone deserves a second mind in the room that's entirely on their side, so that every important conversation ends finished and nothing that was said is ever lost.**

---

### Sources

**Market (§3, §10)**
- [Otter.ai launches Live Assist for real-time AI meeting coaching](https://techintelpro.com/news/marketing/ai/otterai-launchs-live-assist-for-real-time-ai-meeting-coaching)
- [Sayble launches real-time AI copilot (Sep 2026)](https://aitoolly.com/ai-news/article/2026-09-29-sayble-launches-real-time-ai-copilot-delivering-in-call-response-recommendations-for-meetings-and-ne)
- [8 Best Cluely Alternatives in 2026](https://dupple.com/learn/cluely-alternatives)
- [Salesforce AI Research: SalesCopilot preprint (arXiv 2603.21416)](https://arxiv.org/html/2603.21416v1)
- [Clari vs Gong: real-time coaching comparison](https://www.itsconvo.com/blog/clari-vs-gong)
- [Tactiq: live meeting AI](https://tactiq.io/ask-ai-during-meetings)

**Platform and legal constraints (§9). Full list in [architecture.md](architecture.md)**
- [Google Play bans third-party call recording via the Accessibility API (2022)](https://www.theregister.com/2022/04/22/google_banning_thirdparty_callrecording_apps/)
- [Hedy: why iPhone calls can't be recorded by third-party apps](https://hedy.ai/help/recording-phone-calls-on-iphone/)
- [EU AI Act: prohibition of emotion recognition in the workplace (FPF)](https://fpf.org/blog/red-lines-under-eu-ai-act-unpacking-the-prohibition-of-emotion-recognition-in-the-workplace-and-education-institutions/)
- [AI notetaking: wiretapping, discovery and ethical pitfalls (Bloomberg Law)](https://news.bloomberglaw.com/safety/ai-notetaking-poses-wiretapping-discovery-and-ethical-pitfalls)

*Most market claims come from vendors or reviewers. Legal points are orientation, not legal advice.*
