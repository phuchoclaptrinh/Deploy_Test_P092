"""What a coordinator may see and cancel on an assignment job — §6.2, §7.4, §9.

The workspace shows DIRECT jobs so a coordinator can tell "the AI is about to
run" apart from "this ticket needs me". That view is read-only with exactly one
action on it: cancelling the P1/P2 intervention window after a technician
rejected (§6.2). Every other job and status is either the switch working as
configured or a decision already in flight, and is taken back by assigning by
hand instead — which still wins the race (§4.5).

The response is also where §9 gets enforced: a code and a one-line reason, never
`error_detail`, a prompt or a raw model response.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.api.routes.coordinator_tickets import assignment_job_response, job_is_cancellable
from src.database.models.assignment_proposal import AIAssignmentJob
from src.models.api.coordinator import AssignmentJobResponse
from src.models.api.errors import INVALID_STATUS_TRANSITION, DomainError
from src.models.enums import (
    ActivationDelay,
    AssignmentJobStatus,
    AssignmentJobTrigger,
    Priority,
)
from src.services.assignment_job_service import AssignmentJobService
from src.services.assignment_service import AssignmentService
from src.services.assignment_trigger_service import AssignmentTriggerService
from tests.test_v4.factories import approved_ticket, build_world, make_assignment
from tests.test_v4.test_assignment_direct import _enable_auto


def _rejected_grace_job(db, world, *, priority: Priority = Priority.P2) -> AIAssignmentJob:
    """The one cancellable shape: DIRECT + REASSIGN_REJECTED + SCHEDULED_GRACE."""
    _enable_auto(db)
    ticket = approved_ticket(world, priority=priority)
    assignment = make_assignment(world, ticket, world.technician(0))
    AssignmentService(db).reject(world.technician(0).user_id, assignment.id, "Không đúng chuyên môn.")
    return db.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket.id))


# ---------------------------------------------------------------------------
# The response the workspace reads (§7.4, §9)
# ---------------------------------------------------------------------------


def test_the_job_response_carries_what_the_queue_needs(db_session):
    world = build_world(db_session)
    job = _rejected_grace_job(db_session, world)

    body = assignment_job_response(job)

    assert isinstance(body, AssignmentJobResponse)
    assert body.mode == "DIRECT"
    assert body.status == AssignmentJobStatus.SCHEDULED_GRACE.value
    assert body.trigger == AssignmentJobTrigger.REASSIGN_REJECTED.value
    assert body.work_item_type == "TICKET"
    assert body.work_item_id == job.work_item_id
    # Every member, so a case row never renders as a single ticket.
    assert body.ticket_ids == [member.ticket_id for member in job.members]
    assert body.execute_after is not None
    assert body.created_at is not None


def test_the_job_response_hides_the_audit_only_fields(db_session):
    """§9: no prompts, no raw model output, no stack traces."""
    world = build_world(db_session)
    job = _rejected_grace_job(db_session, world)
    job.error_code = "MODEL_FAILED"
    job.error_detail = "Traceback (most recent call last): secret internals"
    job.raw_model_output = {"prompt": "system prompt", "completion": "raw"}
    job.candidate_snapshot = [{"decision_id": str(job.decision_id), "candidates": []}]
    db_session.commit()

    payload = assignment_job_response(job).model_dump()

    assert payload["error_code"] == "MODEL_FAILED"
    assert "error_detail" not in payload
    assert "raw_model_output" not in payload
    assert "candidate_snapshot" not in payload
    assert "input_hash" not in payload


def test_the_job_response_names_the_chosen_technician(db_session):
    world = build_world(db_session)
    _enable_auto(db_session, delay=ActivationDelay.IMMEDIATE)
    approved_ticket(world)
    job = AssignmentTriggerService(db_session).enqueue_newly_eligible()[0]
    technician = world.technician(0)
    AssignmentJobService(db_session).mark_completed(
        job,
        selected_technician_id=technician.user_id,
        reason="Phù hợp kỹ năng và tải thấp nhất.",
        completed_model="scripted-primary",
    )
    db_session.commit()

    body = assignment_job_response(db_session.get(AIAssignmentJob, job.id))

    assert body.selected_technician_id == technician.user_id
    assert body.selected_technician_name == technician.user.full_name
    assert body.decision_reason == "Phù hợp kỹ năng và tải thấp nhất."


# ---------------------------------------------------------------------------
# Cancellation is limited to the §6.2 window
# ---------------------------------------------------------------------------


def test_a_rejected_p1_p2_grace_job_is_cancellable(db_session):
    world = build_world(db_session)
    job = _rejected_grace_job(db_session, world)

    assert job_is_cancellable(job) is True
    assert assignment_job_response(job).cancellable is True

    AssignmentJobService(db_session).cancel_by_coordinator(job, world.coordinator.user_id)
    db_session.commit()

    db_session.refresh(job)
    assert job.status == AssignmentJobStatus.CANCELLED_BY_COORDINATOR.value
    assert job.cancelled_by_user_id == world.coordinator.user_id
    # And the ticket is free for the manual assignment that follows.
    AssignmentService(db_session).assign(world.coordinator.user_id, job.ticket_id, world.technician(1).user_id)


def test_an_initial_auto_job_is_not_cancellable(db_session):
    """The configured delay is the switch working, not an intervention window."""
    world = build_world(db_session)
    _enable_auto(db_session, delay=ActivationDelay.HOURS_2)
    approved_ticket(world)
    job = AssignmentTriggerService(db_session).enqueue_newly_eligible()[0]
    db_session.commit()

    assert job.trigger == AssignmentJobTrigger.INITIAL_AUTO.value
    assert job_is_cancellable(job) is False
    assert assignment_job_response(job).cancellable is False
    with pytest.raises(DomainError) as exc:
        AssignmentJobService(db_session).cancel_by_coordinator(job, world.coordinator.user_id)
    assert exc.value.code == INVALID_STATUS_TRANSITION
    assert exc.value.status_code == 409
    assert db_session.get(AIAssignmentJob, job.id).status == AssignmentJobStatus.SCHEDULED_GRACE.value


def test_a_running_job_is_not_cancellable(db_session):
    world = build_world(db_session)
    job = _rejected_grace_job(db_session, world)
    job.status = AssignmentJobStatus.PRIMARY_RUNNING.value
    db_session.commit()

    assert job_is_cancellable(job) is False
    with pytest.raises(DomainError) as exc:
        AssignmentJobService(db_session).cancel_by_coordinator(job, world.coordinator.user_id)
    assert exc.value.code == INVALID_STATUS_TRANSITION


def test_a_fallback_job_is_not_cancellable(db_session):
    world = build_world(db_session)
    job = _rejected_grace_job(db_session, world)
    job.status = AssignmentJobStatus.FALLBACK_RUNNING.value
    db_session.commit()

    assert job_is_cancellable(job) is False
    with pytest.raises(DomainError):
        AssignmentJobService(db_session).cancel_by_coordinator(job, world.coordinator.user_id)


def test_a_finished_job_is_not_cancellable(db_session):
    world = build_world(db_session)
    job = _rejected_grace_job(db_session, world)
    job.status = AssignmentJobStatus.COMPLETED.value
    db_session.commit()

    with pytest.raises(DomainError) as exc:
        AssignmentJobService(db_session).cancel_by_coordinator(job, world.coordinator.user_id)
    assert exc.value.code == INVALID_STATUS_TRANSITION


def test_a_p3_reassignment_has_no_window_to_cancel(db_session):
    """§6.2: P3 runs immediately, so there is nothing to intervene in."""
    world = build_world(db_session)
    job = _rejected_grace_job(db_session, world, priority=Priority.P3)

    # It is scheduled to run now rather than in five minutes; the coordinator's
    # route in is a manual assignment, which still wins the race.
    assert job.trigger == AssignmentJobTrigger.REASSIGN_REJECTED.value
    AssignmentService(db_session).assign(world.coordinator.user_id, job.ticket_id, world.technician(1).user_id)
    db_session.refresh(job)
    assert job.status == AssignmentJobStatus.CANCELLED_MANUAL_WON.value


def test_a_proposal_job_is_not_cancellable_through_this_route(db_session):
    """§5.1: a PROPOSAL job holds no ticket; the batch is cancelled instead."""
    from src.services.assignment_proposal_service import AssignmentProposalService

    world = build_world(db_session)
    approved_ticket(world)
    batch = AssignmentProposalService(db_session).create_batch(world.coordinator.user_id)
    job = db_session.scalar(select(AIAssignmentJob).where(AIAssignmentJob.proposal_batch_id == batch.id))

    assert job_is_cancellable(job) is False
    with pytest.raises(DomainError) as exc:
        AssignmentJobService(db_session).cancel_by_coordinator(job, world.coordinator.user_id)
    assert exc.value.code == INVALID_STATUS_TRANSITION
