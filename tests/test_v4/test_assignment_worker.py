"""The durable worker process — contract §5.

Two things are being checked here, and neither is about a single service:

* one pass wires the stages together end to end, from "a ticket became
  eligible" to "an assignment exists", with nothing scheduled in-process;
* the schedule stage opens a proposal for review and creates no assignment,
  which is the line between it and the DIRECT stage above it;
* a stage that blows up does not take the pass down with it, because an
  unexpired proposal batch is a batch someone can still confirm against a
  stale snapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.database.models.assignment_proposal import AIAssignmentJob, AssignmentProposalBatch
from src.database.models.assignment_schedule import AssignmentProposalSchedule
from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.ticket_assignment import TicketAssignment
from src.models.enums import AssignmentJobStatus, AssignmentSource, ProposalBatchStatus
from src.workers import assignment_worker
from tests.test_v4.factories import approved_ticket, build_world
from tests.test_v4.scripted_assignment_model import ScriptedAssignmentModel


def _seed_eligible(env):
    db = env.session()
    try:
        world = build_world(db)
        db.add(AutoAssignmentSetting(id=1, enabled=True, activation_delay="IMMEDIATE", version=1))
        db.commit()
        ticket = approved_ticket(world)
        return ticket.id
    finally:
        db.close()


def _patch_agent(monkeypatch, primary=None, fallback=None):
    from src.assignment_agent.service import AssignmentAgentService

    agent = AssignmentAgentService(
        primary or ScriptedAssignmentModel(model_version="scripted-primary"),
        fallback or ScriptedAssignmentModel(model_version="scripted-fallback"),
    )
    monkeypatch.setattr(AssignmentAgentService, "from_settings", classmethod(lambda cls, **kwargs: agent))
    return agent


def test_one_pass_takes_a_ticket_from_eligible_to_assigned(v4_env, monkeypatch):
    ticket_id = _seed_eligible(v4_env)
    _patch_agent(monkeypatch)

    report = assignment_worker.run_once()

    assert report.errors == []
    assert report.jobs_scheduled == 1
    assert report.direct["assignments_created"] == 1

    db = v4_env.session()
    try:
        assignment = db.scalar(select(TicketAssignment).where(TicketAssignment.ticket_id == ticket_id))
        assert assignment is not None
        assert assignment.assignment_source == AssignmentSource.AI_AUTO.value
        job = db.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket_id))
        assert job.status == AssignmentJobStatus.COMPLETED.value
    finally:
        db.close()


def test_a_second_pass_does_nothing_more(v4_env, monkeypatch):
    """The job store is the state, so a repeat pass is a no-op rather than a
    second assignment."""
    _seed_eligible(v4_env)
    _patch_agent(monkeypatch)

    assignment_worker.run_once()
    second = assignment_worker.run_once()

    assert second.jobs_scheduled == 0
    assert second.direct["assignments_created"] == 0

    db = v4_env.session()
    try:
        assert len(db.scalars(select(TicketAssignment)).all()) == 1
    finally:
        db.close()


def test_a_failing_stage_does_not_stop_the_others(v4_env, monkeypatch):
    _seed_eligible(v4_env)
    _patch_agent(monkeypatch)

    def _explode(self, **kwargs):
        raise RuntimeError("direct stage is broken")

    monkeypatch.setattr(
        "src.services.assignment_direct_service.DirectAssignmentService.run_due_jobs", _explode
    )

    report = assignment_worker.run_once()

    assert any("direct" in item for item in report.errors)
    # The stages either side still ran.
    assert report.jobs_scheduled == 1
    assert report.proposal != {}


def test_the_pass_expires_a_stale_proposal_batch(v4_env, monkeypatch):
    _seed_eligible(v4_env)
    agent = _patch_agent(monkeypatch)

    db = v4_env.session()
    try:
        from src.database.models.user_profile import UserProfile
        from src.models.enums import UserRole
        from src.services.assignment_proposal_service import AssignmentProposalService

        coordinator = db.scalar(select(UserProfile).where(UserProfile.role == UserRole.COORDINATOR))
        service = AssignmentProposalService(db, engine=agent)
        batch = service.create_batch(coordinator.user_id)
        service.run_due_batches()
        stored = db.get(AssignmentProposalBatch, batch.id)
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        batch_id = batch.id
    finally:
        db.close()

    report = assignment_worker.run_once()

    assert report.proposal["batches_expired"] == 1
    db = v4_env.session()
    try:
        assert db.get(AssignmentProposalBatch, batch_id).status == ProposalBatchStatus.EXPIRED.value
    finally:
        db.close()


def test_the_worker_entry_point_supports_a_single_pass(v4_env, monkeypatch):
    """`--once` is what a cron entry runs; it must exit 0 on a clean pass."""
    _seed_eligible(v4_env)
    _patch_agent(monkeypatch)

    assert assignment_worker.main(["--once"]) == 0


def test_a_due_schedule_produces_a_reviewable_batch_in_the_same_pass(v4_env, monkeypatch):
    """Stage 5: the recurring schedule opens a draft, and assigns nothing.

    The switch is deliberately left off here, so the only thing that could have
    produced a batch is the schedule — and the only thing that could have
    produced an assignment would be a bug.
    """
    db = v4_env.session()
    try:
        world = build_world(db)
        approved_ticket(world)
        db.add(
            AssignmentProposalSchedule(
                id=1,
                enabled=True,
                interval_code="2_HOURS",
                next_run_at=datetime.now(UTC) - timedelta(minutes=1),
                configured_by_user_id=world.coordinator.user_id,
                version=2,
            )
        )
        db.commit()
    finally:
        db.close()
    _patch_agent(monkeypatch)

    report = assignment_worker.run_once()

    assert report.errors == []
    assert report.schedule["batches_created"] == 1
    db = v4_env.session()
    try:
        batch = db.scalar(select(AssignmentProposalBatch))
        assert batch.created_by_type == "SYSTEM"
        assert batch.confirmed_at is None
        # A draft for review. Nothing was handed to anybody.
        assert db.scalars(select(TicketAssignment)).all() == []
        row = db.get(AssignmentProposalSchedule, 1)
        assert row.last_batch_id == batch.id
        assert row.next_run_at.replace(tzinfo=UTC) > datetime.now(UTC)
    finally:
        db.close()


def test_a_schedule_that_is_not_due_leaves_the_pass_alone(v4_env, monkeypatch):
    db = v4_env.session()
    try:
        world = build_world(db)
        approved_ticket(world)
        db.add(
            AssignmentProposalSchedule(
                id=1,
                enabled=True,
                interval_code="1_DAY",
                next_run_at=datetime.now(UTC) + timedelta(hours=12),
                configured_by_user_id=world.coordinator.user_id,
                version=2,
            )
        )
        db.commit()
    finally:
        db.close()
    _patch_agent(monkeypatch)

    report = assignment_worker.run_once()

    assert report.schedule["due"] is False
    db = v4_env.session()
    try:
        assert db.scalars(select(AssignmentProposalBatch)).all() == []
    finally:
        db.close()
