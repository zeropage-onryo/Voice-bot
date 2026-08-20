"""
The bridge: relays live call audio between Twilio (the phone call) and
the bot's brain, and records both the audio and a transcript of the
conversation as it happens.

Since 2026-08-19 the brain is split in two (see ARCHITECTURE.md's
decision log for why):

- LISTENING + REASONING: one OpenAI Realtime connection, exactly as
  before - it hears the call audio (mulaw 8kHz, no conversion), does
  server-side turn detection, keeps the conversation state, and decides
  what the patient says next. The one change: `output_modalities` is
  ["text"], so it now answers in text instead of speaking.
- SPEAKING: ElevenLabs streaming TTS (bridge/tts.py) turns that text
  into mu-law 8kHz audio, which streams straight back into the call.

Flow for one call
------------------
1. place_call.py tells Twilio to dial PGA's test line and, once
   answered, fetch instructions from POST /twiml?scenario=<id>.
2. /twiml returns TwiML telling Twilio to open a bidirectional audio
   stream to /media-stream, with the scenario id riding along as a
   <Parameter> so it arrives inside the stream's own `start` event.
3. The handler waits for that `start` event, loads the scenario, and
   only then connects to OpenAI - the persona has to be known before
   the session can be configured.
4. Incoming call audio (PGA's agent) goes to OpenAI untouched. When
   OpenAI decides the turn is over, it produces the patient's reply as
   text; we log it as transcript, synthesize it via ElevenLabs, and
   stream the audio frames back to Twilio.
5. Barge-in is ours to handle now (the old speech-to-speech setup did
   it internally): when OpenAI's VAD reports the agent started talking
   while our audio is still playing, we cancel the TTS playback task
   and send Twilio a `clear` so the already-buffered audio is flushed
   from the call instead of talking over them.
6. Every audio chunk (both directions) and every transcript line goes
   to a CallRecorder (recorder.py), which writes one mixed MP3 and one
   transcript file at call end.
"""

import asyncio
import base64
import json
import os
import time
from pathlib import Path
from xml.sax.saxutils import quoteattr

import websockets
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from bridge.recorder import CallRecorder
from bridge.tts import ElevenLabsTTS
from scenarios import loader

load_dotenv()

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
REALTIME_MODEL = "gpt-realtime-2.1-mini"
REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = REPO_ROOT / "recordings"
TRANSCRIPTS_DIR = REPO_ROOT / "transcripts"

def _log(call_state: dict, message: str) -> None:
    """Timestamped diagnostic print, relative to call start - added to
    pin down whether dead air comes from our own latency or PGA's
    agent taking a while to respond. See ITERATION_LOG.md 2026-08-19
    "dead air"."""
    elapsed = time.monotonic() - call_state["call_start"]
    print(f"[t={elapsed:6.1f}s] {message}")


app = FastAPI()
tts = ElevenLabsTTS()


@app.post("/twiml")
async def twiml(request: Request) -> PlainTextResponse:
    """Twilio hits this once the call is answered. The <Parameter>
    element makes the scenario id come back to us inside the media
    stream's `start` event (as customParameters), which is the only
    channel Twilio gives us for per-call data on the stream itself."""
    scenario_id = request.query_params.get("scenario", "")
    host = request.url.hostname
    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{host}/media-stream">
            <Parameter name="scenario" value={quoteattr(scenario_id)} />
        </Stream>
    </Connect>
