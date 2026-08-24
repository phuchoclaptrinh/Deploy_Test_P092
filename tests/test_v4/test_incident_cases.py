"""A case is one unit of work or none at all (§4.2, §4.5, §7.9).

Two rules these tests exist to protect, both regressions caught in review:

* `AssignmentCandidateService.case_draft` must never build a proposal or
  DIRECT work item out of a subset of a case's members. A case with one
  `APPROVED` member and one still `NEW` has to be deferred whole, or the two
  members end up assigned on two separate, unrelated decisions -- exactly
  what grouping them into a case was supposed to prevent. A member that
  already has an active assignment is different: a coordinator took it by
  hand (§4.5), and the case correctly drafts around it with whoever is left.
* `AssignmentService.assign_case` (the case-wide "Gán kỹ thuật viên" action)
  must be all-or-nothing: if any member cannot be assigned, nothing in the
  case is assigned, and every precondition is checked before anything is
  written.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.ticket_assignment import TicketAssignment
from src.models.api.errors import DomainError
from src.models.enums import ClassificationStatus, Priority, TicketStatus
from src.services.assignment_candidates import AssignmentCandidateService
from src.services.assignment_service import AssignmentService
from tests.test_v4.factories import approved_ticket, build_world, make_assignment, make_ticket


def _open_case(world, category, *, count: int) -> IncidentCase:
    now = datetime.now(UTC)
    case = IncidentCase(
        category_id=category.id,
        building_id=world.building.id,
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


def test_case_draft_defers_the_whole_case_when_a_member_is_still_new(db_session):
    world = build_world(db_session, resident_count=2)
    case = _open_case(world, world.water, count=2)
    ready = approved_ticket(world, resident=world.resident(0), category=world.water)
    _join_case(world, case, ready)
    still_new = make_ticket(
        world,
        resident=world.resident(1),
        category=world.water,
        status=TicketStatus.NEW,
        classification_status=ClassificationStatus.RESOLVED,
        priority=Priority.P2,
    )
    _join_case(world, case, still_new)

    candidates = AssignmentCandidateService(db_session)
    draft = candidates.case_draft(case, max_members=5)

    assert draft is None


def test_case_draft_proceeds_once_every_member_is_approved(db_session):
    world = build_world(db_session, resident_count=2)
    case = _open_case(world, world.water, count=2)
    first = approved_ticket(world, resident=world.resident(0), category=world.water)
    second = approved_ticket(world, resident=world.resident(1), category=world.water)
    _join_case(world, case, first)
    _join_case(world, case, second)

    candidates = AssignmentCandidateService(db_session)
    draft = candidates.case_draft(case, max_members=5)

    assert draft is not None
    assert set(draft.ticket_ids) == {first.id, second.id}


def test_case_draft_drafts_around_a_member_a_coordinator_already_won_by_hand(db_session):
    """§4.5: a manual win shrinks the case; it does not block it."""
    world = build_world(db_session, resident_count=3)
    case = _open_case(world, world.water, count=3)
    taken = approved_ticket(world, resident=world.resident(0), category=world.water)
    remaining_a = approved_ticket(world, resident=world.resident(1), category=world.water)
    remaining_b = approved_ticket(world, resident=world.resident(2), category=world.water)
    for ticket in (taken, remaining_a, remaining_b):
        _join_case(world, case, ticket)
    make_assignment(world, taken, world.technician(0))

    candidates = AssignmentCandidateService(db_session)
    draft = candidates.case_draft(case, max_members=5)

    assert draft is not None
    assert set(draft.ticket_ids) == {remaining_a.id, remaining_b.id}


def test_case_draft_returns_none_once_every_member_is_already_handled(db_session):
    world = build_world(db_session, resident_count=2)
    case = _open_case(world, world.water, count=2)
    first = approved_ticket(world, resident=world.resident(0), category=world.water)
    second = approved_ticket(world, resident=world.resident(1), category=world.water)
    _join_case(world, case, first)
    _join_case(world, case, second)
    make_assignment(world, first, world.technician(0))
    make_assignment(world, second, world.technician(1))

    candidates = AssignmentCandidateService(db_session)
    draft = candidates.case_draft(case, max_members=5)

    assert draft is None


def test_assign_case_assigns_every_member_to_the_same_technician(db_session):
    world = build_world(db_session, resident_count=2)
    case = _open_case(world, world.water, count=2)
    first = approved_ticket(world, resident=world.resident(0), category=world.water)
    second = approved_ticket(world, resident=world.resident(1), category=world.water)
    for ticket in (first, second):
        _join_case(world, case, ticket)

    service = AssignmentService(db_session)
    assignments = service.assign_case(world.coordinator.user_id, [first.id, second.id], world.technician(0).user_id)

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
        service.assign_case(world.coordinator.user_id, [ready.id, not_ready.id], world.technician(0).user_id)
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
        service.assign_case(world.coordinator.user_id, [first.id, second.id], world.technician(0).user_id)
        raised = False
    except DomainError:
        raised = True

    assert raised
    assert db_session.scalars(select(TicketAssignment)).first() is None
