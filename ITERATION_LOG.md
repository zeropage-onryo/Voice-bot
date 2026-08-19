# Iteration Log

Running log of real bugs hit during development and how they got fixed -
separate from `ARCHITECTURE.md`'s Decision Log (which is for design
choices, not bug fixes) and `bugs/BUG_REPORT.md` (which is for issues
found *in PGA's agent*, not our own code). This is evidence-of-iteration
material for the submission - what actually broke, not a polished retelling.

---

### 2026-08-18 — `ModuleNotFoundError: No module named 'audioop_lts'` on first bridge server run
- **What happened:** First attempt to run `uvicorn bridge.server:app --port 8000` failed on import, tracing into `bridge/recorder.py`'s `import audioop_lts as audioop` fallback.
- **Root cause:** Two things stacked. First, Python 3.13 (confirmed via the traceback path showing `python3.13`) removed the `audioop` module from the standard library entirely, which `recorder.py` already accounted for via a fallback import to the `audioop-lts` backport package. Second, that fallback package had been added to `requirements.txt` *after* the local venv was already created and `pip install` already run once - so the venv simply didn't have it yet.
- **Fix:** Re-ran `pip install -r requirements.txt` inside the existing venv to pick up the new dependency. No code changes needed - the code was already correct, the environment just needed to catch up.
- **Status:** fix applied (reinstalled dependencies); confirmation that the server now starts clean is still pending.

### 2026-08-19 — First real call connected, then OpenAI rejected `session.turn_detection`
- **What happened:** First fully end-to-end test call: Twilio fetched `/twiml` (200 OK), the media WebSocket connected, and the OpenAI Realtime session opened - but OpenAI immediately returned `invalid_request_error: Unknown parameter: 'session.turn_detection'`.
- **Root cause:** Placed `turn_detection` as a top-level field under `session` in the `session.update` payload. The current Realtime API schema nests it under `session.audio.input.turn_detection`, alongside `format` and `transcription` - an older API version had it at the top level, and the code was written against that.
- **Fix:** Moved `turn_detection` into `session.audio.input`, matching where `format` and `transcription` already lived (`bridge/server.py`).
- **Status:** fix applied; confirmation of a clean full call (audio actually flowing both ways) is still pending the next test call.

### 2026-08-19 — Bot hallucinated into Spanish on its first turn, then briefly spoke as if it were the agent
- **What happened:** In the first successful full call (CA2346562065db120c1382893a6c7e3538), PGA's agent opened in clean English ("This call may be recorded..."). Our patient bot's very first reply was entirely in Spanish, unprompted - nothing in the audio it heard was Spanish, and `DEFAULT_INSTRUCTIONS` never mentions Spanish. This knocked PGA's own IVR into its Spanish-language menu ("Para Español, oprima el 2"), which then itself cut off mid-sentence into English - a secondary effect worth remembering as a possible real PGA-side bug later, but not the root cause here. A related issue in the same call: one bot turn briefly spoke *as the office* ("gracias por llamar" - "thanks for calling") instead of staying in the patient role.
- **Root cause:** Language-drift hallucination from the Realtime model - a known failure mode when a voice model isn't explicitly pinned to one language and one role. `DEFAULT_INSTRUCTIONS` said what the persona wants but never said what language to use or reinforced which role it plays.
- **Fix:** Added explicit lines to `DEFAULT_INSTRUCTIONS`: always speak English regardless of what's heard, and always stay in the patient role, never the agent's.
- **Status:** fix applied; confirmation this actually prevents the drift is still pending the next test call - language hallucinations in voice models aren't always fully eliminated by a prompt instruction alone, so this is worth specifically re-testing for, not just assuming fixed.

### 2026-08-19 — The `audioop_lts` fallback import could never have worked (corrects the 2026-08-18 entry)
- **What happened:** A scratch script copying `recorder.py`'s `import audioop` / `except ImportError: import audioop_lts as audioop` pattern failed with `ModuleNotFoundError: No module named 'audioop_lts'` — *with* the fallback present. Checking the venv showed `audioop-lts` had also silently vanished from it again, so `recorder.py` itself was one import away from the same crash.
- **Root cause:** The package is named `audioop-lts`, but the module it installs is plain `audioop` — a drop-in for the removed stdlib name. `import audioop_lts` has never been a real module, so the fallback branch was dead code that could only ever re-raise a more confusing error. The 2026-08-18 entry's conclusion ("the code was already correct, the environment just needed to catch up") was wrong: installing the package makes the *first* import succeed, which is why the broken fallback was never exercised.
- **Fix:** `recorder.py` now has a single `import audioop` with an `except ImportError` that raises a clear install instruction instead of pretending there's a second module to try. Dependencies reinstalled.
- **Status:** fixed and verified — `recorder.py` imports cleanly, and the offline harness produced a mixed MP3 through the audioop path.

### 2026-08-19 — Barge-in gated on task-liveness would have silently never fired
- **What happened:** First run of the offline bridge harness (fake Twilio + fake OpenAI, real ElevenLabs): the scripted mid-playback `input_audio_buffer.speech_started` produced no `clear` message to Twilio. Barge-in — the piece the migration brief flagged as "most likely to feel wrong on the first attempt" — did nothing at all.
- **Root cause:** The handler only sent `clear` if the TTS playback task was still running. But ElevenLabs streams ~12x faster than realtime, so a 5-second reply is fully handed to Twilio in under half a second and the task exits — while the audio plays on from Twilio's buffer for seconds more. Exactly the window where a real barge-in happens is exactly the window where the task is already done.
- **Fix:** Send `clear` unconditionally on `speech_started` (and cancel any playback task that does happen to be alive). `clear` on an empty buffer is a no-op, so there is no cost when the bot wasn't talking.
- **Status:** fixed; harness now shows media → clear → next reply's media in order. Still needs confirming on a real call with genuine cross-talk — the `interrupting_caller` scenario exists to force that.
