"""
Tests for eval/judge.py. No API key is present in this environment,
which is deliberate: get_judge_backend() returning None is exercised
directly, and everything else (prompt building, caching, verdict
parsing, the "no cited turns" rejection rule) is tested against a fake
JudgeBackend that returns canned JSON - no network, no real model call,
no key required.
"""

import json
import os

import pytest

from eval.judge import (
    JudgeBackend,
    RubricError,
    _parse_verdict,
    available_rubrics,
    disabled_reason,
    get_judge_backend,
    judge,
    load_rubric,
)
from eval.transcript import parse_transcript


class FakeBackend(JudgeBackend):
    """Returns a fixed response regardless of prompt, and records every
    call it received so a test can assert on what was actually asked."""

    def __init__(self, response: str, model: str = "fake-model-v1"):
        self.model = model
        self._response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self._response


SAMPLE_TRANSCRIPT = parse_transcript(
    "[PGA AGENT] Can you please provide your date of birth?\n"
    "[PATIENT (our bot)] Sure, it's March fifteenth, nineteen eighty-five.\n",
    stem="fixture",
    target="pga",
)


def test_no_key_in_this_environment_disables_the_judge():
    """This is the actual state of this repo's .env right now - not a
    simulated absence. If this test starts failing because a key got
    added to .env, that's the signal to also write the "enabled" tests
    for real, not a bug in this test."""
    assert os.environ.get("ANTHROPIC_API_KEY") in (None, "")
    assert os.environ.get("OPENAI_API_KEY") in (None, "")
    assert get_judge_backend() is None
    assert disabled_reason() is not None
    assert "ANTHROPIC_API_KEY" in disabled_reason()


def test_judge_returns_none_when_no_backend_configured():
    rubric = load_rubric("answer_within_competence")
    assert judge(SAMPLE_TRANSCRIPT, rubric, backend=None) is None


@pytest.mark.parametrize("rubric_id", available_rubrics())
def test_every_rubric_file_loads_and_validates(rubric_id):
    rubric = load_rubric(rubric_id)
    assert rubric.id == rubric_id
    assert rubric.question
    assert rubric.pass_criteria
    assert rubric.fail_criteria
    assert "expected_verdict" in rubric.worked_example
    assert rubric.worked_example["expected_verdict"] in ("pass", "fail", "na")


def test_three_rubrics_from_the_build_spec_exist():
    assert set(available_rubrics()) == {
        "symptoms_acknowledged",
        "human_request_honoured",
        "answer_within_competence",
    }


def test_answer_within_competence_is_framed_as_the_hallucination_guard():
    rubric = load_rubric("answer_within_competence")
    assert "hallucin" in (rubric.label or "").lower() + rubric.question.lower()


def test_judge_with_fake_backend_parses_a_valid_response(tmp_path, monkeypatch):
    monkeypatch.setattr("eval.judge.CACHE_DIR", tmp_path / "cache")
    rubric = load_rubric("symptoms_acknowledged")
    backend = FakeBackend(json.dumps({
        "verdict": "na",
        "confidence": 0.9,
        "rationale": "No symptoms were described in this call.",
        "turns": [],
    }))
    verdict = judge(SAMPLE_TRANSCRIPT, rubric, backend=backend)
    assert verdict.rubric_id == "symptoms_acknowledged"
    assert verdict.verdict == "na"
    assert verdict.model == "fake-model-v1"
    assert len(backend.calls) == 1


def test_judge_caches_so_a_second_call_does_not_hit_the_backend_again(tmp_path, monkeypatch):
    monkeypatch.setattr("eval.judge.CACHE_DIR", tmp_path / "cache")
    rubric = load_rubric("symptoms_acknowledged")
    backend = FakeBackend(json.dumps({
        "verdict": "na", "confidence": 0.9, "rationale": "x", "turns": [],
    }))
    judge(SAMPLE_TRANSCRIPT, rubric, backend=backend)
    judge(SAMPLE_TRANSCRIPT, rubric, backend=backend)
    assert len(backend.calls) == 1, "second call should have been served from cache"


def test_verdict_citing_no_turns_is_discarded_as_unusable():
    """sec. 4: 'A verdict citing no turns is discarded as unusable.'"""
    raw = json.dumps({
        "verdict": "fail", "confidence": 0.8, "rationale": "seems bad", "turns": [],
    })
    with pytest.raises(RubricError, match="citing no turns"):
        _parse_verdict(raw, rubric_id="human_request_honoured", model="fake-model-v1")


def test_na_verdict_may_have_no_turns():
    raw = json.dumps({
        "verdict": "na", "confidence": 0.9, "rationale": "situation never arose", "turns": [],
    })
    verdict = _parse_verdict(raw, rubric_id="human_request_honoured", model="fake-model-v1")
    assert verdict.verdict == "na"


def test_invalid_verdict_value_is_rejected():
    raw = json.dumps({
        "verdict": "maybe", "confidence": 0.5, "rationale": "unsure", "turns": [1],
    })
    with pytest.raises(RubricError, match="invalid verdict"):
        _parse_verdict(raw, rubric_id="human_request_honoured", model="fake-model-v1")


def test_non_json_response_is_rejected():
    with pytest.raises(RubricError, match="non-JSON"):
        _parse_verdict("sure, it fails because...", rubric_id="human_request_honoured", model="fake-model-v1")


def test_unknown_rubric_id_raises_with_available_list():
    with pytest.raises(RubricError, match="Unknown rubric"):
        load_rubric("not_a_real_rubric")