</Response>"""
    return PlainTextResponse(content=twiml_response, media_type="text/xml")


@app.websocket("/media-stream")
async def media_stream(twilio_ws: WebSocket):
    """One of these runs per active call."""
    await twilio_ws.accept()

    # Twilio sends `connected` then `start`; nothing about the call is
    # known until `start` arrives, so consume up to it before touching
    # OpenAI - the scenario decides the session's instructions.
    start_msg = None
    async for raw_message in twilio_ws.iter_text():
        message = json.loads(raw_message)
        if message.get("event") == "start":
            start_msg = message
            break
        if message.get("event") == "stop":
            return
    if start_msg is None:
        return

    scenario_id = (start_msg["start"].get("customParameters") or {}).get("scenario", "")
    try:
        scenario = loader.load_scenario(scenario_id)
    except loader.ScenarioError as exc:
        # place_call.py validates before dialing, so landing here means
        # the stream was started by something else. Refuse loudly rather
        # than running a call with no persona.
        print(f"Refusing call: {exc}")
        await twilio_ws.close()
        return

    call_state = {
        "stream_sid": start_msg["start"]["streamSid"],
        "recorder": CallRecorder(start_msg["start"]["callSid"]),
        "voice": loader.get_voice(scenario),
        "playback_task": None,   # the in-flight ElevenLabs->Twilio stream, if any
        "response_active": False,  # OpenAI response in flight (for response.cancel)
        # Wall-clock (time.monotonic()) estimate of when the bot's own
        # audio finishes actually playing on the call - not when the
        # network transfer to Twilio finishes, which is much earlier
        # (ElevenLabs streams ~12x faster than realtime). Used to avoid
        # replying to noise/echo picked up while we're still mid-line.
        # See ITERATION_LOG.md 2026-08-19 "double response" entries.
        "speaking_until": 0.0,
        # Set when a debounced reply attempt found us busy (response
        # already generating) - so that content isn't silently dropped,
        # just deferred until response.done clears.
        "pending_reply": False,
        # Wall-clock call start, purely for the diagnostic timing logs
        # (see ITERATION_LOG.md 2026-08-19 "dead air") - lets us tell
        # PGA's agent being slow apart from our own code adding latency,
        # which a transcript alone can't distinguish.
        "call_start": time.monotonic(),
        # Bumped every time a new transcription segment arrives; a
        # scheduled debounce wait checks its own captured generation
        # against this before acting; if a newer segment has since come
        # in, that wait is stale and bows out - see _maybe_respond and
        # ITERATION_LOG.md 2026-08-19 "debounce, not a flat timer".
        "debounce_gen": 0,
        # The bot's very first reply of the call gets a longer debounce
        # (see FIRST_TURN_DEBOUNCE_SECONDS) - the call-start disclaimer
        # and language menu commonly have a real gap before the live
        # greeting that's longer than the normal mid-conversation
        # debounce, and replying into that gap is replying to boilerplate
        # instead of the actual greeting. See ITERATION_LOG.md 2026-08-19.
        "first_reply_done": False,
    }

    # Pay the ElevenLabs TLS cold-start now, during the greeting,
    # instead of as dead air before the bot's first line.
    asyncio.create_task(tts.warm_up())

    async with websockets.connect(
        REALTIME_URL,
        additional_headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
    ) as openai_ws:
        # Same session shape as the old speech-to-speech setup, minus
        # audio output: text-only output modality. Input transcription
        # stays on - it is where PGA's agent's transcript comes from.
        await openai_ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": loader.build_instructions(scenario),
                "output_modalities": ["text"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcmu"},
                        "transcription": {"model": "whisper-1"},
                        # Nested under audio.input, not top-level -
                        # older schema, see ITERATION_LOG.md 2026-08-19.
                        # Tuned up from bare defaults (2026-08-19, see
                        # ITERATION_LOG.md "double response after hold
                        # transfer") - the default threshold treated brief
                        # ring/hold-transfer noise as speech and fired an
                        # unwanted response while a real one was still
                        # in flight, which is how two bot turns landed
                        # back-to-back with no agent reply between them.
                        # create_response: False - the default (True) let
                        # OpenAI auto-fire a reply on every VAD-detected
                        # turn boundary, including noise/hold-tone blips
                        # that were never real speech. We now decide
                        # ourselves when to reply, gated on an actual
                        # completed transcription (see
                        # conversation.item.input_audio_transcription.completed
                        # below) - see ITERATION_LOG.md 2026-08-19.
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.6,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 600,
                            "create_response": False,
                        },
                    },
                },
            },
        }))

        try:
            await asyncio.gather(
                _twilio_to_openai(twilio_ws, openai_ws, call_state),
                _openai_to_twilio(twilio_ws, openai_ws, call_state),
            )
        finally:
            _cancel_playback(call_state)


async def _twilio_to_openai(twilio_ws: WebSocket, openai_ws, call_state: dict) -> None:
    """Audio from the call (PGA's agent talking) into OpenAI, with a
    copy to the recorder. Unchanged from the speech-to-speech version:
    mulaw straight through, no conversion."""
    async for raw_message in twilio_ws.iter_text():
        message = json.loads(raw_message)
        event = message.get("event")

        if event == "media":
            await openai_ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": message["media"]["payload"],
            }))
            call_state["recorder"].add_agent_audio(
                base64.b64decode(message["media"]["payload"])
            )

        elif event == "stop":
            recorder = call_state["recorder"]
            paths = recorder.save(RECORDINGS_DIR, TRANSCRIPTS_DIR)
            print(f"Call {recorder.call_sid} saved: {paths}")
            break


async def _openai_to_twilio(twilio_ws: WebSocket, openai_ws, call_state: dict) -> None:
    """Text replies from OpenAI out to the call as ElevenLabs audio,
    plus transcript capture for both sides and barge-in handling."""
    text_buffer = []

    async for raw_message in openai_ws:
        event = json.loads(raw_message)
        event_type = event.get("type")

        if event_type in ("response.output_text.delta", "response.text.delta"):
            # The patient's reply, arriving as text instead of audio.
            # (Two names: GA vs earlier schema - accept either rather
            # than repeating the turn_detection schema surprise.)
            text_buffer.append(event.get("delta", ""))

        elif event_type == "response.created":
            call_state["response_active"] = True

        elif event_type == "response.done":
            call_state["response_active"] = False
            text = "".join(text_buffer).strip()
            text_buffer.clear()
            if not text:
                # Occasional quirk of text-modality sessions: a response
                # with no text deltas. The canonical result lives on the
                # response object, so fall back to digging it out.
                text = _text_from_response(event.get("response", {}))
            if text:
                _log(call_state, f"our response text ready: {text[:60]!r}")
                call_state["recorder"].add_transcript_line("PATIENT (our bot)", text)
                _cancel_playback(call_state)
                call_state["playback_task"] = asyncio.create_task(
                    _speak(text, twilio_ws, call_state)
                )
            if call_state["pending_reply"]:
                # A transcription came in while this response was still
                # generating - it's already part of OpenAI's conversation
                # state (transcribed items aren't lost, just not replied
                # to yet), so ask for that reply now instead of leaving
                # it unanswered.
                call_state["pending_reply"] = False
                call_state["response_active"] = True
                _log(call_state, "response.create sent (deferred, after prior response finished)")
                await openai_ws.send(json.dumps({"type": "response.create"}))

        elif event_type == "input_audio_buffer.speech_started":
            # PGA's agent started talking. Stop any in-flight TTS and
            # flush Twilio's audio buffer unconditionally. It has to be
            # unconditional: ElevenLabs streams ~12x faster than
            # realtime, so a whole reply lands in Twilio's buffer well
            # before it finishes *playing* - the playback task being
            # done says nothing about whether audio is still coming out
            # of the phone. `clear` on an empty buffer is a no-op, so
            # the only cost of always sending it is nothing.
            _cancel_playback(call_state)
            await twilio_ws.send_text(json.dumps({
                "event": "clear",
                "streamSid": call_state["stream_sid"],
            }))
            if call_state["response_active"]:
                await openai_ws.send(json.dumps({"type": "response.cancel"}))
                call_state["response_active"] = False
                text_buffer.clear()

        elif event_type == "conversation.item.input_audio_transcription.completed":
            # PGA's agent's side, transcribed from the audio we heard.
            transcript_text = event.get("transcript", "")
            call_state["recorder"].add_transcript_line("PGA AGENT", transcript_text)
            _log(call_state, f"PGA transcript ready: {transcript_text[:60]!r}")

            # We (not OpenAI) decide when to reply now - see the
            # create_response: False note above. Debounced rather than
            # fired immediately or gated on a flat timer: a fixed grace
            # period was a guess at how long PGA's disclaimer/menu
            # takes, and firing per-segment stacked replies when Whisper
            # splits one utterance into multiple chunks (both logged in
            # ITERATION_LOG.md 2026-08-19). Instead, every new segment
            # resets a short wait; we only actually reply once nothing
            # new has arrived for DEBOUNCE_SECONDS - i.e. once the other
            # side has genuinely stopped talking, whatever that content
            # was and however long it took.
            if transcript_text.strip():
                call_state["debounce_gen"] += 1
                asyncio.create_task(
                    _maybe_respond(call_state, openai_ws, call_state["debounce_gen"])
                )

        elif event_type == "conversation.item.input_audio_transcription.failed":
            # Whisper couldn't transcribe this turn (silence, pure
            # noise, a tone) - correctly not replying to it, rather
            # than the old behavior of auto-replying to anything VAD
            # flagged as speech regardless of content.
            pass

        elif event_type == "error":
            # response_cancel_not_active is expected noise, not a real
            # problem: it fires whenever our local response_active flag
            # says a response is in flight but OpenAI already finished
            # it server-side before our cancel arrived (see
            # ITERATION_LOG.md 2026-08-19). Harmless - don't print it as
            # if something broke, since it never does.
            error_code = event.get("error", {}).get("code")
            if error_code != "response_cancel_not_active":
                print(f"OpenAI Realtime error: {event}")


DEBOUNCE_SECONDS = 1.2  # how long a real pause has to be before we reply
# Longer wait for the bot's first-ever reply on a call - see
# call_state["first_reply_done"] above for why.
FIRST_TURN_DEBOUNCE_SECONDS = 4.0


async def _maybe_respond(call_state: dict, openai_ws, generation: int) -> None:
    """Waits out a debounce; if no newer transcription segment has
    arrived in the meantime (checked via the generation counter) and
    we're clear to speak, sends response.create. This is what actually
    makes the bot wait for the other side to *finish* talking, instead
    of reacting to every individual transcribed fragment or guessing a
    flat number of seconds - see ITERATION_LOG.md 2026-08-19."""
    wait = (
        DEBOUNCE_SECONDS
        if call_state["first_reply_done"]
        else FIRST_TURN_DEBOUNCE_SECONDS
    )
    await asyncio.sleep(wait)
    if call_state["debounce_gen"] != generation:
        return  # superseded by a newer segment; that task owns this now

    # Also wait out our own remaining speaking time, if any - a segment
    # transcribed while we were still mid-line shouldn't spawn a reply
    # on top of it (see the "speaking_until" note in call_state).
    remaining = call_state["speaking_until"] - time.monotonic()
    if remaining > 0:
        await asyncio.sleep(remaining)
        if call_state["debounce_gen"] != generation:
            return

    if call_state["response_active"]:
        # A response is already generating - don't stack a second one
        # on top (that's what garbled the text buffer, see
        # ITERATION_LOG.md). Deferred instead: response.done will fire
        # this once the in-flight one finishes.
        call_state["pending_reply"] = True
        return

    call_state["response_active"] = True
    call_state["first_reply_done"] = True
    _log(call_state, "response.create sent")
    await openai_ws.send(json.dumps({"type": "response.create"}))


async def _speak(text: str, twilio_ws: WebSocket, call_state: dict) -> None:
    """Stream one reply's ElevenLabs audio into the call, recording
    each chunk as the patient's side. Runs as its own task so the event
    loop above stays free to notice a barge-in and cancel it."""
    total_bytes = 0
    try:
        async for chunk in tts.stream(text, call_state["voice"]):
            await twilio_ws.send_text(json.dumps({
                "event": "media",
                "streamSid": call_state["stream_sid"],
                "media": {"payload": base64.b64encode(chunk).decode()},
            }))
            call_state["recorder"].add_patient_audio(chunk)
            total_bytes += len(chunk)
    except asyncio.CancelledError:
        raise  # barge-in - the cancel is the point, don't swallow it
    except Exception as exc:  # noqa: BLE001 - a voiceless turn beats a dead call
        print(f"ElevenLabs TTS failed for one turn: {exc}")
    finally:
        # mulaw 8kHz = 8000 bytes/sec, so bytes sent = seconds of audio
        # Twilio still has queued up to actually play, even though the
        # network transfer above (this loop) already finished. Mark how
        # long from *now* the call will still have our audio coming out
        # of it, so a transcription completing during that window isn't
        # mistaken for a real new turn. See call_state's "speaking_until".
        seconds_of_audio = total_bytes / 8000
        call_state["speaking_until"] = time.monotonic() + seconds_of_audio


def _cancel_playback(call_state: dict) -> None:
    task = call_state.get("playback_task")
    if task and not task.done():
        task.cancel()
    call_state["playback_task"] = None


def _text_from_response(response: dict) -> str:
    """Pull the final text out of a response.done payload: response ->
    output items -> content parts -> text fields."""
    parts = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in ("output_text", "text") and content.get("text"):
                parts.append(content["text"])
    return " ".join(parts).strip()
