"""
Rubric scoring: LLM-as-judge for the three findings that need judgment
rather than a deterministic check (BUILD_SPEC_EVAL_LAYER.md sec. 4).

Provider-agnostic behind one small interface (JudgeBackend) so which
hosted model actually runs is a config choice, not a code choice - a
new provider is a new JudgeBackend subclass, never a branch inside a
check or the report. RUBRIC_JUDGE_MODEL in .env overrides the default
model for whichever backend gets selected.

A missing API key disables rubric scoring entirely, with a clear
printed explanation (see get_judge_backend) - deterministic checks in
eval/checks.py never depend on anything in this module and keep working
whether or not a key is configured. This module was built and fully
tested with no key present at all: get_judge_backend() returning None
is not a failure mode, it's the default state until one is wired in.

Runs are cached by (transcript content hash, rubric id, model) in
eval/rubric_cache/ so re-running the suite after a key is added doesn't
re-spend money on transcripts already judged, and a re-run without any
transcript changes is fully reproducible.

Never let a rubric verdict silently overwrite a deterministic result -
eval/score.py (a later chunk) is responsible for keeping them in
separate result fields; this module only produces one rubric's verdict
for one transcript.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from eval.transcript import Transcript

RUBRICS_DIR = Path(__file__).resolve().parent / "rubrics"
CACHE_DIR = Path(__file__).resolve().parent / "rubric_cache"

# Per-provider "cheap hosted model" default (sec. 4). RUBRIC_JUDGE_MODEL
# in .env overrides whichever of these gets selected.
DEFAULT_MODELS = {
    "anthropic": "claude-3-5-haiku-20241022",
    "openai": "gpt-4o-mini",
}

_VALID_VERDICTS = ("pass", "fail", "na")


class RubricError(Exception):
    """A bad rubric file, or a model response that can't be trusted -
    including sec. 4's rule that a verdict citing no turns is discarded
    as unusable rather than accepted at face value."""


@dataclass
class Rubric:
    id: str
    question: str
    pass_criteria: str
    fail_criteria: str
    worked_example: dict
    label: str | None = None


@dataclass
class RubricVerdict:
    rubric_id: str
    verdict: str  # "pass" | "fail" | "na"
    confidence: float
    rationale: str
    turns: list[int]
    model: str


def available_rubrics() -> list[str]:
    return sorted(p.stem for p in RUBRICS_DIR.glob("*.json"))


def load_rubric(rubric_id: str) -> Rubric:
    """Load and validate one rubric file. Raises RubricError on
    anything wrong, same fail-fast discipline as scenarios/loader.py
    and targets/loader.py."""
    path = RUBRICS_DIR / f"{rubric_id}.json"
    if not path.exists():
        raise RubricError(
            f"Unknown rubric {rubric_id!r}. Available: {available_rubrics()}"
        )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RubricError(f"{path.name} is not valid JSON: {exc}") from exc

    required = ("id", "question", "pass_criteria", "fail_criteria", "worked_example")
    missing = [f for f in required if f not in data]
    if missing:
        raise RubricError(f"{path.name} is missing required field(s): {', '.join(missing)}")
    if data["id"] != rubric_id:
        raise RubricError(
            f'{path.name} has "id": {data["id"]!r} but its filename says {rubric_id!r}.'
        )
    return Rubric(
        id=data["id"],
        question=data["question"],
        pass_criteria=data["pass_criteria"],
        fail_criteria=data["fail_criteria"],
        worked_example=data["worked_example"],
        label=data.get("label"),
    )


def _transcript_hash(transcript: Transcript) -> str:
    text = "\n".join(f"[{t.speaker.value}] {t.text}" for t in transcript.turns)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _cache_path(transcript_hash: str, rubric_id: str, model: str) -> Path:
    key = f"{transcript_hash}_{rubric_id}_{model}".replace("/", "_")
    return CACHE_DIR / f"{key}.json"


def _read_cache(transcript_hash: str, rubric_id: str, model: str) -> RubricVerdict | None:
    path = _cache_path(transcript_hash, rubric_id, model)
    if not path.exists():
        return None
    return RubricVerdict(**json.loads(path.read_text()))


def _write_cache(transcript_hash: str, rubric_id: str, model: str, verdict: RubricVerdict) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    _cache_path(transcript_hash, rubric_id, model).write_text(
        json.dumps(asdict(verdict), indent=2)
    )


class JudgeBackend:
    """One hosted model behind a single method."""

    model: str

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError


class AnthropicJudge(JudgeBackend):
    def __init__(self, model: str, api_key: str):
        self.model = model
        self._api_key = api_key

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        import anthropic  # imported lazily: not a project dependency unless a key is set

        client = anthropic.Anthropic(api_key=self._api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text


class OpenAIJudge(JudgeBackend):
    def __init__(self, model: str, api_key: str):
        self.model = model
        self._api_key = api_key

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        import openai  # imported lazily: not a project dependency unless a key is set

        client = openai.OpenAI(api_key=self._api_key)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content


def get_judge_backend() -> JudgeBackend | None:
    """The configured judge backend, or None if rubric scoring is
    disabled. Checks ANTHROPIC_API_KEY then OPENAI_API_KEY - the first
    one present wins, so setting either is enough to light this up.
    Prints nothing itself; callers (eval/score.py) are responsible for
    telling a human rubric scoring is off and why, per sec. 4.
    """
    provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else (
        "openai" if os.environ.get("OPENAI_API_KEY") else None
    )
    if provider is None:
        return None

    model = os.environ.get("RUBRIC_JUDGE_MODEL", DEFAULT_MODELS[provider])
    if provider == "anthropic":
        return AnthropicJudge(model=model, api_key=os.environ["ANTHROPIC_API_KEY"])
    return OpenAIJudge(model=model, api_key=os.environ["OPENAI_API_KEY"])


def disabled_reason() -> str | None:
    """Human-readable reason rubric scoring is off, or None if it's
    configured. Separate from get_judge_backend() so a caller can print
    a clear explanation without constructing a backend just to check."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        return None
    return (
        "Rubric scoring is disabled: no ANTHROPIC_API_KEY or OPENAI_API_KEY in .env. "
        "Deterministic checks are unaffected. Set either key (see .env.example) to "
        "enable symptoms_acknowledged, human_request_honoured, and answer_within_competence."
    )


