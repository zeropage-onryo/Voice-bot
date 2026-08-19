"""
Entry point: places one outbound call from our Twilio number to PGA's
test line, and tells Twilio where to fetch the bridge's TwiML from so
the live audio stream can start.

Usage:
    python place_call.py --scenario scheduling_simple
    python place_call.py --list

This script's only job is to *start* the call. Everything about how the
call actually sounds and behaves - the persona, the audio bridging, the
turn-taking - lives in bridge/server.py, which must already be running
and reachable at PUBLIC_BASE_URL (e.g. via `ngrok http 8000`) before
this script is run. Kept deliberately separate so each piece can be
understood on its own: this file is "dial the phone," the bridge is
"have the conversation."

The scenario id rides along as a query parameter on the /twiml URL;
the bridge echoes it into the media stream's <Parameter> so the
WebSocket handler knows which persona this specific call uses.
"""

import argparse
import os
from urllib.parse import quote

from dotenv import load_dotenv
from twilio.rest import Client

from scenarios import loader

load_dotenv()

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_PHONE_NUMBER = os.environ["TWILIO_PHONE_NUMBER"]
PGA_TEST_LINE = os.environ["PGA_TEST_LINE"]
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")


def place_call(scenario_id: str) -> str:
    # Fail fast, before any Twilio traffic: a bad scenario id or a bad
    # voice id must never get as far as dialing PGA's real test line.
    try:
        scenario = loader.load_scenario(scenario_id)
    except loader.ScenarioError as exc:
        raise SystemExit(f"error: {exc}") from exc

    if not PUBLIC_BASE_URL:
        raise SystemExit(
            "PUBLIC_BASE_URL is not set in .env - the bridge server needs "
            "to be running and publicly reachable (e.g. `ngrok http 8000`, "
            "then paste that URL into .env) before a call can be placed."
        )
    if not os.environ.get("ELEVENLABS_API_KEY"):
        raise SystemExit(
            "ELEVENLABS_API_KEY is not set in .env - the bridge's TTS layer "
            "needs it, and a call placed without it would connect and then "
            "have no voice."
        )

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    call = client.calls.create(
        to=PGA_TEST_LINE,
        from_=TWILIO_PHONE_NUMBER,
        url=f"{PUBLIC_BASE_URL}/twiml?scenario={quote(scenario_id)}",
        method="POST",
    )

    print(f"Call placed. SID: {call.sid} | scenario: {scenario_id} ({scenario['description']})")
    return call.sid


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Place a test call to PGA's line.")
    parser.add_argument(
        "--scenario",
        help="Which caller scenario/persona to use for this call (see scenarios/).",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available scenarios and exit (places no call).",
    )
    args = parser.parse_args()

    if args.list:
        for sid in loader.available_scenarios():
            print(f"  {sid:40} {loader.load_scenario(sid)['description']}")
        raise SystemExit(0)

    if not args.scenario:
        parser.error("--scenario is required (or use --list to see what exists)")
    place_call(args.scenario)
