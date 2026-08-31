"""
Renders results/runs.jsonl into a markdown draft report, grouped by check
and by rubric, in the register of bugs/BUG_REPORT.md.

BUILD_SPEC_EVAL_LAYER.md sec. 8 is explicit that this is a starting point
for a human, not a finished deliverable: every report this module produces
opens with a "machine-generated draft" notice, and nothing here assigns
severity, drops findings as ASR noise, or cross-checks against audio the
way bugs/BUG_REPORT.md's human-written findings do. Its job is to turn
results/runs.jsonl (already scored by eval/score.py) into something a
person can read once and then edit down into a real report, not to BE the
real report.

If eval/score.py has been run more than once for the same call (the
matrix grows, a check gets fixed and re-run), only the latest run_id per
call stem is used - a report is a snapshot of where things stand now, not
a history of every run (that's what results/runs.jsonl itself is for).

CLI:
    python -m eval.report --target pga
    python -m eval.report --target pga --out results/report_pga.md
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "results"
RUNS_PATH = RESULTS_DIR / "runs.jsonl"

# Human-readable labels for machine check/rubric ids. A check or rubric id
# missing from these dicts still renders (falls back to the raw id) rather
# than raising - a new check shouldn't break reporting until someone gets
# around to labeling it.
CHECK_LABELS = {
    "transfer_never_completes": "Transfer promised but never completes",
    "identity_disclosed_before_verification": "Identity disclosed before verification",
    "dead_air_prompted_caller": "Dead air prompted the caller to check the line was live",
    "phi_collected_before_failed_lookup": "PHI collected before a failed lookup",
    "brand_name_misstated": "Brand name misstated",
    "inconsistent_failure_messaging": "Inconsistent failure messaging across calls",
}

RUBRIC_LABELS = {
    "symptoms_acknowledged": "Symptoms acknowledged",
    "human_request_honoured": "Human request honoured",
    "answer_within_competence": "Answer within competence (hallucination guard)",
}

DRAFT_NOTICE = (
    "> **Machine-generated draft.** Every finding below comes straight from "
    "`eval/score.py`'s deterministic checks and rubric verdicts against transcript "
    "text alone - nothing here has been cross-checked against call audio, weighed "
    "for severity, or filtered for ASR noise the way `bugs/BUG_REPORT.md`'s findings "
    "were. Read this as a starting point for a human pass, not a finished report."
)


def load_records(target: str, *, runs_path: Path = RUNS_PATH) -> list[dict]:
    """Every record in `runs_path` for `target`, oldest first. Returns an
    empty list if the file doesn't exist yet (score_target hasn't been run) -
    that's a normal state to report on, not an error."""
    if not runs_path.exists():
        return []
    records = []
    with runs_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("target") == target:
                records.append(record)
    return records


def _latest_run_per_call(records: list[dict]) -> list[dict]:
    """Keep only the most recent run_id per call stem. run_id is prefixed
    with a sortable UTC timestamp (see eval/score.py), so a plain string
    max works without parsing it back into a datetime."""
    latest: dict[str, dict] = {}
    for record in records:
        stem = record["call"]["stem"]
        current = latest.get(stem)
        if current is None or record["run_id"] > current["run_id"]:
            latest[stem] = record
    # Stable, readable ordering: by call stem (which sorts by the ordinal
    # prefix already used throughout this repo's filenames).
    return [latest[stem] for stem in sorted(latest)]


def _render_evidence(evidence: list[dict]) -> str:
    lines = []
    for item in evidence:
        quote = item.get("quote", "")
        turn_index = item.get("turn_index")
        prefix = f"[{turn_index}] " if turn_index is not None else ""
        lines.append(f">   {prefix}{quote}")
    return "\n".join(lines)


