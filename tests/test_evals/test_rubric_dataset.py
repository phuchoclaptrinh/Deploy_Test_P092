"""The 260-case rubric test set, held to the contract it is supposed to measure.

A test set is code. It can be wrong in the same ways -- a vocabulary that drifts
from the enum, a weight that no longer matches the rubric -- and when it is
wrong, every conclusion drawn from it is wrong in a way that looks like a model
problem. These tests check the dataset itself, before anybody spends a model call
on it.

The workbook is a working artefact rather than a fixture, so these tests skip
when it is absent instead of failing. What they must not do is pass quietly when
it is present and disagrees.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.domain.risk_scoring import BLOCKER_FLOORS, CRITERION_NAMES, CRITERION_WEIGHTS, SCORE_THRESHOLDS
from src.evals.rubric_dataset import (
    BLOCKER_CODE_BY_LOCAL_NAME,
    CRITERION_BY_LOCAL_NAME,
    CRITERION_COLUMNS,
    DEFAULT_DATASET,
    NEEDS_CLARIFICATION,
    READY,
    REJECTED,
    load_rubric_cases,
    load_workbook_rubric,
)

pytest.importorskip("openpyxl", reason="the rubric workbook needs openpyxl to read")
pytestmark = pytest.mark.skipif(
    not DEFAULT_DATASET.exists(), reason=f"{DEFAULT_DATASET} is not in this checkout"
)


@pytest.fixture(scope="module")
def cases():
    return load_rubric_cases()


@pytest.fixture(scope="module")
def workbook_rubric():
    return load_workbook_rubric()


# ---------------------------------------------------------------------------
# The vocabulary the dataset and the code have to share.
# ---------------------------------------------------------------------------


def test_every_dataset_blocker_name_maps_to_a_contract_code():
    assert set(BLOCKER_CODE_BY_LOCAL_NAME.values()) == set(BLOCKER_FLOORS)


def test_no_two_dataset_names_map_to_one_code():
    """A collision would silently merge two cases about different emergencies."""
    codes = list(BLOCKER_CODE_BY_LOCAL_NAME.values())
    assert len(set(codes)) == len(codes)


def test_the_criterion_columns_are_the_five_the_contract_names():
    assert set(CRITERION_COLUMNS) == set(CRITERION_NAMES)
    assert set(CRITERION_BY_LOCAL_NAME.values()) == set(CRITERION_NAMES)


# ---------------------------------------------------------------------------
# The dataset loads, and says what it claims to say.
# ---------------------------------------------------------------------------


def test_the_workbook_holds_two_hundred_and_sixty_cases(cases):
    assert len(cases) == 260
    assert len({case.tc_id for case in cases}) == 260


def test_every_case_states_one_of_the_three_expected_outcomes(cases):
    allowed = {READY, NEEDS_CLARIFICATION, REJECTED}
    unexpected = {case.tc_id: case.classification_state for case in cases if case.classification_state not in allowed}
    assert not unexpected, f"unrecognised classification states: {unexpected}"


#: The two rows that deliberately state an impossible score. Both say so in
#: their description ("Payload agent gia lap loi contract"): they exist to check
#: that the contract refuses an out-of-scale number rather than clamping it.
CONTRACT_VIOLATION_CASES = {"TC-256", "TC-257"}


def test_every_score_the_dataset_states_is_on_the_scale(cases):
    off_scale = {
        case.tc_id: case.criteria
        for case in cases
        if case.tc_id not in CONTRACT_VIOLATION_CASES
        and any(value is not None and not (0 <= value <= 4) for value in case.criteria.values())
    }
    assert not off_scale, f"scores outside 0-4 in rows that are not about that: {off_scale}"


def test_the_deliberate_contract_violations_are_actually_refused(cases):
    """The two off-scale rows, run against the contract they are testing.

    Asserted rather than excused. A row claiming to test a rejection is worth
    nothing unless something rejects it, and `RiskCriteriaPayload` is what does:
    `ge=0, le=4` on each field, so an out-of-scale score is a validation error
    and never a clamped value.
    """
    from pydantic import ValidationError

    from src.models.agent_schemas import RiskCriteriaPayload

    by_id = {case.tc_id: case for case in cases}
    for tc_id in sorted(CONTRACT_VIOLATION_CASES):
        case = by_id[tc_id]
        assert case.criteria_complete, f"{tc_id} should carry five scores to be refused on one"
        with pytest.raises(ValidationError):
            RiskCriteriaPayload(**{name: case.criteria[name] for name in CRITERION_NAMES})


def test_a_case_that_expects_a_conclusion_carries_the_five_scores(cases):
    """The contract has no exit that concludes on four criteria, so a row
    expecting one is a row the runner cannot evaluate."""
    incomplete = [
        case.tc_id for case in cases if case.classification_state == READY and not case.criteria_complete
    ]
    assert incomplete == ["TC-179", "TC-180"], (
        "The set of conclusion-expecting rows with missing scores changed. Both known "
        f"rows are duplicate-driven cases whose scores come from a master; got {incomplete}."
    )


def test_every_blocker_a_case_claims_has_a_floor(cases):
    for case in cases:
        for code in case.blockers:
            assert code in BLOCKER_FLOORS, f"{case.tc_id} claims {code} which floors nothing"


# ---------------------------------------------------------------------------
# The workbook recomputes the rubric. That copy has to agree with this one.
# ---------------------------------------------------------------------------


def test_the_thresholds_agree(workbook_rubric):
    for floor, band in SCORE_THRESHOLDS:
        theirs = workbook_rubric.thresholds.get(band)
        if theirs is not None:
            assert theirs == floor, f"{band.value}: workbook starts at {theirs}, contract at {floor}"


def test_the_blocker_floors_agree(workbook_rubric):
    assert workbook_rubric.blocker_floors == dict(BLOCKER_FLOORS)


def test_the_workbook_weights_still_sum_to_a_hundred(workbook_rubric):
    assert sum(workbook_rubric.weights.values()) == Decimal(100)


def test_the_workbook_weights_agree_with_the_contract(workbook_rubric):
    """The workbook computes the score itself, so its weights are a second copy.

    They disagreed once -- the contract carried 30/25/20/15/10 while the
    workbook carried 25/5/50/15/5 -- and every expected score in the file was
    produced by the workbook's numbers. The workbook won, and this is what
    stops the two drifting apart again: a rubric change that does not reach the
    spreadsheet fails here rather than in 197 case comparisons that each look
    like a model error.
    """
    assert workbook_rubric.weights == dict(CRITERION_WEIGHTS)
