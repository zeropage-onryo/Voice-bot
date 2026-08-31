"""
Target profile loading + validation.

A "target" is the agent under test - who we're calling and what we
already know about it. Before this, that was implicit: one project, one
target (PGA), its name and phone number hardcoded wherever they were
needed. Testing more than one agent is the whole point of generalizing
this harness, so the target stops being implicit and becomes a file,
mirroring exactly how scenarios/loader.py treats personas: a JSON file
per target in this directory, validated eagerly so a bad target id or a
malformed profile fails before anything dials, not partway through a
report that silently used the wrong canonical name.

Shape of a target file
-----------------------
    {
      "slug": "pga",
      "label": "human-readable name, shown in reports",
      "phone": "+1...",
      "canonical_names": ["every correct spelling/name a check should accept"],
      "domain": "healthcare_intake",
      "closing_phrases": ["exact or near-exact strings the agent's own goodbye uses"],
      "consent": "how this target was authorized for testing - see the
                  operating note in BUILD_SPEC_EVAL_LAYER.md before adding one",
      "notes": "anything a human running the suite against this target needs to know"
    }

`canonical_names` and `closing_phrases` exist so checks like
`brand_name_misstated` never hardcode one target's name inline - the
check reads them from whichever target profile a run says it's testing.
"""

import json
from pathlib import Path

TARGETS_DIR = Path(__file__).resolve().parent

REQUIRED_FIELDS = ("slug", "label", "phone", "canonical_names", "domain", "consent")


class TargetError(Exception):
    """Raised for any bad target file or bad target slug.

    A distinct type so callers can catch target problems specifically,
    the same way ScenarioError is kept separate from a generic exception.
    """


def available_targets() -> list[str]:
    """Every target slug on disk, sorted."""
    return sorted(p.stem for p in TARGETS_DIR.glob("*.json"))


def load_target(slug: str) -> dict:
    """Load and validate one target profile. Raises TargetError on
    anything wrong - unknown slug, malformed JSON, missing/malformed
    field."""
    path = TARGETS_DIR / f"{slug}.json"
    if not path.exists():
        known = available_targets()
        raise TargetError(
            f"Unknown target {slug!r}.\n"
            f"Available targets ({len(known)}):\n  " + "\n  ".join(known)
        )

    try:
        target = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise TargetError(f"{path.name} is not valid JSON: {exc}") from exc

    if not isinstance(target, dict):
        raise TargetError(f"{path.name} must contain a JSON object, got {type(target).__name__}.")

    missing = [f for f in REQUIRED_FIELDS if not target.get(f)]
    if missing:
        raise TargetError(f"{path.name} is missing required field(s): {', '.join(missing)}")

    if target["slug"] != slug:
        raise TargetError(
            f'{path.name} has "slug": {target["slug"]!r} but its filename says {slug!r}. '
            "These must match, since the filename is what --target takes."
        )

    if not isinstance(target["canonical_names"], list) or not all(
        isinstance(n, str) and n.strip() for n in target["canonical_names"]
    ):
        raise TargetError(f'{path.name}: "canonical_names" must be a non-empty list of strings.')

    closing_phrases = target.get("closing_phrases", [])
    if not isinstance(closing_phrases, list) or not all(isinstance(p, str) for p in closing_phrases):
        raise TargetError(f'{path.name}: "closing_phrases", if present, must be a list of strings.')

    return target
