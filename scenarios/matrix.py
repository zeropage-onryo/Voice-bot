"""
Scenario matrix: composes a domain-specific "intent" (what the caller
wants) with a domain-agnostic "behavior" (how they act while getting it)
into a full scenario dict, in the exact shape scenarios/loader.py expects.

This is the generalization step referenced in the platform plan doc
(claude/voice-bot-platform-plan.md): the 12 hand-written scenarios in
scenarios/*.json are excellent but monolithic - each one bakes a
caller's goal and their manner of speaking into a single file, so every
new combination (an interrupting caller who wants a refill, a frustrated
caller with a vague symptom) means writing a whole new file by hand.
Splitting those two concerns into primitives means N intents x M
behaviors gives N*M scenarios for free, and a brand new target domain
only has to write its own intents/<domain>/*.json - it reuses every
existing behavior for free too.

This split falls directly out of how scenarios/loader.py already treats
a scenario: `goal` ("WHO YOU ARE AND WHAT YOU WANT") and `voice_direction`
("HOW YOU COME ACROSS") are already two separate prompt sections
(build_instructions). An intent primitive supplies goal_template
verbatim as `goal`; a behavior primitive supplies voice_direction (and
optionally voice/speed) verbatim. Composing them is not clever text
blending - it's just filling in two fields that were already meant to
be independent.

What this deliberately does NOT do: decompose every existing scenario.
wrong_number_confusion and after_hours_urgent stay hand-written, because
in both of them the "behavior" (confused, then sheepish; tense,
controlled urgency) only makes sense as a reaction to that exact
situation - there's no reusable "off-domain caller" or "medical
emergency" behavior that would compose sensibly with a different
intent. Forcing every scenario through the matrix would produce a
worse, more artificial persona than just writing the edge case by hand.
The matrix is for the combinatorial middle, not every scenario that
exists.

Shape of a behavior primitive (scenarios/primitives/behaviors/<id>.json)
--------------------------------------------------------------------
    {"id": ..., "description": ..., "voice_direction": ...,
     "voice": <optional>, "speed": <optional>}

Shape of an intent primitive (scenarios/primitives/intents/<domain>/<id>.json)
--------------------------------------------------------------------
    {"id": ..., "domain": ..., "description": ..., "goal_template": ...}

An intent's `domain` field must match the healthcare_intake, etc.
subdirectory it lives in - the same "filename/id must agree with itself"
discipline scenarios/loader.py already applies to scenario files.
"""

from __future__ import annotations

import json
from itertools import product
from pathlib import Path

from scenarios import loader as scenario_loader

PRIMITIVES_DIR = Path(__file__).resolve().parent / "primitives"
BEHAVIORS_DIR = PRIMITIVES_DIR / "behaviors"
INTENTS_DIR = PRIMITIVES_DIR / "intents"

REQUIRED_BEHAVIOR_FIELDS = ("id", "description", "voice_direction")
REQUIRED_INTENT_FIELDS = ("id", "domain", "description", "goal_template")


class MatrixError(Exception):
    """A bad primitive file, an unknown intent/behavior/domain, or a
    composed scenario that would fail scenarios/loader.py's own
    validation (bad voice id, out-of-range speed). Same fail-fast
    discipline as ScenarioError, TargetError, RubricError."""


def available_behaviors() -> list[str]:
    return sorted(p.stem for p in BEHAVIORS_DIR.glob("*.json"))


def available_domains() -> list[str]:
    return sorted(p.name for p in INTENTS_DIR.iterdir() if p.is_dir())


def available_intents(domain: str) -> list[str]:
    domain_dir = INTENTS_DIR / domain
    if not domain_dir.is_dir():
        raise MatrixError(
            f"Unknown domain {domain!r}. Available: {available_domains()}"
        )
    return sorted(p.stem for p in domain_dir.glob("*.json"))


def load_behavior(behavior_id: str) -> dict:
    """Load and validate one behavior primitive. Raises MatrixError on
    anything wrong."""
    path = BEHAVIORS_DIR / f"{behavior_id}.json"
    if not path.exists():
        raise MatrixError(
            f"Unknown behavior {behavior_id!r}. Available: {available_behaviors()}"
        )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise MatrixError(f"{path.name} is not valid JSON: {exc}") from exc

    missing = [f for f in REQUIRED_BEHAVIOR_FIELDS if not str(data.get(f, "")).strip()]
    if missing:
        raise MatrixError(f"{path.name} is missing required field(s): {', '.join(missing)}")
    if data["id"] != behavior_id:
        raise MatrixError(
            f'{path.name} has "id": {data["id"]!r} but its filename says {behavior_id!r}.'
        )
    return data


