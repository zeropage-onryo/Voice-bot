"""
Scenario loading + validation.

A "scenario" is one test case: who is calling, what they want, and how
they sound. Each lives as a JSON file in this directory so a new test
case is a data change, not a code change - the scenarios are the
highest-frequency edit surface in this project.

The loader's job is to fail *fast and loudly*. A bad scenario id or a
bad voice id must stop the process before `place_call.py` dials PGA's
line, because a call placed with a broken persona still costs a real
phone call to a real test line and still has to be listened to before
anyone notices it was wrong.

Shape of a scenario file
------------------------
    {
      "id": "scheduling_simple",
      "description": "one-line summary, shown in --list",
      "goal": "who the caller is and what they're trying to achieve",
      "voice": "<ElevenLabs voice id>",
      "voice_direction": "how they sound - pacing, energy, hesitancy",
      "speed": 0.85
    }

`voice_direction` shapes wording/pacing through the prompt; the optional
`speed` field (default 1.0) changes actual TTS playback rate - prompt
direction alone can't make the synthesized voice physically slower.

`goal` and `voice_direction` are joined with the shared guardrails in
`build_instructions()` to make the system prompt for the reasoning step.
"""

import json
from pathlib import Path

SCENARIOS_DIR = Path(__file__).resolve().parent

# Curated shortlist of ElevenLabs voice ids, mirroring the previous
# approach of validating against a fixed set rather than hitting the
# voice-list API on every call.
#
# Two reasons this is a hardcoded shortlist and not a live API lookup:
# a network call in the fail-fast path would mean an outage turns "bad
# voice id" into "can't place any call at all", and pinning the set
# means a scenario's voice can't silently change under it when the
# account's voice library is edited.
#
# Verified live against GET /v2/voices on 2026-08-19. Labels are that
# endpoint's own metadata, kept here because picking a voice for a
# persona is the whole point of the list.
VALID_VOICES = {
    "hpp4J3VqNfWAUOO0d1Us": "Bella - female, middle-aged, american, professional",
    "CwhRBWXzGAHq8TQ4Fs17": "Roger - male, middle-aged, american, laid-back",
    "EXAVITQu4vr4xnSDxMaL": "Sarah - female, young, american, reassuring",
    "FGY2WhTYpPnrIDTdsKH5": "Laura - female, young, american, sassy",
    "IKne3meq5aSn9XLyUdCD": "Charlie - male, young, australian, energetic",
    "JBFqnCBsd6RMkjVDRZzb": "George - male, middle-aged, british, warm",
    "N2lVS1w4EtoT3dr4eOWO": "Callum - male, middle-aged, american, husky",
    "SAz9YHcvj6GT2YYXdXww": "River - neutral, middle-aged, american, calm",
    "TX3LPaxmHKxFdv7VOQHJ": "Liam - male, young, american, confident",
    "Xb7hH8MSUJpSbSDYk0k2": "Alice - female, middle-aged, british, clear",
    "XrExE9yKIg1WjnnlVkGX": "Matilda - female, middle-aged, american, upbeat",
    "bIHbv24MWmeRgasZH58o": "Will - male, young, american, chill",
    "cgSgspJ2msm6clMCkdW9": "Jessica - female, young, american, playful",
    "cjVigY5qzO86Huf0OWal": "Eric - male, middle-aged, american, smooth",
    "iP95p4xoKVk53GoZ742B": "Chris - male, middle-aged, american, casual",
    "nPczCjzI2devNBz1zQrb": "Brian - male, middle-aged, american, deep",
    "onwK4e9ZLuTAKqWW03F9": "Daniel - male, middle-aged, british, formal",
    "pFZP5JQG7iQjIQuC4Bku": "Lily - female, middle-aged, british, confident",
    "pNInz6obpgDQGcFmaJgB": "Adam - male, middle-aged, american, firm",
    "pqHfZKP75CvOlQylNhV4": "Bill - male, OLDER, american, wise/mature",
}

DEFAULT_VOICE = "iP95p4xoKVk53GoZ742B"  # Chris - neutral conversational default

DEFAULT_SPEED = 1.0
# ElevenLabs accepts roughly 0.7 (slower) to 1.2 (faster); 1.0 is normal pace.
SPEED_RANGE = (0.7, 1.2)

REQUIRED_FIELDS = ("id", "description", "goal")

