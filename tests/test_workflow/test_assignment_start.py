"""The replacement technician lifecycle: ASSIGNED -> IN_PROGRESS -> COMPLETED.

There is no acknowledgement step. What that removal costs, and what has to hold
in its place, is what these tests pin down:

* the acceptance state and its endpoint are gone, not merely unused;
* `/start` is the first positive action, and it does everything the old pair of
  calls did between them -- ownership, status, ticket state, the one-live-job
  rule, and the queue-head rule that used to have nowhere to live;
* the schedule is renumbered in the same transaction, because a queue that only
  refreshes on the next unrelated write is a queue whose head is a guess.

The race between two concurrent `/start` calls lives in
`test_assignment_start_race.py`, which needs a database that can hold two
transactions at once and therefore its own harness.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.database.models.audit_log import AuditLog
from src.database.models.notification import Notification
from src.domain.assignment_transitions import ALLOWED_ASSIGNMENT_TRANSITIONS
from src.models.api.errors import DomainError
from src.models.enums import AssignmentStatus, ClassificationStatus, Priority, TicketStatus
from src.services.assignment_service import AssignmentService
from tests.test_workflow.factories import build_world, make_assignment, make_ticket


def _approved(world, **kwargs):
    kwargs.setdefault("status", TicketStatus.APPROVED)
    kwargs.setdefault("classification_status", ClassificationStatus.RESOLVED)
    kwargs.setdefault("category", world.water)
    kwargs.setdefault("priority", Priority.P2)
    return make_ticket(world, **kwargs)


def _queued(world, technician, count: int, *, now: datetime | None = None):
    """`count` assigned tickets on one technician, renumbered by the scheduler.

    Built through `reindex_technicians` rather than by writing `planned_order`
    by hand: the head of the queue is the scheduler's opinion, and a test that
    stated it directly would pass even if the service read a number nobody had
    computed.
    """
    from src.dispatch.planning import reindex_technicians

    now = now or datetime.now(UTC)
    rows = []
    for index in range(count):
        ticket = _approved(world, resident=world.resident(index), created_at=now - timedelta(hours=count - index))
        rows.append(make_assignment(world, ticket, technician, assigned_at=now))
    reindex_technicians(world.db, {technician.user_id}, now)
    world.db.commit()
    return sorted(rows, key=lambda row: row.planned_order)


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


def test_assigned_goes_straight_to_in_progress():
    """No intermediate state to pass through, and none to fall back to."""
    assert AssignmentStatus.IN_PROGRESS in ALLOWED_ASSIGNMENT_TRANSITIONS[AssignmentStatus.ASSIGNED]
    assert set(ALLOWED_ASSIGNMENT_TRANSITIONS) == {AssignmentStatus.ASSIGNED, AssignmentStatus.IN_PROGRESS}
    assert ALLOWED_ASSIGNMENT_TRANSITIONS[AssignmentStatus.ASSIGNED] == {
        AssignmentStatus.IN_PROGRESS,
        AssignmentStatus.REJECTED,
        AssignmentStatus.REASSIGNED,
        AssignmentStatus.UNABLE_TO_HANDLE,
    }
    assert ALLOWED_ASSIGNMENT_TRANSITIONS[AssignmentStatus.IN_PROGRESS] == {
        AssignmentStatus.COMPLETED,
        AssignmentStatus.UNABLE_TO_HANDLE,
    }


def test_no_transition_can_reach_an_accepted_state():
    """Not "rejected as invalid" -- there is no value to name as a target."""
    reachable = set().union(*ALLOWED_ASSIGNMENT_TRANSITIONS.values())
    assert not any(status.value == "ACCEPTED" for status in reachable)
    assert not hasattr(AssignmentStatus, "ACCEPTED")


def test_the_service_has_no_acceptance_call(db_session):
    world = build_world(db_session, technician_count=1)
    service = AssignmentService(db_session)
    assert not hasattr(service, "accept")
    assert world is not None


@pytest.mark.asyncio
async def test_the_accept_endpoint_is_gone(client):
    """A stale client gets a 404 rather than a silent no-op."""
    from uuid import uuid4

    response = await client.post(f"/api/v1/technician/assignments/{uuid4()}/accept")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Starting work
# ---------------------------------------------------------------------------


def test_the_head_of_the_queue_can_be_started(db_session):
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    head, _next = _queued(world, technician, 2)

    started = AssignmentService(db_session).start(technician.user_id, head.id)

    assert started.status is AssignmentStatus.IN_PROGRESS
    assert started.started_at is not None
    assert started.ticket.status is TicketStatus.IN_PROGRESS


def test_a_non_head_queued_ticket_cannot_be_started(db_session):
    """The technician does not get to re-plan the day from their phone.

    Refused with a distinct code, not a generic transition error: "you may not
    do this yet" and "this is impossible" are different answers, and only the
    first has an action attached to it.
    """
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    _head, second, _third = _queued(world, technician, 3)

    with pytest.raises(DomainError) as raised:
        AssignmentService(db_session).start(technician.user_id, second.id)

    assert raised.value.code == "ASSIGNMENT_NOT_AT_QUEUE_HEAD"
    assert raised.value.status_code == 409
    assert "Làm ngay" in raised.value.message
    db_session.rollback()
    db_session.refresh(second)
    assert second.status is AssignmentStatus.ASSIGNED
    assert second.ticket.status is TicketStatus.APPROVED


def test_a_stale_queue_position_cannot_be_used_to_jump_the_queue(db_session):
    """The stored number is re-derived before it is trusted.

    This is the whole reason `/start` re-simulates first. A row can hold a
    `planned_order` that was true when it was written and is not true now --
    another job finished, the day moved -- and a client holding that copy is
    exactly what "do not rely on frontend order" means. The backend recomputes,
    then judges.
    """
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    head, second = _queued(world, technician, 2)
    # A stale record claiming the second job is "Làm ngay".
    second.planned_order = 0
    head.planned_order = 1
    db_session.commit()

    with pytest.raises(DomainError) as raised:
        AssignmentService(db_session).start(technician.user_id, second.id)
    assert raised.value.code == "ASSIGNMENT_NOT_AT_QUEUE_HEAD"

    db_session.rollback()
    # And the real head is startable, because the same re-simulation corrected
    # its number too.
    started = AssignmentService(db_session).start(technician.user_id, head.id)
    assert started.status is AssignmentStatus.IN_PROGRESS
    assert started.planned_order == 0


def test_an_unscheduled_assignment_is_scheduled_before_it_is_judged(db_session):
    """A null order is not "first"; it is a row that was never simulated.

    Reachable if a future write path forgets to schedule. The pre-start
    re-simulation gives it a real position, and that position -- not the null --
    decides.
    """
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    head, second = _queued(world, technician, 2)
    second.planned_order = None
    db_session.commit()

    with pytest.raises(DomainError) as raised:
        AssignmentService(db_session).start(technician.user_id, second.id)
    assert raised.value.code == "ASSIGNMENT_NOT_AT_QUEUE_HEAD"

    # The re-simulation happened inside the transaction the refusal rolled
    # back, so a rejected request leaves nothing behind -- including the
    # renumbering it used to make its decision.
    db_session.rollback()
    db_session.refresh(second)
    assert second.planned_order is None
    # And the head it identified is the one that can actually start.
    started = AssignmentService(db_session).start(technician.user_id, head.id)
    assert started.status is AssignmentStatus.IN_PROGRESS


def test_starting_reindexes_everything_still_queued(db_session):
    """The live job is pinned in front, so the rest of the day moves.

    Renumbering in the same transaction is what keeps the *next* start request
    honest: the head is read from the queue as it is, not as the client last
    saw it.
    """
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    head, second, third = _queued(world, technician, 3)
    before = {row.id: row.planned_start_at for row in (second, third)}

    AssignmentService(db_session).start(technician.user_id, head.id)

    for row in (head, second, third):
        db_session.refresh(row)
    # Every active assignment carries a fresh, contiguous order.
    assert sorted(row.planned_order for row in (head, second, third)) == [0, 1, 2]
    # The one in progress stays at the front; the scheduler pins it there.
    assert head.planned_order == 0
    # And the queued work was re-simulated rather than left as it was found.
    assert all(row.planned_start_at is not None for row in (second, third))
    assert any(before[row.id] != row.planned_start_at for row in (second, third))


def test_starting_writes_the_audit_event_and_tells_the_resident(db_session):
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    head, = _queued(world, technician, 1)

    AssignmentService(db_session).start(technician.user_id, head.id)

    actions = set(db_session.scalars(select(AuditLog.action)))
    assert "START_ASSIGNMENT" in actions
    assert "ACCEPT_ASSIGNMENT" not in actions
    events = set(db_session.scalars(select(Notification.notification_type)))
    assert "TICKET_STARTED" in events
    assert not any(event.startswith("ASSIGNMENT_ACCEPTANCE") for event in events)
    # The ticket timeline records the start in its own words.
    reasons = [row.reason for row in head.ticket.status_history]
    assert "Technician started work." in reasons


def test_a_technician_cannot_start_work_that_is_not_theirs(db_session):
    world = build_world(db_session, technician_count=2)
    head, = _queued(world, world.technician(0), 1)

    with pytest.raises(DomainError) as raised:
        AssignmentService(db_session).start(world.technician(1).user_id, head.id)
    assert raised.value.code == "ASSIGNMENT_NOT_FOUND"


def test_a_ticket_that_is_not_approved_cannot_be_started(db_session):
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    head, = _queued(world, technician, 1)
    head.ticket.status = TicketStatus.WAITING_RESIDENT_INFO
    db_session.commit()

    with pytest.raises(DomainError) as raised:
        AssignmentService(db_session).start(technician.user_id, head.id)
    assert raised.value.status_code == 409


def test_a_technician_holding_a_live_job_cannot_start_another(db_session):
    """§3: one IN_PROGRESS ticket at a time, as a readable error."""
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    head, second = _queued(world, technician, 2)
    AssignmentService(db_session).start(technician.user_id, head.id)

    with pytest.raises(DomainError) as raised:
        AssignmentService(db_session).start(technician.user_id, second.id)
    assert raised.value.code == "TECHNICIAN_NOT_ELIGIBLE"


# ---------------------------------------------------------------------------
# What a technician is told when work arrives
# ---------------------------------------------------------------------------


def test_every_path_that_creates_an_assignment_says_the_same_thing(db_session):
    """Manual, Visual Assignment and automatic dispatch share one message.

    Three code paths wrote three variations of it, and the Visual Assignment
    one still asked the technician to "xác nhận nhận việc" -- an action that no
    longer exists. Sharing the constant is what stops them drifting again.
    """
    from src.services.assignment_support import (
        NEW_ASSIGNMENT_BODY_AUTOMATIC,
        NEW_ASSIGNMENT_BODY_COORDINATOR,
        NEW_ASSIGNMENT_TITLE,
    )

    bodies = (NEW_ASSIGNMENT_BODY_COORDINATOR, NEW_ASSIGNMENT_BODY_AUTOMATIC)
    for body in bodies:
        # The removed action, in the two spellings the product used.
        assert "xác nhận nhận việc" not in body
        assert "nhận việc" not in body.lower()
        # And no promise the system cannot keep: there is no start deadline.
        for forbidden in ("hạn", "trước ", "quá hạn", "tự động phân lại", "SLA"):
            assert forbidden.lower() not in body.lower(), f"{body!r} implies a deadline via {forbidden!r}"
        # It points at the queue instead.
        assert "Làm ngay" in body
    assert NEW_ASSIGNMENT_TITLE == "Bạn có công việc mới"


def test_a_manual_assignment_notifies_the_technician_with_that_message(db_session):
    """The constant is not merely defined -- it is what actually gets written."""
    from datetime import time

    from src.dispatch.shift import VN_TZ
    from src.services.assignment_support import NEW_ASSIGNMENT_BODY_COORDINATOR, NEW_ASSIGNMENT_TITLE

    world = build_world(db_session, technician_count=1)
    ticket = _approved(world)
    # Inside the 08:00-18:00 window the manual path requires.
    in_shift = datetime.combine(datetime.now(UTC).date(), time(10, 0), tzinfo=VN_TZ).astimezone(UTC)

    AssignmentService(db_session).assign(
        world.coordinator.user_id, ticket.id, world.technician(0).user_id, now=in_shift
    )

    notices = list(
        db_session.scalars(
            select(Notification).where(
                Notification.recipient_user_id == world.technician(0).user_id,
                Notification.notification_type == "ASSIGNMENT_CREATED",
            )
        )
    )
    assert len(notices) == 1
    assert notices[0].title == NEW_ASSIGNMENT_TITLE
    assert notices[0].body == NEW_ASSIGNMENT_BODY_COORDINATOR


# ---------------------------------------------------------------------------
# Nothing acceptance-shaped survives on the payloads
# ---------------------------------------------------------------------------


def test_the_assignment_row_carries_no_acceptance_columns(db_session):
    world = build_world(db_session, technician_count=1)
    row = make_assignment(world, _approved(world), world.technician(0))
    for column in ("accepted_at", "acceptance_due_at", "acceptance_warning_at", "warning_sent_at", "cycle_started_at"):
        assert not hasattr(row, column)


def test_the_timeout_sweep_no_longer_touches_assignments(db_session):
    """It swept acceptance deadlines; there is no deadline left to sweep.

    Deliberate, not forgotten: no start SLA has been approved, so nothing here
    may end an assignment on a clock.
    """
    from src.services.operational_timeout_service import OperationalTimeoutService

    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    assignment = make_assignment(
        world, _approved(world), technician, assigned_at=datetime.now(UTC) - timedelta(days=3)
    )

    report = OperationalTimeoutService(db_session).sweep()

    assert set(report) == {"resident_question_timeouts"}
    db_session.refresh(assignment)
    assert assignment.is_active is True
    assert assignment.status is AssignmentStatus.ASSIGNED