def load_intent(domain: str, intent_id: str) -> dict:
    """Load and validate one intent primitive. Raises MatrixError on
    anything wrong, including a domain field that disagrees with the
    directory it was found in."""
    if not (INTENTS_DIR / domain).is_dir():
        raise MatrixError(
            f"Unknown domain {domain!r}. Available: {available_domains()}"
        )
    path = INTENTS_DIR / domain / f"{intent_id}.json"
    if not path.exists():
        raise MatrixError(
            f"Unknown intent {intent_id!r} for domain {domain!r}. "
            f"Available: {available_intents(domain)}"
        )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise MatrixError(f"{path.name} is not valid JSON: {exc}") from exc

    missing = [f for f in REQUIRED_INTENT_FIELDS if not str(data.get(f, "")).strip()]
    if missing:
        raise MatrixError(f"{path.name} is missing required field(s): {', '.join(missing)}")
    if data["id"] != intent_id:
        raise MatrixError(
            f'{path.name} has "id": {data["id"]!r} but its filename says {intent_id!r}.'
        )
    if data["domain"] != domain:
        raise MatrixError(
            f'{path.name} has "domain": {data["domain"]!r} but lives under '
            f"intents/{domain}/. These must match."
        )
    return data


def compose(domain: str, intent_id: str, behavior_id: str) -> dict:
    """Combine one intent and one behavior into a full scenario dict,
    validated against the exact rules scenarios/loader.py enforces
    (required fields, known voice id, in-range speed) before this ever
    touches disk. Raises MatrixError (not ScenarioError) on a bad
    combination, so a caller can tell "a primitive itself is broken"
    apart from "a real, hand-written scenario is broken".
    """
    intent = load_intent(domain, intent_id)
    behavior = load_behavior(behavior_id)

    scenario = {
        "id": f"{intent_id}__{behavior_id}",
        "description": f"{intent['description']} ({behavior['description']})",
        "goal": intent["goal_template"],
        "voice_direction": behavior["voice_direction"],
    }
    if "voice" in behavior:
        scenario["voice"] = behavior["voice"]
    if "speed" in behavior:
        scenario["speed"] = behavior["speed"]

    _validate_composed(scenario)
    return scenario


def _validate_composed(scenario: dict) -> None:
    """Re-runs scenarios/loader.py's own field/voice/speed checks against
    a composed (not-yet-written) scenario dict, translating any failure
    into a MatrixError. Deliberately does not touch disk or call
    scenario_loader.load_scenario directly - that function reads from
    scenario_loader.SCENARIOS_DIR by path, and a composed scenario may
    not be written there (or anywhere) yet.
    """
    missing = [f for f in scenario_loader.REQUIRED_FIELDS if not str(scenario.get(f, "")).strip()]
    if missing:
        raise MatrixError(
            f"Composed scenario {scenario['id']!r} is missing required field(s): "
            f"{', '.join(missing)}"
        )
    try:
        scenario_loader.get_voice(scenario)
        scenario_loader.get_speed(scenario)
    except scenario_loader.ScenarioError as exc:
        raise MatrixError(f"Composed scenario {scenario['id']!r} is invalid: {exc}") from exc


def full_matrix(domain: str) -> list[dict]:
    """Every intent x behavior combination for `domain`, composed and
    validated. The Cartesian product, not a curated subset - trimming
    down to "the interesting combinations" is a job for whoever calls
    this, not something baked into the matrix itself.
    """
    return [
        compose(domain, intent_id, behavior_id)
        for intent_id, behavior_id in product(available_intents(domain), available_behaviors())
    ]


def write_scenario(
    scenario: dict, *, scenarios_dir: Path = scenario_loader.SCENARIOS_DIR, overwrite: bool = False
) -> Path:
    """Write a composed scenario to `scenarios_dir/<id>.json`. Refuses to
    overwrite an existing file unless `overwrite=True` - a matrix
    combination landing on the same id as one of the 12 hand-written
    scenarios should stop loudly, not silently clobber curated,
    already-validated persona work.
    """
    path = scenarios_dir / f"{scenario['id']}.json"
    if path.exists() and not overwrite:
        raise MatrixError(
            f"{path} already exists. Pass overwrite=True to replace it, "
            "or this is likely an id collision with a hand-written scenario."
        )
    path.write_text(json.dumps(scenario, indent=2) + "\n")
    return path


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="List or materialize intent x behavior scenario combinations."
    )
    parser.add_argument("--domain", required=True, help="Intent domain, e.g. healthcare_intake.")
    parser.add_argument("--list", action="store_true", help="List every composed scenario id.")
    parser.add_argument(
        "--write", nargs=2, metavar=("INTENT", "BEHAVIOR"), help="Write one combination to disk."
    )
    parser.add_argument(
        "--write-all", action="store_true", help="Write every combination not already on disk."
    )
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting existing files.")
    args = parser.parse_args()

    if args.write:
        intent_id, behavior_id = args.write
        scenario = compose(args.domain, intent_id, behavior_id)
        path = write_scenario(scenario, overwrite=args.overwrite)
        print(f"Wrote {path}")
        return

    matrix = full_matrix(args.domain)

    if args.write_all:
        written, skipped = 0, 0
        for scenario in matrix:
            try:
                write_scenario(scenario, overwrite=args.overwrite)
                written += 1
            except MatrixError:
                skipped += 1
        print(f"Wrote {written} scenario(s), skipped {skipped} already on disk.")
        return

    # Default (and explicit --list): print without writing anything.
    for scenario in matrix:
        print(f"{scenario['id']:50} {scenario['description']}")


if __name__ == "__main__":
    main()
