"""
Transcript parsing.

Turns the `[SPEAKER] text` files fetch_conversation.py writes into the
one small, neutral data model every check and rubric in this package
works against. This module is the single place that knows the literal
speaker-label strings that appear in a transcript file; nothing
downstream may hardcode them (see BUILD_SPEC_EVAL_LAYER.md sec. 3.3 -
"It is forbidden to reference a call's filename, stem, scenario id or
ordinal to decide an outcome" extends to speaker labels too: a check
that special-cases the string "PGA AGENT" only works for one target).

Two labels exist in every transcript today, both defined in
fetch_conversation.SPEAKER_LABELS: "PATIENT (our bot)" is our own
persona, "PGA AGENT" is the agent under test. That second label is
frozen PGA-specific wording baked into fetch_conversation.py itself
(a file BUILD_SPEC_EVAL_LAYER.md's hard constraints forbid touching),
so it will keep appearing verbatim in every transcript file this
project writes, regardless of which real-world target a future call
actually tests. That's a known wart, not a bug this module should paper
over: DEFAULT_LABEL_TO_SPEAKER maps the two literal strings that exist
today to neutral roles, and a target profile may override the mapping
(via `label_to_speaker=`) if a later target's calls are ever produced
some other way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Speaker(Enum):
    CALLER = "caller"  # our own persona - the scenario system's voice
    AGENT_UNDER_TEST = "agent_under_test"  # whoever we're testing


# The literal strings fetch_conversation.SPEAKER_LABELS writes today.
# Kept as plain strings here (not an import of that dict) because this
# mapping is about how *this* package interprets a transcript file, not
# about re-exposing fetch_conversation's internals.
DEFAULT_LABEL_TO_SPEAKER: dict[str, Speaker] = {
    "PATIENT (our bot)": Speaker.CALLER,
    "PGA AGENT": Speaker.AGENT_UNDER_TEST,
}

_LINE_RE = re.compile(r"^\[(?P<label>[^\]]+)\]\s?(?P<text>.*)$")


class TranscriptError(ValueError):
    """A transcript file doesn't match the expected `[SPEAKER] text` shape."""


@dataclass
class Turn:
    index: int
    speaker: Speaker
    text: str


@dataclass
class Transcript:
    stem: str
    turns: list[Turn]
    target: str


def parse_transcript(
    text: str,
    *,
    stem: str,
    target: str,
    label_to_speaker: dict[str, Speaker] | None = None,
) -> Transcript:
    """Parse `[LABEL] text` lines into a Transcript.

    A line with no leading `[LABEL]` continues the previous turn.
    Multi-line turns aren't produced by fetch_conversation.py today, but
    a parser that silently corrupts them the moment one appears is worse
    than one that never had to handle it (BUILD_SPEC_EVAL_LAYER.md 3.1).

    An unrecognized label is a hard error, not a skip: it means the
    label mapping changed - a new target, a fetch_conversation.py edit -
    and every check that ran against this transcript without noticing
    would be silently wrong about who said what.
    """
    mapping = label_to_speaker or DEFAULT_LABEL_TO_SPEAKER
    turns: list[Turn] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = _LINE_RE.match(line)
        if match is None:
            if not turns:
                raise TranscriptError(
                    f"{stem}: line has no [SPEAKER] label and there is no "
                    f"prior turn to continue it: {line!r}"
                )
            turns[-1].text = f"{turns[-1].text} {line}".strip()
            continue

        label = match.group("label")
        if label not in mapping:
            raise TranscriptError(
                f"{stem}: unrecognized speaker label {label!r}. Known "
                f"labels: {sorted(mapping)}. Fix the mapping (pass "
                "label_to_speaker=, or update DEFAULT_LABEL_TO_SPEAKER) "
                "rather than let this line pass through unattributed."
            )

        turns.append(
            Turn(index=len(turns), speaker=mapping[label], text=match.group("text").strip())
        )

    return Transcript(stem=stem, turns=turns, target=target)


def load_transcript(
    path: Path,
    *,
    target: str,
    label_to_speaker: dict[str, Speaker] | None = None,
) -> Transcript:
    """Read and parse one transcript file. `stem` is taken from the
    filename, matching how every recording/transcript pair in this repo
    is already keyed (see README.md's per-call table)."""
    return parse_transcript(
        path.read_text(),
        stem=path.stem,
        target=target,
        label_to_speaker=label_to_speaker,
    )
