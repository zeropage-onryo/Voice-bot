# Voice Agent Evaluation Harness

A Python harness that **QA-tests production voice agents by calling them**. It places real outbound
phone calls to any number, plays a scripted patient persona that pursues a goal across a live
unscripted conversation, and captures a paired recording and transcript for every run as evidence.

Built to answer a question that's hard to answer any other way: *does this voice agent actually hold
up on the phone, and where exactly does it break?*

---

## Case study: a production healthcare intake agent

The harness was first pointed at [Pretty Good AI](https://pgai.us)'s public patient-intake test line
— a live GPT-backed phone agent for a fictional orthopedics clinic.

**13 calls · ~21 minutes of conversation · 12 scenarios · 8 documented defects**

The findings are in [`bugs/BUG_REPORT.md`](bugs/BUG_REPORT.md). The headline ones:

- **A transfer that never happens.** In 8 of 13 calls the agent offered to connect the caller to a
  human, confirmed the handoff, and then dropped the call into a generic goodbye. It never once
  completed a transfer. On one call it narrated a transfer in progress ("okay, it's ringing now")
  three seconds before the line hung up.
- **Identity disclosure before verification.** In 5 of 13 calls the agent's first substantive move
  was to read the name on the account back to an unverified caller — *"I see you're calling from the
  number we have on file. Am I speaking with Alex?"* — which both discloses PHI and reduces
  verification to a leading yes/no question.
- **Ambiguous symptoms discarded.** A caller describing three weeks of dizziness and poor sleep was
  answered with "can you please provide your date of birth?" and the symptoms were never referenced
  again — though the same agent correctly escalated overt chest pain to 911 on another call.

Every finding was cross-checked against the call **audio**, not just the transcript. One candidate
bug was retracted when re-transcribing the recording showed the defect was our own ASR mishearing a
word, and that retraction is documented in the report rather than quietly dropped.

## The calls

Each row's recording and transcript share a filename stem: `recordings/<stem>.mp3` and
`transcripts/<stem>.txt`.

| # | Stem | Scenario tests | Length | Bugs surfaced |
|---|------|----------------|--------|---------------|
| 1 | `01_scheduling_simple` | Baseline: polite caller books a routine check-up | 1:48 | 1, 7 |
| 2 | `02_slow_processing_elderly_caller` | Elderly caller needing time; asks for repeats | 2:42 | 1, 2 |
| 3 | `03_after_hours_urgent` | Symptoms warranting escalation, not a booking | 0:47 | — clean |
| 4 | `04_interrupting_caller` | Turn-taking stress; caller wants a service not offered | 0:51 | — clean |
| 5 | `05_refill_controlled_substance` | Refill request for a controlled substance (Adderall) | 2:26 | 1, 2, 4, 7 |
| 6 | `06_vague_symptoms_triage` | Ambiguous symptoms; how the agent triages | 2:05 | 1, 3, 7 |
| 7 | `07_wrong_number_confusion` | Caller thinks they dialed a different business | 0:54 | 8 |
| 8 | `08_insurance_question_no_appointment` | Insurance answer only; unnecessary-booking pressure | 1:14 | 6 |
| 9 | `09_cancel_and_rebook` | Cancel and rebook a different day in one call | 2:24 | 1, 2, 4, 7 |
| 10 | `10_family_member_on_behalf` | Daughter booking for her mother, not herself | 1:13 | 8 |
| 11 | `11_refill_simple` | Baseline refill: routine prescription renewal | 2:16 | 1, 7 |
| 12 | `12_frustrated_repeat_caller` | Third call on one billing issue; asks for a supervisor | 1:40 | 1, 2, 5 |
| 13 | `13_interrupting_caller_retry` | Turn-taking stress, second attempt | 2:11 | 1, 2, 4, 7 |

All calls were placed from a single consistent number so the agent under test saw one caller ID
throughout.

## How it works

A scenario JSON defines a persona — who they are, what they want, how they speak, which voice and
speaking rate to use. `place_call.py` loads it, validates it, and hands the built system prompt and
voice to ElevenLabs' Agents platform as a per-call override. Their hosted agent (GPT-4o reasoning,
`ulaw_8000` telephony audio both ways, platform-native turn detection and barge-in) places the call
through Twilio and holds the conversation. Afterwards `fetch_conversation.py` pulls the recording and
transcript down and writes them out as a matched pair.

The agent is deliberately configured with an **empty first message**, so it waits for the line under
test to speak first rather than opening on a recorded disclaimer.

**This is the third architecture.** The first two — OpenAI Realtime speech-to-speech, then a
self-hosted FastAPI bridge with hand-built turn-taking and ElevenLabs TTS — were built, tested on
real calls, and replaced. [`ARCHITECTURE.md`](ARCHITECTURE.md) carries the full decision log with the
alternatives considered and the measurements behind each choice;
[`ITERATION_LOG.md`](ITERATION_LOG.md) is the unedited bug-by-bug record of what broke along the way,
including the eight consecutive turn-taking race conditions that ultimately justified deleting the
hand-built layer entirely.

## Setup

1. `python -m venv venv && source venv/bin/activate`
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in your Twilio + ElevenLabs credentials, and set
   `TARGET_NUMBER` to the agent you want to test.
4. One-time: `python setup_agent.py` — creates the ElevenLabs agent and imports your Twilio number,
   then writes both ids into `.env`. Refuses to run again once those ids are already set.
5. Run: `python place_call.py --scenario scheduling_simple`

No local server and no tunnel to run first — ElevenLabs' platform places and hosts the call directly.

## Usage

```
python place_call.py --list                          # list available scenarios
python place_call.py --scenario <scenario_id>        # call, then fetch recording + transcript
python place_call.py --scenario <scenario_id> --no-wait
python fetch_conversation.py <conversation_id> --name <call_sid>
```

## Scenarios

Twelve personas in `scenarios/*.json`, each a small JSON file (goal, voice id, voice direction,
optional speaking rate) validated at load time so a bad scenario fails before anything dials.

They aren't arbitrary. The suite was designed against published research on where voice interfaces
actually fail — W3C COGA guidance on cognitive accessibility and imposed timeouts, a JMIR study on
harmful guidance from voice assistants, and the NEDA "Tessa" chatbot incident as a case of missing
escalation paths. The reasoning, sources, and the claims that *didn't* survive verification are in
[`RESEARCH_SCENARIOS.md`](RESEARCH_SCENARIOS.md).

## Repo layout

```
place_call.py         # Entry point: places a call, fetches results
fetch_conversation.py # Standalone: (re-)download any call's recording + transcript
setup_agent.py        # One-time ElevenLabs agent + phone number setup
scenarios/            # Persona definitions (JSON) + loader.py (validation, prompt building)
recordings/           # Call audio (MP3), one per call
transcripts/          # Call transcripts, [SPEAKER] text format, paired with recordings/
bugs/BUG_REPORT.md    # Findings from the case study
ARCHITECTURE.md       # How it works, plus the full decision log
ITERATION_LOG.md      # Bug-by-bug debugging history across all three architectures
RESEARCH_SCENARIOS.md # Research behind the scenario suite
archive/              # Superseded architectures and their calls — see archive/README.md
```

## Stack

Python · ElevenLabs Agents (GPT-4o reasoning + streaming TTS) · Twilio Programmable Voice ·
G.711 µ-law 8kHz telephony audio · `asyncio` · previously FastAPI + WebSockets + OpenAI Realtime API
(see `archive/`)
