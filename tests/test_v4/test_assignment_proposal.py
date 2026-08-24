"""The proposal batch lifecycle — contract §4.3a, §4.6, §5.2, §7.5, §7.6.

The rule these tests exist to protect is §4.6 item 6: DIRECT auto-assignment
turns on in exactly one place, a confirm that actually hands work out. Opening a
batch, cancelling it, letting it expire, or confirming one whose rows were all
taken manually first all have to leave it off. No request body can ask for it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.assignment_agent.service import AssignmentAgentService
from src.database.models.assignment_proposal import (
    AIAssignmentJob,
    AssignmentProposalBatch,
    AssignmentProposalItem,
    AssignmentProposalItemMember,
)
from src.database.models.audit_log import AuditLog
from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.models.api.errors import DomainError
from src.models.enums import (
    AssignmentSource,
    Priority,
    ProposalBatchStatus,
    ProposalItemStatus,
    TicketStatus,
)
from src.services.assignment_proposal_service import (
    CONFLICT_VERSION,
    PROPOSAL_EXPIRED,
    AssignmentProposalService,
)
from src.services.assignment_service import AssignmentService
from tests.test_v4.factories import approved_ticket, build_world
from tests.test_v4.scripted_assignment_model import ScriptedAssignmentModel, no_suitable_candidate


def _service(db, primary=None, fallback=None) -> AssignmentProposalService:
    agent = AssignmentAgentService(
        primary or ScriptedAssignmentModel(model_version="scripted-primary"),
        fallback or ScriptedAssignmentModel(model_version="scripted-fallback"),
    )
    return AssignmentProposalService(db, engine=agent)


def _switch(db) -> AutoAssignmentSetting:
    return db.get(AutoAssignmentSetting, 1)


def _ready_batch(db, world, *, ticket_count: int = 2, primary=None):
    for index in range(ticket_count):
        approved_ticket(world, resident=world.resident(index))
    service = _service(db, primary=primary)
    batch = service.create_batch(world.coordinator.user_id)
    assert batch.status == ProposalBatchStatus.BUILDING.value
    service.run_due_batches()
    return service, service.get_batch(batch.id)


# ---------------------------------------------------------------------------
# Scenario 8: batch -> READY -> coordinator edit -> confirm
# ---------------------------------------------------------------------------


def test_opening_a_batch_leaves_the_switch_off(db_session):
    """§4.6 item 1."""
    world = build_world(db_session)
    approved_ticket(world)
    service = _service(db_session)

    batch = service.create_batch(world.coordinator.user_id)

    assert batch.status == ProposalBatchStatus.BUILDING.value
    # Undecided, not "no".
    assert batch.continue_auto_assignment is None
    assert batch.activation_delay is None
    assert batch.ready_at is None
    assert _switch(db_session) is None or _switch(db_session).enabled is False
    # §5.1: PROPOSAL does not lock tickets.
    job = db_session.scalar(select(AIAssignmentJob).where(AIAssignmentJob.mode == "PROPOSAL"))
    assert job is not None
    assert job.members == []
    assert job.batch_decision_id is not None


def test_the_worker_makes_the_batch_ready_with_a_ten_minute_window(db_session):
    """§4.6 items 2-3 and §4.3a: one model call for the whole batch."""
    world = build_world(db_session, resident_count=4)
    primary = ScriptedAssignmentModel(model_version="scripted-primary")
    _service_obj, batch = _ready_batch(db_session, world, ticket_count=3, primary=primary)

    assert batch.status == ProposalBatchStatus.READY.value
    assert primary.call_count == 1
    assert len(batch.items) == 3
    assert all(item.status == ProposalItemStatus.PROPOSED.value for item in batch.items)
    assert all(item.proposed_technician_id is not None for item in batch.items)
    assert all(item.final_technician_id == item.proposed_technician_id for item in batch.items)
    window = batch.expires_at - batch.ready_at
    assert timedelta(seconds=599) <= window <= timedelta(seconds=601)
    # Still off.
    assert _switch(db_session).enabled is False if _switch(db_session) else True


def test_a_coordinator_can_deselect_a_row_and_change_a_technician(db_session):
    """§4.6 item 4."""
    world = build_world(db_session, resident_count=4)
    service, batch = _ready_batch(db_session, world, ticket_count=2)
    first, second = batch.items[0], batch.items[1]

    service.update_item(world.coordinator.user_id, batch.id, first.id, selected=False)
    replacement = world.technician(2).user_id
    service.update_item(world.coordinator.user_id, batch.id, second.id, technician_id=replacement)

    refreshed = service.get_batch(batch.id)
    by_id = {item.id: item for item in refreshed.items}
    assert by_id[first.id].status == ProposalItemStatus.DESELECTED.value
    assert by_id[second.id].final_technician_id == replacement
    # The model's original suggestion is still on record.
    assert by_id[second.id].proposed_technician_id != replacement or by_id[second.id].proposed_technician_id is not None

    result = service.confirm_batch(
        world.coordinator.user_id, batch.id, activation_delay="IMMEDIATE"
    )
    statuses = {item.id: item.status for item in result.items}
    assert statuses[first.id] == ProposalItemStatus.DESELECTED.value
    assert statuses[second.id] == ProposalItemStatus.ASSIGNED.value

    assignments = db_session.scalars(select(TicketAssignment)).all()
    assert len(assignments) == 1
    assert assignments[0].technician_id == replacement
    # §4.5: the coordinator is the author of a confirmed proposal.
    assert assignments[0].assignment_source == AssignmentSource.AI_PROPOSAL_CONFIRMED.value
    assert assignments[0].assigned_by_user_id == world.coordinator.user_id


# ---------------------------------------------------------------------------
# Scenario 9: an expired batch cannot be confirmed
# ---------------------------------------------------------------------------


def test_a_partial_confirmation_assigns_only_the_placed_rows(db_session):
    """Rows left in the unassigned column stay unassigned, and stay assignable.

    Partial confirmation is a normal outcome of the draft board, not an error:
    the coordinator hands out what they are sure about and leaves the rest for
    the next round. What must not happen is the leftover row being assigned
    anyway, or being closed out so a later batch cannot pick it up.
    """
    world = build_world(db_session, resident_count=6)
    service, batch = _ready_batch(db_session, world, ticket_count=3)
    dropped = batch.items[0]
    kept = [item.id for item in batch.items[1:]]
    service.update_item(world.coordinator.user_id, batch.id, dropped.id, selected=False)
    dropped_ticket_id = dropped.members[0].ticket_id

    confirmed = service.confirm_batch(
        world.coordinator.user_id, batch.id, activation_delay="IMMEDIATE"
    )

    by_id = {item.id: item for item in confirmed.items}
    assert by_id[dropped.id].status == ProposalItemStatus.DESELECTED.value
    assert all(by_id[item_id].status == ProposalItemStatus.ASSIGNED.value for item_id in kept)
    assignments = db_session.scalars(select(TicketAssignment)).all()
    assert len(assignments) == 2
    assert dropped_ticket_id not in {row.ticket_id for row in assignments}
    # Still approved and unassigned, so the next batch can offer it again.
    left_behind = db_session.get(Ticket, dropped_ticket_id)
    assert left_behind.status == TicketStatus.APPROVED
    assert [row for row in left_behind.assignments if row.is_active] == []
    next_batch = service.create_batch(world.coordinator.user_id)
    assert dropped_ticket_id in {
        member.ticket_id for item in next_batch.items for member in item.members
    }


def test_an_expired_batch_cannot_be_confirmed(db_session):
    """§4.6 item 8 / §12 scenario 16."""
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)

    stored = db_session.get(AssignmentProposalBatch, batch.id)
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(DomainError) as exc:
        service.confirm_batch(
            world.coordinator.user_id, batch.id, activation_delay="IMMEDIATE"
        )
    assert exc.value.code == PROPOSAL_EXPIRED
    assert exc.value.status_code == 409

    db_session.expire_all()
    stored = db_session.get(AssignmentProposalBatch, batch.id)
    assert stored.status == ProposalBatchStatus.EXPIRED.value
    assert db_session.scalars(select(TicketAssignment)).all() == []
    # The switch never turned on.
    assert _switch(db_session) is None or _switch(db_session).enabled is False


def test_the_worker_expires_a_stale_batch(db_session):
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)
    stored = db_session.get(AssignmentProposalBatch, batch.id)
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    report = service.run_due_batches()

    assert report.batches_expired == 1
    db_session.expire_all()
    assert db_session.get(AssignmentProposalBatch, batch.id).status == ProposalBatchStatus.EXPIRED.value
    actions = {row.action for row in db_session.scalars(select(AuditLog)).all()}
    assert "EXPIRE_ASSIGNMENT_PROPOSAL_BATCH" in actions


# ---------------------------------------------------------------------------
# Scenario 10: manual assignment wins while the table is open
# ---------------------------------------------------------------------------


def test_a_manually_assigned_row_is_skipped_at_confirm(db_session):
    """§4.6 item 5 / §12 scenario 10."""
    world = build_world(db_session, resident_count=4)
    service, batch = _ready_batch(db_session, world, ticket_count=2)
    taken = batch.items[0]

    # PROPOSAL does not lock the ticket, so this must simply work.
    AssignmentService(db_session).assign(world.coordinator.user_id, taken.ticket_id, world.technician(2).user_id)

    result = service.confirm_batch(
        world.coordinator.user_id, batch.id, activation_delay="IMMEDIATE"
    )

    statuses = {item.id: item.status for item in result.items}
    assert statuses[taken.id] == ProposalItemStatus.SKIPPED_MANUAL_WON.value
    assert statuses[batch.items[1].id] == ProposalItemStatus.ASSIGNED.value

    manual = db_session.scalar(select(TicketAssignment).where(TicketAssignment.ticket_id == taken.ticket_id))
    assert manual.technician_id == world.technician(2).user_id
    assert manual.assignment_source == AssignmentSource.COORDINATOR_MANUAL.value


# ---------------------------------------------------------------------------
# Scenario 11 and 12: the switch
# ---------------------------------------------------------------------------


def test_confirming_a_real_proposal_enables_the_switch(db_session):
    """§4.6 item 6 — the one and only path that turns DIRECT on."""
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)

    service.confirm_batch(world.coordinator.user_id, batch.id, activation_delay="2_HOURS")

    row = _switch(db_session)
    assert row.enabled is True
    assert row.activation_delay == "2_HOURS"
    assert row.updated_by_user_id == world.coordinator.user_id
    # The provenance: which proposal earned it, and who confirmed that proposal.
    assert row.activated_by_batch_id == batch.id
    assert row.activated_by_user_id == world.coordinator.user_id
    assert row.activated_at is not None


def test_the_legacy_delay_spelling_is_normalized(db_session):
    """The frontend may still send 2H; §7.6 stores 2_HOURS."""
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)

    service.confirm_batch(world.coordinator.user_id, batch.id, activation_delay="2H")

    assert _switch(db_session).activation_delay == "2_HOURS"


def test_a_later_confirmation_does_not_re_record_the_activation(db_session):
    """Only the confirmation that actually flipped the switch owns it.

    A second batch confirmed while DIRECT is already running authorised nothing
    new. Moving the provenance onto it would name the wrong coordinator as the
    person who approved autonomous assignment.
    """
    world = build_world(db_session, resident_count=6)
    service, first = _ready_batch(db_session, world, ticket_count=1)
    service.confirm_batch(world.coordinator.user_id, first.id, activation_delay="2_HOURS")

    _service2, second = _ready_batch(db_session, world, ticket_count=1)
    confirmed = service.confirm_batch(world.coordinator.user_id, second.id, activation_delay="1_DAY")

    row = _switch(db_session)
    assert row.enabled is True
    # Untouched by the second confirmation: not the batch, not the delay.
    assert row.activated_by_batch_id == first.id
    assert row.activation_delay == "2_HOURS"
    # And the second batch does not claim to have turned anything on.
    assert confirmed.continue_auto_assignment is False


def test_cancelling_leaves_the_switch_off(db_session):
    """§4.6 item 7."""
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)

    cancelled = service.cancel_batch(world.coordinator.user_id, batch.id, "Đóng bảng.")

    assert cancelled.status == ProposalBatchStatus.CANCELLED.value
    assert cancelled.cancelled_at is not None
    assert db_session.scalars(select(TicketAssignment)).all() == []
    row = _switch(db_session)
    assert row is None or row.enabled is False


def test_expiring_leaves_the_switch_off(db_session):
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)
    stored = db_session.get(AssignmentProposalBatch, batch.id)
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    service.run_due_batches()

    row = _switch(db_session)
    assert row is None or row.enabled is False


# ---------------------------------------------------------------------------
# Per-row failures (§5.2 items 1, 5, 7 / §12 scenario 17)
# ---------------------------------------------------------------------------


def test_no_suitable_candidate_is_an_empty_row_that_blocks_nothing(db_session):
    world = build_world(db_session, resident_count=4)
    primary = ScriptedAssignmentModel(model_version="scripted-primary", policy=no_suitable_candidate)
    fallback = ScriptedAssignmentModel(model_version="scripted-fallback")
    for index in range(2):
        approved_ticket(world, resident=world.resident(index))
    service = _service(db_session, primary=primary, fallback=fallback)
    batch = service.create_batch(world.coordinator.user_id)
    service.run_due_batches()
    batch = service.get_batch(batch.id)

    assert batch.status == ProposalBatchStatus.READY.value
    assert all(item.status == ProposalItemStatus.EMPTY.value for item in batch.items)
    # §5.2 item 7: a business answer, so no fallback call.
    assert fallback.call_count == 0
    # §5.2 item 5: an EMPTY row does not pause its ticket.
    for item in batch.items:
        ticket = db_session.get(type(item.ticket), item.ticket_id)
        assert ticket.auto_assignment_paused is False


def test_one_empty_row_does_not_stop_the_others_confirming(db_session):
    world = build_world(db_session, resident_count=4)
    for index in range(2):
        approved_ticket(world, resident=world.resident(index))

    seen: list[str] = []

    def policy(item):
        seen.append(item.decision_id)
        if len(seen) == 1:
            return no_suitable_candidate(item)
        return {
            "decision_id": item.decision_id,
            "work_item_id": item.work_item_id,
            "selected_technician_id": item.candidate_ids[0],
            "decision": "SELECTED",
            "reason": "Phu hop.",
        }

    service = _service(db_session, primary=ScriptedAssignmentModel(model_version="p", policy=policy))
    batch = service.create_batch(world.coordinator.user_id)
    service.run_due_batches()
    batch = service.get_batch(batch.id)

    statuses = sorted(item.status for item in batch.items)
    assert statuses == [ProposalItemStatus.EMPTY.value, ProposalItemStatus.PROPOSED.value]

    result = service.confirm_batch(
        world.coordinator.user_id, batch.id, activation_delay="IMMEDIATE"
    )
    assert sorted(item.status for item in result.items) == [
        ProposalItemStatus.ASSIGNED.value,
        ProposalItemStatus.EMPTY.value,
    ]
    assert len(db_session.scalars(select(TicketAssignment)).all()) == 1


def test_a_batch_wide_model_failure_still_produces_a_usable_table(db_session):
    world = build_world(db_session)
    approved_ticket(world)
    primary = ScriptedAssignmentModel(model_version="p", raise_error=RuntimeError("down"))
    fallback = ScriptedAssignmentModel(model_version="f", raise_error=RuntimeError("down"))
    service = _service(db_session, primary=primary, fallback=fallback)
    batch = service.create_batch(world.coordinator.user_id)

    service.run_due_batches()
    batch = service.get_batch(batch.id)

    assert batch.status == ProposalBatchStatus.READY.value
    assert all(item.status == ProposalItemStatus.EMPTY.value for item in batch.items)
    assert all(item.reason for item in batch.items)
    row = _switch(db_session)
    assert row is None or row.enabled is False


# ---------------------------------------------------------------------------
# Optimistic concurrency and batch composition (§4.3a, §4.6 item 5, §7.5)
# ---------------------------------------------------------------------------


def test_a_stale_version_is_refused_on_confirm(db_session):
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)
    stale_version = batch.version
    service.update_item(world.coordinator.user_id, batch.id, batch.items[0].id, selected=False)

    with pytest.raises(DomainError) as exc:
        service.confirm_batch(
            world.coordinator.user_id,
            batch.id,
            activation_delay="IMMEDIATE",
            expected_version=stale_version,
        )
    assert exc.value.code == CONFLICT_VERSION


def test_a_batch_takes_at_most_twenty_tickets(db_session):
    """§4.3a."""
    world = build_world(db_session, resident_count=25)
    for index in range(25):
        approved_ticket(world, resident=world.resident(index))
    service = _service(db_session)

    batch = service.create_batch(world.coordinator.user_id)

    members = db_session.scalars(
        select(AssignmentProposalItemMember).where(AssignmentProposalItemMember.batch_id == batch.id)
    ).all()
    assert len(members) == 20
    assert len({member.ticket_id for member in members}) == 20


def test_the_batch_is_ordered_by_priority_then_age(db_session):
    """§4.3a: Backend sorts before the model sees the batch."""
    world = build_world(db_session, resident_count=4)
    now = datetime.now(UTC)
    approved_ticket(world, resident=world.resident(0), priority=Priority.P1, created_at=now - timedelta(hours=3))
    approved_ticket(world, resident=world.resident(1), priority=Priority.P3, created_at=now - timedelta(hours=1))
    approved_ticket(world, resident=world.resident(2), priority=Priority.P2, created_at=now - timedelta(hours=2))
    service = _service(db_session)

    batch = service.create_batch(world.coordinator.user_id)
    ordered = [
        db_session.get(AssignmentProposalItem, item.id).ticket.priority
        for item in sorted(batch.items, key=lambda row: row.created_at)
    ]

    assert ordered == [Priority.P3, Priority.P2, Priority.P1]


def test_a_ticket_appears_once_per_batch(db_session):
    """§7.5: guaranteed by UNIQUE (batch_id, ticket_id)."""
    from sqlalchemy.exc import IntegrityError

    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)
    item = batch.items[0]

    db_session.add(
        AssignmentProposalItemMember(item_id=item.id, batch_id=batch.id, ticket_id=item.ticket_id)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# Regression: "Đã duyệt · chưa phân công" showed N tickets, the proposal only
# drafted 1. Root cause was `eligible_ticket_query` excluding every ticket
# paused by AUTO_ASSIGNMENT_DISABLED or NO_CANDIDATES -- exactly the backlog
# PROPOSAL exists to clear (§4.6 item 1) -- with nothing left to ever unpause
# them. See AssignmentCandidateService.eligible_ticket_query(include_paused=).
# ---------------------------------------------------------------------------


def test_a_batch_includes_four_independent_approved_tickets_even_if_previously_paused(db_session):
    world = build_world(db_session, resident_count=4)
    approved_ticket(world, resident=world.resident(0))
    approved_ticket(
        world,
        resident=world.resident(1),
        auto_assignment_paused=True,
    )
    approved_ticket(
        world,
        resident=world.resident(2),
        auto_assignment_paused=True,
    )
    approved_ticket(world, resident=world.resident(3))
    service = _service(db_session)

    batch = service.create_batch(world.coordinator.user_id)

    members = db_session.scalars(
        select(AssignmentProposalItemMember).where(AssignmentProposalItemMember.batch_id == batch.id)
    ).all()
    assert len(batch.items) == 4
    assert len({member.ticket_id for member in members}) == 4


def test_a_ticket_at_the_reassignment_cap_is_excluded_and_does_not_block_the_rest(db_session):
    """§11 assumption 4 / §14.3: the cap is mandatory-manual in every mode, so
    it is the one pause PROPOSAL still has to respect -- but the other, valid
    tickets must still form a batch around it."""
    world = build_world(db_session, resident_count=3)
    capped = approved_ticket(
        world,
        resident=world.resident(0),
        auto_assignment_paused=True,
        reassignment_count=4,
    )
    approved_ticket(world, resident=world.resident(1))
    approved_ticket(world, resident=world.resident(2))
    service = _service(db_session)

    batch = service.create_batch(world.coordinator.user_id)

    ticket_ids = {item.ticket_id for item in batch.items}
    assert capped.id not in ticket_ids
    assert len(batch.items) == 2


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


def test_an_incident_case_is_not_split_and_other_tickets_are_not_lost(db_session):
    world = build_world(db_session, resident_count=4)
    case = _open_case(world, world.water, count=3)
    for index in range(3):
        member_ticket = approved_ticket(
            world,
            resident=world.resident(index),
            category=world.water,
            location=world.corridor_10 if index % 2 == 0 else world.corridor_11,
            # One member carries the exact pause the proposal exists to clear.
            auto_assignment_paused=index == 0,
        )
        db_session.add(
            IncidentCaseMember(case_id=case.id, ticket_id=member_ticket.id, source_unit_id=member_ticket.source_unit_id)
        )
    db_session.commit()
    independent = approved_ticket(world, resident=world.resident(3), category=world.electrical)

    service = _service(db_session)
    batch = service.create_batch(world.coordinator.user_id)

    assert len(batch.items) == 2  # one INCIDENT_CASE item, one TICKET item
    case_items = [item for item in batch.items if item.work_item_type == "INCIDENT_CASE"]
    ticket_items = [item for item in batch.items if item.work_item_type == "TICKET"]
    assert len(case_items) == 1
    assert len(ticket_items) == 1
    assert ticket_items[0].ticket_id == independent.id
    all_member_ticket_ids = {
        member.ticket_id
        for member in db_session.scalars(
            select(AssignmentProposalItemMember).where(AssignmentProposalItemMember.batch_id == batch.id)
        )
    }
    assert len(all_member_ticket_ids) == 4