# Guardrails appended to every persona. These are not decoration: the
# language-drift and role-drift lines are here because both actually
# happened on a real call (see ITERATION_LOG.md, 2026-08-19) - the bot
# answered its first turn entirely in Spanish and then briefly spoke as
# though it were the office rather than the caller.
SHARED_GUARDRAILS = """
HOW TO SPEAK
- Speak naturally, like a real person on the phone: short sentences,
  contractions, the occasional filler word. You are not reading a script.
- Keep each turn brief. Real callers say one thing at a time and wait.
- Never narrate stage directions or describe your own tone out loud.

HARD RULES - these override anything above
- Always speak English, regardless of what language you hear or think you
  hear. Never switch languages, not even for a single word or greeting.
- You are ALWAYS the person who placed this call. Never speak as the
  office, the receptionist, or the agent answering. If you catch yourself
  starting to answer as the office, stop and respond as the caller.
- Never mention that you are an AI, a test, a simulation, or a bot, and
  never break character, even if you are asked directly.
- If the call reaches a natural end, say a brief goodbye and stop talking
  rather than inventing new business to keep it going.
""".strip()


class ScenarioError(Exception):
    """Raised for any bad scenario file or bad scenario id.

    A distinct type so callers can catch scenario problems specifically
    rather than swallowing every exception on the way to placing a call.
    """


def available_scenarios() -> list[str]:
    """Every scenario id on disk, sorted. Used for --list and to build
    the 'did you mean' part of the error on an unknown id."""
    return sorted(p.stem for p in SCENARIOS_DIR.glob("*.json"))


def load_scenario(scenario_id: str) -> dict:
    """Load and validate one scenario. Raises ScenarioError on anything
    wrong - unknown id, malformed JSON, missing field, unknown voice."""
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        known = available_scenarios()
        raise ScenarioError(
            f"Unknown scenario {scenario_id!r}.\n"
            f"Available scenarios ({len(known)}):\n  "
            + "\n  ".join(known)
        )

    try:
        scenario = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"{path.name} is not valid JSON: {exc}") from exc

    if not isinstance(scenario, dict):
        raise ScenarioError(f"{path.name} must contain a JSON object, got {type(scenario).__name__}.")

    missing = [f for f in REQUIRED_FIELDS if not str(scenario.get(f, "")).strip()]
    if missing:
        raise ScenarioError(f"{path.name} is missing required field(s): {', '.join(missing)}")

    # The filename is the id that gets typed on the command line, so a
    # mismatch between it and the "id" field means one of them is a lie.
    if scenario["id"] != scenario_id:
        raise ScenarioError(
            f'{path.name} has "id": {scenario["id"]!r} but its filename says {scenario_id!r}. '
            "These must match, since the filename is what --scenario takes."
        )

    # Validate voice and speed here, at load time, rather than at the
    # point of use. Bad values must stop the run before the phone rings.
    get_voice(scenario)
    get_speed(scenario)
    return scenario


def get_voice(scenario: dict) -> str:
    """The ElevenLabs voice id for this scenario, validated against the
    curated shortlist. Scenarios may omit "voice" and get the default."""
    voice = scenario.get("voice") or DEFAULT_VOICE
    if voice not in VALID_VOICES:
        raise ScenarioError(
            f"Scenario {scenario.get('id')!r} requests unknown voice {voice!r}.\n"
            "Valid ElevenLabs voice ids:\n  "
            + "\n  ".join(f"{vid}  {label}" for vid, label in sorted(VALID_VOICES.items(), key=lambda kv: kv[1]))
        )
    return voice


def get_speed(scenario: dict) -> float:
    """TTS playback speed for this scenario, validated against the range
    ElevenLabs actually accepts. Optional; omitted means normal pace."""
    speed = scenario.get("speed", DEFAULT_SPEED)
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not (
        SPEED_RANGE[0] <= speed <= SPEED_RANGE[1]
    ):
        raise ScenarioError(
            f'{scenario.get("id")!r}: "speed" must be a number between '
            f"{SPEED_RANGE[0]} and {SPEED_RANGE[1]}, got {speed!r}."
        )
    return float(speed)


def build_instructions(scenario: dict) -> str:
    """The full system prompt for the reasoning step: who this caller is,
    how they sound, and the guardrails that apply to every persona."""
    parts = [
        "You are a patient calling a medical office on the telephone. "
        "Stay in this role for the entire call.",
        "",
        "WHO YOU ARE AND WHAT YOU WANT",
        scenario["goal"].strip(),
    ]

    direction = str(scenario.get("voice_direction", "")).strip()
    if direction:
        parts += ["", "HOW YOU COME ACROSS", direction]

    parts += ["", SHARED_GUARDRAILS]
    return "\n".join(parts)
