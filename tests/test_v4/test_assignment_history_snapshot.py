"""A confirmed round is a record of what happened, not a view of what is.

Assignment history used to render from live rows: the ticket's current
category, the location's current label, the technician profile's current name.
That quietly rewrites the past. Rename a category in September and the August
record claims the coordinator approved something they never saw; deactivate a
technician and the work appears to have gone to nobody.

So confirmation freezes a snapshot inside the same transaction as the
assignments, and the history endpoint reads that and only that. These tests
change the world underneath a confirmed record and assert it does not move.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.database.models.assignment_proposal import AssignmentProposalBatch
from src.database.models.location import Location
from src.database.models.technician import TechnicianProfile
from src.database.models.ticket import Ticket
from src.database.models.user_profile import UserProfile
from src.models.enums import Priority, ProposalBatchCreatedBy, ProposalItemStatus
from src.services.assignment_history_service import AssignmentHistoryService
from src.services.assignment_schedule_service import AssignmentScheduleService
from tests.test_v4.factories import build_world
from tests.test_v4.test_assignment_proposal import _ready_batch


def _confirm(db, world, *, ticket_count: int = 2):
    proposals, batch = _ready_batch(db, world, ticket_count=ticket_count)
    confirmed = proposals.confirm_batch(world.coordinator.user_id, batch.id)
    return proposals, confirmed


def _record(db, batch_id):
    return AssignmentHistoryService(db).get_record(batch_id)


# ---------------------------------------------------------------------------
# The snapshot is written with the assignments, and is complete
# ---------------------------------------------------------------------------


def test_confirming_freezes_a_snapshot_of_what_was_approved(db_session):
    world = build_world(db_session, resident_count=4)
    _proposals, batch = _confirm(db_session, world, ticket_count=2)

    snapshot = db_session.get(AssignmentProposalBatch, batch.id).confirmation_snapshot
    assert snapshot is not None
    assert snapshot["confirmed_by_user_id"] == str(world.coordinator.user_id)
    assert snapshot["created_by_type"] == ProposalBatchCreatedBy.COORDINATOR.value
    assert len(snapshot["items"]) == 2
    for item in snapshot["items"]:
        assert item["status"] == ProposalItemStatus.ASSIGNED.value
        assert item["final_technician_id"]
        assert item["final_technician_name"]
        member = item["members"][0]
        # Every fact the history table renders, copied rather than joined.
        assert member["display_code"].startswith("PA-")
        assert member["category"]
        assert member["location_label"]
        assert member["priority"] == Priority.P2.value
        assert member["created_at"] and member["sla_due_at"]


def test_the_snapshot_carries_no_prompt_or_raw_model_output(db_session):
    """§9: a sanitized business reason may travel; nothing the model emitted."""
    world = build_world(db_session, resident_count=4)
    _proposals, batch = _confirm(db_session, world, ticket_count=1)

    snapshot = db_session.get(AssignmentProposalBatch, batch.id).confirmation_snapshot
    text = str(snapshot)
    for leak in ("raw_model_output", "candidate_snapshot", "prompt", "error_detail"):
        assert leak not in text


# ---------------------------------------------------------------------------
# The world changes; the record does not
# ---------------------------------------------------------------------------


def test_a_confirmed_record_survives_a_renamed_category_and_moved_location(db_session):
    world = build_world(db_session, resident_count=4)
    _proposals, batch = _confirm(db_session, world, ticket_count=1)
    before = _record(db_session, batch.id)
    member_before = before.items[0].members[0]

    # Everything a live join would have picked up, changed underneath it.
    world.elevator.code = "THANG_MAY_DOI_TEN"
    db_session.scalar(select(Location).where(Location.id == world.corridor_10.id)).label = "Vị trí đã đổi"
    ticket = db_session.get(Ticket, batch.items[0].members[0].ticket_id)
    ticket.priority = Priority.P1
    ticket.sla_due_at = datetime.now(UTC) + timedelta(days=30)
    db_session.commit()

    after = _record(db_session, batch.id).items[0].members[0]
    assert after.category == member_before.category != "THANG_MAY_DOI_TEN"
    assert after.location_label == member_before.location_label != "Vị trí đã đổi"
    assert after.priority == Priority.P2.value
    assert after.sla_due_at == member_before.sla_due_at


def test_a_confirmed_record_keeps_the_names_people_had_at_the_time(db_session):
    world = build_world(db_session, resident_count=4)
    _proposals, batch = _confirm(db_session, world, ticket_count=1)
    before = _record(db_session, batch.id)
    technician_before = before.items[0].final_technician_name

    # The technician leaves and is renamed; the coordinator is renamed too.
    profile = db_session.get(TechnicianProfile, batch.items[0].final_technician_id)
    profile.is_active = False
    db_session.get(UserProfile, profile.user_id).full_name = "Người đã nghỉ"
    db_session.get(UserProfile, world.coordinator.user_id).full_name = "Tên mới"
    db_session.commit()

    after = _record(db_session, batch.id)
    assert after.items[0].final_technician_name == technician_before != "Người đã nghỉ"
    assert after.confirmed_by_name == before.confirmed_by_name != "Tên mới"


def test_a_coordinator_override_stays_visible_after_the_roster_changes(db_session):
    world = build_world(db_session, resident_count=4)
    proposals, batch = _ready_batch(db_session, world, ticket_count=1)
    item = batch.items[0]
    proposed = item.proposed_technician_id
    replacement = next(row for row in world.technicians if row.user_id != proposed)
    proposals.update_item(world.coordinator.user_id, batch.id, item.id, technician_id=replacement.user_id)
    proposals.confirm_batch(world.coordinator.user_id, batch.id)

    row = _record(db_session, batch.id).items[0]
    assert row.coordinator_override is True
    # Both facts survive: what the model suggested, and what a human chose.
    assert row.proposed_technician_id == str(proposed)
    assert row.final_technician_id == str(replacement.user_id)


# ---------------------------------------------------------------------------
# Actors, counts and the pre-snapshot rows
# ---------------------------------------------------------------------------


def test_a_scheduled_round_is_opened_by_the_system_and_confirmed_by_a_person(db_session):
    world = build_world(db_session, resident_count=4)
    proposals, batch = _ready_batch(db_session, world, ticket_count=1)
    # Stand in for a run of the recurring schedule.
    db_session.get(AssignmentProposalBatch, batch.id).created_by_type = ProposalBatchCreatedBy.SYSTEM.value
    db_session.commit()
    proposals.confirm_batch(world.coordinator.user_id, batch.id)

    record = _record(db_session, batch.id)
    assert record.created_by_type == ProposalBatchCreatedBy.SYSTEM.value
    # §8.1: the confirmation is never the system's, whoever opened the batch.
    assert record.confirmed_by_name == world.coordinator.full_name


def test_counts_follow_ticket_members_and_distinct_technicians(db_session):
    world = build_world(db_session, resident_count=6)
    _proposals, batch = _confirm(db_session, world, ticket_count=3)

    record = _record(db_session, batch.id)
    assert record.ticket_count == 3
    assert record.technician_count == len({item.final_technician_id for item in record.items})


def test_the_followup_repeat_appears_on_the_record(db_session):
    world = build_world(db_session, resident_count=4)
    proposals, batch = _confirm(db_session, world, ticket_count=1)
    AssignmentScheduleService(db_session, proposals=proposals).update(
        world.coordinator.user_id, enabled=True, interval="3_DAYS", after_batch_id=batch.id
    )

    assert _record(db_session, batch.id).followup_schedule == "3_DAYS"


def test_only_confirmed_rounds_are_history(db_session):
    world = build_world(db_session, resident_count=4)
    proposals, ready = _ready_batch(db_session, world, ticket_count=1)
    proposals.cancel_batch(world.coordinator.user_id, ready.id)

    assert AssignmentHistoryService(db_session).list_records() == []
    assert _record(db_session, ready.id) is None


def test_a_round_confirmed_before_snapshots_is_reported_not_reconstructed(db_session):
    """The one honest answer for a row with no snapshot is "no detail"."""
    world = build_world(db_session, resident_count=4)
    _proposals, batch = _confirm(db_session, world, ticket_count=1)
    db_session.get(AssignmentProposalBatch, batch.id).confirmation_snapshot = None
    db_session.commit()

    record = _record(db_session, batch.id)
    assert record.has_snapshot is False
    assert record.items == []
    assert record.ticket_count == 0
    # `confirmed_at` is a column on the batch, so it is still true.
    assert record.confirmed_at is not None
    # A name would have to come from a live join, so it is not offered.
    assert record.confirmed_by_name is None
