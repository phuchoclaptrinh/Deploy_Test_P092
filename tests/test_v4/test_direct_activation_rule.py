"""DIRECT auto-assignment: stoppable at any moment, startable one way only.

The asymmetry is the rule. Turning DIRECT **off** stops the system assigning
tickets with nobody looking, so it is always available and takes effect at once.
Turning it **on** authorises exactly that autonomy, so it may only happen as a
consequence of a named coordinator confirming a proposal batch that actually
handed work out — never on request, never from a toggle, never from a schedule.

A request body could be forged, replayed, or sent with no proposal behind it at
all. So no request body can ask for activation: `confirm_batch` decides from
state the caller cannot influence, and `AutoAssignmentSettingsService.update`
refuses the transition for every caller rather than trusting the router.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.database.models.assignment_proposal import AssignmentProposalBatch
from src.database.models.assignment_schedule import AssignmentProposalSchedule
from src.database.models.audit_log import AuditLog
from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.ticket_assignment import TicketAssignment
from src.models.api.errors import (
    AUTO_ASSIGNMENT_PROPOSAL_REQUIRED,
    CONFLICT_VERSION,
    PROPOSAL_EXPIRED,
    DomainError,
)
from src.models.enums import ProposalBatchCreatedBy, ProposalBatchStatus
from src.services.v4_workflow_service import AutoAssignmentSettingsService
from tests.test_v4.factories import approved_ticket, build_world
from tests.test_v4.test_assignment_proposal import _ready_batch, _service, _switch
from tests.test_v4.test_proposal_schedule import _schedule


def _settings(db) -> AutoAssignmentSettingsService:
    return AutoAssignmentSettingsService(db)


def _turn_on(db, world, *, delay: str = "IMMEDIATE"):
    """The only legal way in: confirm a proposal that assigns real work."""
    service, batch = _ready_batch(db, world, ticket_count=1)
    service.confirm_batch(world.coordinator.user_id, batch.id, activation_delay=delay)
    assert _switch(db).enabled is True
    return service, batch


# ---------------------------------------------------------------------------
# 1. Off is always available, and takes effect immediately
# ---------------------------------------------------------------------------


def test_a_coordinator_can_stop_direct_immediately(db_session):
    world = build_world(db_session, resident_count=4)
    _turn_on(db_session, world, delay="2_HOURS")

    row = _settings(db_session).update(
        world.coordinator.user_id, enabled=False, activation_delay="2_HOURS"
    )

    assert row.enabled is False
    assert row.updated_by_user_id == world.coordinator.user_id


def test_stopping_direct_clears_the_activation_provenance(db_session):
    """The provenance explained why DIRECT was on. Off, it explains nothing."""
    world = build_world(db_session, resident_count=4)
    _turn_on(db_session, world)
    assert _switch(db_session).activated_by_batch_id is not None

    _settings(db_session).update(world.coordinator.user_id, enabled=False, activation_delay="IMMEDIATE")

    row = _switch(db_session)
    assert (row.activated_by_batch_id, row.activated_by_user_id, row.activated_at) == (None, None, None)


def test_stopping_direct_leaves_finished_work_alone(db_session):
    """Turning the feature off is not a retraction of what it already did."""
    world = build_world(db_session, resident_count=4)
    _service_obj, batch = _turn_on(db_session, world)
    assignments_before = {row.id for row in db_session.scalars(select(TicketAssignment))}
    assert assignments_before

    _settings(db_session).update(world.coordinator.user_id, enabled=False, activation_delay="IMMEDIATE")

    assert {row.id for row in db_session.scalars(select(TicketAssignment))} == assignments_before
    # The confirmed batch and its record are untouched too.
    confirmed = db_session.get(AssignmentProposalBatch, batch.id)
    assert confirmed.status == ProposalBatchStatus.CONFIRMED.value
    assert confirmed.confirmation_snapshot is not None


# ---------------------------------------------------------------------------
# 2. On is not reachable from the settings API, for any caller
# ---------------------------------------------------------------------------


def test_the_settings_api_cannot_start_direct(db_session):
    world = build_world(db_session)
    service = _settings(db_session)
    service.get()

    with pytest.raises(DomainError) as exc:
        service.update(world.coordinator.user_id, enabled=True, activation_delay="IMMEDIATE")

    assert exc.value.code == AUTO_ASSIGNMENT_PROPOSAL_REQUIRED
    assert exc.value.status_code == 409


def test_it_cannot_be_restarted_through_the_settings_api_either(db_session):
    """Having been on once buys nothing: the next start needs a new proposal."""
    world = build_world(db_session, resident_count=4)
    _turn_on(db_session, world)
    _settings(db_session).update(world.coordinator.user_id, enabled=False, activation_delay="IMMEDIATE")

    with pytest.raises(DomainError) as exc:
        _settings(db_session).update(world.coordinator.user_id, enabled=True, activation_delay="IMMEDIATE")

    assert exc.value.code == AUTO_ASSIGNMENT_PROPOSAL_REQUIRED
    assert _switch(db_session).enabled is False


def test_the_confirm_request_carries_no_activation_flag(db_session):
    """There is no `continue_auto_assignment` to send any more.

    Removing it is the point rather than a tidy-up: while it existed, the client
    decided whether DIRECT turned on, and a client's word is exactly what this
    rule refuses to take.
    """
    from src.models.api.coordinator import AssignmentProposalConfirmRequest
    from src.services.assignment_proposal_service import AssignmentProposalService

    assert "continue_auto_assignment" not in AssignmentProposalConfirmRequest.model_fields
    # `extra="forbid"`, so an old client sending it is rejected outright rather
    # than having it silently ignored.
    with pytest.raises(ValueError):
        AssignmentProposalConfirmRequest(continue_auto_assignment=True)

    import inspect

    signature = inspect.signature(AssignmentProposalService.confirm_batch)
    assert "continue_auto_assignment" not in signature.parameters


# ---------------------------------------------------------------------------
# 4. Confirming a real proposal starts DIRECT, atomically
# ---------------------------------------------------------------------------


def test_confirming_a_proposal_starts_direct_in_one_transaction(db_session):
    world = build_world(db_session, resident_count=4)
    service, batch = _ready_batch(db_session, world, ticket_count=2)
    assert _switch(db_session) is None or _switch(db_session).enabled is False

    confirmed = service.confirm_batch(world.coordinator.user_id, batch.id, activation_delay="1_DAY")

    row = _switch(db_session)
    assert row.enabled is True
    assert row.activation_delay == "1_DAY"
    # The assignments and the activation landed together.
    assert len(db_session.scalars(select(TicketAssignment)).all()) == 2
    assert confirmed.continue_auto_assignment is True


def test_a_confirmation_that_fails_leaves_direct_off_and_nothing_assigned(db_session, monkeypatch):
    """Atomic in the direction that matters: no half-authorised autonomy."""
    world = build_world(db_session, resident_count=4)
    service, batch = _ready_batch(db_session, world, ticket_count=2)

    def _explode(*_args, **_kwargs):
        raise RuntimeError("notification backend is down")

    monkeypatch.setattr(service.side_effects, "notify_technician", _explode)

    with pytest.raises(RuntimeError):
        service.confirm_batch(world.coordinator.user_id, batch.id)

    row = _switch(db_session)
    assert row is None or row.enabled is False
    assert db_session.scalars(select(TicketAssignment)).all() == []
    assert db_session.get(AssignmentProposalBatch, batch.id).status == ProposalBatchStatus.READY.value


def test_a_stale_version_neither_assigns_nor_activates(db_session):
    world = build_world(db_session, resident_count=4)
    service, batch = _ready_batch(db_session, world, ticket_count=1)

    with pytest.raises(DomainError) as exc:
        service.confirm_batch(world.coordinator.user_id, batch.id, expected_version=batch.version + 5)

    assert exc.value.code == CONFLICT_VERSION
    assert _switch(db_session) is None or _switch(db_session).enabled is False
    assert db_session.scalars(select(TicketAssignment)).all() == []


# ---------------------------------------------------------------------------
# 5. Nothing short of a successful confirmation activates it
# ---------------------------------------------------------------------------


def test_merely_creating_a_proposal_does_not_start_direct(db_session):
    world = build_world(db_session)
    approved_ticket(world)

    _service(db_session).create_batch(world.coordinator.user_id)

    assert _switch(db_session) is None or _switch(db_session).enabled is False


def test_cancelling_a_proposal_does_not_start_direct(db_session):
    world = build_world(db_session, resident_count=4)
    service, batch = _ready_batch(db_session, world, ticket_count=1)

    service.cancel_batch(world.coordinator.user_id, batch.id, "Đổi ý.")

    assert _switch(db_session) is None or _switch(db_session).enabled is False


def test_an_expired_proposal_cannot_start_direct(db_session):
    world = build_world(db_session, resident_count=4)
    service, batch = _ready_batch(db_session, world, ticket_count=1)
    stored = db_session.get(AssignmentProposalBatch, batch.id)
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(DomainError) as exc:
        service.confirm_batch(world.coordinator.user_id, batch.id)

    assert exc.value.code == PROPOSAL_EXPIRED
    assert _switch(db_session) is None or _switch(db_session).enabled is False


def test_a_confirmation_that_assigns_nothing_does_not_start_direct(db_session):
    """Every row was taken by hand first, so nobody authorised anything.

    The batch still confirms — that is the existing manual-wins behaviour — but
    a confirmation that handed no work out is not evidence that a coordinator
    approved autonomous assignment.
    """
    from src.services.assignment_service import AssignmentService

    world = build_world(db_session, resident_count=4)
    service, batch = _ready_batch(db_session, world, ticket_count=1)
    item = batch.items[0]
    AssignmentService(db_session).assign(
        world.coordinator.user_id, item.members[0].ticket_id, world.technician(2).user_id
    )

    confirmed = service.confirm_batch(world.coordinator.user_id, batch.id)

    assert confirmed.status == ProposalBatchStatus.CONFIRMED.value
    assert confirmed.continue_auto_assignment is False
    row = _switch(db_session)
    assert row is None or row.enabled is False


# ---------------------------------------------------------------------------
# 6 and 7. The recurring schedule and DIRECT stay strangers
# ---------------------------------------------------------------------------


def test_a_scheduler_created_batch_does_not_start_direct_by_existing(db_session):
    world = build_world(db_session, resident_count=4)
    approved_ticket(world)
    service = _schedule(db_session)
    service.update(world.coordinator.user_id, enabled=True, interval="2_HOURS")
    db_session.get(AssignmentProposalSchedule, 1).next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()

    report = service.run_due()

    assert report.batches_created == 1
    batch = db_session.scalar(select(AssignmentProposalBatch))
    assert batch.created_by_type == ProposalBatchCreatedBy.SYSTEM.value
    # The system opened a table. Only a person can turn it into autonomy.
    assert _switch(db_session) is None or _switch(db_session).enabled is False


def test_a_named_coordinator_confirming_a_scheduled_batch_does_start_direct(db_session):
    world = build_world(db_session, resident_count=4)
    approved_ticket(world)
    proposals = _service(db_session)
    service = _schedule(db_session, proposals)
    service.update(world.coordinator.user_id, enabled=True, interval="2_HOURS")
    db_session.get(AssignmentProposalSchedule, 1).next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    service.run_due()
    proposals.run_due_batches()
    batch = db_session.scalar(select(AssignmentProposalBatch))

    proposals.confirm_batch(world.coordinator.user_id, batch.id)

    row = _switch(db_session)
    assert row.enabled is True
    # A system-opened batch, but the authorisation is the coordinator's.
    assert row.activated_by_user_id == world.coordinator.user_id
    assert row.activated_by_batch_id == batch.id


def test_stopping_direct_leaves_the_recurring_schedule_running(db_session):
    """Two features, two switches. Stopping one must not quietly stop the other."""
    world = build_world(db_session, resident_count=4)
    _turn_on(db_session, world)
    _schedule(db_session).update(world.coordinator.user_id, enabled=True, interval="1_DAY")
    before = db_session.get(AssignmentProposalSchedule, 1)
    version_before, next_run_before = before.version, before.next_run_at

    _settings(db_session).update(world.coordinator.user_id, enabled=False, activation_delay="IMMEDIATE")

    row = db_session.get(AssignmentProposalSchedule, 1)
    assert row.enabled is True
    assert row.interval_code == "1_DAY"
    assert (row.version, row.next_run_at) == (version_before, next_run_before)


def test_the_recurring_schedule_never_writes_to_the_direct_switch(db_session):
    world = build_world(db_session, resident_count=4)
    _turn_on(db_session, world, delay="5_HOURS")
    before = _switch(db_session)
    version_before = before.version

    _schedule(db_session).update(world.coordinator.user_id, enabled=True, interval="3_DAYS")

    row = _switch(db_session)
    assert row.enabled is True
    assert row.activation_delay == "5_HOURS"
    assert row.version == version_before


# ---------------------------------------------------------------------------
# 8. The audit says which proposal and which coordinator
# ---------------------------------------------------------------------------


def test_the_audit_names_the_proposal_and_the_coordinator_that_started_direct(db_session):
    world = build_world(db_session, resident_count=4)
    service, batch = _ready_batch(db_session, world, ticket_count=1)

    service.confirm_batch(world.coordinator.user_id, batch.id, activation_delay="2_HOURS")

    entry = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "ACTIVATE_DIRECT_AUTO_ASSIGNMENT")
    )
    assert entry is not None
    assert entry.actor_user_id == world.coordinator.user_id
    assert entry.actor_role == "COORDINATOR"
    # The entity is the batch, so the trail leads from the switch to the table
    # of work a human actually looked at.
    assert entry.entity_id == batch.id
    assert entry.before_data == {"enabled": False}
    assert entry.after_data["activated_by_batch_id"] == str(batch.id)
    assert entry.after_data["activated_by_user_id"] == str(world.coordinator.user_id)
    assert entry.after_data["activation_delay"] == "2_HOURS"


def test_stopping_direct_is_audited_too(db_session):
    world = build_world(db_session, resident_count=4)
    _turn_on(db_session, world)

    _settings(db_session).update(world.coordinator.user_id, enabled=False, activation_delay="IMMEDIATE")

    entry = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "DISABLE_DIRECT_AUTO_ASSIGNMENT")
    )
    assert entry is not None
    assert entry.actor_user_id == world.coordinator.user_id
    assert entry.before_data == {"enabled": True}
    assert entry.after_data["enabled"] is False


def test_no_activation_audit_when_nothing_was_activated(db_session):
    """A delay change while already on authorised nothing, so it claims nothing."""
    world = build_world(db_session, resident_count=4)
    _turn_on(db_session, world)
    db_session.query(AuditLog).delete()
    db_session.commit()

    _settings(db_session).update(world.coordinator.user_id, enabled=True, activation_delay="3_DAYS")

    assert (
        db_session.scalar(select(AuditLog).where(AuditLog.action == "ACTIVATE_DIRECT_AUTO_ASSIGNMENT"))
        is None
    )
    assert db_session.get(AutoAssignmentSetting, 1).activation_delay == "3_DAYS"
