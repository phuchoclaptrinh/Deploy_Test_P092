"""The v4 flow end to end against a real PostgreSQL database (§1.7, §3, §4-§5).

What this suite adds over `tests/test_v4/`, which runs the same services on
SQLite: the row locks are real `SELECT ... FOR UPDATE`, the partial unique
indexes are enforced by the database rather than by the service that wrote
them, `TRUNCATE ... CASCADE` proves the foreign keys are the ones the
migration created, and `FOR UPDATE SKIP LOCKED` in the job store is doing what
it claims. SQLite silently accepts several of those.

Every model call is scripted. The suite makes no network request, needs no API
key, and asserts nothing about model quality — only about what Backend does
with a decision.

Run it with the procedure in docs/v4_operations.md §8. Without
`V4_E2E_DATABASE_URL` the whole module skips.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.agents.analysis_dispatch import resume_analysis, run_analysis
from src.agents.v4.llm_client import ActionDecisionV4
from src.database.models.ai_agent_session import AIAgentQuestion, AIAnalysisSession
from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.assignment_proposal import AIAssignmentJob
from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.building import Building
from src.database.models.category import CategoryCatalog
from src.database.models.floor import Floor
from src.database.models.location import Location
from src.database.models.location_type import LocationType
from src.database.models.resident_profile import ResidentProfile
from src.database.models.technician import TechnicianProfile, TechnicianSkill
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.database.models.unit import Unit
from src.database.models.user_profile import UserProfile
from src.models.api.errors import DomainError
from src.models.enums import (
    Category,
    ClassificationStatus,
    Priority,
    Severity,
    TicketStatus,
    UserRole,
)
from src.services.agent_backend_service import AgentBackendService
from src.workers import assignment_worker
from tests.test_v4.scripted_llm import ScriptedAnalysisLLMV4

pytestmark = pytest.mark.postgres_e2e


# ---------------------------------------------------------------------------
# One seeded building, shared by the whole module.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def world(pg_env):
    """Two elevators on one floor — the asset identity §2.2 turns on.

    Lift A and lift B are the same Category on the same floor, so a duplicate
    check that keyed on Category and floor instead of `location_id` would link
    two unrelated tickets, and this fixture is what makes that visible.
    """
    db = pg_env.session()
    try:
        building = Building(code="A", name="Tower A")
        floor = Floor(building=building, floor_code="10", display_name="Tang 10", adjacency_index=10)
        lift_type = LocationType(code="ELEVATOR", display_name="Thang may")
        lift_a = Location(building=building, floor=floor, location_type=lift_type, label="Thang may A")
        lift_b = Location(building=building, floor=floor, location_type=lift_type, label="Thang may B")
        elevator = CategoryCatalog(code=Category.ELEVATOR, display_name="Thang may", base_score=35)
        coordinator = UserProfile(user_id=uuid4(), role=UserRole.COORDINATOR, full_name="Dieu phoi vien")
        _auth_user(db, coordinator.user_id)
        db.add_all([building, floor, lift_type, lift_a, lift_b, elevator, coordinator])

        residents = []
        for index in range(3):
            unit = Unit(building=building, floor=floor, unit_code=f"A-{1000 + index}")
            user = UserProfile(user_id=uuid4(), role=UserRole.RESIDENT, full_name=f"Cu dan {index}")
            _auth_user(db, user.user_id)
            profile = ResidentProfile(user=user, unit=unit, is_primary=True)
            db.add_all([unit, user, profile])
            residents.append(profile)

        technicians = []
        for index in range(2):
            user = UserProfile(user_id=uuid4(), role=UserRole.TECHNICIAN, full_name=f"Ky thuat vien {index}")
            _auth_user(db, user.user_id)
            profile = TechnicianProfile(user=user, is_active=True, is_available=True)
            db.add_all([user, profile])
            technicians.append(profile)
        db.commit()

        for profile in technicians:
            db.add(TechnicianSkill(technician_id=profile.user_id, category_id=elevator.id))
        db.commit()

        return {
            "env": pg_env,
            "lift_a": lift_a.id,
            "lift_b": lift_b.id,
            "elevator_id": elevator.id,
            "elevator_name": elevator.display_name,
            "residents": [profile.user_id for profile in residents],
            "resident_units": [profile.unit_id for profile in residents],
            "technicians": [profile.user_id for profile in technicians],
            "coordinator": coordinator.user_id,
        }
    finally:
        db.close()


def _auth_user(db, user_id) -> None:
    """`user_profiles.user_id` is a real FK onto the Supabase `auth.users` table.

    A plain PostgreSQL instance needs the shim in `scripts/postgres_test_shim.sql`
    for this to exist at all.
    """
    db.execute(
        text("INSERT INTO auth.users (id, email) VALUES (:id, :email) ON CONFLICT DO NOTHING"),
        {"id": str(user_id), "email": f"{user_id}@example.invalid"},
    )


def _ticket(world, resident_index: int, location_id, *, description: str):
    db = world["env"].session()
    try:
        ticket = Ticket(
            reporter_user_id=world["residents"][resident_index],
            source_unit_id=world["resident_units"][resident_index],
            location_id=location_id,
            description=description,
            status=TicketStatus.NEW,
            classification_status=ClassificationStatus.PROCESSING,
            severity=Severity.MEDIUM,
            created_at=datetime.now(UTC),
            sla_started_at=datetime.now(UTC),
        )
        db.add(ticket)
        db.commit()
        return ticket.id
    finally:
        db.close()


def _scripted(world, **overrides) -> ScriptedAnalysisLLMV4:
    return ScriptedAnalysisLLMV4(text_categories=[world["elevator_name"]], **overrides)


# ---------------------------------------------------------------------------
# §1.7 / §3: analysis reaches a business exit and is finalized once.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def master_ticket_id(world):
    """ANALYSIS_COMPLETE on lift A. Also the duplicate master for later."""
    ticket_id = _ticket(world, 0, world["lift_a"], description="Thang may A dung giua tang 10 va 11.")
    outcome = run_analysis(ticket_id, llm=_scripted(world))

    assert outcome.result is not None
    assert outcome.result.exit_reason.value == "ANALYSIS_COMPLETE"
    # Not a soft assertion: a non-empty list means the Agent had to work around
    # a Backend capability it expected to exist.
    assert outcome.dependency_gaps == []
    assert outcome.finalized is True
    return ticket_id


def test_analysis_complete_is_persisted_by_finalize_v4(world, master_ticket_id):
    db = world["env"].session()
    try:
        session = db.scalar(
            select(AIAnalysisSession).where(AIAnalysisSession.ticket_id == master_ticket_id)
        )
        ticket = db.get(Ticket, master_ticket_id)
        run = db.scalar(select(AIAnalysisRun).where(AIAnalysisRun.ticket_id == master_ticket_id))

        # §1.7.9: a session must never be left RUNNING, whatever happened.
        assert session.status == "COMPLETED"
        assert ticket.classification_status is ClassificationStatus.RESOLVED
        assert ticket.category_id == world["elevator_id"]
        assert ticket.priority is not None
        assert ticket.sla_due_at is not None
        assert run.contract_version == "v4"
    finally:
        db.close()


def test_a_resident_question_pauses_and_the_same_session_resumes(world):
    """§1.7.5: the answer resumes the session it paused, not a new one."""
    ticket_id = _ticket(world, 1, world["lift_b"], description="Thang may B co tieng dong la.")
    llm = _scripted(
        world,
        is_confident=False,
        actions=[
            ActionDecisionV4(
                action="ASK_RESIDENT",
                reason="Can biet co ai bi ket ben trong khong.",
                question_text="Hien co ai dang ket trong thang may khong?",
                question_options=["Co", "Khong"],
            ),
            ActionDecisionV4(action="CONCLUDE", reason="Da du du lieu."),
        ],
    )

    first = run_analysis(ticket_id, llm=llm)
    assert first.awaiting_resident is True

    db = world["env"].session()
    try:
        session = db.scalar(select(AIAnalysisSession).where(AIAnalysisSession.ticket_id == ticket_id))
        question = db.scalar(select(AIAgentQuestion).where(AIAgentQuestion.session_id == session.id))
        assert question is not None
        question.status = "ANSWERED"
        question.answer_type = "OPTION"
        question.answer_text = "Khong"
        question.answered_at = datetime.now(UTC)
        db.commit()
        session_id = session.id
    finally:
        db.close()

    second = resume_analysis(session_id, llm=llm)
    assert second.session_id == session_id
    assert second.finalized is True

    db = world["env"].session()
    try:
        runs = db.scalars(select(AIAnalysisRun).where(AIAnalysisRun.ticket_id == ticket_id)).all()
        assert len(runs) == 1
        assert db.get(AIAnalysisSession, session_id).status == "COMPLETED"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# §3.1: DUPLICATE_EXISTING links and stops. §1.7.9: finalize is idempotent.
# ---------------------------------------------------------------------------


def test_duplicate_existing_links_the_ticket_and_creates_no_assignment(world, master_ticket_id):
    db = world["env"].session()
    try:
        master = db.get(Ticket, master_ticket_id)
        master.status = TicketStatus.IN_PROGRESS
        db.commit()
    finally:
        db.close()

    ticket_id = _ticket(world, 2, world["lift_a"], description="Thang may A lai dung giua tang.")
    outcome = run_analysis(
        ticket_id,
        llm=_scripted(
            world,
            duplicate_verdicts=["SAME_INCIDENT"],
            duplicate_reason="Cung thang may A, cung hien tuong dung giua tang.",
        ),
    )
    assert outcome.result.exit_reason.value == "DUPLICATE_EXISTING"
    assert outcome.finalized is True

    db = world["env"].session()
    try:
        ticket = db.get(Ticket, ticket_id)
        assert ticket.status is TicketStatus.LINKED_DUPLICATE
        assert ticket.duplicate_of_ticket_id == master_ticket_id
        # A linked duplicate is not work: no Priority, no SLA clock, and
        # nothing for the assignment path to pick up.
        assert ticket.priority is None
        assert ticket.sla_due_at is None
        assert ticket.duplicate_analysis_run_id is not None
        assert (
            db.scalars(select(TicketAssignment).where(TicketAssignment.ticket_id == ticket_id)).all() == []
        )
    finally:
        db.close()


def test_finalize_replays_on_the_same_payload_and_conflicts_on_a_different_one(world):
    """§1.7.9 against the real partial unique index.

    A replay of the identical payload must return the stored run rather than
    write a second one, and a *different* payload for the same session must be
    refused — the two together are what make the Agent safe to retry.
    """
    ticket_id = _ticket(world, 0, world["lift_b"], description="Thang may B rung manh khi chay.")
    outcome = run_analysis(ticket_id, llm=_scripted(world))
    assert outcome.finalized is True

    db = world["env"].session()
    try:
        replay = AgentBackendService(db).finalize_v4(
            outcome.result, idempotency_key=str(outcome.result.analysis_session_id)
        )
        assert replay.id == outcome.analysis_run_id
    finally:
        db.close()

    changed = outcome.result.model_copy(update={"severity": Severity.HIGH})
    db = world["env"].session()
    try:
        with pytest.raises(DomainError) as excinfo:
            AgentBackendService(db).finalize_v4(changed, idempotency_key="a-different-key")
        assert excinfo.value.code == "ANALYSIS_ALREADY_FINALIZED"
        assert excinfo.value.status_code == 409
    finally:
        db.close()

    db = world["env"].session()
    try:
        runs = db.scalars(select(AIAnalysisRun).where(AIAnalysisRun.ticket_id == ticket_id)).all()
        assert len(runs) == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# §4-§5: the durable worker turns an eligible ticket into an AI_AUTO assignment.
# ---------------------------------------------------------------------------


def test_the_worker_creates_an_ai_auto_assignment(world):
    db = world["env"].session()
    try:
        db.add(AutoAssignmentSetting(id=1, enabled=True, activation_delay="IMMEDIATE", version=1))
        ticket = Ticket(
            reporter_user_id=world["residents"][0],
            source_unit_id=world["resident_units"][0],
            location_id=world["lift_a"],
            description="Thang may A ket cua.",
            status=TicketStatus.APPROVED,
            classification_status=ClassificationStatus.RESOLVED,
            category_id=world["elevator_id"],
            priority=Priority.P2,
            severity=Severity.MEDIUM,
            score_total=Decimal("40.00"),
            created_at=datetime.now(UTC),
            sla_started_at=datetime.now(UTC),
            sla_due_at=datetime.now(UTC) + timedelta(hours=3),
            approved_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db.add(ticket)
        db.commit()
        ticket_id = ticket.id
    finally:
        db.close()

    # No engine is injected and no model is scripted: the worker builds what
    # `ASSIGNMENT_DECISION_ENGINE` says, which ships as `RULE_ENGINE_V1`. That
    # is the end-to-end claim worth making here — a real deployment assigns
    # this ticket against a real PostgreSQL with no model configured at all.
    report = assignment_worker.run_once()

    assert report.errors == []
    assert report.jobs_scheduled == 1
    assert report.direct["assignments_created"] == 1

    db = world["env"].session()
    try:
        assignment = db.scalar(select(TicketAssignment).where(TicketAssignment.ticket_id == ticket_id))
        job = db.scalar(select(AIAssignmentJob).where(AIAssignmentJob.ticket_id == ticket_id))
        assert assignment.assignment_source == "AI_AUTO"
        # ck_ticket_assignments_human_source_has_actor: only AI_AUTO may have
        # no human author, and PostgreSQL is what enforces that here.
        assert assignment.assigned_by_user_id is None
        assert assignment.acceptance_reassign_at is not None
        assert job.status == "COMPLETED"
        assert job.candidate_snapshot
        assert job.raw_model_output is not None
        assert job.primary_model == "RULE_ENGINE_V1"
        assert job.completed_model == "RULE_ENGINE_V1"
        # The partial unique index only permits one active member per ticket,
        # so a finished job has to release its members or the next round of
        # this ticket could never be scheduled.
        assert all(not member.is_active for member in job.members)
    finally:
        db.close()


def test_a_second_worker_pass_changes_nothing(world):
    """Idempotence of the durable queue itself: the job store is the state."""
    before = assignment_worker.run_once()
    assert before.errors == []
    assert before.jobs_scheduled == 0
    assert before.direct["assignments_created"] == 0
