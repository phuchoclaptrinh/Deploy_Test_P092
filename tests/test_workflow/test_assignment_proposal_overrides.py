"""Empty batches and coordinator technician overrides — §4.2, §4.3, §4.6.

Two decisions this module pins down, both of which only make sense once you
know the coordinator is the person who turns auto-assignment on:

* **An empty batch is still a batch, but it is not confirmable.** With nothing
  eligible there is nothing to ask a model about, so the batch is READY
  immediately — and confirming it is refused, because an empty table is no
  evidence that a coordinator reviewed any work (§4.2, §4.6 item 6).
* **The coordinator may move a row to any *active* technician**, including one
  the AI never considered. That widens who may be chosen, never who the AI
  considered: the candidate snapshot and `proposed_technician_id` are untouched,
  so the audit still separates the model's suggestion from the human's choice.
  An inactive technician is refused everywhere, on edit and again at confirm.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from src.database.models.assignment_proposal import AIAssignmentJob
from src.services.assignment_proposal_service import _aware

from src.database.models.audit_log import AuditLog
from src.database.models.notification import Notification
from src.database.models.technician import TechnicianSkill
from src.database.models.ticket_assignment import TicketAssignment
from src.models.api.errors import (
    PROPOSAL_NOTHING_TO_ASSIGN,
    TECHNICIAN_NOT_ELIGIBLE,
    TECHNICIAN_NOT_FOUND,
    DomainError,
)
from src.models.enums import AssignmentSource, ProposalBatchStatus, ProposalItemStatus
from tests.test_v4.factories import build_world
from tests.test_v4.scripted_assignment_model import ScriptedAssignmentModel
from tests.test_v4.test_assignment_proposal import _ready_batch, _service, _switch

# ---------------------------------------------------------------------------
# An empty batch is still the way to turn the switch on (§4.2, §4.6 item 6)
# ---------------------------------------------------------------------------


def test_an_empty_batch_is_ready_without_calling_the_model(db_session):
    world = build_world(db_session)
    primary = ScriptedAssignmentModel(model_version="scripted-primary")
    fallback = ScriptedAssignmentModel(model_version="scripted-fallback")
    service = _service(db_session, primary=primary, fallback=fallback)

    batch = service.create_batch(world.coordinator.user_id)

    assert batch.items == []
    # Straight to READY: no BUILDING wait for an answer nobody needs.
    assert batch.status == ProposalBatchStatus.READY.value
    assert batch.ready_at is not None
    assert _aware(batch.expires_at) - _aware(batch.ready_at) == timedelta(seconds=600)
    assert primary.call_count == 0
    assert fallback.call_count == 0
    # No PROPOSAL job either, because there is no model round to represent.
    assert db_session.scalars(select(AIAssignmentJob)).all() == []
    row = _switch(db_session)
    assert row is None or row.enabled is False


def test_an_empty_batch_cannot_be_confirmed_at_all(db_session):
    """An empty table is no evidence that anyone reviewed any work.

    It used to be confirmable as the way to turn DIRECT on for future tickets.
    That is exactly backwards: authorising autonomous assignment on the strength
    of an empty screen is the thing the proposal-first rule exists to stop.
    """
    world = build_world(db_session)
    service = _service(db_session)
    batch = service.create_batch(world.coordinator.user_id)

    with pytest.raises(DomainError) as exc:
        service.confirm_batch(world.coordinator.user_id, batch.id)

    assert exc.value.code == PROPOSAL_NOTHING_TO_ASSIGN
    assert exc.value.status_code == 409
    # Still open, so the coordinator can cancel it or wait for eligible work.
    assert service.get_batch(batch.id).status == ProposalBatchStatus.READY.value
    row = _switch(db_session)
    assert row is None or row.enabled is False


def test_a_refused_empty_confirmation_creates_nothing(db_session):
    world = build_world(db_session)
    service = _service(db_session)
    batch = service.create_batch(world.coordinator.user_id)

    with pytest.raises(DomainError):
        service.confirm_batch(world.coordinator.user_id, batch.id)

    assert db_session.scalars(select(TicketAssignment)).all() == []
    assert db_session.scalars(select(Notification)).all() == []


def test_a_batch_whose_rows_are_all_deselected_is_treated_the_same(db_session):
    """Nothing to assign is about the rows, not about how they got that way."""
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)
    service.update_item(world.coordinator.user_id, batch.id, batch.items[0].id, selected=False)

    with pytest.raises(DomainError) as exc:
        service.confirm_batch(
            world.coordinator.user_id, batch.id, activation_delay="IMMEDIATE"
        )

    assert exc.value.code == PROPOSAL_NOTHING_TO_ASSIGN


# ---------------------------------------------------------------------------
# The coordinator may add an active technician the AI never saw
# ---------------------------------------------------------------------------


def test_a_coordinator_may_pick_an_active_technician_outside_the_snapshot(db_session):
    world = build_world(db_session)
    outsider = world.technician(2)
    # Nothing about `outsider` makes them an AI candidate for this Category:
    # the skill rows are gone, so the snapshot could never have held them.
    db_session.query(TechnicianSkill).filter(TechnicianSkill.technician_id == outsider.user_id).delete()
    db_session.commit()
    service, batch = _ready_batch(db_session, world, ticket_count=1)
    item = batch.items[0]
    snapshot_ids = {
        UUID(str(candidate["technician_id"]))
        for job in db_session.scalars(select(AIAssignmentJob))
        for entry in (job.candidate_snapshot or [])
        for candidate in entry["candidates"]
    }
    assert outsider.user_id not in snapshot_ids

    updated = service.update_item(world.coordinator.user_id, batch.id, item.id, technician_id=outsider.user_id)

    row = next(entry for entry in updated.items if entry.id == item.id)
    assert row.final_technician_id == outsider.user_id
    # The AI's suggestion is a separate fact and survives the override.
    assert row.proposed_technician_id is not None
    assert row.proposed_technician_id != outsider.user_id


def test_the_audit_keeps_both_the_proposal_and_the_final_choice(db_session):
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)
    item = batch.items[0]
    proposed = item.proposed_technician_id
    outsider = next(row for row in world.technicians if row.user_id != proposed)
    service.update_item(world.coordinator.user_id, batch.id, item.id, technician_id=outsider.user_id)

    service.confirm_batch(
        world.coordinator.user_id, batch.id, activation_delay="IMMEDIATE"
    )

    entry = db_session.scalar(select(AuditLog).where(AuditLog.action == "AI_PROPOSAL_ASSIGNMENT_CONFIRMED"))
    assert entry.after_data["technician_id"] == str(outsider.user_id)
    assert entry.after_data["proposed_technician_id"] == str(proposed)
    assert entry.actor_role == "COORDINATOR"
    assert entry.actor_user_id == world.coordinator.user_id
    assignment = db_session.scalar(select(TicketAssignment))
    assert assignment.technician_id == outsider.user_id
    assert assignment.assignment_source == AssignmentSource.AI_PROPOSAL_CONFIRMED.value


def test_an_inactive_technician_is_refused_on_edit(db_session):
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)
    item = batch.items[0]
    version_before = batch.version
    retired = next(row for row in world.technicians if row.user_id != item.proposed_technician_id)
    retired.is_active = False
    db_session.commit()

    with pytest.raises(DomainError) as exc:
        service.update_item(world.coordinator.user_id, batch.id, item.id, technician_id=retired.user_id)

    assert exc.value.code == TECHNICIAN_NOT_ELIGIBLE
    assert exc.value.status_code == 409
    stored = service.get_batch(batch.id)
    # Neither the row nor the batch version moved.
    assert stored.version == version_before
    assert next(row for row in stored.items if row.id == item.id).final_technician_id != retired.user_id


def test_an_unknown_technician_is_refused_on_edit(db_session):
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)

    with pytest.raises(DomainError) as exc:
        service.update_item(world.coordinator.user_id, batch.id, batch.items[0].id, technician_id=uuid4())

    assert exc.value.code == TECHNICIAN_NOT_FOUND


def test_a_technician_deactivated_after_the_edit_loses_only_its_own_row(db_session):
    """§4.3: one stale row must not discard the rest of the batch."""
    world = build_world(db_session, resident_count=4)
    service, batch = _ready_batch(db_session, world, ticket_count=2)
    doomed, survivor = batch.items[0], batch.items[1]
    retiring = next(
        row
        for row in world.technicians
        if row.user_id not in {doomed.proposed_technician_id, survivor.proposed_technician_id}
    )
    service.update_item(world.coordinator.user_id, batch.id, doomed.id, technician_id=retiring.user_id)
    retiring.is_active = False
    db_session.commit()

    result = service.confirm_batch(
        world.coordinator.user_id, batch.id, activation_delay="IMMEDIATE"
    )

    rows = {row.id: row for row in result.items}
    assert rows[doomed.id].status == ProposalItemStatus.EMPTY.value
    assert rows[doomed.id].final_technician_id is None
    assert rows[doomed.id].reason
    # The model's suggestion is still on the row, so the audit trail is intact.
    assert rows[doomed.id].proposed_technician_id is not None
    assert rows[survivor.id].status == ProposalItemStatus.ASSIGNED.value

    assignments = db_session.scalars(select(TicketAssignment)).all()
    assert len(assignments) == 1
    assert assignments[0].ticket_id == survivor.ticket_id
    assert all(row.technician_id != retiring.user_id for row in assignments)
