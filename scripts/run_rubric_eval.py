"""Check the 260-case rubric test set against the live implementation.

    python scripts/run_rubric_eval.py                     # summary
    python scripts/run_rubric_eval.py --show 20           # plus the first 20 mismatches
    python scripts/run_rubric_eval.py --json out.json     # machine-readable

What this does *not* do is call a model. It checks the half of every case that
is arithmetic: given the five criterion scores and the blockers a case says the
Agent should produce, does `src/domain/risk_scoring.py` produce the risk score,
the score band and the final priority the case expects?

That is worth separating from a model run for two reasons. It is free, so it can
be part of a normal test loop. And when it fails, the failure is about the rubric
or the dataset rather than about the model -- which is exactly the confusion an
end-to-end eval creates when the numbers underneath it have drifted.

The first thing it reports is whether the workbook's own `Rubric` sheet still
agrees with the contract. The workbook recomputes the score in spreadsheet
formulas over its own weights, so if those weights differ, every expected score
in the file was produced by a different rubric and no per-case comparison below
means anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.domain.risk_scoring import (  # noqa: E402
    BLOCKER_FLOORS,
    CRITERION_NAMES,
    CRITERION_WEIGHTS,
    SCORE_THRESHOLDS,
    RiskCriterionScores,
    calculate_risk_score,
)
from src.evals.rubric_dataset import (  # noqa: E402
    DEFAULT_DATASET,
    READY,
    RubricCase,
    load_rubric_cases,
    load_workbook_rubric,
)


def compare_rubrics(path: Path) -> list[str]:
    """Where the workbook's own constants differ from the contract's."""
    workbook = load_workbook_rubric(path)
    differences: list[str] = []
    for name in CRITERION_NAMES:
        theirs = workbook.weights.get(name)
        ours = CRITERION_WEIGHTS[name]
        if theirs is None:
            differences.append(f"weight {name}: workbook has none, contract has {ours}")
        elif theirs != ours:
            differences.append(f"weight {name}: workbook {theirs}, contract {ours}")

    contract_thresholds = {band: floor for floor, band in SCORE_THRESHOLDS}
    for band, floor in contract_thresholds.items():
        theirs = workbook.thresholds.get(band)
        if theirs is not None and theirs != floor:
            differences.append(f"threshold {band.value}: workbook {theirs}, contract {floor}")

    for code, band in BLOCKER_FLOORS.items():
        theirs = workbook.blocker_floors.get(code)
        if theirs is None:
            differences.append(f"blocker {code.value}: absent from the workbook")
        elif theirs is not band:
            differences.append(f"blocker {code.value}: workbook floors at {theirs.value}, contract at {band.value}")
    return differences


def check_case(case: RubricCase) -> dict[str, object] | None:
    """One case, recomputed. `None` when it agrees, a report when it does not."""
    if not case.criteria_complete:
        return None
    if case.expected_risk_score is None:
        # A formula Excel never cached. Reported separately, not as a mismatch:
        # the dataset states no expectation here to disagree with.
        return {"tc_id": case.tc_id, "kind": "uncomputed"}

    scores = RiskCriterionScores(**{name: case.criteria[name] for name in CRITERION_NAMES})
    result = calculate_risk_score(
        scores,
        blocker_codes=case.blockers,
        backend_scope_score=(
            case.effective_scope_score if case.effective_scope_score != case.criteria["affected_scope"] else None
        ),
    )
    problems: list[str] = []
    if result.risk_score != case.expected_risk_score:
        problems.append(f"risk_score {result.risk_score} != expected {case.expected_risk_score}")
    if case.expected_score_priority and result.score_priority is not case.expected_score_priority:
        problems.append(
            f"score band {result.score_priority.value} != expected {case.expected_score_priority.value}"
        )
    if case.expected_final_priority and result.final_priority is not case.expected_final_priority:
        problems.append(
            f"final priority {result.final_priority.value} != expected {case.expected_final_priority.value}"
        )
    if not problems:
        return None
    return {
        "tc_id": case.tc_id,
        "kind": "mismatch",
        "group": case.group,
        "criteria": {name: case.criteria[name] for name in CRITERION_NAMES},
        "blockers": [code.value for code in case.blockers],
        "computed_risk_score": str(result.risk_score),
        "expected_risk_score": str(case.expected_risk_score),
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--show", type=int, default=5, help="How many mismatching cases to print in full.")
    parser.add_argument("--json", type=Path, default=None, help="Write the full report here.")
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"Dataset not found: {args.dataset}", file=sys.stderr)
        return 2

    cases = load_rubric_cases(args.dataset)
    print(f"Dataset: {args.dataset}")
    print(f"Cases:   {len(cases)}")

    states = Counter(case.classification_state for case in cases)
    print("  by expected classification state: " + ", ".join(f"{k}={v}" for k, v in sorted(states.items())))
    reviewed = sum(1 for case in cases if case.human_reviewed)
    print(f"  human-reviewed rows: {reviewed}/{len(cases)}")

    print("\n--- Rubric constants ---")
    differences = compare_rubrics(args.dataset)
    if differences:
        print("The workbook does NOT compute the same rubric as the contract:")
        for line in differences:
            print(f"  * {line}")
        print(
            "\nEvery expected score below was produced by the workbook's weights, so the\n"
            "per-case comparison measures the gap between two rubrics, not the quality of\n"
            "any case. Reconcile `docs/risk_scoring_v2.md` and the `Rubric` sheet first."
        )
    else:
        print("Workbook weights, thresholds and blocker floors match the contract.")

    print("\n--- Per-case arithmetic ---")
    reports = [report for report in (check_case(case) for case in cases) if report is not None]
    mismatches = [report for report in reports if report["kind"] == "mismatch"]
    uncomputed = [report for report in reports if report["kind"] == "uncomputed"]
    scorable = [case for case in cases if case.criteria_complete]
    agreed = len(scorable) - len(mismatches) - len(uncomputed)
    print(f"  cases with all five scores: {len(scorable)}")
    print(f"  agree with the implementation: {agreed}")
    print(f"  disagree: {len(mismatches)}")
    print(f"  no cached expected score: {len(uncomputed)}")

    incomplete = [case for case in cases if not case.criteria_complete and case.classification_state == READY]
    if incomplete:
        print(
            f"\n  {len(incomplete)} case(s) expect a conclusion but do not carry five scores: "
            + ", ".join(case.tc_id for case in incomplete[:10])
        )

    for report in mismatches[: args.show]:
        print(f"\n  {report['tc_id']} [{report['group']}]")
        print(f"    criteria: {report['criteria']}")
        if report["blockers"]:
            print(f"    blockers: {', '.join(report['blockers'])}")
        for problem in report["problems"]:
            print(f"    - {problem}")
    if len(mismatches) > args.show:
        print(f"\n  ... and {len(mismatches) - args.show} more. Use --show or --json.")

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "dataset": str(args.dataset),
                    "case_count": len(cases),
                    "rubric_differences": differences,
                    "reports": reports,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nWrote {args.json}")

    # A non-zero exit when anything disagrees, so this can gate a pipeline.
    return 1 if differences or mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
