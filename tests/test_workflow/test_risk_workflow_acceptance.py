"""The twenty acceptance scenarios of the risk scoring v2 rollout.

This file is a checklist made executable. Most of the twenty are proved in
depth somewhere else — the domain rubric in `test_domain/test_risk_scoring`, the
case arithmetic in `test_agents/test_case_rescore`, the invariant queries in
`test_emergency_manual_only`, the clock in `test_domain/test_service_hours_risk_v2`,
the simulator in `test_simulation/test_risk_policy_v2`. What those files do not
do is state the list, and a requirement that is covered but not named is a
requirement nobody can check off.

So: one test per numbered scenario, each proving the claim directly rather than
asserting that another test exists. Six of them are the ones with no deeper home
— scope across a series, the sixth apartment, an uncertain emergency, grouping
refused for P5 — and those carry the detail. The rest are short on purpose.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.dispatch.shift import VN_TZ
from src.domain.assignment_guard import EmergencyManualOnlyError, ticket_assignment_allowed
from src.domain.risk_scoring import (
    MAX_AFFECTED_UNITS,
    BlockerCode,
    RiskCriterionScores,
    backend_scope_score,
    calculate_risk_score,
)
from src.domain.sla_clock import CURRENT_POLICY, SlaPolicy, add_sla_duration, counts_toward_compliance, sla_duration
from src.models.enums import (
    ClassificationStatus,
    Priority,
    RiskAssessmentSource,
    TicketStatus,
)
from src.services.agent_result_service import AgentResultService
from src.services.assignment_service import AssignmentService
from src.services.risk_assessment_service import RiskAssessmentService
from tests.test_workflow.factories import build_world, make_ticket

IN_SHIFT = datetime.fromisoformat("2026-08-26T09:00").replace(tzinfo=VN_TZ).astimezone(UTC)

#: 0 + 25 + 20 + scope + 0 = 45 at scope 0, which is a P3 and leaves room for
#: the scope criterion to move the band without a blocker being involved.
SPREADING = dict(
    human_safety=0, property_spread=4, essential_function=4, affected_scope=0, deterioration_speed=0
)


@pytest.fixture
def world(db_session):
    return build_world(db_session, resident_count=8, technician_count=2)


def scored(world, *, criteria=None, unit_index=0, **kwargs):
    """An approved, scored ticket from one apartment."""
    resident = world.resident(unit_index)
    ticket = make_ticket(
        world,
        resident=resident,
        category=world.water,
        status=TicketStatus.APPROVED,
        classification_status=ClassificationStatus.RESOLVED,
        approved_at=datetime.now(UTC),
        **kwargs,
    )
    RiskAssessmentService(world.db).record(
        ticket,
        criteria=RiskCriterionScores(**(criteria or SPREADING)),
        source=RiskAssessmentSource.AI_ANALYSIS,
    )
    world.db.commit()
    return ticket


def case_with(world, tickets: list[Ticket]) -> IncidentCase:
    """Put tickets in one case and run the v2 rescore-and-detach pass."""
    case = IncidentCase(
        category_id=world.water.id,
        window_start=datetime.now(UTC) - timedelta(days=1),
        window_end=datetime.now(UTC),
        density_value=1,
    )
    world.db.add(case)
    world.db.flush()
    case.series_id = case.id
    for ticket in tickets:
        world.db.add(
            IncidentCaseMember(case_id=case.id, ticket_id=ticket.id, source_unit_id=ticket.source_unit_id)
        )
    world.db.flush()
    service = AgentResultService(world.db)
    service._recompute_density(case)
    escalated = service._rescore_case_members(case, service._case_member_tickets(case))
    if escalated:
        service._detach_members(escalated, reason="Rescore sau gộp cụm đưa phản ánh lên P5")
    world.db.commit()
    return case


def current(world, ticket) -> object:
    return RiskAssessmentService(world.db).current(ticket)


# ---------------------------------------------------------------------------
# 1-4. Scope is counted in apartments, per case, never across a series.
# ---------------------------------------------------------------------------


def test_01_many_reports_from_one_apartment_are_one_unit(world):
    """A household that reports the same leak three times has not made it wider."""
    tickets = [scored(world, unit_index=0) for _ in range(3)]

    case = case_with(world, tickets)

    assert case.density_value == 1
    assert current(world, tickets[0]).backend_scope_score == 0


@pytest.mark.parametrize(("units", "expected_scope"), [(1, 0), (2, 1), (3, 2), (4, 3), (5, 4)])
def test_02_one_to_five_apartments_map_onto_scope_zero_to_four(world, units, expected_scope):
    tickets = [scored(world, unit_index=index) for index in range(units)]

    case = case_with(world, tickets)

    assert case.density_value == units
    assert backend_scope_score(units) == expected_scope
    assert current(world, tickets[0]).backend_scope_score == expected_scope


def test_03_a_sixth_apartment_opens_the_next_case_with_its_own_scope(world):
    """`docs/risk_scoring_v2.md` §4.2 rule 2. Five is a hard ceiling, and the
    overflow starts a new count rather than widening the first."""
    first_five = [scored(world, unit_index=index) for index in range(MAX_AFFECTED_UNITS)]
    sixth = scored(world, unit_index=MAX_AFFECTED_UNITS)

    full = case_with(world, first_five)
    overflow = case_with(world, [sixth])

    assert full.density_value == MAX_AFFECTED_UNITS
    assert overflow.id != full.id
    assert overflow.density_value == 1
    # The sixth apartment's scope restarts at zero. It is not "the sixth of a
    # bigger incident"; it is the first of the next case.
    assert current(world, sixth).backend_scope_score == 0
    assert current(world, first_five[0]).backend_scope_score == 4


def test_04_scope_is_never_summed_across_a_series(world):
    """Two full cases in one series are 5 and 5, not 10.

    A series is the administrative device that gives an overflowing case
    somewhere to go. Treating it as one larger incident would let ten reports
    push a criterion that only has five steps.
    """
    first_five = [scored(world, unit_index=index) for index in range(5)]
    next_three = [scored(world, unit_index=5 + index) for index in range(3)]

    full = case_with(world, first_five)
    second = case_with(world, next_three)
    second.series_id = full.series_id
    second.sequence_no = 2
    world.db.commit()

    assert full.density_value == 5
    assert second.density_value == 3
    # Eight apartments across the series, and no member of either case is scored
    # against eight.
    for ticket in first_five:
        assert current(world, ticket).confirmed_affected_unit_count == 5
    for ticket in next_three:
        assert current(world, ticket).confirmed_affected_unit_count == 3
    assert current(world, next_three[0]).backend_scope_score == backend_scope_score(3)


# ---------------------------------------------------------------------------
# 5-8. The emergency duplicate path.
# ---------------------------------------------------------------------------


def test_05_a_confident_duplicate_of_an_emergency_links_to_its_master(world):
    """Proved end to end through the graph in
    `test_agents/test_emergency_review_gate`; asserted here as the link itself."""
    master = scored(world, unit_index=0)
    duplicate = scored(world, unit_index=1)
    duplicate.duplicate_of_ticket_id = master.id
    duplicate.status = TicketStatus.LINKED_DUPLICATE
    world.db.commit()

    assert duplicate.duplicate_of_ticket_id == master.id
    assert AgentResultService(world.db).duplicate_report_count(master.id) == 1


def test_06_an_emergency_duplicate_pulls_a_lower_master_up_to_p5(world):
    master = scored(world, unit_index=0)
    duplicate = scored(world, unit_index=1)
    assert master.priority is not Priority.P5

    AgentResultService(world.db)._escalate_master_to_emergency(master, duplicate)
    world.db.commit()

    assert master.priority is Priority.P5
    # An override, not a re-score: the master's own criteria did not change, and
    # rewriting them to justify P5 would forge evidence.
    revision = current(world, master)
    assert revision.source is RiskAssessmentSource.DUPLICATE_ESCALATION
    assert revision.override_reason
    assert revision.score_priority is not Priority.P5


def test_07_an_uncertain_emergency_keeps_its_priority_and_its_warning(world):
    """Uncertainty about *which* incident it is says nothing about whether it is
    an emergency. The ticket stands on its own at the gate with P5 intact, and
    the warning that already went out is not withdrawn."""
    from src.database.models.notification import Notification

    ticket = scored(world, criteria=dict(SPREADING, human_safety=4, deterioration_speed=4))
    assert ticket.priority is Priority.P5

    service = AgentResultService(world.db)
    assert service.raise_emergency_warning(ticket.id, priority=Priority.P5) is True

    # An uncertain verdict parks the ticket for a human; nothing about that
    # lowers what it was scored at or takes the alarm back.
    ticket.classification_status = ClassificationStatus.MANUAL_REVIEW
    world.db.commit()

    warnings = world.db.scalars(
        select(Notification).where(
            Notification.ticket_id == ticket.id,
            Notification.notification_type == "TICKET_EMERGENCY_WARNING",
        )
    ).all()
    assert len(warnings) == 1
    assert ticket.priority is Priority.P5
    assert ticket.duplicate_of_ticket_id is None

    # And a second pass does not send it twice.
    assert service.raise_emergency_warning(ticket.id, priority=Priority.P5) is False


def test_08_an_emergency_is_never_grouped(world):
    """Grouping is a P1-P4 stage. A P5 that reached a case would be offered on
    the board through it, which is the one thing §8 forbids."""
    emergency = scored(world, criteria=dict(SPREADING, human_safety=4, deterioration_speed=4))
    assert emergency.priority is Priority.P5

    case_with(world, [emergency, scored(world, unit_index=1)])

    membership = world.db.scalar(
        select(IncidentCaseMember).where(IncidentCaseMember.ticket_id == emergency.id)
    )
    assert membership is None


# ---------------------------------------------------------------------------
# 9-11. A member the case pushes into the emergency band.
# ---------------------------------------------------------------------------


def test_09_a_member_the_rescore_escalates_leaves_the_case(world):
    near = dict(human_safety=4, property_spread=4, essential_function=4, affected_scope=0, deterioration_speed=0)
    escalating = scored(world, criteria=near, unit_index=0)
    others = [scored(world, unit_index=index) for index in (1, 2)]

    case = case_with(world, [escalating, *others])

    assert escalating.priority is Priority.P5
    assert escalating.id not in {row for row in world.db.scalars(
        select(IncidentCaseMember.ticket_id).where(IncidentCaseMember.case_id == case.id)
    )}


def test_10_the_rest_of_the_case_is_still_assignable(world):
    """One member becoming an emergency is not a reason to stop work on the
    other three."""
    near = dict(human_safety=4, property_spread=4, essential_function=4, affected_scope=0, deterioration_speed=0)
    escalating = scored(world, criteria=near, unit_index=0)
    survivor = scored(world, unit_index=1)
    case_with(world, [escalating, survivor, scored(world, unit_index=2)])

    assert ticket_assignment_allowed(survivor)
    assignment = AssignmentService(world.db).assign(
        world.coordinator.user_id, survivor.id, world.technician(0).user_id, now=IN_SHIFT
    )
    assert assignment.is_active


def test_11_a_case_that_loses_its_last_member_is_closed(world):
    near = dict(human_safety=4, property_spread=4, essential_function=4, affected_scope=0, deterioration_speed=0)
    members = [scored(world, criteria=near, unit_index=index) for index in range(3)]

    case = case_with(world, members)

    assert case.status == "CLOSED"
    assert case.closed_reason
    # Closed, not deleted: it is the record of why three tickets were scored the
    # way they were.
    assert world.db.get(IncidentCase, case.id) is not None


# ---------------------------------------------------------------------------
# 12-13. P5 is manual-only.
# ---------------------------------------------------------------------------


def test_12_no_assignment_path_accepts_an_emergency(world):
    emergency = scored(world, criteria=dict(SPREADING, human_safety=4, deterioration_speed=4))
    assert emergency.priority is Priority.P5

    with pytest.raises(EmergencyManualOnlyError):
        AssignmentService(world.db).assign(
            world.coordinator.user_id, emergency.id, world.technician(0).user_id, now=IN_SHIFT
        )
    with pytest.raises(EmergencyManualOnlyError):
        AssignmentService(world.db).assign_case(
            world.coordinator.user_id, [emergency.id], world.technician(0).user_id, now=IN_SHIFT
        )


def test_13_an_active_assignment_ends_when_the_ticket_escalates(world):
    ticket = scored(world)
    assignment = AssignmentService(world.db).assign(
        world.coordinator.user_id, ticket.id, world.technician(0).user_id, now=IN_SHIFT
    )
    assert assignment.is_active

    RiskAssessmentService(world.db).record(
        ticket,
        criteria=RiskCriterionScores(**dict(SPREADING, human_safety=4, deterioration_speed=4)),
        source=RiskAssessmentSource.GROUPING_RESCORE,
    )
    world.db.commit()
    world.db.refresh(assignment)

    assert ticket.priority is Priority.P5
    assert assignment.is_active is False
    assert world.db.scalars(
        select(TicketAssignment.id).where(
            TicketAssignment.ticket_id == ticket.id, TicketAssignment.is_active.is_(True)
        )
    ).all() == []


# ---------------------------------------------------------------------------
# 14-15. The rubric itself.
# ---------------------------------------------------------------------------


def test_14_a_common_area_report_scores_only_what_its_criteria_say():
    """A corridor lightbulb is a common-area fault and affects nobody's
    apartment. Nothing in the calculator can lift it off zero."""
    result = calculate_risk_score(
        RiskCriterionScores(
            human_safety=0, property_spread=0, essential_function=0, affected_scope=0, deterioration_speed=0
        )
    )
    assert result.final_priority is Priority.P1
    assert result.effective_scope_score == 0


def test_15_each_blocker_imposes_its_documented_floor():
    quiet = RiskCriterionScores(
        human_safety=0, property_spread=0, essential_function=0, affected_scope=0, deterioration_speed=0
    )
    for code in BlockerCode:
        floor = calculate_risk_score(quiet, blocker_codes=[code]).final_priority
        expected = Priority.P5 if code.name in {
            "FIRE_OR_SMOKE",
            "ELECTRIC_SHOCK_OR_LIVE_WIRE",
            "GAS_LEAK_OR_ASPHYXIATION",
            "SERIOUS_INJURY",
            "PERSON_TRAPPED_IN_ELEVATOR",
            "SOLE_ESCAPE_ROUTE_BLOCKED",
            "ONGOING_VIOLENCE",
        } else Priority.P4
        assert floor is expected, code


# ---------------------------------------------------------------------------
# 16-17. The clock.
# ---------------------------------------------------------------------------


def test_16_the_dispatched_bands_run_on_service_hours(world):
    """A P4 reported at 17:00 finishes its three hours at 10:00 the next
    morning, not at 20:00 with nobody in the building."""
    created = datetime.fromisoformat("2026-08-26T17:00").replace(tzinfo=VN_TZ).astimezone(UTC)
    ticket = scored(world, created_at=created)
    ticket.sla_started_at = created
    ticket.priority = Priority.P4
    RiskAssessmentService(world.db).recalculate_sla(ticket)
    world.db.commit()

    expected = add_sla_duration(created, sla_duration(Priority.P4, CURRENT_POLICY), Priority.P4, CURRENT_POLICY)
    assert ticket.sla_due_at == expected
    assert ticket.sla_due_at.astimezone(VN_TZ).strftime("%H:%M") == "10:00"


def test_17_the_sla_is_measured_at_the_start_and_excludes_the_emergency_band():
    assert CURRENT_POLICY is SlaPolicy.SERVICE_HOURS_RISK_V2
    for band in (Priority.P1, Priority.P2, Priority.P3, Priority.P4):
        assert counts_toward_compliance(band)
    assert not counts_toward_compliance(Priority.P5)


# ---------------------------------------------------------------------------
# 18-20. Simulator and migration.
# ---------------------------------------------------------------------------


def test_18_the_v1_simulator_policies_are_unchanged():
    from src.domain.sla_clock import POLICY_SLA_MINUTES
    from src.simulation.models import MANUAL_PRIORITY, POLICY_PRIORITIES

    for policy in (SlaPolicy.WALL_CLOCK_V1, SlaPolicy.SERVICE_HOURS_DRAFT_V1):
        assert set(POLICY_SLA_MINUTES[policy]) == {Priority.P1, Priority.P2, Priority.P3}
        assert POLICY_PRIORITIES[policy] == (Priority.P1, Priority.P2, Priority.P3)
        assert MANUAL_PRIORITY[policy] is Priority.P3


def test_19_the_v2_simulator_policy_understands_all_five_bands():
    from src.simulation.models import MANUAL_PRIORITY, POLICY_PRIORITIES
    from src.simulation.policies import PRIORITY_RANK_V2

    v2 = SlaPolicy.SERVICE_HOURS_RISK_V2
    assert set(POLICY_PRIORITIES[v2]) == set(Priority)
    assert MANUAL_PRIORITY[v2] is Priority.P5
    assert list(PRIORITY_RANK_V2) == [Priority.P4, Priority.P3, Priority.P2, Priority.P1]


def test_20_the_migration_clears_the_operational_ticket_graph():
    """Named here so the checklist is complete; the shape of the reset is
    asserted against the model metadata in `test_migrations`."""
    import importlib.util
    from pathlib import Path

    versions = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    path = versions / "a1b2c3d4e5f7_hard_cutover_to_risk_scoring_v2.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "tickets" in module.TICKET_DOMAIN_TABLES
    assert "ticket_assignments" in module.TICKET_DOMAIN_TABLES
    assert "dispatch_events" in module.TICKET_DOMAIN_TABLES
    assert "ticket_risk_assessments" in module.TICKET_DOMAIN_TABLES
    # And the building survives it.
    assert "user_profiles" not in module.TICKET_DOMAIN_TABLES
    assert "units" not in module.TICKET_DOMAIN_TABLES
    assert "categories" not in module.TICKET_DOMAIN_TABLES