def _render_check_findings(records: list[dict]) -> str:
    """One section per per-call check id that fired `fail` on at least
    one call in this run, each listing the calls it fired on plus its
    cited evidence. A check that never fired isn't silently missing -
    it's named in the "checked and clean" summary line at the end."""
    by_check: dict[str, list[tuple[str, list[dict]]]] = defaultdict(list)
    all_check_ids: set[str] = set()

    for record in records:
        for result in record.get("checks", []):
            all_check_ids.add(result["id"])
            if result["status"] == "fail":
                by_check[result["id"]].append((record["call"]["stem"], result["evidence"]))

    if not all_check_ids:
        return ""

    sections = []
    for check_id in sorted(by_check):
        label = CHECK_LABELS.get(check_id, check_id)
        calls = by_check[check_id]
        heading = f"### {label} (`{check_id}`)\n\n"
        heading += f"**Calls:** {', '.join(stem for stem, _ in calls)} — **{len(calls)} of {len(records)}**\n"
        body_parts = []
        for stem, evidence in calls:
            body_parts.append(f"\n- `{stem}`:\n{_render_evidence(evidence)}")
        sections.append(heading + "".join(body_parts))

    clean = sorted(cid for cid in all_check_ids if cid not in by_check)
    footer = ""
    if clean:
        clean_labels = ", ".join(f"{CHECK_LABELS.get(cid, cid)} (`{cid}`)" for cid in clean)
        footer = f"\n\n**Checked and clean across all calls:** {clean_labels}"

    return "\n\n---\n\n".join(sections) + footer


def _render_cross_call_findings(records: list[dict]) -> str:
    """Cross-call checks (eval/checks.py's CROSS_CALL_CHECKS) run once
    over the whole call set, and eval/score.py copies that single result
    onto every record it produced - so unlike _render_check_findings,
    this reads one exemplar record rather than aggregating per call.
    Which calls are implicated comes from the evidence itself (each
    item's own "stem" field, set by inconsistent_failure_messaging),
    not from which records the result happened to be duplicated onto."""
    exemplar = next((r for r in records if r.get("cross_call_checks")), None)
    if exemplar is None:
        return ""

    sections = []
    all_check_ids: set[str] = set()
    fired_ids: set[str] = set()
    for result in exemplar["cross_call_checks"]:
        all_check_ids.add(result["id"])
        if result["status"] != "fail":
            continue
        fired_ids.add(result["id"])
        label = CHECK_LABELS.get(result["id"], result["id"])
        stems = sorted({e.get("stem", "?") for e in result["evidence"]})
        heading = f"### {label} (`{result['id']}`)\n\n"
        heading += f"**Calls implicated:** {', '.join(stems)}\n"
        body_parts = []
        for item in result["evidence"]:
            stem = item.get("stem", "?")
            body_parts.append(f"\n- `{stem}` [{item.get('turn_index')}]: {item.get('quote', '')}")
        sections.append(heading + "".join(body_parts))

    clean = sorted(cid for cid in all_check_ids if cid not in fired_ids)
    footer = ""
    if clean:
        clean_labels = ", ".join(f"{CHECK_LABELS.get(cid, cid)} (`{cid}`)" for cid in clean)
        footer = f"\n\n**Checked and clean:** {clean_labels}"

    return "\n\n---\n\n".join(sections) + footer


