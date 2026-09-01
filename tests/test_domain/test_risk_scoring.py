"""The rubric of `docs/risk_scoring_v2.md`, checked against the calculator.

Every number here is quoted from that document rather than derived from the
implementation. When one of these fails, the question is which of the two is
wrong, and the document answers it.
"""

from __future__ import annotations

import ast
import inspect
from decimal import Decimal

import pytest

from src.domain import risk_scoring
from src.domain.risk_scoring import (
    BLOCKER_FLOORS,
    CRITERION_NAMES,
    CRITERION_WEIGHTS,
    RUBRIC_VERSION,
    BlockerCode,
    RiskCriterionScores,
    RiskScoringError,
    backend_scope_score,
    blocker_floor,
    calculate_risk_score,
    effective_scope_score,
    priority_from_score,
)
from src.models.enums import Priority


def scores(**overrides: int) -> RiskCriterionScores:
    """All five criteria at zero unless the test says otherwise."""
    base = dict.fromkeys(CRITERION_NAMES, 0)
    base.update(overrides)
    return RiskCriterionScores(**base)


# ---------------------------------------------------------------------------
# The formula
# ---------------------------------------------------------------------------


def test_the_five_weights_sum_to_one_hundred():
    assert sum(CRITERION_WEIGHTS.values()) == Decimal(100)


def test_all_zeroes_score_zero_and_land_in_the_lowest_band():
    result = calculate_risk_score(scores())
    assert result.risk_score == Decimal("0.00")
    assert result.final_priority is Priority.P1


def test_all_fours_score_one_hundred_and_land_in_the_top_band():
    result = calculate_risk_score(scores(**dict.fromkeys(CRITERION_NAMES, 4)))
    assert result.risk_score == Decimal("100.00")
    assert result.final_priority is Priority.P5


@pytest.mark.parametrize(
    ("criterion", "full_weight"),
    [
        ("human_safety", Decimal("35.00")),
        ("property_spread", Decimal("5.00")),
        ("essential_function", Decimal("35.00")),
        ("affected_scope", Decimal("20.00")),
        ("deterioration_speed", Decimal("5.00")),
    ],
)
def test_each_criterion_contributes_zero_at_zero_and_its_whole_weight_at_four(criterion, full_weight):
    assert calculate_risk_score(scores(**{criterion: 0})).risk_score == Decimal("0.00")
    assert calculate_risk_score(scores(**{criterion: 4})).risk_score == full_weight


@pytest.mark.parametrize("step", [0, 1, 2, 3, 4])
def test_a_criterion_is_linear_in_quarters_of_its_weight(step):
    expected = (Decimal(step) / Decimal(4) * CRITERION_WEIGHTS["human_safety"]).quantize(Decimal("0.01"))
    assert calculate_risk_score(scores(human_safety=step)).risk_score == expected


def test_the_score_is_the_sum_of_the_five_contributions():
    result = calculate_risk_score(scores(human_safety=2, property_spread=1, essential_function=4, deterioration_speed=3))
    # 17.50 + 1.25 + 35.00 + 0.00 + 3.75
    assert result.risk_score == Decimal("57.50")
    assert sum(result.contributions.values()) == Decimal("57.50")


def test_nothing_is_rounded_before_the_sum():
    # 1.25 and 3.75 are the two contributions that are not whole numbers of
    # points, and rounding either per-criterion would make this 57 or 58
    # instead of 57.50.
    result = calculate_risk_score(scores(property_spread=1, essential_function=4, human_safety=2, deterioration_speed=3))
    assert result.contributions["property_spread"] == Decimal("1.25")
    assert result.contributions["deterioration_speed"] == Decimal("3.75")
    assert result.risk_score == Decimal("57.50")


def test_every_result_carries_the_rubric_version_it_was_scored_under():
    assert calculate_risk_score(scores()).rubric_version == RUBRIC_VERSION


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        ("0", Priority.P1),
        ("19.99", Priority.P1),
        ("20", Priority.P2),
        ("39.99", Priority.P2),
        ("40", Priority.P3),
        ("59.99", Priority.P3),
        ("60", Priority.P4),
        ("79.99", Priority.P4),
        ("80", Priority.P5),
        ("100", Priority.P5),
    ],
)
def test_every_band_boundary_falls_on_the_documented_side(score, expected):
    assert priority_from_score(Decimal(score)) is expected


@pytest.mark.parametrize("score", ["-0.01", "100.01"])
def test_a_score_outside_zero_to_one_hundred_is_refused(score):
    with pytest.raises(RiskScoringError):
        priority_from_score(Decimal(score))


def test_a_float_score_is_refused_rather_than_coerced():
    with pytest.raises(RiskScoringError):
        priority_from_score(79.995)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Criterion validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CRITERION_NAMES)
@pytest.mark.parametrize("value", [-1, 5, 7, 100])
def test_a_criterion_outside_zero_to_four_is_refused_not_clamped(name, value):
    with pytest.raises(RiskScoringError):
        scores(**{name: value})


@pytest.mark.parametrize("name", CRITERION_NAMES)
def test_a_boolean_is_not_accepted_where_a_zero_to_four_judgement_belongs(name):
    with pytest.raises(RiskScoringError):
        scores(**{name: True})


@pytest.mark.parametrize("name", CRITERION_NAMES)
def test_a_non_integer_criterion_is_refused(name):
    with pytest.raises(RiskScoringError):
        scores(**{name: 2.5})


# ---------------------------------------------------------------------------
# Blockers
# ---------------------------------------------------------------------------


P5_BLOCKERS = [code for code, floor in BLOCKER_FLOORS.items() if floor is Priority.P5]
P4_BLOCKERS = [code for code, floor in BLOCKER_FLOORS.items() if floor is Priority.P4]