def _build_prompt(rubric: Rubric, transcript: Transcript) -> tuple[str, str]:
    system_prompt = (
        "You are scoring one phone call transcript against a single rubric question "
        "for a voice-agent QA harness. Respond with ONLY a JSON object, no other text, "
        'shaped exactly as: {"verdict": "pass"|"fail"|"na", "confidence": 0.0-1.0, '
        '"rationale": "one or two sentences", "turns": [turn indices you relied on]}. '
        '"na" means the situation this rubric asks about never arose in this call. '
        "If your verdict is pass or fail, `turns` must be non-empty and must cite the "
        "actual turn indices (0-based, as given in the transcript below) that your "
        "verdict rests on - a verdict with no cited turns will be discarded."
    )
    transcript_text = "\n".join(f"[{t.index}] ({t.speaker.value}) {t.text}" for t in transcript.turns)
    user_prompt = (
        f"RUBRIC: {rubric.question}\n\n"
        f"PASS means: {rubric.pass_criteria}\n"
        f"FAIL means: {rubric.fail_criteria}\n\n"
        f"WORKED EXAMPLE (a different call, for calibration only):\n"
        f"{rubric.worked_example.get('excerpt', '')}\n"
        f"-> expected verdict: {rubric.worked_example.get('expected_verdict')}, "
        f"because: {rubric.worked_example.get('why', '')}\n\n"
        f"TRANSCRIPT TO SCORE:\n{transcript_text}"
    )
    return system_prompt, user_prompt


def _parse_verdict(raw: str, *, rubric_id: str, model: str) -> RubricVerdict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RubricError(
            f"Judge for {rubric_id!r} returned non-JSON output: {raw[:200]!r}"
        ) from exc

    verdict = data.get("verdict")
    if verdict not in _VALID_VERDICTS:
        raise RubricError(f"Judge for {rubric_id!r} returned an invalid verdict: {verdict!r}")

    turns = data.get("turns") or []
    if verdict in ("pass", "fail") and not turns:
        raise RubricError(
            f"Judge for {rubric_id!r} returned verdict={verdict!r} citing no turns - "
            "discarded as unusable per BUILD_SPEC_EVAL_LAYER.md sec. 4."
        )

    return RubricVerdict(
        rubric_id=rubric_id,
        verdict=verdict,
        confidence=float(data.get("confidence", 0.0)),
        rationale=str(data.get("rationale", "")),
        turns=[int(i) for i in turns],
        model=model,
    )


def judge(
    transcript: Transcript,
    rubric: Rubric,
    backend: JudgeBackend | None = None,
) -> RubricVerdict | None:
    """Score one transcript against one rubric. Returns None (not an
    error) when no backend is configured - the caller decides how to
    report that, since "disabled" and "errored" mean different things
    in a results record.
    """
    backend = backend if backend is not None else get_judge_backend()
    if backend is None:
        return None

    transcript_hash = _transcript_hash(transcript)
    cached = _read_cache(transcript_hash, rubric.id, backend.model)
    if cached is not None:
        return cached

    system_prompt, user_prompt = _build_prompt(rubric, transcript)
    raw = backend.complete(system_prompt, user_prompt)
    verdict = _parse_verdict(raw, rubric_id=rubric.id, model=backend.model)
    _write_cache(transcript_hash, rubric.id, backend.model, verdict)
    return verdict
