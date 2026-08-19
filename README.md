# PGA Voice Bot Challenge

An automated "patient caller" voice bot built for Pretty Good AI's AI Engineering Challenge. It places real phone calls to PGA's test line, plays a realistic patient persona pursuing a specific scenario (scheduling, refills, edge cases), and captures the recording + transcript for bug analysis.

> Status: scaffolding in progress — this README will be rewritten as a full case study (problem, architecture diagram, key decisions, results, demo) once the bot is working end to end.

## What this is

- **Working code** — Python voice bot that dials PGA's test line and holds a live conversation with their AI agent.
- **Call recordings + transcripts** — evidence for the bug report.
- **Bug report** — quality issues found in PGA's agent, with transcript timestamps.
- **Architecture doc** — see `ARCHITECTURE.md`.

## Setup

1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in your Twilio + OpenAI credentials.
4. (dev only) Expose your local bridge server with `ngrok http 8000` and put that URL in `PUBLIC_BASE_URL`.
5. Run: `python place_call.py --scenario scheduling_simple`

## Repo layout

```
bridge/          # WebSocket bridge server: Twilio Media Streams <-> OpenAI Realtime API
scenarios/        # Caller persona / scenario prompt definitions
recordings/        # Call audio (OGG/MP3)
transcripts/       # Call transcripts
bugs/               # Bug report
place_call.py        # Entry point: places an outbound call for a given scenario
```
