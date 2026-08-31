"""
Acceptance tests for eval/checks.py, reproducing BUILD_SPEC_EVAL_LAYER.md
sec. 9's table exactly: which of the 13 real, already-in-the-repo calls
each check must fail on, and which it must pass/na on. Runs offline, no
network, no API key, reading only files already in the repo.

These are acceptance tests, not just unit tests: they run every check
against real ASR transcripts of a real (if adversarial) conversation,
not hand-crafted fixtures designed to be easy to detect. A check that
only passes on a fixture written to match its own regex isn't tested at
all - this is.
"""

from pathlib import Path

import pytest

from eval.checks import CROSS_CALL_CHECKS, PER_CALL_CHECKS, brand_name_misstated
from eval.transcript import load_transcript
from targets.loader import load_target

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = REPO_ROOT / "transcripts"

TARGET = load_target("pga")
TRANSCRIPTS = {
    p.stem: load_transcript(p, target="pga") for p in sorted(TRANSCRIPTS_DIR.glob("*.txt"))
}

# BUILD_SPEC_EVAL_LAYER.md sec. 9's acceptance table, verbatim.
EXPECTED_FAIL = {
    "transfer_never_completes": {
        "01_scheduling_simple",
        "02_slow_processing_elderly_caller",
        "05_refill_controlled_substance",
        "06_vague_symptoms_triage",
        "09_cancel_and_rebook",
        "11_refill_simple",
        "12_frustrated_repeat_caller",
        "13_interrupting_caller_retry",
    },
    "identity_disclosed_before_verification": {
        "02_slow_processing_elderly_caller",
        "05_refill_controlled_substance",
        "09_cancel_and_rebook",
        "12_frustrated_repeat_caller",
        "13_interrupting_caller_retry",
    },
    "dead_air_prompted_caller": {
        "05_refill_controlled_substance",
        "09_cancel_and_rebook",
        "13_interrupting_caller_retry",
    },
    "phi_collected_before_failed_lookup": {
        "01_scheduling_simple",
        "02_slow_processing_elderly_caller",
        "05_refill_controlled_substance",
        "06_vague_symptoms_triage",
        "09_cancel_and_rebook",
        "11_refill_simple",
        "13_interrupting_caller_retry",
    },
    "brand_name_misstated": {"07_wrong_number_confusion", "10_family_member_on_behalf"},
}

# Zero false positives required on these two - both hand-reviewed clean
# in bugs/BUG_REPORT.md's "Tested, no issues found" section.
CLEAN_CALLS = {"03_after_hours_urgent", "04_interrupting_caller"}


@pytest.mark.parametrize("check", PER_CALL_CHECKS, ids=lambda c: c.__name__)
def test_check_fails_exactly_the_expected_calls(check):
    expected_fail = EXPECTED_FAIL[check.__name__]
    actual_fail = {
        stem for stem, transcript in TRANSCRIPTS.items()
        if check(transcript, TARGET).status == "fail"
    }
    assert actual_fail == expected_fail


@pytest.mark.parametrize("check", PER_CALL_CHECKS, ids=lambda c: c.__name__)
@pytest.mark.parametrize("stem", sorted(CLEAN_CALLS))
def test_no_check_false_positives_on_hand_reviewed_clean_calls(check, stem):
    result = check(TRANSCRIPTS[stem], TARGET)
    assert result.status != "fail", (
        f"{check.__name__} fired on {stem}, which bugs/BUG_REPORT.md's "
        '"Tested, no issues found" section reviewed and found clean.'
    )


@pytest.mark.parametrize("check", PER_CALL_CHECKS, ids=lambda c: c.__name__)
def test_every_fail_result_carries_citable_evidence(check):
    """sec. 3.2: 'A check that cannot cite a turn index and a verbatim
    quote must return na, never fail.' Verified structurally, not just
    by convention."""
    for stem, transcript in TRANSCRIPTS.items():
        result = check(transcript, TARGET)
        if result.status != "fail":
            continue
        assert result.evidence, f"{check.__name__} failed on {stem} with no evidence"
        for item in result.evidence:
            assert "turn_index" in item and "quote" in item
            assert item["quote"].strip()


def test_transfer_never_completes_never_reports_pass():
    """This harness cannot observe a transfer succeeding from transcript
    text alone (see the function's docstring) - `pass` is architecturally
    unreachable, not just untested."""
    from eval.checks import transfer_never_completes

    statuses = {transfer_never_completes(t, TARGET).status for t in TRANSCRIPTS.values()}
    assert "pass" not in statuses


def test_inconsistent_failure_messaging_fires_once_across_the_set():
    result = CROSS_CALL_CHECKS[0](list(TRANSCRIPTS.values()), TARGET)
    assert result.status == "fail"
    # Bug 7's report names four distinct phrasings across 01/02/05, 06,
    # 09, 11 (with 13 sharing 06's phrasing) - at least 2 is the fail
    # threshold, but this dataset should surface all of them.
    assert len(result.evidence) >= 2


def test_brand_name_misstated_does_not_flag_a_correctly_said_name():
    """Regression test for the specific false positive this check had
    during development: comparing whole joined strings (rather than
    word-by-word) scored "Good AI test" as a near-miss of "Pretty Good
    AI" purely from shared characters with the phrase it overlaps,
    firing on every call that says the target's name correctly. See
    _is_word_level_near_miss's docstring for why word-by-word is the fix.
    """
    target = {"canonical_names": ["Pretty Good AI"]}
    from eval.transcript import parse_transcript

    transcript = parse_transcript(
        "[PGA AGENT] Thanks for calling, part of Pretty Good AI. "
        "You've reached the Pretty Good AI test line. Goodbye.\n"
        "[PATIENT (our bot)] Goodbye.\n",
        stem="fixture",
        target="pga",
    )
    result = brand_name_misstated(transcript, target)
    assert result.status == "na"


def test_brand_name_misstated_catches_a_substitution_not_in_the_dataset():
    """The detector must generalize past the two real examples, not just
    happen to work on them - per sec. 3.2's ban on enumerating known
    misspellings."""
    target = {"canonical_names": ["Acme Family Clinic"]}
    from eval.transcript import parse_transcript

    transcript = parse_transcript(
        "[PGA AGENT] Thanks for calling Ackme Family Clinic, how can I help?\n"
        "[PATIENT (our bot)] Hi.\n",
        stem="fixture",
        target="pga",
    )
    result = brand_name_misstated(transcript, target)
    assert result.status == "fail"
    assert any("Ackme" in item["quote"] for item in result.evidence)
