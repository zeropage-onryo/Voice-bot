"""
Entry point: places one outbound call from our Twilio number to PGA's
test line, then (by default) waits for it to finish and pulls down the
recording + transcript.

Usage:
    python place_call.py --scenario scheduling_simple
    python place_call.py --scenario scheduling_simple --no-wait
    python place_call.py --list

Since 2026-08-19 the call itself runs entirely on ElevenLabs' Agents
platform (see ARCHITECTURE.md's decision log): their API tells Twilio
to dial, their agent (GPT-4o reasoning + the scenario's ElevenLabs
voice) holds the conversation with platform-native turn-taking and
barge-in, and their side records it. There is no local bridge server,
no ngrok, and nothing to run before this script - the scenario's whole
persona travels in the per-call override below.
"""

import argparse
import os

from dotenv import load_dotenv
from elevenlabs import ElevenLabs

import fetch_conversation
from scenarios import loader

load_dotenv()

PGA_TEST_LINE = os.environ["PGA_TEST_LINE"]


def place_call(scenario_id: str, wait: bool = True) -> str:
    # Fail fast, before any network traffic: a bad scenario id or a bad
    # voice id must never get as far as dialing PGA's real test line.
    try:
        scenario = loader.load_scenario(scenario_id)
    except loader.ScenarioError as exc:
        raise SystemExit(f"error: {exc}") from exc

    missing = [v for v in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID",
                           "ELEVENLABS_AGENT_PHONE_NUMBER_ID") if not os.environ.get(v)]
    if missing:
        raise SystemExit(
            f"Missing in .env: {', '.join(missing)}. "
            "Run `python setup_agent.py` once to create the agent and "
            "import the Twilio number (needs ELEVENLABS_API_KEY set)."
        )

    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    response = client.conversational_ai.twilio.outbound_call(
        agent_id=os.environ["ELEVENLABS_AGENT_ID"],
        agent_phone_number_id=os.environ["ELEVENLABS_AGENT_PHONE_NUMBER_ID"],
        to_number=PGA_TEST_LINE,
        call_recording_enabled=True,
        # The whole scenario system plugs in right here: the persona
        # becomes the agent's system prompt for this one call, and the
        # scenario's voice id becomes its voice. Deliberately no
        # first_message override - PGA's line speaks first (disclaimer,
        # then greeting) and the agent is configured to wait for it.
        conversation_initiation_client_data={
            "conversation_config_override": {
                "agent": {
                    "prompt": {"prompt": loader.build_instructions(scenario)},
                },
                "tts": {
                    "voice_id": loader.get_voice(scenario),
                    "speed": loader.get_speed(scenario),
                },
            },
        },
    )

    if not response.success or not response.conversation_id:
        raise SystemExit(f"ElevenLabs did not start the call: {response.message!r}")

    print(
        f"Call placed. conversation: {response.conversation_id} | "
        f"callSid: {response.call_sid or '?'} | "
        f"scenario: {scenario_id} ({scenario['description']})"
    )

    if wait:
        # Name the files by Twilio call SID when we have one, matching
        # the CAxxxx naming of every recording already in the repo.
        fetch_conversation.fetch(response.conversation_id, file_stem=response.call_sid)
    else:
        print(f"Fetch later with: python fetch_conversation.py {response.conversation_id}"
              + (f" --name {response.call_sid}" if response.call_sid else ""))
    return response.conversation_id


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
    parser.add_argument(
        "--no-wait", action="store_true",
        help="Place the call and exit instead of waiting to download the "
             "recording/transcript.",
    )
    args = parser.parse_args()

    if args.list:
        for sid in loader.available_scenarios():
            print(f"  {sid:40} {loader.load_scenario(sid)['description']}")
        raise SystemExit(0)

    if not args.scenario:
        parser.error("--scenario is required (or use --list to see what exists)")
    place_call(args.scenario, wait=not args.no_wait)
