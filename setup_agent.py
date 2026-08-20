"""
One-time ElevenLabs setup for this project. Run once, then never again
(it refuses to run if .env already has the ids it would create).

Creates the two account-side objects the new architecture needs:

1. The agent - the "patient caller" itself, hosted by ElevenLabs:
   GPT-4o as the reasoning LLM, telephone audio (ulaw_8000) both ways,
   an EMPTY first message (PGA's line speaks first - recorded
   disclaimer, then a live greeting - so the bot must wait and respond,
   never open), and per-call overrides enabled for exactly the two
   fields the scenario system injects: the system prompt and the voice.
   Overrides are disabled by default on ElevenLabs' side for security,
   so enabling them here is what makes place_call.py's
   conversation_config_override work at all.

2. The phone number - imports the existing Twilio number into
   ElevenLabs so their platform can place calls from it directly
   (this replaces our own twilio.rest usage entirely).

Both ids are appended to .env when done.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from elevenlabs import ElevenLabs
from elevenlabs.types import (
    AgentConfig,
    AgentConfigOverrideConfig,
    AgentPlatformSettingsRequestModel,
    AsrConversationalConfig,
    ConversationalConfig,
    ConversationConfigClientOverrideConfigInput,
    ConversationInitiationClientDataConfigInput,
    PromptAgentApiModelOverrideConfig,
    TtsConversationalConfigOverrideConfig,
)
from elevenlabs.conversational_ai.phone_numbers.types.phone_numbers_create_request_body import (
    PhoneNumbersCreateRequestBody_Twilio,
)

from scenarios import loader

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH)

AGENT_NAME = "PGA patient caller"


def main() -> None:
    for var in ("ELEVENLABS_AGENT_ID", "ELEVENLABS_AGENT_PHONE_NUMBER_ID"):
        if os.environ.get(var):
            raise SystemExit(
                f"{var} is already set in .env - setup has already run. "
                "Delete the line (and the objects in the ElevenLabs "
                "dashboard) if you really want to recreate them."
            )

    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

    # The prompt set here is a placeholder: every real call overrides it
    # with the scenario's build_instructions(). It exists so that if an
    # override ever silently fails to apply, the call is obviously wrong
    # (the bot announces it has no scenario) instead of subtly wrong.
    agent = client.conversational_ai.agents.create(
        name=AGENT_NAME,
        conversation_config=ConversationalConfig(
            agent=AgentConfig(
                first_message="",  # empty = wait for the other side to speak first
                language="en",
                prompt={
                    "prompt": (
                        "You are a placeholder persona. If you are hearing this, "
                        "a per-call scenario override failed to apply - say only: "
                        "'Test call misconfigured, please hang up.'"
                    ),
                    "llm": "gpt-4o",
                },
            ),
            tts={
                "model_id": "eleven_flash_v2",
                "voice_id": loader.DEFAULT_VOICE,
                "agent_output_audio_format": "ulaw_8000",
            },
            asr=AsrConversationalConfig(user_input_audio_format="ulaw_8000"),
        ),
        platform_settings=AgentPlatformSettingsRequestModel(
            overrides=ConversationInitiationClientDataConfigInput(
                conversation_config_override=ConversationConfigClientOverrideConfigInput(
                    agent=AgentConfigOverrideConfig(
                        prompt=PromptAgentApiModelOverrideConfig(prompt=True),
                    ),
                    tts=TtsConversationalConfigOverrideConfig(voice_id=True, speed=True),
                ),
            ),
        ),
    )
    print(f"Agent created: {agent.agent_id} ({AGENT_NAME})")

    phone = client.conversational_ai.phone_numbers.create(
        request=PhoneNumbersCreateRequestBody_Twilio(
            phone_number=os.environ["TWILIO_PHONE_NUMBER"],
            label="PGA voice bot (Twilio)",
            sid=os.environ["TWILIO_ACCOUNT_SID"],
            token=os.environ["TWILIO_AUTH_TOKEN"],
        ),
    )
    print(f"Twilio number imported: {phone.phone_number_id}")

    with ENV_PATH.open("a") as f:
        f.write(
            "\n# ElevenLabs Agents platform (created by setup_agent.py)\n"
            f"ELEVENLABS_AGENT_ID={agent.agent_id}\n"
            f"ELEVENLABS_AGENT_PHONE_NUMBER_ID={phone.phone_number_id}\n"
        )
    print(f"Both ids appended to {ENV_PATH}")


if __name__ == "__main__":
    sys.exit(main())
