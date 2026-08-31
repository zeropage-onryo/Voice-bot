"""
Shared ElevenLabs Conversational AI client construction and account-side
helpers.

Factored out of place_call.py / setup_agent.py / fetch_conversation.py,
which each built their own ElevenLabs(api_key=...) client and re-derived
the same "did .env forget something" checks inline. That duplication was
harmless while this project only placed outbound test calls, but it stops
being harmless the moment a second, unrelated calling path exists - a
live inbound business agent needs the exact same authenticated client and
the exact same fail-fast behavior, and copy-pasting it again is how the
two paths quietly drift (one gets a fix or a clearer error message, the
other doesn't). This module is the one place both depend on instead.

Deliberately NOT here: anything about *what* an agent says or *which*
call it's on. A patient-persona testing agent and a business receptionist
agent build very different AgentConfig objects - that stays with each
caller. This module only owns "do we have credentials, and give me a
client / give me a Twilio number imported into ElevenLabs."
"""

import os

from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.phone_numbers.types.phone_numbers_create_request_body import (
    PhoneNumbersCreateRequestBody_Twilio,
)


class ConfigError(SystemExit):
    """Missing required .env configuration.

    Subclasses SystemExit so a caller can just let it propagate: it exits
    the CLI with a clean one-line message instead of a traceback, exactly
    like the checks it replaces did.
    """


def require_env(*names: str) -> None:
    """Fail fast if any of `names` is unset or empty in the environment.

    Call this before any network traffic, same discipline place_call.py
    already used for scenario/voice validation: a missing credential must
    never get as far as dialing a real phone line.
    """
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise ConfigError(
            f"Missing in .env: {', '.join(missing)}. "
            "Run `python setup_agent.py` once to create the agent and "
            "import the Twilio number (needs ELEVENLABS_API_KEY set)."
        )


def get_client() -> ElevenLabs:
    """Build the one ElevenLabs client every script in this project uses."""
    require_env("ELEVENLABS_API_KEY")
    return ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])


def import_twilio_number(client: ElevenLabs, *, phone_number: str, label: str) -> str:
    """Import a Twilio number into ElevenLabs so their platform can place
    or receive calls on it directly.

    Shared because both the outbound testing agent (setup_agent.py today)
    and any future inbound business agent need this exact call - the only
    difference between them is the label, which the caller supplies.
    """
    require_env("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")
    phone = client.conversational_ai.phone_numbers.create(
        request=PhoneNumbersCreateRequestBody_Twilio(
            phone_number=phone_number,
            label=label,
            sid=os.environ["TWILIO_ACCOUNT_SID"],
            token=os.environ["TWILIO_AUTH_TOKEN"],
        ),
    )
    return phone.phone_number_id