def _render_rubric_findings(records: list[dict]) -> str:
    """Same shape as _render_check_findings but for rubric verdicts.
    "na" verdicts are omitted from the failure listing (the situation
    never arose - not a finding) but still count toward "checked and
    clean" if no call ever failed that rubric. Rubric errors (e.g. an
    unusable verdict eval/judge.py discarded) get their own callout so
    they aren't confused with either a pass or a fail."""
    reasons = {r.get("rubrics_disabled_reason") for r in records}
    if reasons and all(reasons):
        # Every record agrees rubrics were off/disabled for this run.
        (reason,) = reasons if len(reasons) == 1 else (next(iter(reasons)),)
        return f"_Rubric scoring was not run for this report: {reason}_"

    by_rubric: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    errors: dict[str, list[str]] = defaultdict(list)
    seen_rubric_ids: set[str] = set()

    for record in records:
        for entry in record.get("rubrics", []):
            seen_rubric_ids.add(entry["id"])
            if "error" in entry:
                errors[entry["id"]].append(record["call"]["stem"])
            elif entry["verdict"] == "fail":
                by_rubric[entry["id"]].append((record["call"]["stem"], entry))

    if not seen_rubric_ids:
        return "_No rubric verdicts found in this run._"

    sections = []
    for rubric_id in sorted(by_rubric):
        label = RUBRIC_LABELS.get(rubric_id, rubric_id)
        calls = by_rubric[rubric_id]
        heading = f"### {label} (`{rubric_id}`)\n\n"
        heading += f"**Calls:** {', '.join(stem for stem, _ in calls)} — **{len(calls)} of {len(records)}**\n"
        body_parts = []
        for stem, entry in calls:
            turns = ", ".join(str(t) for t in entry.get("turns", []))
            body_parts.append(
                f"\n- `{stem}` (turns {turns}, confidence {entry.get('confidence')}): "
                f"{entry.get('rationale')}"
            )
        sections.append(heading + "".join(body_parts))

    for rubric_id, stems in sorted(errors.items()):
        label = RUBRIC_LABELS.get(rubric_id, rubric_id)
        sections.append(
            f"### ⚠ {label} (`{rubric_id}`) — verdict discarded as unusable\n\n"
            f"**Calls:** {', '.join(stems)} — the judge's response didn't cite turns "
            "and was dropped per BUILD_SPEC_EVAL_LAYER.md sec. 4 rather than trusted at face value."
        )

    clean = sorted(rid for rid in seen_rubric_ids if rid not in by_rubric and rid not in errors)
    footer = ""
    if clean:
        clean_labels = ", ".join(f"{RUBRIC_LABELS.get(rid, rid)} (`{rid}`)" for rid in clean)
        footer = f"\n\n**Checked and clean across all calls:** {clean_labels}"

    if not sections:
        return f"_No rubric failures found across {len(records)} call(s)._" + footer

    return "\n\n---\n\n".join(sections) + footer


def render_report(target: str, *, runs_path: Path = RUNS_PATH) -> str:
    """The full markdown report for `target`, built from the latest run
    of each call currently in `runs_path`. Raises ValueError (not a
    silent empty report) if nothing has been scored yet - an empty report
    file looks identical to "everything passed", which would be worse
    than an explicit error telling the caller to run eval/score.py first.
    """
    records = _latest_run_per_call(load_records(target, runs_path=runs_path))
    if not records:
        raise ValueError(
            f"No results found for target {target!r} in {runs_path}. "
            f"Run `python -m eval.score --target {target} --all` first."
        )

    stems = ", ".join(r["call"]["stem"] for r in records)
    lines = [
        f"# {target} — Eval Report (draft)",
        "",
        DRAFT_NOTICE,
        "",
        f"**Scope:** {len(records)} call(s) — {stems}",
        "",
        "---",
        "",
        "## Deterministic findings",
        "",
        _render_check_findings(records),
    ]

    cross_call_section = _render_cross_call_findings(records)
    if cross_call_section:
        lines += ["", "## Cross-call findings", "", cross_call_section]

    lines += ["", "## Rubric findings", "", _render_rubric_findings(records)]

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render results/runs.jsonl into a markdown draft report."
    )
    parser.add_argument("--target", required=True, help="Target slug (targets/<slug>.json).")
    parser.add_argument(
        "--out", help="Write the report to this path instead of printing it to stdout."
    )
    args = parser.parse_args()

    report = render_report(args.target)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report)
        print(f"Wrote report to {out_path}")
    else:
        print(report)


if __name__ == "__main__":
    main()
