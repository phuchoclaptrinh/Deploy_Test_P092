"""A real pipeline on a real database, with a scripted model in place of a live one.

These tests drive `src.agents.service` end to end: the compiled LangGraph, the
Backend services, the scoring rules and the actual database writes. Only the
model is replaced, because it is the one component whose answers a test needs to
control.

Two pieces of scaffolding make that possible.

**A file-backed SQLite database.** The pipeline is written the way a background
worker is: every stage opens its own short-lived `SessionLocal()`, commits and
closes. An in-memory database would give each of those its own empty schema, so
the fixture points `SessionLocal` at one temporary file for the duration of a
test. That also makes the tests exercise the same commit boundaries production
does, which is where the interesting bugs live.

**No storage.** Signed download URLs need a configured bucket. There is none
here, so `StorageService` is stubbed to refuse, which the pipeline already
handles by analysing text-only. Tests that care about images would need a real
bucket and are not in this suite.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import src.agents.service as agent_service
import src.database.models  # noqa: F401  -- registers every mapper
import src.database.session as session_module
import src.services.agent_common as agent_common
from src.agents.llm_client import DuplicateJudgement, GroupingProposal, UnifiedClassification
from src.database.base import Base
from src.database.models.ai_agent_session import AIAgentQuestion, AIAnalysisSession
from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.category import CategoryCatalog
from src.database.models.floor import Floor
from src.database.models.location import Location
from src.database.models.location_type import LocationType
from src.database.models.resident_profile import ResidentProfile
from src.database.models.ticket import Ticket
from src.database.models.unit import Unit
from src.database.models.user_profile import UserProfile
from src.models.api.errors import DomainError
from src.models.enums import ClassificationStatus, TicketStatus, UserRole
from src.services.agent_backend_service import AgentBackendService


class _NoStorage:
    """Storage is not configured, so the pipeline analyses text only."""

    def create_signed_download_url(self, *_args, **_kwargs):
        raise DomainError("STORAGE_NOT_CONFIGURED", "no storage in tests", 500)


# ---------------------------------------------------------------------------
# Scripted model.
# ---------------------------------------------------------------------------


class ScriptedLLM:
    """Returns queued answers and records every call.

    Recording the calls is the point: "the duplicate judgement is skipped when
    there are no candidates" and "P3 never reaches the duplicate stage" are
    claims about calls that must *not* happen, and a mock that only returns
    values cannot express them.
    """

    def __init__(self, classifications, judgements=None, grouping=None):
        self.classifications = list(classifications)
        self.judgements = list(judgements or [])
        self.grouping = grouping
        self.calls: list[str] = []

    def classify(self, **_kwargs):
        self.calls.append("classify")
        if not self.classifications:
            raise AssertionError("classify was called more often than the test scripted it")
        return self.classifications.pop(0)

    def judge_duplicate(self, **_kwargs):
        self.calls.append("judge_duplicate")
        if not self.judgements:
            raise AssertionError("judge_duplicate was called more often than the test scripted it")
        return self.judgements.pop(0)

    def judge_grouping(self, **_kwargs):
        self.calls.append("judge_grouping")
        if self.grouping is None:
            raise AssertionError("judge_grouping was called but the test did not script it")
        return self.grouping


class ExplodingLLM:
    """Fails the way a real outage does: after the call has been made."""

    def __init__(self, error: Exception, *, stage: str = "classify"):
        self.error = error
        self.stage = stage
        self.calls: list[str] = []

    def classify(self, **_kwargs):
        self.calls.append("classify")
        raise self.error

    def judge_duplicate(self, **_kwargs):  # pragma: no cover - never reached
        self.calls.append("judge_duplicate")
        raise self.error

    def judge_grouping(self, **_kwargs):  # pragma: no cover - never reached
        self.calls.append("judge_grouping")
        raise self.error


def classification(**overrides) -> UnifiedClassification:
    """A plain, unambiguous water leak. Tests override only what they are about."""
    payload = {
        "category": "Nước",
        "text_category": "Nước",
        "image_category": None,
        "severity": "MEDIUM",
        "red_flag": False,
        "understandable": True,
        "image_relevant": None,
        "location_consistent": True,
        "incident_facts": ["nước rỉ từ trần nhà tắm"],
        "ai_reason": "Cư dân mô tả nước rỉ liên tục từ trần nhà tắm.",
    }
    payload.update(overrides)
    return UnifiedClassification(**payload)


def duplicate_judgement(**overrides) -> DuplicateJudgement:
    payload = {
        "verdict": "DIFFERENT_INCIDENT",
        "master_ticket_id": None,
        "reason": "Không có ứng viên nào trùng.",
    }
    payload.update(overrides)
    return DuplicateJudgement(**payload)


# ---------------------------------------------------------------------------
# The world these tests run in.
# ---------------------------------------------------------------------------


@dataclass
class AgentWorld:
    """Ids only. Every test re-reads through a fresh session, because the
    pipeline commits from sessions the test does not hold."""

    session_factory: sessionmaker
    resident: UUID
    neighbour: UUID
    coordinator: UUID
    unit_a: UUID
    unit_b: UUID
    unit_c: UUID
    unit_d: UUID
    #: Two bathrooms on adjacent floors, plus a lift on floor 3. Same category,
    #: different `location_id` -- which is what tells a correct duplicate from a
    #: wrong one.
    bath_a: UUID
    bath_b: UUID
    lift: UUID
    #: Kept clear of the duplicate scenarios so grouping is not competing with
    #: them for the five candidate slots.
    damp_c: UUID
    damp_d: UUID
    water: UUID
    elevator: UUID
    noise: UUID
    wall_damp: UUID
    security: UUID

    # -- reading back ----------------------------------------------------

    def ticket(self, ticket_id: UUID) -> Ticket:
        with self.session_factory() as db:
            return db.get(Ticket, ticket_id)

    def latest_run(self, ticket_id: UUID) -> AIAnalysisRun | None:
        with self.session_factory() as db:
            return (
                db.query(AIAnalysisRun)
                .filter(AIAnalysisRun.ticket_id == ticket_id)
                .order_by(AIAnalysisRun.run_number.desc())
                .first()
            )

    def runs(self, ticket_id: UUID) -> list[AIAnalysisRun]:
        with self.session_factory() as db:
            return list(
                db.query(AIAnalysisRun)
                .filter(AIAnalysisRun.ticket_id == ticket_id)
                .order_by(AIAnalysisRun.run_number)
            )

    def latest_session_id(self, ticket_id: UUID) -> UUID | None:
        """The session the last round used. Completed, once it finalized."""
        with self.session_factory() as db:
            return db.scalar(
                select(AIAnalysisSession.id)
                .where(AIAnalysisSession.ticket_id == ticket_id)
                .order_by(AIAnalysisSession.started_at.desc())
                .limit(1)
            )

    def pending_question(self, ticket_id: UUID) -> AIAgentQuestion | None:
        with self.session_factory() as db:
            return (
                db.query(AIAgentQuestion)
                .filter(AIAgentQuestion.ticket_id == ticket_id, AIAgentQuestion.status == "PENDING")
                .order_by(AIAgentQuestion.asked_at.desc())
                .first()
            )

    def questions(self, ticket_id: UUID) -> list[AIAgentQuestion]:
        with self.session_factory() as db:
            return list(
                db.query(AIAgentQuestion)
                .filter(AIAgentQuestion.ticket_id == ticket_id)
                .order_by(AIAgentQuestion.asked_at)
            )

    # -- writing ---------------------------------------------------------

    def make_ticket(
        self,
        *,
        location_id: UUID,
        unit_id: UUID,
        reporter: UUID | None = None,
        description: str = "Nước rỉ liên tục từ trần nhà tắm.",
        status: TicketStatus = TicketStatus.NEW,
        category_id: UUID | None = None,
        completed_at: datetime | None = None,
    ) -> UUID:
        with self.session_factory() as db:
            ticket = Ticket(
                reporter_user_id=reporter or self.resident,
                source_unit_id=unit_id,
                location_id=location_id,
                description=description,
                status=status,
                classification_status=(
                    ClassificationStatus.PENDING if category_id is None else ClassificationStatus.RESOLVED
                ),
                category_id=category_id,
                completed_at=completed_at,
            )
            db.add(ticket)
            db.commit()
            return ticket.id

    def answer(
        self,
        ticket_id: UUID,
        question_id: UUID,
        text: str | None,
        *,
        answer_type: str = "OPTION",
        location_id: UUID | None = None,
        unit_id: UUID | None = None,
    ) -> AIAgentQuestion:
        with self.session_factory() as db:
            profile = (
                db.query(ResidentProfile).filter(ResidentProfile.unit_id == (unit_id or self.unit_a)).first()
            )
            return AgentBackendService(db).answer_question(
                profile,
                ticket_id,
                question_id,
                profile.user_id,
                answer_type=answer_type,
                answer_text=text,
                selected_location_id=location_id,
            )


@pytest.fixture
def agent_world(tmp_path, monkeypatch) -> AgentWorld:
    os.environ.setdefault("OPENAI_API_KEY", "test-key")
    db_path = tmp_path / "agent.db"
    url = f"sqlite+pysqlite:///{db_path}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    # Every stage of the pipeline opens its own session; they all have to land
    # in this one file.
    monkeypatch.setattr(session_module, "SessionLocal", session_factory)
    monkeypatch.setattr(agent_service, "SessionLocal", session_factory)
    monkeypatch.setattr(agent_common, "StorageService", lambda *a, **k: _NoStorage())

    with session_factory() as db:
        floor_3 = Floor(floor_code="F03", display_name="Tầng 3", adjacency_index=3)
        floor_4 = Floor(floor_code="F04", display_name="Tầng 4", adjacency_index=4)
        floor_7 = Floor(floor_code="F07", display_name="Tầng 7", adjacency_index=7)
        floor_8 = Floor(floor_code="F08", display_name="Tầng 8", adjacency_index=8)
        bathroom = LocationType(code="BATHROOM", display_name="Nhà tắm")
        lift_type = LocationType(code="ELEVATOR", display_name="Thang máy")
        gate_type = LocationType(code="ENTRANCE_GATE", display_name="Cổng chính")
        db.add_all([floor_3, floor_4, floor_7, floor_8, bathroom, lift_type, gate_type])
        db.flush()

        unit_a = Unit(floor_id=floor_3.id, unit_code="F0301")
        unit_b = Unit(floor_id=floor_4.id, unit_code="F0401")
        unit_c = Unit(floor_id=floor_7.id, unit_code="F0701")
        unit_d = Unit(floor_id=floor_8.id, unit_code="F0801")
        db.add_all([unit_a, unit_b, unit_c, unit_d])
        db.flush()

        bath_a = Location(floor_id=floor_3.id, location_type_id=bathroom.id, unit_id=unit_a.id, label="Nhà tắm F0301")
        bath_b = Location(floor_id=floor_4.id, location_type_id=bathroom.id, unit_id=unit_b.id, label="Nhà tắm F0401")
        lift = Location(floor_id=floor_3.id, location_type_id=lift_type.id, label="Thang máy tầng 3")
        damp_c = Location(floor_id=floor_7.id, location_type_id=bathroom.id, unit_id=unit_c.id, label="Nhà tắm F0701")
        damp_d = Location(floor_id=floor_8.id, location_type_id=bathroom.id, unit_id=unit_d.id, label="Nhà tắm F0801")
        db.add_all([bath_a, bath_b, lift, damp_c, damp_d])

        # Base scores and ceilings copied from the approved catalog, because
        # the P3 tests turn on what these actually score to.
        water = CategoryCatalog(code="WATER", display_name="Nước", base_score=10, priority_ceiling=None)
        elevator = CategoryCatalog(code="ELEVATOR", display_name="Thang máy", base_score=35, priority_ceiling=None)
        noise = CategoryCatalog(code="NOISE", display_name="Tiếng ồn", base_score=10, priority_ceiling="P1")
        wall_damp = CategoryCatalog(code="WALL_DAMP", display_name="Thấm tường", base_score=20, priority_ceiling=None)
        security = CategoryCatalog(
            code="SECURITY_SAFETY", display_name="An ninh / An toàn", base_score=40, priority_ceiling=None
        )
        db.add_all([water, elevator, noise, wall_damp, security])

        resident = UserProfile(user_id=uuid4(), role=UserRole.RESIDENT, full_name="Cư dân A", is_active=True)
        neighbour = UserProfile(user_id=uuid4(), role=UserRole.RESIDENT, full_name="Cư dân B", is_active=True)
        coordinator = UserProfile(user_id=uuid4(), role=UserRole.COORDINATOR, full_name="BQL", is_active=True)
        db.add_all([resident, neighbour, coordinator])
        db.flush()
        db.add_all(
            [
                ResidentProfile(user_id=resident.user_id, unit_id=unit_a.id, is_primary=True),
                ResidentProfile(user_id=neighbour.user_id, unit_id=unit_b.id, is_primary=True),
            ]
        )
        db.commit()

        world = AgentWorld(
            session_factory=session_factory,
            resident=resident.user_id,
            neighbour=neighbour.user_id,
            coordinator=coordinator.user_id,
            unit_a=unit_a.id,
            unit_b=unit_b.id,
            unit_c=unit_c.id,
            unit_d=unit_d.id,
            bath_a=bath_a.id,
            bath_b=bath_b.id,
            lift=lift.id,
            damp_c=damp_c.id,
            damp_d=damp_d.id,
            water=water.id,
            elevator=elevator.id,
            noise=noise.id,
            wall_damp=wall_damp.id,
            security=security.id,
        )

    yield world
    engine.dispose()


@pytest.fixture
def recently_completed_master(agent_world):
    """A matching report that was closed a few minutes ago.

    The one-hour window is what turns an apparent duplicate into a question, so
    a fixture that lands inside it is worth having exactly once.
    """
    return agent_world.make_ticket(
        location_id=agent_world.bath_a,
        unit_id=agent_world.unit_a,
        reporter=agent_world.neighbour,
        description="Nước rỉ trần nhà tắm, đã xử lý.",
        status=TicketStatus.COMPLETED,
        category_id=agent_world.water,
        completed_at=datetime.now(UTC) - timedelta(minutes=10),
    )


__all__ = [
    "AgentWorld",
    "DuplicateJudgement",
    "ExplodingLLM",
    "GroupingProposal",
    "ScriptedLLM",
    "classification",
    "duplicate_judgement",
]
