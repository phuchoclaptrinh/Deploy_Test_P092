"""The §3 hard constraints, and the line between hard and advisory.

§3's list is closed. These tests exist mostly to stop it growing: a workload cap
or a "too far away" rule added here would silently become a thing the agent may
not override and the board must refuse, and §3 does not put either on the list.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.dispatch.eligibility import (
    EligibilityInput,
    eligible_technician_ids,
    hard_constraint_violations,
    is_eligible,
)
from src.dispatch.shift import VN_TZ
from src.models.enums import PlacementWarningCode

WATER = uuid4()
ELEVATOR = uuid4()
IN_SHIFT = datetime.fromisoformat("2026-08-26T10:00").replace(tzinfo=VN_TZ).astimezone(UTC)
AFTER_HOURS = datetime.fromisoformat("2026-08-26T21:00").replace(tzinfo=VN_TZ).astimezone(UTC)


def technician(
    number: int = 1,
    *,
    active: bool = True,
    available: bool = True,
    user_active: bool = True,
    skills: frozenset[UUID] = frozenset({WATER}),
) -> EligibilityInput:
    return EligibilityInput(
        technician_id=UUID(int=number),
        is_active=active,
        is_available=available,
        user_is_active=user_active,
        skill_category_ids=skills,
    )


def test_a_fully_qualified_technician_inside_the_shift_passes():
    assert hard_constraint_violations(technician(), category_id=WATER, now=IN_SHIFT) == ()
    assert is_eligible(technician(), category_id=WATER, now=IN_SHIFT) is True


def test_a_missing_skill_is_never_overridable():
    """§3: a technician who lacks the required skill must never be selected."""
    violations = hard_constraint_violations(technician(), category_id=ELEVATOR, now=IN_SHIFT)
    assert PlacementWarningCode.MISSING_SKILL in violations


def test_an_inactive_or_unavailable_technician_fails():
    for row in (
        technician(active=False),
        technician(available=False),
        technician(user_active=False),
    ):
        assert PlacementWarningCode.TECHNICIAN_UNAVAILABLE in hard_constraint_violations(
            row, category_id=WATER, now=IN_SHIFT
        )


def test_outside_the_working_shift_nobody_is_eligible():
    """§3's shift rule is about the clock, so it takes out the whole roster."""
    roster = [technician(1), technician(2)]
    assert eligible_technician_ids(roster, category_id=WATER, now=AFTER_HOURS) == []
    assert PlacementWarningCode.OUT_OF_SHIFT in hard_constraint_violations(
        technician(), category_id=WATER, now=AFTER_HOURS
    )


def test_a_technician_who_already_refused_this_work_is_excluded():
    """Scoped to one work item, not a blacklist.

    The same technician stays an ordinary candidate for every other ticket,
    which is why the exclusion is a parameter rather than a profile flag.
    """
    row = technician(7)
    excluded = frozenset({row.technician_id})

    assert not is_eligible(row, category_id=WATER, now=IN_SHIFT, excluded_technician_ids=excluded)
    assert is_eligible(row, category_id=WATER, now=IN_SHIFT) is True


def test_the_reported_violations_lead_with_the_pairing_problem():
    """A UI showing one badge should show "no skill", not "out of shift".

    The first is about this pairing and will not clear on its own; the second is
    about the clock and will.
    """
    violations = hard_constraint_violations(
        technician(available=False), category_id=ELEVATOR, now=AFTER_HOURS
    )
    assert violations[0] is PlacementWarningCode.MISSING_SKILL
    assert violations[-1] is PlacementWarningCode.OUT_OF_SHIFT


def test_a_technician_failing_two_rules_reports_unavailable_once():
    violations = hard_constraint_violations(
        technician(9, active=False),
        category_id=WATER,
        now=IN_SHIFT,
        excluded_technician_ids=frozenset({UUID(int=9)}),
    )
    assert violations.count(PlacementWarningCode.TECHNICIAN_UNAVAILABLE) == 1


def test_workload_and_schedule_risk_are_not_hard_constraints():
    """§3 lists neither, and this is where that stays true.

    They are real signals -- the board shows both -- but a placement that merely
    makes someone's day long is Building Management's judgement to make, while
    one that hands work to someone without the skill is not.
    """
    violations = hard_constraint_violations(technician(), category_id=WATER, now=IN_SHIFT)
    assert PlacementWarningCode.OVERLOADED not in violations
    assert PlacementWarningCode.SCHEDULE_RISK not in violations


def test_the_candidate_set_is_sorted_and_contains_only_eligible_ids():
    roster = [
        technician(3),
        technician(1, skills=frozenset({ELEVATOR})),
        technician(2, available=False),
        technician(4),
    ]
    assert eligible_technician_ids(roster, category_id=WATER, now=IN_SHIFT) == sorted(
        [UUID(int=3), UUID(int=4)], key=str
    )


def test_a_null_category_skips_the_skill_check_only():
    """A ticket with no category cannot demand a skill, but everything else holds."""
    assert is_eligible(technician(skills=frozenset()), category_id=None, now=IN_SHIFT) is True
    assert is_eligible(technician(skills=frozenset(), active=False), category_id=None, now=IN_SHIFT) is False
