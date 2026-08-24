# archive/

Superseded work from earlier architectures, kept as a record rather than deleted.
**Nothing in this directory is current.** It is kept because how the system got here is part of the answer.

- `bridge-v2-retired/` — the self-hosted FastAPI bridge (Twilio Media Streams ↔ OpenAI Realtime ↔
  ElevenLabs TTS) that ran calls before the migration to ElevenLabs Agents. See `ITERATION_LOG.md`.
- `pre-migration-calls/` — the 9 calls made against that bridge. **Not representative voice-quality samples.** The
  bot sometimes replied to the recorded disclaimer and talked over the live greeting, and the
  bridge's recorder mixed the two audio directions out of sync (the bot's side is front-packed to
  the start of each file, so its apparent timing is wrong). Both defects are why the architecture
  changed; both are documented in `ITERATION_LOG.md` and `ARCHITECTURE.md`.

The 13 current calls are in `recordings/` and `transcripts/` at the repo root.
