"""
Orchestrates the eval layer: runs every deterministic check (and, if a
judge backend is configured, every rubric) against one target's
transcripts, and appends one JSONL record per call to results/runs.jsonl.

This module never dials anything and never modifies a transcript or
recording it reads - it's the offline layer BUILD_SPEC_EVAL_LAYER.md
sec. 1 describes: it scores artifacts place_call.py and
fetch_conversation.py already produced, so growing the suite from 13
calls to 100+ costs compute, not another 21 minutes of real phone calls.

results/ is gitignored (sec. 10's Definition of Done) - runs.jsonl is
regenerable from the transcripts already in the repo, and "the product
of a regression suite is change over time" (sec. 8) is a promise about
what accumulates on a machine that runs this repeatedly, not something
this project's own git history needs to carry.

CLI:
    python -m eval.score --target pga --all
    python -m eval.score --target pga --call 06_vague_symptoms_triage
    python -m eval.score --target pga --all --no-rubrics
    python -m eval.score --target pga --all --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from eval.checks import CROSS_CALL_CHECKS, PER_CALL_CHECKS
from eval.judge import (
    RubricError,
    available_rubrics,
    disabled_reason,
    get_judge_backend,
    judge,
    load_rubric,
)
from eval.transcript import Transcript, load_transcript
from scenarios import loader as scenario_loader
from targets.loader import load_target

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = REPO_ROOT / "transcripts"
RECORDINGS_DIR = REPO_ROOT / "recordings"
RESULTS_DIR = REPO_ROOT / "results"
RUNS_PATH = RESULTS_DIR / "runs.jsonl"


def infer_scenario_id(stem: str) -> str | None:
    """Best-effort scenario id for a call's *record-keeping metadata*
    only - never fed back into a check's verdict (sec. 3.3's rule
    against filename/stem-based outcomes is about deciding pass/fail,
    not about labeling a result for a human reader, but this function
    still isn't used by anything in eval/checks.py).

    Inferred from this repo's own filename convention: an ordinal
    prefix, and occasionally a `_retry` suffix for a scenario run twice
    (`13_interrupting_caller_retry` -> `interrupting_caller`, per
    README.md's own call index). Returns None rather than guessing
    wrong when neither form matches a real scenario id.
    """
    base = re.sub(r"^\d+_", "", stem)
    known = set(scenario_loader.available_scenarios())
    if base in known:
        return base
    stripped = re.sub(r"_retry\d*$", "", base)
    if stripped in known:
        return stripped
    return None


def score_call(transcript: Transcript, target: dict, *, run_rubrics: bool) -> dict:
    """Score one transcript: every deterministic check, plus every
    rubric if `run_rubrics` and a judge backend is configured. Never
    raises on a rubric problem - a RubricError (e.g. an unusable verdict
    citing no turns) is recorded as an error entry for that one rubric,
    not allowed to take down scoring for the whole call."""
    checks = [check(transcript, target) for check in PER_CALL_CHECKS]

    rubric_results: list[dict] = []
    reason = disabled_reason()
    if not run_rubrics:
        rubrics_disabled_reason = "not requested"
    else:
        rubrics_disabled_reason = reason  # None means rubrics are enabled and ran below.
    if run_rubrics and reason is None:
        backend = get_judge_backend()
        for rubric_id in available_rubrics():
            rubric = load_rubric(rubric_id)
            try:
                verdict = judge(transcript, rubric, backend=backend)
            except RubricError as exc:
                rubric_results.append({"id": rubric_id, "error": str(exc)})
                continue
            if verdict is not None:
                rubric_results.append(
                    {
                        "id": verdict.rubric_id,
                        "verdict": verdict.verdict,
                        "confidence": verdict.confidence,
                        "rationale": verdict.rationale,
                        "turns": verdict.turns,
                        "model": verdict.model,
                    }
                )

    recording_path = RECORDINGS_DIR / f"{transcript.stem}.mp3"
    transcript_path = TRANSCRIPTS_DIR / f"{transcript.stem}.txt"

    return {
        "target": target["slug"],
        "scenario_id": infer_scenario_id(transcript.stem),
        "call": {
            # Historical calls in this repo were renamed to human-readable
            # stems during the "reframe" pass, which is when the original
            # conversation_id/call_sid metadata was lost - these are None
            # rather than fabricated. A future call placed after this
            # module exists could carry them for real, but doing that
            # means touching place_call.py/fetch_conversation.py, which
            # sec. 2's hard constraints forbid beyond the --target
            # addition already made.
            "stem": transcript.stem,
            "conversation_id": None,
            "call_sid": None,
            "duration_s": None,
        },
        "artifacts": {
            "transcript": str(transcript_path.relative_to(REPO_ROOT)),
            "recording": (
                str(recording_path.relative_to(REPO_ROOT)) if recording_path.exists() else None
            ),
            "retranscript": None,  # eval/retranscribe.py, not built yet
        },
        "harness": {"reasoning_model": None, "voice_id": None, "speed": None},
        "checks": [
            {"id": result.id, "status": result.status, "evidence": result.evidence}
            for result in checks
        ],
        "rubrics": rubric_results,
        "rubrics_disabled_reason": rubrics_disabled_reason,
    }


def score_target(
    target_slug: str, *, stems: list[str] | None = None, run_rubrics: bool = True
) -> list[dict]:
    """Score every transcript for `target_slug` (or just `stems`, if
    given). Cross-call checks only run over the full set - scoring a
    single --call intentionally skips them, since a cross-call verdict
    from one call in isolation would be meaningless."""
    target = load_target(target_slug)
    paths = (
        [TRANSCRIPTS_DIR / f"{stem}.txt" for stem in stems]
        if stems
        else sorted(TRANSCRIPTS_DIR.glob("*.txt"))
    )
    transcripts = [load_transcript(path, target=target_slug) for path in paths]

    records = [score_call(t, target, run_rubrics=run_rubrics) for t in transcripts]

    if stems is None:
        for check in CROSS_CALL_CHECKS:
            result = check(transcripts, target)
            for record in records:
                record.setdefault("cross_call_checks", []).append(
                    {"id": result.id, "status": result.status, "evidence": result.evidence}
                )

    run_id = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}_{target_slug}"
    for record in records:
        record["run_id"] = run_id

    return records


def append_results(records: list[dict], *, runs_path: Path = RUNS_PATH) -> None:
    runs_path.parent.mkdir(parents=True, exist_ok=True)
    with runs_path.open("a") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score existing call artifacts offline (no phone calls placed).")
    parser.add_argument("--target", required=True, help="Target slug (targets/<slug>.json).")
    parser.add_argument("--call", help="Score just this one transcript stem instead of --all.")
    parser.add_argument("--all", action="store_true", help="Score every transcript for this target.")
    parser.add_argument(
        "--no-rubrics", action="store_true", help="Skip rubric scoring even if a key is configured."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print a summary; don't write results/runs.jsonl."
    )
    args = parser.parse_args()

    if not args.all and not args.call:
        parser.error("pass --all or --call <stem>")

    reason = disabled_reason()
    if reason and not args.no_rubrics:
        print(reason)

    records = score_target(
        args.target,
        stems=[args.call] if args.call else None,
        run_rubrics=not args.no_rubrics,
    )

    for record in records:
        fails = [c["id"] for c in record["checks"] if c["status"] == "fail"]
        print(f"{record['call']['stem']:40} fails={fails or 'none'}")

    if not args.dry_run:
        append_results(records)
        print(f"Wrote {len(records)} record(s) to {RUNS_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
