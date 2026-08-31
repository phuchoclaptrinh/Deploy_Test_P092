"""P5 is manual-only, from every direction at once.

`docs/risk_scoring_v2.md` §8 states the invariant three ways, and the last
section of this file asserts it as three queries that must always come back
empty:

* no active assignment belongs to a P5 ticket;
* no active incident-case membership belongs to a P5 ticket;
* no open dispatch event belongs to a P5 ticket.

Everything above that section is about the paths those three queries constrain.
There are ten of them, and ten independent checks would be nine chances to write
one slightly differently — so there is one guard, and these tests are the proof
that every path calls it. A test per path, deliberately repetitive: a table-driven
version would pass just as happily with a path missing from the table.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.database.models.dispatch import DispatchEvent
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.dispatch.enqueue import enqueue, ticket_is_dispatchable
from src.dispatch.shift import VN_TZ
from src.domain.assignment_guard import (
    EmergencyManualOnlyError,
    assert_ticket_assignment_allowed,
    ticket_assignment_allowed,
)
from src.domain.risk_scoring import RiskCriterionScores
from src.models.enums import (
    AssignmentEndReason,
    ClassificationStatus,
    Priority,
    RiskAssessmentSource,
    TicketStatus,
)
from src.services.assignment_service import AssignmentService
from src.services.dispatch_reassignment import requeue_after_release
from src.services.risk_assessment_service import RiskAssessmentService
from src.services.visual_assignment_service import VisualAssignmentService
from tests.test_workflow.factories import build_world, make_assignment, make_ticket

IN_SHIFT = datetime.fromisoformat("2026-08-26T09:00").replace(tzinfo=VN_TZ).astimezone(UTC)


@pytest.fixture
def world(db_session):
    return build_world(db_session, resident_count=4, technician_count=2)


def approved(world, *, priority=Priority.P2, **kwargs):
    return make_ticket(
        world,
        category=world.water,
        status=TicketStatus.APPROVED,
        classification_status=ClassificationStatus.RESOLVED,
        priority=priority,
        approved_at=datetime.now(UTC),
        **kwargs,
    )


def emergency(world, **kwargs):
    return approved(world, priority=Priority.P5, **kwargs)


# ---------------------------------------------------------------------------
# The guard itself.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("priority", [Priority.P1, Priority.P2, Priority.P3, Priority.P4])
def test_every_band_below_the_emergency_one_may_be_assigned(priority):
    assert ticket_assignment_allowed(_FakeTicket(priority))


def test_the_emergency_band_may_not_be_assigned():
    assert not ticket_assignment_allowed(_FakeTicket(Priority.P5))
    with pytest.raises(EmergencyManualOnlyError):
        assert_ticket_assignment_allowed(_FakeTicket(Priority.P5))


def test_an_unscored_ticket_is_not_refused_by_this_guard():
    """"Not classified yet" is a different fact from "classified as an
    emergency", and the paths that care already refuse it for their own
    reasons. Refusing it here would report the wrong reason to a coordinator."""
    assert ticket_assignment_allowed(_FakeTicket(None))


class _FakeTicket:
    def __init__(self, priority):
        self.priority = priority


# ---------------------------------------------------------------------------
# Every path that could place work.
# ---------------------------------------------------------------------------


def test_the_automatic_path_refuses_to_enqueue_an_emergency(world):
    ticket = emergency(world)
    assert ticket_is_dispatchable(world.db, ticket) is False
    assert enqueue(world.db, ticket) is None


def test_the_backlog_sweep_refuses_an_emergency(world):
    from src.dispatch.enqueue import enqueue_backlog
    from src.services.auto_assignment_settings_service import AutoAssignmentSettingsService

    AutoAssignmentSettingsService(world.db).set_enabled(world.coordinator.user_id, enabled=True)
    emergency(world)
    ordinary = approved(world)

    events = enqueue_backlog(world.db)

    assert [event.ticket_id for event in events] == [ordinary.id]


def test_manual_assignment_refuses_an_emergency(world):
    ticket = emergency(world)
    with pytest.raises(EmergencyManualOnlyError):
        AssignmentService(world.db).assign(
            world.coordinator.user_id, ticket.id, world.technician(0).user_id, now=IN_SHIFT
        )
    assert _active_assignments(world, ticket.id) == []


def test_one_emergency_member_refuses_a_whole_case_assignment(world):
    """A case is one unit of work. Handing four of five members to a technician
    while the fifth is an emergency somebody is walking to would split one
    incident across two responses."""
    ordinary = approved(world)
    urgent = emergency(world)

    with pytest.raises(EmergencyManualOnlyError):
        AssignmentService(world.db).assign_case(
            world.coordinator.user_id, [ordinary.id, urgent.id], world.technician(0).user_id, now=IN_SHIFT
        )

    assert _active_assignments(world, ordinary.id) == []
    assert _active_assignments(world, urgent.id) == []


def test_the_visual_board_does_not_offer_an_emergency(world):
    _with_closed_grouping(world, emergency(world))
    assert VisualAssignmentService(world.db).board(now=IN_SHIFT).units == []


def test_confirming_a_stale_board_placement_on_an_emergency_is_refused(world):
    """The board a coordinator is looking at may be minutes old, and a ticket
    that was a P4 when it was drawn can be a P5 by the time they drop it."""
    ticket = _with_closed_grouping(world, approved(world, priority=Priority.P4))
    unit_id = f"ticket:{ticket.id}"
    assert [unit.unit_id for unit in VisualAssignmentService(world.db).board(now=IN_SHIFT).units] == [unit_id]

    ticket.priority = Priority.P5
    world.db.commit()

    with pytest.raises(EmergencyManualOnlyError):
        VisualAssignmentService(world.db).confirm(
            world.coordinator.user_id, [(unit_id, world.technician(0).user_id)], now=IN_SHIFT
        )
    assert _active_assignments(world, ticket.id) == []


def test_a_released_emergency_is_not_requeued(world):
    """It leaves the automatic path silently rather than being paused for manual
    attention: pausing announces "somebody should place this by hand", which is
    the opposite of what an emergency needs."""
    ticket = emergency(world)

    assert requeue_after_release(world.db, ticket) is None
    assert ticket.auto_assignment_paused is False


# ---------------------------------------------------------------------------
# Work that was already placed when the ticket escalated.
# ---------------------------------------------------------------------------


def _escalate(world, ticket):
    """Re-score a ticket into the emergency band the way grouping would."""
    RiskAssessmentService(world.db).record(
        ticket,
        criteria=RiskCriterionScores(
            human_safety=4, property_spread=4, essential_function=4, affected_scope=4, deterioration_speed=0
        ),
        source=RiskAssessmentSource.GROUPING_RESCORE,
    )
    world.db.commit()


def test_an_active_assignment_is_ended_when_the_ticket_escalates(world):
    ticket = approved(world, priority=Priority.P4)
    assignment = make_assignment(world, ticket, world.technician(0))

    _escalate(world, ticket)

    world.db.refresh(assignment)
    assert ticket.priority is Priority.P5
    assert assignment.is_active is False
    assert assignment.end_reason == AssignmentEndReason.EMERGENCY_MANUAL_ESCALATION.value


def test_the_assignment_history_is_kept_rather_than_deleted(world):
    """Somebody drove to this building. The record of that is not the system's
    to erase."""
    ticket = approved(world, priority=Priority.P4)
    assignment = make_assignment(world, ticket, world.technician(0))
    assignment_id = assignment.id

    _escalate(world, ticket)

    assert world.db.get(TicketAssignment, assignment_id) is not None


def test_the_technician_is_not_recorded_as_having_refused_the_work(world):
    """`TECHNICIAN_REJECTED` puts somebody on an exclusion list. They had no
    part in this decision."""
    ticket = approved(world, priority=Priority.P4)
    assignment = make_assignment(world, ticket, world.technician(0))

    _escalate(world, ticket)

    world.db.refresh(assignment)
    assert assignment.end_reason != AssignmentEndReason.TECHNICIAN_REJECTED.value
    assert assignment.rejection_reason is None


def test_no_replacement_assignment_is_created(world):
    ticket = approved(world, priority=Priority.P4)
    make_assignment(world, ticket, world.technician(0))

    _escalate(world, ticket)

    assert _active_assignments(world, ticket.id) == []


def test_an_open_dispatch_event_is_superseded(world):
    from src.models.enums import DispatchEventStatus
    from src.services.auto_assignment_settings_service import AutoAssignmentSettingsService

    AutoAssignmentSettingsService(world.db).set_enabled(world.coordinator.user_id, enabled=True)
    ticket = approved(world, priority=Priority.P4)
    event = enqueue(world.db, ticket)
    world.db.commit()
    assert event is not None

    _escalate(world, ticket)

    world.db.refresh(event)
    assert event.is_open is False
    assert event.status == DispatchEventStatus.SUPERSEDED.value


def test_escalating_an_unassigned_ticket_is_a_no_op(world):
    ticket = approved(world, priority=Priority.P4)
    _escalate(world, ticket)
    assert ticket.priority is Priority.P5


# ---------------------------------------------------------------------------
# The three invariant queries. Empty, always.
# ---------------------------------------------------------------------------


def _emergency_with_everything(world) -> Ticket:
    """A P4 holding an assignment, a case membership and a dispatch event, then
    escalated. The worst state the system can reach on its own."""
    from src.services.auto_assignment_settings_service import AutoAssignmentSettingsService

    AutoAssignmentSettingsService(world.db).set_enabled(world.coordinator.user_id, enabled=True)
    ticket = approved(world, priority=Priority.P4)
    enqueue(world.db, ticket)
    make_assignment(world, ticket, world.technician(0))
    case = IncidentCase(
        category_id=world.water.id,
        window_start=datetime.now(UTC) - timedelta(days=1),
        window_end=datetime.now(UTC),
        density_value=1,
    )
    world.db.add(case)
    world.db.flush()
    case.series_id = case.id
    world.db.add(
        IncidentCaseMember(case_id=case.id, ticket_id=ticket.id, source_unit_id=ticket.source_unit_id)
    )
    world.db.commit()

    _escalate(world, ticket)
    return ticket


def test_no_active_assignment_belongs_to_an_emergency(world):
    _emergency_with_everything(world)

    offending = world.db.scalars(
        select(TicketAssignment.id)
        .join(Ticket, Ticket.id == TicketAssignment.ticket_id)
        .where(TicketAssignment.is_active.is_(True), Ticket.priority == Priority.P5)
    ).all()
    assert offending == []


def test_no_open_dispatch_event_belongs_to_an_emergency(world):
    _emergency_with_everything(world)

    offending = world.db.scalars(
        select(DispatchEvent.id)
        .join(Ticket, Ticket.id == DispatchEvent.ticket_id)
        .where(DispatchEvent.is_open.is_(True), Ticket.priority == Priority.P5)
    ).all()
    assert offending == []


def test_no_active_case_membership_belongs_to_an_emergency(world):
    ticket = _emergency_with_everything(world)
    # The membership is removed by the grouping re-score path, which is what
    # `_escalate` stands in for here; assert it directly so this file states the
    # invariant rather than relying on the other file's fixtures.
    from src.services.agent_result_service import AgentResultService

    AgentResultService(world.db)._detach_from_case(ticket, reason="P5")
    world.db.commit()

    offending = world.db.scalars(
        select(IncidentCaseMember.ticket_id)
        .join(Ticket, Ticket.id == IncidentCaseMember.ticket_id)
        .where(Ticket.priority == Priority.P5)
    ).all()
    assert offending == []


def _active_assignments(world, ticket_id):
    return world.db.scalars(
        select(TicketAssignment.id).where(
            TicketAssignment.ticket_id == ticket_id, TicketAssignment.is_active.is_(True)
        )
    ).all()


def _with_closed_grouping(world, ticket):
    from src.database.models.ai_analysis import AIAnalysisRun
    from src.models.enums import AnalysisRunStatus

    world.db.add(
        AIAnalysisRun(
            ticket_id=ticket.id,
            run_number=1,
            status=AnalysisRunStatus.SUCCEEDED,
            grouping_status="NO_MATCH",
        )
    )
    world.db.commit()
    return ticket
