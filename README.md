# PGA Voice Bot Challenge

An automated "patient caller" voice bot built for Pretty Good AI's AI Engineering Challenge. It places real phone calls to PGA's test line, plays a realistic patient persona pursuing a specific scenario (scheduling, refills, edge cases), and captures the recording + transcript for bug analysis.

## What this is

- **Working code** — a Python voice bot that dials PGA's test line via ElevenLabs Agents (GPT-4o reasoning + a distinct ElevenLabs voice per persona) and holds a live conversation with their AI agent, with turn-taking, VAD, and barge-in handled by the platform.
- **12 caller scenarios** — `scenarios/*.json`, covering simple scheduling, rescheduling/cancellation, controlled-substance and simple refills, insurance questions, and edge cases (interruptions, vague symptoms, wrong numbers, a frustrated repeat caller, an after-hours urgent case, and more).
- **Call recordings + transcripts** — `recordings/` (MP3) and `transcripts/` (`[SPEAKER] text` format), evidence for the bug report.
- **Bug report** — `bugs/BUG_REPORT.md`: quality issues found in PGA's agent, with transcript citations.
- **Architecture doc** — `ARCHITECTURE.md`: how the system works today, plus a full decision log of how it got there (including two earlier architectures that were tried and replaced).

## Setup

1. `python -m venv venv && source venv/bin/activate`
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in your Twilio + ElevenLabs credentials.
4. One-time: `python setup_agent.py` — creates the ElevenLabs agent and imports your Twilio number, then writes both ids into `.env`. Refuses to run again once those ids are already set.
5. Run: `python place_call.py --scenario scheduling_simple` — places the call, waits for it to finish, and downloads the recording + transcript automatically.

That's the whole workflow — no local server, no tunnel (ngrok or otherwise) to run first. ElevenLabs' platform places and hosts the call directly.

## Usage

List available scenarios:

```
python place_call.py --list
```

Place a call and wait for the recording/transcript:

```
python place_call.py --scenario <scenario_id>
```

Place a call without waiting (fetch the result later):

```
python place_call.py --scenario <scenario_id> --no-wait
python fetch_conversation.py <conversation_id> --name <call_sid>
```

## Repo layout

```
scenarios/            # Caller persona / scenario definitions (JSON) + loader.py (validation, prompt building)
recordings/           # Call audio (MP3)
transcripts/          # Call transcripts, [SPEAKER] text format
bugs/                 # BUG_REPORT.md
place_call.py         # Entry point: places a call via ElevenLabs Agents, fetches results
fetch_conversation.py # Standalone: (re-)download any call's recording + transcript by conversation id
setup_agent.py        # One-time ElevenLabs agent + phone number setup
ARCHITECTURE.md        # How the system works, plus the full decision log
ITERATION_LOG.md       # Bug-by-bug debugging history from earlier architectures
RESEARCH_SCENARIOS.md  # Background research behind the scenario suite
```

## Notes on the test calls

All test calls go to PGA's test line, from a single consistent Twilio number for the whole project (see `.env`'s `TWILIO_PHONE_NUMBER` / `PGA_TEST_LINE`). A handful of calls in `recordings/originals/` and `transcripts/` predate the current architecture (see `ARCHITECTURE.md`'s Decision Log) and are kept as historical record; calls under the current ElevenLabs Agents architecture are what count toward the submission's minimum.
