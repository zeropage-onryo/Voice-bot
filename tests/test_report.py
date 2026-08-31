"""
Tests for eval/report.py: rendering results/runs.jsonl into the markdown
draft report. Builds its own scored records via eval.score (offline, no
key needed) and writes to temp runs.jsonl files - never touches a
developer's real results/runs.jsonl.
"""

from pathlib import Path

import pytest

from eval.report import (
    DRAFT_NOTICE,
    _latest_run_per_call,
    load_records,
    render_report,
)
from eval.score import append_results, score_target

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def runs_path(tmp_path):
    path = tmp_path / "runs.jsonl"
    records = score_target("pga", stems=None, run_rubrics=False)
    append_results(records, runs_path=path)
    return path


def test_load_records_reads_only_the_requested_target(tmp_path):
    path = tmp_path / "runs.jsonl"
    pga_records = score_target("pga", stems=["01_scheduling_simple"], run_rubrics=False)
    append_results(pga_records, runs_path=path)
    # A record for some other target should never leak into a report for "pga".
    other = dict(pga_records[0])
    other["target"] = "some_other_target"
    append_results([other], runs_path=path)

    loaded = load_records("pga", runs_path=path)
    assert len(loaded) == 1
    assert loaded[0]["target"] == "pga"


def test_load_records_returns_empty_list_for_missing_file(tmp_path):
    assert load_records("pga", runs_path=tmp_path / "does_not_exist.jsonl") == []


def test_latest_run_per_call_keeps_the_newest_run_id():
    older = {"run_id": "2020-01-01T00:00:00Z_pga", "call": {"stem": "x"}, "checks": []}
    newer = {"run_id": "2030-01-01T00:00:00Z_pga", "call": {"stem": "x"}, "checks": []}
    result = _latest_run_per_call([older, newer])
    assert len(result) == 1
    assert result[0]["run_id"] == newer["run_id"]


def test_latest_run_per_call_keeps_every_distinct_call():
    records = score_target("pga", stems=None, run_rubrics=False)
    result = _latest_run_per_call(records)
    assert len(result) == 13


def test_render_report_raises_when_nothing_has_been_scored(tmp_path):
    with pytest.raises(ValueError, match="No results found"):
        render_report("pga", runs_path=tmp_path / "runs.jsonl")


def test_render_report_opens_with_the_draft_notice(runs_path):
    report = render_report("pga", runs_path=runs_path)
    assert DRAFT_NOTICE in report


def test_render_report_lists_every_call_in_scope(runs_path):
    report = render_report("pga", runs_path=runs_path)
    for stem in [
        "01_scheduling_simple",
        "07_wrong_number_confusion",
        "13_interrupting_caller_retry",
    ]:
        assert stem in report


def test_render_report_surfaces_the_known_transfer_defect(runs_path):
    """Cross-checks against bugs/BUG_REPORT.md's Bug 1: 8 of 13 calls."""
    report = render_report("pga", runs_path=runs_path)
    assert "Transfer promised but never completes" in report
    assert "8 of 13" in report


def test_render_report_cross_call_finding_is_not_duplicated_per_record():
    """Regression test: eval/score.py copies one cross-call CheckResult
    onto every record it produces (see score_target). An earlier version
    of this report rendered that as though the check had fired
    separately on every call, printing the same four evidence quotes
    five times over. The fix reads a single exemplar's evidence (each
    item carries its own "stem") instead of iterating per record - this
    asserts the fix holds by checking the finding's evidence block
    appears exactly once even though 13 records all carry the same
    cross_call_checks payload.
    """
    from eval.report import _render_cross_call_findings

    records = score_target("pga", stems=None, run_rubrics=False)
    section = _render_cross_call_findings(records)
    assert section.count("### Inconsistent failure messaging") == 1


def test_render_report_names_calls_implicated_in_cross_call_finding(runs_path):
    report = render_report("pga", runs_path=runs_path)
    assert "Inconsistent failure messaging across calls" in report
    assert "Calls implicated:" in report


def test_render_report_notes_rubrics_disabled_when_no_key_present(runs_path):
    report = render_report("pga", runs_path=runs_path)
    assert "Rubric scoring was not run for this report" in report


def test_render_report_uses_latest_run_when_scored_twice(tmp_path):
    path = tmp_path / "runs.jsonl"
    first = score_target("pga", stems=["01_scheduling_simple"], run_rubrics=False)
    append_results(first, runs_path=path)
    second = score_target("pga", stems=["01_scheduling_simple"], run_rubrics=False)
    append_results(second, runs_path=path)

    # Both runs are on disk, but the report scopes to 1 call, not 2.
    report = render_report("pga", runs_path=path)
    assert "Scope:** 1 call(s)" in report


def test_unlabeled_check_id_falls_back_to_raw_id_rather_than_raising():
    """A future check added to eval/checks.py without a CHECK_LABELS
    entry should still render, just under its raw id, instead of
    breaking every report until someone remembers to label it."""
    from eval.report import _render_check_findings

    records = [
        {
            "call": {"stem": "fixture_call"},
            "checks": [
                {"id": "a_brand_new_check_nobody_labeled_yet", "status": "fail", "evidence": []}
            ],
        }
    ]
    section = _render_check_findings(records)
    assert "a_brand_new_check_nobody_labeled_yet" in section
