"""A case is one unit of work or none at all (§1, §7.9).

`AssignmentService.assign_case` -- the case-wide "Gán kỹ thuật viên" action --
must be all-or-nothing: if any member cannot be assigned, nothing in the case
is assigned, and every precondition is checked before anything is written.
Splitting a case across two technicians is exactly what grouping them was
supposed to prevent.

The old `case_draft` tests that used to live here checked the same rule from
the proposal side, which §9 removes. Their replacement is
`tests/test_workflow/test_visual_assignment.py`, where a case appears on the
board as a single indivisible unit -- and does so by never being offered as
separate cards, rather than by being validated afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.ticket_assignment import TicketAssignment
from src.models.api.errors import DomainError
from src.models.enums import ClassificationStatus, Priority, TicketStatus
from src.services.assignment_service import AssignmentService
from tests.test_workflow.factories import approved_ticket, build_world, make_ticket

IN_SHIFT = datetime(2026, 8, 26, 3, 0, tzinfo=UTC)
OUTSIDE_SHIFT = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _open_case(world, category, *, count: int) -> IncidentCase:
    now = datetime.now(UTC)
    case = IncidentCase(
        category_id=category.id,
        status="OPEN",
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
        density_value=count,
        sequence_no=1,
    )
    world.db.add(case)
    world.db.commit()
    return case


def _join_case(world, case: IncidentCase, ticket) -> None:
    world.db.add(IncidentCaseMember(case_id=case.id, ticket_id=ticket.id, source_unit_id=ticket.source_unit_id))
    world.db.commit()


def test_assign_case_assigns_every_member_to_the_same_technician(db_session):
    world = build_world(db_session, resident_count=2)
    case = _open_case(world, world.water, count=2)
    first = approved_ticket(world, resident=world.resident(0), category=world.water)
    second = approved_ticket(world, resident=world.resident(1), category=world.water)
    for ticket in (first, second):
        _join_case(world, case, ticket)

    service = AssignmentService(db_session)
    assignments = service.assign_case(
        world.coordinator.user_id, [first.id, second.id], world.technician(0).user_id, now=IN_SHIFT
    )

    assert {assignment.ticket_id for assignment in assignments} == {first.id, second.id}
    assert all(assignment.technician_id == world.technician(0).user_id for assignment in assignments)


def test_assign_case_writes_nothing_when_one_member_is_not_assignable(db_session):
    """The core regression: a case used to hand out whatever it could and
    report the rest as skipped. One unready member must now block the whole
    case, with zero assignments written for any member."""
    world = build_world(db_session, resident_count=2)
    case = _open_case(world, world.water, count=2)
    ready = approved_ticket(world, resident=world.resident(0), category=world.water)
    not_ready = make_ticket(
        world,
        resident=world.resident(1),
        category=world.water,
        status=TicketStatus.NEW,
        classification_status=ClassificationStatus.RESOLVED,
        priority=Priority.P2,
    )
    for ticket in (ready, not_ready):
        _join_case(world, case, ticket)

    service = AssignmentService(db_session)
    try:
        service.assign_case(
            world.coordinator.user_id, [ready.id, not_ready.id], world.technician(0).user_id, now=IN_SHIFT
        )
        raised = False
    except DomainError:
        raised = True

    assert raised
    assert db_session.scalars(select(TicketAssignment)).first() is None


def test_assign_case_writes_nothing_when_the_technician_lacks_the_skill(db_session):
    world = build_world(db_session, resident_count=2)
    case = _open_case(world, world.water, count=2)
    first = approved_ticket(world, resident=world.resident(0), category=world.water)
    second = approved_ticket(world, resident=world.resident(1), category=world.water)
    for ticket in (first, second):
        _join_case(world, case, ticket)
    # Narrow the technician's skills to exclude water, so the second
    # precondition check (skill match) is what fails this time.
    from src.database.models.technician import TechnicianSkill

    db_session.query(TechnicianSkill).filter(
        TechnicianSkill.technician_id == world.technician(0).user_id,
        TechnicianSkill.category_id == world.water.id,
    ).delete()
    db_session.commit()

    service = AssignmentService(db_session)
    try:
        service.assign_case(world.coordinator.user_id, [first.id, second.id], world.technician(0).user_id, now=IN_SHIFT)
        raised = False
    except DomainError:
        raised = True

    assert raised
    assert db_session.scalars(select(TicketAssignment)).first() is None


def test_every_manual_assignment_path_rejects_outside_the_shift(db_session):
    """The ticket panel and case action must match the visual board's shift gate."""
    world = build_world(db_session, resident_count=2)
    first = approved_ticket(world, resident=world.resident(0), category=world.water)
    second = approved_ticket(world, resident=world.resident(1), category=world.water)
    service = AssignmentService(db_session)

    with pytest.raises(DomainError, match="08:00 đến 18:00"):
        service.assign(world.coordinator.user_id, first.id, world.technician(0).user_id, now=OUTSIDE_SHIFT)
    with pytest.raises(DomainError, match="08:00 đến 18:00"):
        service.assign_case(
            world.coordinator.user_id,
            [first.id, second.id],
            world.technician(0).user_id,
            now=OUTSIDE_SHIFT,
        )

    assert db_session.scalars(select(TicketAssignment)).first() is None
