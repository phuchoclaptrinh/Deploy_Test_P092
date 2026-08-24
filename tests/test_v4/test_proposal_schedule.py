"""The recurring proposal schedule — a draft generator, never an assigner.

The whole reason this feature has its own table, its own service and its own
worker stage is that the previous UI borrowed `auto_assignment_settings` to
express it. That told coordinators "the system will show you another table in
two hours" while the setting actually meant "the system will start assigning
approved tickets by itself two hours after each one is approved". The tests
here pin the difference down from both directions:

* a due run produces a `BUILDING`/`READY` batch and **zero** assignments, and
* configuring the schedule leaves the V4 switch exactly where it was.

The rest guards the things a periodic job gets wrong: firing twice on one due
time, stacking a second table on top of an open one, leaving an empty table on
screen every interval, and catching up on a missed week all at once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.database.models.assignment_proposal import AssignmentProposalBatch
from src.database.models.assignment_schedule import AssignmentProposalSchedule
from src.database.models.audit_log import AuditLog
from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.notification import Notification
from src.database.models.ticket_assignment import TicketAssignment
from src.models.api.errors import CONFLICT_VERSION, INVALID_STATUS_TRANSITION, DomainError
from src.models.enums import ProposalBatchCreatedBy, ProposalBatchStatus
from src.services.assignment_schedule_service import (
    INTERVALS,
    NO_REPEAT,
    AssignmentScheduleService,
)
from tests.test_v4.factories import approved_ticket, build_world
from tests.test_v4.test_assignment_proposal import _ready_batch, _service


def _schedule(db, proposals=None) -> AssignmentScheduleService:
    return AssignmentScheduleService(db, proposals=proposals or _service(db))


def _row(db) -> AssignmentProposalSchedule:
    return db.get(AssignmentProposalSchedule, 1)


# ---------------------------------------------------------------------------
# Configuration is durable, and is not the V4 switch
# ---------------------------------------------------------------------------


def test_the_schedule_starts_off_and_persists_when_configured(db_session):
    world = build_world(db_session)
    service = _schedule(db_session)

    assert service.get().enabled is False

    before = datetime.now(UTC)
    service.update(world.coordinator.user_id, enabled=True, interval="1_DAY")

    row = _row(db_session)
    assert row.enabled is True
    assert row.interval_code == "1_DAY"
    assert row.configured_by_user_id == world.coordinator.user_id
    assert row.version == 2
    # A full interval from now, not from some earlier due time.
    assert row.next_run_at is not None
    assert timedelta(hours=23) < _aware(row.next_run_at) - before < timedelta(hours=25)


def test_configuring_the_schedule_never_touches_the_v4_switch(db_session):
    """The two features share nothing, including by accident."""
    world = build_world(db_session)
    _schedule(db_session).update(world.coordinator.user_id, enabled=True, interval="2_HOURS")

    switch = db_session.get(AutoAssignmentSetting, 1)
    assert switch is None or switch.enabled is False


def test_turning_the_schedule_off_clears_the_interval_and_the_due_time(db_session):
    world = build_world(db_session)
    service = _schedule(db_session)
    service.update(world.coordinator.user_id, enabled=True, interval="3_DAYS")

    service.update(world.coordinator.user_id, enabled=False, interval=None)

    row = _row(db_session)
    assert row.enabled is False
    assert row.interval_code is None
    # Nothing left that could still come due.
    assert row.next_run_at is None


def test_enabling_without_an_interval_is_refused(db_session):
    """An enabled schedule with no interval would look on and never fire."""
    world = build_world(db_session)

    with pytest.raises(DomainError) as exc:
        _schedule(db_session).update(world.coordinator.user_id, enabled=True, interval=None)

    assert exc.value.code == INVALID_STATUS_TRANSITION
    assert _row(db_session) is None or _row(db_session).enabled is False


def test_a_stale_version_is_refused_rather_than_overwriting(db_session):
    world = build_world(db_session)
    service = _schedule(db_session)
    service.update(world.coordinator.user_id, enabled=True, interval="2_HOURS")

    with pytest.raises(DomainError) as exc:
        service.update(world.coordinator.user_id, enabled=True, interval="3_DAYS", expected_version=1)

    assert exc.value.code == CONFLICT_VERSION
    assert _row(db_session).interval_code == "2_HOURS"


# ---------------------------------------------------------------------------
# The repeat chosen after a confirmation is recorded on that batch, once
# ---------------------------------------------------------------------------


def test_the_repeat_chosen_after_a_confirmation_is_recorded_on_that_batch(db_session):
    world = build_world(db_session, resident_count=4)
    proposals, batch = _ready_batch(db_session, world, ticket_count=1)
    proposals.confirm_batch(world.coordinator.user_id, batch.id)

    _schedule(db_session, proposals).update(
        world.coordinator.user_id, enabled=True, interval="1_DAY", after_batch_id=batch.id
    )

    assert db_session.get(AssignmentProposalBatch, batch.id).followup_schedule == "1_DAY"


def test_declining_the_repeat_is_recorded_as_a_decline(db_session):
    """Asked and declined is a different fact from never asked."""
    world = build_world(db_session, resident_count=4)
    proposals, batch = _ready_batch(db_session, world, ticket_count=1)
    proposals.confirm_batch(world.coordinator.user_id, batch.id)

    _schedule(db_session, proposals).update(
        world.coordinator.user_id, enabled=False, interval=None, after_batch_id=batch.id
    )

    assert db_session.get(AssignmentProposalBatch, batch.id).followup_schedule == NO_REPEAT


def test_the_same_answer_twice_is_a_double_click_and_a_different_one_is_refused(db_session):
    world = build_world(db_session, resident_count=4)
    proposals, batch = _ready_batch(db_session, world, ticket_count=1)
    proposals.confirm_batch(world.coordinator.user_id, batch.id)
    service = _schedule(db_session, proposals)
    service.update(world.coordinator.user_id, enabled=True, interval="2_HOURS", after_batch_id=batch.id)

    # Same answer again: tolerated, because it is the button being pressed twice.
    service.update(world.coordinator.user_id, enabled=True, interval="2_HOURS", after_batch_id=batch.id)

    with pytest.raises(DomainError) as exc:
        service.update(world.coordinator.user_id, enabled=True, interval="3_DAYS", after_batch_id=batch.id)

    assert exc.value.code == INVALID_STATUS_TRANSITION
    # The record keeps the answer that was actually given at the time.
    assert db_session.get(AssignmentProposalBatch, batch.id).followup_schedule == "2_HOURS"


# ---------------------------------------------------------------------------
# A due run builds a table for review, and assigns nothing
# ---------------------------------------------------------------------------


def _make_due(db, world, interval: str = "2_HOURS") -> AssignmentScheduleService:
    service = _schedule(db)
    service.update(world.coordinator.user_id, enabled=True, interval=interval)
    row = _row(db)
    row.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    return service


def test_a_due_run_opens_a_reviewable_batch_and_assigns_nothing(db_session):
    world = build_world(db_session, resident_count=4)
    approved_ticket(world, resident=world.resident(0))
    approved_ticket(world, resident=world.resident(1))
    service = _make_due(db_session, world)

    report = service.run_due()

    assert report.due is True
    assert report.batches_created == 1
    batch = db_session.scalar(select(AssignmentProposalBatch))
    # Reviewable, not done: it still has to be confirmed by a person.
    assert batch.status == ProposalBatchStatus.BUILDING.value
    assert batch.confirmed_at is None
    assert db_session.scalars(select(TicketAssignment)).all() == []
    assert db_session.scalars(select(Notification)).all() == []


def test_a_scheduled_batch_belongs_to_the_system_not_to_a_coordinator(db_session):
    world = build_world(db_session, resident_count=4)
    approved_ticket(world)
    service = _make_due(db_session, world)

    service.run_due()

    batch = db_session.scalar(select(AssignmentProposalBatch))
    assert batch.created_by_type == ProposalBatchCreatedBy.SYSTEM.value
    # No borrowed identity: nobody was present when this batch was opened.
    assert batch.requested_by_user_id is None
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "CREATE_ASSIGNMENT_PROPOSAL_BATCH")
    )
    assert audit.actor_role == "SYSTEM"
    assert audit.actor_user_id is None


def test_a_due_run_with_nothing_eligible_creates_no_visible_batch(db_session):
    """Otherwise an empty table would appear on the screen every interval."""
    world = build_world(db_session)
    service = _make_due(db_session, world, interval="1_DAY")

    report = service.run_due()

    assert report.due is True
    assert report.skipped_no_work == 1
    assert db_session.scalars(select(AssignmentProposalBatch)).all() == []
    # The schedule still moves on rather than retrying the same due time.
    row = _row(db_session)
    assert row.last_run_at is not None
    assert _aware(row.next_run_at) > datetime.now(UTC)


def test_a_due_run_does_not_stack_a_second_table_on_an_open_one(db_session):
    world = build_world(db_session, resident_count=4)
    approved_ticket(world, resident=world.resident(0))
    proposals = _service(db_session)
    open_batch = proposals.create_batch(world.coordinator.user_id)
    approved_ticket(world, resident=world.resident(1))
    service = _make_due(db_session, world)
    service._proposals = proposals

    report = service.run_due()

    assert report.skipped_active_batch == 1
    assert report.batches_created == 0
    batches = db_session.scalars(select(AssignmentProposalBatch)).all()
    assert [row.id for row in batches] == [open_batch.id]


def test_the_schedule_fires_once_per_due_time(db_session):
    world = build_world(db_session, resident_count=4)
    approved_ticket(world)
    service = _make_due(db_session, world)

    first = service.run_due()
    second = service.run_due()

    assert first.batches_created == 1
    assert second.due is False
    assert len(db_session.scalars(select(AssignmentProposalBatch)).all()) == 1


def test_a_schedule_that_is_off_never_becomes_due(db_session):
    world = build_world(db_session)
    approved_ticket(world)
    service = _schedule(db_session)
    service.update(world.coordinator.user_id, enabled=False, interval=None)

    assert service.run_due().due is False
    assert db_session.scalars(select(AssignmentProposalBatch)).all() == []


def test_a_missed_week_is_caught_up_in_one_step_not_one_per_pass(db_session):
    """A worker that was down must not fire once per pass until it catches up."""
    previous = datetime.now(UTC) - timedelta(days=7)
    now = datetime.now(UTC)

    nxt = AssignmentScheduleService._advance(previous, "1_DAY", now)

    assert nxt > now
    assert nxt - now < INTERVALS["1_DAY"]


# ---------------------------------------------------------------------------
# Opening the workspace is a read
# ---------------------------------------------------------------------------


def test_opening_the_workspace_creates_nothing(db_session):
    """State 1 is what the coordinator sees *before* anything is requested.

    Entering the workspace issues exactly the two reads below. The previous
    version created a batch on the way in, which is why opening the screen used
    to show an empty BUILDING surface before anyone had looked at the queue.
    """
    world = build_world(db_session)
    approved_ticket(world)
    proposals = _service(db_session)

    proposals.list_batches()
    _schedule(db_session, proposals).get()

    assert db_session.scalars(select(AssignmentProposalBatch)).all() == []
    assert db_session.scalars(select(TicketAssignment)).all() == []


def test_reading_the_schedule_does_not_turn_it_on(db_session):
    build_world(db_session)

    row = _schedule(db_session).get()

    assert row.enabled is False
    assert row.interval_code is None
    assert row.next_run_at is None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
