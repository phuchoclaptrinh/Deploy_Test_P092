"""DIRECT assignment orchestration — contract §4.2-§4.5, §5.2, §6.

Everything below runs the real job store, the real candidate builder and the
real `AssignmentAgentService`; only the two model clients are scripted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from src.assignment_agent.service import AssignmentAgentService
from src.database.models.assignment_proposal import AIAssignmentJob, AIAssignmentJobMember
from src.database.models.audit_log import AuditLog
from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.notification import Notification
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.models.enums import (
    ActivationDelay,
    AssignmentEndReason,
    AssignmentJobStatus,
    AssignmentSource,
    AssignmentStatus,
    Priority,
)
from src.services.assignment_direct_service import DirectAssignmentService
from src.services.assignment_service import AssignmentService
from src.services.assignment_trigger_service import AssignmentTriggerService
from tests.test_v4.factories import approved_ticket, build_world, make_assignment
from tests.test_v4.scripted_assignment_model import (
    ScriptedAssignmentModel,
    broken_decision,
    no_suitable_candidate,
    select_index,
)


def _aware(value):
    """SQLite hands back naive datetimes; PostgreSQL does not."""
    return value if value is None or value.tzinfo else value.replace(tzinfo=UTC)


def _enable_auto(db, *, delay: ActivationDelay = ActivationDelay.IMMEDIATE) -> None:
    row = db.get(AutoAssignmentSetting, 1)
    if row is None:
        row = AutoAssignmentSetting(id=1)
        db.add(row)
    row.enabled = True
    row.activation_delay = delay.value
    db.commit()


def _agent(primary=None, fallback=None) -> AssignmentAgentService:
    return AssignmentAgentService(
        primary or ScriptedAssignmentModel(model_version="scripted-primary"),
        fallback or ScriptedAssignmentModel(model_version="scripted-fallback"),
    )


def _run(db, agent=None):
    return DirectAssignmentService(db, engine=agent or _agent()).run_due_jobs()


# ---------------------------------------------------------------------------
# Scenario 1: eligible ticket -> DIRECT job -> AI_AUTO assignment
# ---------------------------------------------------------------------------


def test_an_eligible_ticket_becomes_an_ai_auto_assignment(db_session):
    world = build_world(db_session)
    _enable_auto(db_session)
    ticket = approved_ticket(world)

    jobs = AssignmentTriggerService(db_session).enqueue_newly_eligible()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.mode == "DIRECT"
    assert job.status == AssignmentJobStatus.SCHEDULED_GRACE.value
    assert job.decision_id is not None
    # §5.1: the ticket is locked into exactly this job.
    members = db_session.scalars(select(AIAssignmentJobMember).where(AIAssignmentJobMember.job_id == job.id)).all()
    assert [member.ticket_id for member in members] == [ticket.id]
    assert members[0].is_active is True

    report = _run(db_session)

    assert report.assignments_created == 1
    assignment = db_session.scalar(select(TicketAssignment).where(TicketAssignment.ticket_id == ticket.id))
    assert assignment.assignment_source == AssignmentSource.AI_AUTO.value
    # §4.5 step 4: no human author, and the audit actor is SYSTEM.
    assert assignment.assigned_by_user_id is None
    assert assignment.assignment_job_id == job.id
    assert assignment.acceptance_reassign_at is not None
    assert assignment.cycle_started_at is not None

    db_session.refresh(job)
    assert job.status == AssignmentJobStatus.COMPLETED.value
    assert job.candidate_snapshot
    assert job.raw_model_output is not None
    # A terminal job releases its ticket.
    assert all(not member.is_active for member in job.members)

    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "AI_ASSIGNMENT_CREATED")).all()
    assert len(audit) == 1
    assert audit[0].actor_role == "SYSTEM"
    assert audit[0].actor_user_id is None


# ---------------------------------------------------------------------------
# Scenario 2: P3 skips the configured delay
# ---------------------------------------------------------------------------


def test_p3_skips_the_activation_delay_and_p1_waits(db_session):
    world = build_world(db_session)
    _enable_auto(db_session, delay=ActivationDelay.DAYS_3)
    now = datetime.now(UTC)

    urgent = approved_ticket(world, priority=Priority.P3, approved_at=now, resident=world.resident(0))
    slow = approved_ticket(world, priority=Priority.P1, approved_at=now, resident=world.resident(1))

    jobs = AssignmentTriggerService(db_session).enqueue_newly_eligible(now=now)

    scheduled = {job.ticket_id for job in jobs}
    assert urgent.id in scheduled
    # §4.2: P1 has to wait out the three-day activation delay.
    assert slow.id not in scheduled


def test_p1_is_scheduled_once_the_delay_has_elapsed(db_session):
    world = build_world(db_session)
    _enable_auto(db_session, delay=ActivationDelay.HOURS_2)
    approved_at = datetime.now(UTC) - timedelta(hours=3)
    ticket = approved_ticket(world, priority=Priority.P1, approved_at=approved_at)

    jobs = AssignmentTriggerService(db_session).enqueue_newly_eligible()

    assert [job.ticket_id for job in jobs] == [ticket.id]


def test_auto_disabled_creates_no_job(db_session):
    world = build_world(db_session)
    approved_ticket(world)
    # The switch defaults to off and opening nothing turns it on.
    assert AssignmentTriggerService(db_session).enqueue_newly_eligible() == []
    assert db_session.scalars(select(AIAssignmentJob)).all() == []


# ---------------------------------------------------------------------------
# Scenario 3: technician rejection -> immediate reassignment job
# ---------------------------------------------------------------------------


def test_a_p3_rejection_schedules_an_immediate_job_and_excludes_the_rejector(db_session):
    """§6.2 row 1 plus §4.3 rule 1 / §12 scenario 22."""
    world = build_world(db_session)
    _enable_auto(db_session)
    ticket = approved_ticket(world, priority=Priority.P3)
    assignment = make_assignment(world, ticket, world.technician(0))

    AssignmentService(db_session).reject(world.technician(0).user_id, assignment.id, "Đang ở công trình khác.")

    db_session.refresh(ticket)
    assert ticket.reassignment_count == 1
    db_session.refresh(assignment)
    assert assignment.status is AssignmentStatus.REJECTED
    assert assignment.end_reason == AssignmentEndReason.TECHNICIAN_REJECTED.value
    assert assignment.rejection_reason

    job = db_session.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket.id))
    assert job is not None
    assert job.trigger == "REASSIGN_REJECTED"
    # P3: no grace window.
    assert _aware(job.execute_after) <= datetime.now(UTC) + timedelta(seconds=1)
    assert str(world.technician(0).user_id) in (job.excluded_technician_ids or [])

    report = _run(db_session)
    assert report.assignments_created == 1
    new_assignment = db_session.scalar(
        select(TicketAssignment).where(TicketAssignment.ticket_id == ticket.id, TicketAssignment.is_active.is_(True))
    )
    # The technician who declined is not offered the same work item again.
    assert new_assignment.technician_id != world.technician(0).user_id


def test_a_p2_rejection_waits_out_the_grace_window(db_session):
    """§6.2 row 2 / §12 scenario 5."""
    world = build_world(db_session)
    _enable_auto(db_session)
    ticket = approved_ticket(world, priority=Priority.P2)
    assignment = make_assignment(world, ticket, world.technician(0))
    before = datetime.now(UTC)

    AssignmentService(db_session).reject(world.technician(0).user_id, assignment.id, "Không thể tới kịp.")

    job = db_session.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket.id))
    assert job.status == AssignmentJobStatus.SCHEDULED_GRACE.value
    assert _aware(job.execute_after) >= before + timedelta(seconds=290)

    # Nothing runs while the window is open.
    assert _run(db_session).jobs_claimed == 0


def test_a_coordinator_can_cancel_the_job_inside_the_grace_window(db_session):
    """§6.2: the window exists so a human can take the ticket back."""
    world = build_world(db_session)
    _enable_auto(db_session)
    ticket = approved_ticket(world, priority=Priority.P2)
    assignment = make_assignment(world, ticket, world.technician(0))
    AssignmentService(db_session).reject(world.technician(0).user_id, assignment.id, "Bận việc khác.")

    job = db_session.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket.id))
    from src.services.assignment_job_service import AssignmentJobService

    AssignmentJobService(db_session).cancel_by_coordinator(job, world.coordinator.user_id)
    db_session.commit()

    db_session.refresh(job)
    assert job.status == AssignmentJobStatus.CANCELLED_BY_COORDINATOR.value
    assert all(not member.is_active for member in job.members)
    # The ticket is free for a manual assignment straight away.
    AssignmentService(db_session).assign(world.coordinator.user_id, ticket.id, world.technician(1).user_id)


# ---------------------------------------------------------------------------
# Scenario 4: acceptance timeout -> warning then reassignment
# ---------------------------------------------------------------------------


def test_acceptance_timeout_warns_then_reassigns(db_session):
    """§6.3 / §12 scenario 18."""
    from src.services.operational_timeout_service import OperationalTimeoutService

    world = build_world(db_session)
    _enable_auto(db_session)
    ticket = approved_ticket(world, priority=Priority.P2)
    assignment = make_assignment(world, ticket, world.technician(0))
    assignment.acceptance_warning_at = datetime.now(UTC) - timedelta(minutes=5)
    assignment.acceptance_reassign_at = datetime.now(UTC) + timedelta(minutes=5)
    db_session.commit()

    first = OperationalTimeoutService(db_session).sweep()
    assert first["technician_acceptance_warnings"] == 1
    db_session.refresh(assignment)
    assert assignment.warning_sent_at is not None
    assert assignment.is_active is True

    assignment.acceptance_reassign_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()

    second = OperationalTimeoutService(db_session).sweep()
    assert second["technician_acceptance_reassignments"] == 1
    db_session.refresh(assignment)
    db_session.refresh(ticket)
    assert assignment.status is AssignmentStatus.REASSIGNED
    assert assignment.end_reason == AssignmentEndReason.ACCEPTANCE_TIMEOUT.value
    assert ticket.reassignment_count == 1

    job = db_session.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket.id))
    assert job is not None
    assert job.trigger == "REASSIGN_SILENT"


def test_an_accepted_assignment_is_left_alone_by_the_sweep(db_session):
    from src.services.operational_timeout_service import OperationalTimeoutService

    world = build_world(db_session)
    ticket = approved_ticket(world, priority=Priority.P2)
    assignment = make_assignment(world, ticket, world.technician(0), status=AssignmentStatus.ACCEPTED)
    assignment.accepted_at = datetime.now(UTC)
    assignment.acceptance_reassign_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()

    report = OperationalTimeoutService(db_session).sweep()

    assert report["technician_acceptance_reassignments"] == 0
    db_session.refresh(assignment)
    assert assignment.is_active is True


# ---------------------------------------------------------------------------
# Scenario 5: reassignment cap -> manual path
# ---------------------------------------------------------------------------


def test_the_fourth_change_stops_the_ai_path(db_session):
    """§11 assumption 4 / §12 scenario 9."""
    world = build_world(db_session)
    _enable_auto(db_session)
    ticket = approved_ticket(world, priority=Priority.P3, reassignment_count=3)
    assignment = make_assignment(world, ticket, world.technician(0))

    AssignmentService(db_session).reject(world.technician(0).user_id, assignment.id, "Không nhận được.")

    db_session.refresh(ticket)
    assert ticket.reassignment_count == 4
    assert ticket.auto_assignment_paused is True
    assert ticket.auto_assignment_pause_reason == "REASSIGNMENT_CAP_REACHED"
    assert db_session.scalars(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket.id)).all() == []

    alerts = db_session.scalars(
        select(Notification).where(Notification.notification_type == "ASSIGNMENT_MANUAL_REQUIRED")
    ).all()
    assert alerts


# ---------------------------------------------------------------------------
# Scenario 6: no candidate -> no model call, manual path
# ---------------------------------------------------------------------------


def test_no_candidates_means_no_model_call_at_all(db_session):
    """§5.2 item 1 / §12 scenario 8."""
    world = build_world(db_session, technician_count=1)
    _enable_auto(db_session)
    ticket = approved_ticket(world)
    # The only technician turns availability off after the job is scheduled.
    jobs = AssignmentTriggerService(db_session).enqueue_newly_eligible()
    assert len(jobs) == 1
    world.technician(0).is_available = False
    db_session.commit()

    primary = ScriptedAssignmentModel(model_version="scripted-primary")
    fallback = ScriptedAssignmentModel(model_version="scripted-fallback")
    report = _run(db_session, _agent(primary, fallback))

    assert report.no_candidates == 1
    assert primary.call_count == 0
    assert fallback.call_count == 0

    db_session.refresh(ticket)
    assert ticket.auto_assignment_paused is True
    assert ticket.auto_assignment_pause_reason == "NO_CANDIDATES"
    job = db_session.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket.id))
    assert job.status == AssignmentJobStatus.MANUAL_REQUIRED.value
    assert job.error_code == "NO_CANDIDATES"


def test_no_suitable_candidate_never_calls_the_fallback(db_session):
    """§5.2 item 7."""
    world = build_world(db_session)
    _enable_auto(db_session)
    ticket = approved_ticket(world)
    AssignmentTriggerService(db_session).enqueue_newly_eligible()

    primary = ScriptedAssignmentModel(model_version="scripted-primary", policy=no_suitable_candidate)
    fallback = ScriptedAssignmentModel(model_version="scripted-fallback")
    report = _run(db_session, _agent(primary, fallback))

    assert primary.call_count == 1
    assert fallback.call_count == 0
    assert report.assignments_created == 0
    db_session.refresh(ticket)
    assert ticket.auto_assignment_paused is True
    job = db_session.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket.id))
    # §5.1: a business answer, so the job COMPLETED rather than FAILED.
    assert job.status == AssignmentJobStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# Scenario 7: partial primary failure -> fallback only for the failed decision
# ---------------------------------------------------------------------------


def test_only_the_broken_decision_goes_to_the_fallback(db_session):
    """§5.2 items 3-4 / §12 scenario 25."""
    world = build_world(db_session, resident_count=4)
    _enable_auto(db_session)
    first = approved_ticket(world, resident=world.resident(0))
    second = approved_ticket(world, resident=world.resident(1))
    jobs = AssignmentTriggerService(db_session).enqueue_newly_eligible()
    assert len(jobs) == 2
    broken_decision_id = str(jobs[0].decision_id)

    def primary_policy(item):
        if item.decision_id == broken_decision_id:
            return broken_decision(item)
        return {
            "decision_id": item.decision_id,
            "work_item_id": item.work_item_id,
            "selected_technician_id": item.candidate_ids[0],
            "decision": "SELECTED",
            "reason": "Quyet dinh hop le tu model chinh.",
        }

    primary = ScriptedAssignmentModel(model_version="scripted-primary", policy=primary_policy)
    fallback = ScriptedAssignmentModel(model_version="scripted-fallback", policy=select_index(1))
    report = _run(db_session, _agent(primary, fallback))

    assert primary.call_count == 1
    assert fallback.call_count == 1
    # The fallback was only asked about the broken item.
    assert broken_decision_id in fallback.calls[0]
    assert str(jobs[1].decision_id) not in fallback.calls[0]
    assert report.assignments_created == 2

    for ticket in (first, second):
        assignment = db_session.scalar(
            select(TicketAssignment).where(TicketAssignment.ticket_id == ticket.id)
        )
        assert assignment is not None
        assert assignment.assignment_source == AssignmentSource.AI_AUTO.value

    models = {
        job.completed_model
        for job in db_session.scalars(select(AIAssignmentJob)).all()
        if job.completed_model
    }
    # A batch may legitimately mix the two models (§4.4).
    assert models == {"scripted-primary", "scripted-fallback"}


def test_both_models_failing_pauses_only_the_affected_tickets(db_session):
    """§5.2 item 5-6: the global switch is untouched."""
    world = build_world(db_session)
    _enable_auto(db_session)
    ticket = approved_ticket(world)
    AssignmentTriggerService(db_session).enqueue_newly_eligible()

    primary = ScriptedAssignmentModel(model_version="p", raise_error=RuntimeError("primary down"))
    fallback = ScriptedAssignmentModel(model_version="f", raise_error=RuntimeError("fallback down"))
    report = _run(db_session, _agent(primary, fallback))

    assert report.manual_required == 1
    db_session.refresh(ticket)
    assert ticket.auto_assignment_paused is True
    assert db_session.get(AutoAssignmentSetting, 1).enabled is True
    job = db_session.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket.id))
    assert job.status == AssignmentJobStatus.MANUAL_REQUIRED.value

    alerts = db_session.scalars(
        select(Notification).where(Notification.notification_type == "ASSIGNMENT_MANUAL_REQUIRED")
    ).all()
    assert alerts
    # §9: an error code, never a raw model response.
    assert "primary down" not in repr([alert.payload for alert in alerts])


# ---------------------------------------------------------------------------
# Scenario 10: manual assignment wins
# ---------------------------------------------------------------------------


def test_manual_assignment_cancels_the_open_job(db_session):
    """§4.5: the AI does not overwrite, and does not retry."""
    world = build_world(db_session)
    _enable_auto(db_session)
    ticket = approved_ticket(world, priority=Priority.P2)
    rejected = make_assignment(world, ticket, world.technician(0))
    AssignmentService(db_session).reject(world.technician(0).user_id, rejected.id, "Không nhận.")
    job = db_session.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket.id))
    assert job.status == AssignmentJobStatus.SCHEDULED_GRACE.value

    AssignmentService(db_session).assign(world.coordinator.user_id, ticket.id, world.technician(1).user_id)

    db_session.refresh(job)
    assert job.status == AssignmentJobStatus.CANCELLED_MANUAL_WON.value
    assert all(not member.is_active for member in job.members)

    active = db_session.scalar(
        select(TicketAssignment).where(TicketAssignment.ticket_id == ticket.id, TicketAssignment.is_active.is_(True))
    )
    assert active.technician_id == world.technician(1).user_id
    assert active.assignment_source == AssignmentSource.COORDINATOR_MANUAL.value


def test_a_ticket_cannot_be_in_two_unfinished_direct_jobs(db_session):
    """§5.1, enforced by the partial unique index."""
    world = build_world(db_session)
    _enable_auto(db_session)
    approved_ticket(world)

    first = AssignmentTriggerService(db_session).enqueue_newly_eligible()
    second = AssignmentTriggerService(db_session).enqueue_newly_eligible()

    assert len(first) == 1
    assert second == []


# ---------------------------------------------------------------------------
# Incident cases (§4.2, §4.5 step 5)
# ---------------------------------------------------------------------------


def _case_with_members(world, count: int) -> IncidentCase:
    now = datetime.now(UTC)
    case = IncidentCase(
        category_id=world.water.id,
        building_id=world.building.id,
        status="OPEN",
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
        density_value=count,
        sequence_no=1,
    )
    world.db.add(case)
    world.db.flush()
    case.series_id = case.id
    for index in range(count):
        ticket = approved_ticket(
            world,
            resident=world.resident(index),
            category=world.water,
            priority=Priority.P2,
            location=world.corridor_10 if index % 2 == 0 else world.corridor_11,
        )
        world.db.add(
            IncidentCaseMember(case_id=case.id, ticket_id=ticket.id, source_unit_id=ticket.source_unit_id)
        )
    world.db.commit()
    return case


def test_a_case_is_one_decision_and_one_assignment_per_member(db_session):
    """§12 scenario 24 and 27."""
    world = build_world(db_session, resident_count=6)
    _enable_auto(db_session)
    case = _case_with_members(world, 3)

    jobs = AssignmentTriggerService(db_session).enqueue_newly_eligible()
    assert len(jobs) == 1
    assert jobs[0].work_item_type == "INCIDENT_CASE"
    assert jobs[0].incident_case_id == case.id
    assert len(jobs[0].members) == 3

    report = _run(db_session)

    assert report.assignments_created == 3
    assignments = db_session.scalars(select(TicketAssignment)).all()
    assert len({assignment.technician_id for assignment in assignments}) == 1
    # §4.5 step 5: 1.00 + 0.25 * 2 for three P2 members.
    assert all(assignment.completion_sla_extension_factor == Decimal("1.50") for assignment in assignments)
    assert all(assignment.case_member_count_snapshot == 3 for assignment in assignments)


def test_the_case_sla_factor_follows_the_members_actually_written(db_session):
    """§4.5: a manual win shrinks the case, so the factor is recomputed."""
    world = build_world(db_session, resident_count=6)
    _enable_auto(db_session)
    case = _case_with_members(world, 3)
    AssignmentTriggerService(db_session).enqueue_newly_eligible()

    taken = db_session.scalar(
        select(Ticket)
        .join(IncidentCaseMember, IncidentCaseMember.ticket_id == Ticket.id)
        .where(IncidentCaseMember.case_id == case.id)
        .order_by(Ticket.created_at)
    )
    AssignmentService(db_session).assign(world.coordinator.user_id, taken.id, world.technician(2).user_id)

    report = _run(db_session)

    ai_assignments = db_session.scalars(
        select(TicketAssignment).where(TicketAssignment.assignment_source == AssignmentSource.AI_AUTO.value)
    ).all()
    assert report.assignments_created == 2
    assert len(ai_assignments) == 2
    # Two members, not the three in the original snapshot.
    assert all(assignment.completion_sla_extension_factor == Decimal("1.25") for assignment in ai_assignments)
    assert all(assignment.case_member_count_snapshot == 2 for assignment in ai_assignments)


def test_p3_case_members_never_stretch_the_sla(db_session):
    """§11 assumption 8: P3 keeps its five minutes."""
    world = build_world(db_session, resident_count=6)
    _enable_auto(db_session)
    now = datetime.now(UTC)
    case = IncidentCase(
        category_id=world.electrical.id,
        building_id=world.building.id,
        status="OPEN",
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
        density_value=2,
        sequence_no=1,
    )
    db_session.add(case)
    db_session.flush()
    case.series_id = case.id
    for index in range(2):
        ticket = approved_ticket(
            world, resident=world.resident(index), category=world.electrical, priority=Priority.P3
        )
        db_session.add(
            IncidentCaseMember(case_id=case.id, ticket_id=ticket.id, source_unit_id=ticket.source_unit_id)
        )
    db_session.commit()

    AssignmentTriggerService(db_session).enqueue_newly_eligible()
    _run(db_session)

    assignments = db_session.scalars(select(TicketAssignment)).all()
    assert assignments
    assert all(assignment.completion_sla_extension_factor == Decimal("1.00") for assignment in assignments)


# ---------------------------------------------------------------------------
# Batching (§4.3)
# ---------------------------------------------------------------------------


def test_several_work_items_share_one_model_request(db_session):
    """§12 scenario 25: batching does not turn DIRECT into PROPOSAL."""
    world = build_world(db_session, resident_count=4)
    _enable_auto(db_session)
    for index in range(3):
        approved_ticket(world, resident=world.resident(index))

    jobs = AssignmentTriggerService(db_session).enqueue_newly_eligible()
    assert len(jobs) == 3

    primary = ScriptedAssignmentModel(model_version="scripted-primary")
    report = _run(db_session, _agent(primary))

    assert report.model_requests == 1
    assert primary.call_count == 1
    assert report.assignments_created == 3
    # Independent decisions, independent jobs.
    assert len({job.decision_id for job in db_session.scalars(select(AIAssignmentJob)).all()}) == 3
