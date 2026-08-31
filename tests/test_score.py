"""
Tests for eval/score.py: the orchestration layer tying checks + rubrics
into results/runs.jsonl records. Runs offline against the 13 real
transcripts already in the repo - no phone calls, no API key, no network.

Tests write to a temp runs.jsonl (never the real results/runs.jsonl) via
the `runs_path`/`out_dir` parameters, so running this suite never touches
a developer's or CI's actual results/ directory.
"""

import json
from pathlib import Path

import pytest

from eval.score import (
    append_results,
    infer_scenario_id,
    score_call,
    score_target,
)
from eval.transcript import load_transcript
from scenarios import loader as scenario_loader
from targets.loader import load_target

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = REPO_ROOT / "transcripts"

ALL_STEMS = [p.stem for p in sorted(TRANSCRIPTS_DIR.glob("*.txt"))]


def test_thirteen_real_transcripts_are_present():
    # Guards every other test in this file against silently running on
    # fewer calls than the repo actually has (e.g. a bad glob).
    assert len(ALL_STEMS) == 13


@pytest.mark.parametrize("stem", ALL_STEMS)
def test_infer_scenario_id_resolves_every_real_stem_to_a_known_scenario(stem):
    scenario_id = infer_scenario_id(stem)
    assert scenario_id is not None, f"{stem} did not resolve to any known scenario"
    assert scenario_id in set(scenario_loader.available_scenarios())


def test_infer_scenario_id_handles_the_retry_suffix():
    # The one stem in this repo that doesn't match its scenario id
    # verbatim - this is the specific edge case the function exists for.
    assert infer_scenario_id("13_interrupting_caller_retry") == "interrupting_caller"


def test_infer_scenario_id_returns_none_rather_than_guessing():
    assert infer_scenario_id("99_not_a_real_scenario_at_all") is None


def test_score_call_never_feeds_the_stem_into_the_verdict():
    """sec. 3.3's rule is about eval/checks.py, but score_call is where a
    filename-based shortcut would be easiest to sneak in via
    scenario_id - assert the field is metadata only, not consumed by
    scoring, by confirming two different stems of the same underlying
    scenario (13_interrupting_caller_retry and a hypothetical rename)
    would score identically. Simplest direct test: scoring is a pure
    function of transcript + target, so calling it twice on the same
    transcript object gives byte-identical checks.
    """
    target = load_target("pga")
    transcript = load_transcript(TRANSCRIPTS_DIR / "07_wrong_number_confusion.txt", target="pga")
    first = score_call(transcript, target, run_rubrics=False)
    second = score_call(transcript, target, run_rubrics=False)
    assert first["checks"] == second["checks"]


def test_score_call_reports_rubrics_disabled_when_no_key_present():
    target = load_target("pga")
    transcript = load_transcript(TRANSCRIPTS_DIR / "01_scheduling_simple.txt", target="pga")
    record = score_call(transcript, target, run_rubrics=True)
    assert record["rubrics"] == []
    assert record["rubrics_disabled_reason"] is not None
    assert "ANTHROPIC_API_KEY" in record["rubrics_disabled_reason"]


def test_score_call_with_run_rubrics_false_reports_not_requested():
    target = load_target("pga")
    transcript = load_transcript(TRANSCRIPTS_DIR / "01_scheduling_simple.txt", target="pga")
    record = score_call(transcript, target, run_rubrics=False)
    assert record["rubrics"] == []
    assert record["rubrics_disabled_reason"] == "not requested"


def test_score_call_marks_missing_recording_as_none():
    target = load_target("pga")
    transcript = load_transcript(TRANSCRIPTS_DIR / "01_scheduling_simple.txt", target="pga")
    record = score_call(transcript, target, run_rubrics=False)
    # This repo's recordings/ may or may not have every mp3 checked in;
    # either way the field must be a real path or None, never a path to
    # a file that doesn't exist.
    recording = record["artifacts"]["recording"]
    if recording is not None:
        assert (REPO_ROOT / recording).exists()


def test_score_target_all_produces_one_record_per_transcript():
    records = score_target("pga", stems=None, run_rubrics=False)
    assert len(records) == 13
    assert {r["call"]["stem"] for r in records} == set(ALL_STEMS)


def test_score_target_with_stems_scores_only_those_calls():
    records = score_target("pga", stems=["04_interrupting_caller"], run_rubrics=False)
    assert len(records) == 1
    assert records[0]["call"]["stem"] == "04_interrupting_caller"


def test_score_target_with_stems_skips_cross_call_checks():
    """A cross-call verdict from a single isolated call would be
    meaningless (there's nothing to cross-reference), so --call must
    skip CROSS_CALL_CHECKS entirely rather than running them on a set of
    one."""
    records = score_target("pga", stems=["04_interrupting_caller"], run_rubrics=False)
    assert "cross_call_checks" not in records[0]


def test_score_target_all_includes_cross_call_checks_on_every_record():
    records = score_target("pga", stems=None, run_rubrics=False)
    assert all("cross_call_checks" in r for r in records)
    assert all(r["cross_call_checks"] for r in records)


def test_score_target_all_records_share_one_run_id():
    records = score_target("pga", stems=None, run_rubrics=False)
    run_ids = {r["run_id"] for r in records}
    assert len(run_ids) == 1


def test_score_target_known_defects_reproduce_the_acceptance_table():
    """Spot-check against bugs/BUG_REPORT.md's own numbers (Bug 1: 8 of
    13 calls never complete a promised transfer) via the orchestration
    layer, not just eval/checks.py directly - this is what actually
    ships in results/runs.jsonl.
    """
    records = score_target("pga", stems=None, run_rubrics=False)
    transfer_fails = [
        r["call"]["stem"]
        for r in records
        for c in r["checks"]
        if c["id"] == "transfer_never_completes" and c["status"] == "fail"
    ]
    assert len(transfer_fails) == 8


def test_append_results_writes_valid_jsonl(tmp_path):
    runs_path = tmp_path / "runs.jsonl"
    records = score_target("pga", stems=["03_after_hours_urgent"], run_rubrics=False)
    append_results(records, runs_path=runs_path)

    lines = runs_path.read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["call"]["stem"] == "03_after_hours_urgent"


def test_append_results_appends_rather_than_overwrites(tmp_path):
    runs_path = tmp_path / "runs.jsonl"
    first = score_target("pga", stems=["03_after_hours_urgent"], run_rubrics=False)
    second = score_target("pga", stems=["04_interrupting_caller"], run_rubrics=False)
    append_results(first, runs_path=runs_path)
    append_results(second, runs_path=runs_path)

    lines = runs_path.read_text().splitlines()
    assert len(lines) == 2


def test_append_results_creates_parent_directory(tmp_path):
    runs_path = tmp_path / "nested" / "results" / "runs.jsonl"
    records = score_target("pga", stems=["03_after_hours_urgent"], run_rubrics=False)
    append_results(records, runs_path=runs_path)
    assert runs_path.exists()