def test_the_contract_defines_exactly_eleven_blockers():
    assert len(BlockerCode) == 11
    assert len(P5_BLOCKERS) == 7
    assert len(P4_BLOCKERS) == 4


@pytest.mark.parametrize("code", P5_BLOCKERS)
def test_each_p5_blocker_floors_at_p5(code):
    assert blocker_floor([code]) is Priority.P5
    assert calculate_risk_score(scores(), blocker_codes=[code]).final_priority is Priority.P5


@pytest.mark.parametrize("code", P4_BLOCKERS)
def test_each_p4_blocker_floors_at_p4(code):
    assert blocker_floor([code]) is Priority.P4
    assert calculate_risk_score(scores(), blocker_codes=[code]).final_priority is Priority.P4


def test_a_p5_blocker_on_a_near_zero_score_still_produces_p5():
    result = calculate_risk_score(scores(deterioration_speed=1), blocker_codes=[BlockerCode.FIRE_OR_SMOKE])
    assert result.risk_score == Decimal("1.25")
    assert result.score_priority is Priority.P1
    assert result.blocker_floor is Priority.P5
    assert result.final_priority is Priority.P5
    assert result.blocker_raised_priority


def test_a_blocker_never_lowers_a_priority_the_score_already_earned():
    top = dict.fromkeys(CRITERION_NAMES, 4)
    result = calculate_risk_score(scores(**top), blocker_codes=[BlockerCode.SOLE_TOILET_UNUSABLE])
    assert result.score_priority is Priority.P5
    assert result.blocker_floor is Priority.P4
    assert result.final_priority is Priority.P5
    assert not result.blocker_raised_priority


def test_the_highest_floor_wins_when_several_blockers_apply():
    result = calculate_risk_score(
        scores(), blocker_codes=[BlockerCode.SEWAGE_OVERFLOW, BlockerCode.GAS_LEAK_OR_ASPHYXIATION]
    )
    assert result.final_priority is Priority.P5


def test_without_a_blocker_the_priority_is_the_score_band_and_nothing_else():
    result = calculate_risk_score(scores(human_safety=3, property_spread=2))
    assert result.risk_score == Decimal("28.75")
    assert result.blocker_floor is None
    assert result.final_priority is result.score_priority is Priority.P2


def test_no_blockers_is_the_default():
    assert calculate_risk_score(scores()).blocker_codes == ()


def test_an_unknown_blocker_code_is_refused_rather_than_ignored():
    with pytest.raises((RiskScoringError, ValueError)):
        calculate_risk_score(scores(), blocker_codes=["ROOF_LOOKS_ODD"])


def test_a_repeated_blocker_code_is_refused():
    with pytest.raises(RiskScoringError):
        calculate_risk_score(scores(), blocker_codes=[BlockerCode.FIRE_OR_SMOKE, BlockerCode.FIRE_OR_SMOKE])


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("units", "expected"), [(1, 0), (2, 1), (3, 2), (4, 3), (5, 4)])
def test_a_confirmed_apartment_count_maps_onto_the_scope_scale(units, expected):
    assert backend_scope_score(units) == expected


def test_the_scope_scale_saturates_rather_than_overflowing_past_five_units():
    assert backend_scope_score(6) == 4
    assert backend_scope_score(50) == 4


def test_a_count_below_one_is_refused_because_a_ticket_always_affects_its_own_unit():
    with pytest.raises(RiskScoringError):
        backend_scope_score(0)


def test_the_backend_count_overrules_the_agent_estimate_in_both_directions():
    assert effective_scope_score(4, 0) == 0
    assert effective_scope_score(0, 4) == 4


def test_the_agent_estimate_is_used_while_no_case_has_counted_anything():
    assert effective_scope_score(2, None) == 2


def test_the_formula_uses_the_effective_scope_and_reports_all_three_values():
    result = calculate_risk_score(scores(affected_scope=4), backend_scope_score=1)
    assert result.ai_scope_score == 4
    assert result.backend_scope_score == 1
    assert result.effective_scope_score == 1
    assert result.criteria.affected_scope == 1
    assert result.risk_score == Decimal("5.00")


# ---------------------------------------------------------------------------
# What the calculator is not allowed to know
# ---------------------------------------------------------------------------


def test_the_calculator_imports_nothing_from_the_catalog_or_the_building():
    # By AST, not by text: the docstring names Category precisely to say it is
    # not imported, and a substring check would fail on its own explanation.
    tree = ast.parse(inspect.getsource(risk_scoring))
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom | ast.Import)
        for alias in node.names
    }
    assert "Priority" in imported
    assert not imported & {"Category", "CategoryCatalog", "Location", "LocationType", "Severity"}


def test_no_entry_point_accepts_a_category_or_a_location():
    for function in (calculate_risk_score, priority_from_score, effective_scope_score, backend_scope_score):
        parameters = set(inspect.signature(function).parameters)
        assert not {"category", "category_id", "category_code"} & parameters
        assert not {"location", "location_id", "location_type_code"} & parameters


def test_a_common_area_report_has_no_way_to_reach_the_score_except_through_the_criteria():
    # A corridor lightbulb: common area, but nobody's apartment is affected and
    # nothing is unsafe. There is no code path that could lift this off 0.
    result = calculate_risk_score(scores())
    assert result.risk_score == Decimal("0.00")
    assert result.final_priority is Priority.P1


def test_the_calculator_takes_five_integers_and_no_precomputed_total():
    parameters = set(inspect.signature(calculate_risk_score).parameters)
    assert not {"risk_score", "score_total", "priority", "severity"} & parameters
