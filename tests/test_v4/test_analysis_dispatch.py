"""End-to-end analysis rounds through the version-aware dispatcher.

These exercise the real graph, the real tool port and the real
`finalize_v4()`; only the four LLM calls are scripted. What is being checked is
Backend behaviour, not model quality:

* a new ticket runs on v4 and comes out finalized, never still RUNNING;
* a resident question pauses and the *same* session resumes and finalizes;
* a v3 session still resumes on v3 even while v4 is the default;
* a technical failure is never quietly downgraded to a v3 business result.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.agents.analysis_dispatch import (
    AnalysisContractVersion,
    contract_version_for_model,
    default_contract_version,
    resume_analysis,
    run_analysis,
)
from src.agents.v4.llm_client import ActionDecisionV4, ExtractionContractError
from src.agents.v4.nodes import AgentNodesV4
from src.agents.v4.service import MODEL_VERSION_V4
from src.database.models.ai_agent_session import AIAgentQuestion, AIAnalysisSession
from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.ticket import Ticket
from src.models.enums import ClassificationStatus, TicketStatus
from src.services.agent_backend_service import AgentBackendService
from tests.test_v4.factories import build_world, make_ticket
from tests.test_v4.scripted_llm import ScriptedAnalysisLLMV4


def _seed(env):
    db = env.session()
    try:
        world = build_world(db)
        ticket = make_ticket(world, location=world.elevator_a, description="Thang máy dừng giữa tầng 10 và 11.")
        ids = {
            "ticket_id": ticket.id,
            "elevator": world.elevator.display_name,
            "water": world.water.display_name,
            "elevator_a": world.elevator_a.id,
            "resident_1": world.residents[1].user_id,
            "unit_1": world.residents[1].unit_id,
            "elevator_category_id": world.elevator.id,
        }
        return ids
    finally:
        db.close()


def _ticket(env, ticket_id) -> Ticket:
    db = env.session()
    try:
        return db.get(Ticket, ticket_id)
    finally:
        db.close()


def _session_for(env, ticket_id) -> AIAnalysisSession:
    db = env.session()
    try:
        return db.scalar(select(AIAnalysisSession).where(AIAnalysisSession.ticket_id == ticket_id))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scenario 1: new ticket -> v4 session -> ANALYSIS_COMPLETE -> finalized
# ---------------------------------------------------------------------------


def test_new_ticket_runs_on_v4_and_is_finalized(v4_env, v4_contract):
    ids = _seed(v4_env)
    llm = ScriptedAnalysisLLMV4(text_categories=[ids["elevator"]])

    outcome = run_analysis(ids["ticket_id"], llm=llm)

    assert outcome is not None
    assert outcome.result is not None
    assert outcome.result.exit_reason.value == "ANALYSIS_COMPLETE"
    assert outcome.finalized
    assert outcome.finalize_error is None
    assert outcome.dependency_gaps == []

    ticket = _ticket(v4_env, ids["ticket_id"])
    session = _session_for(v4_env, ids["ticket_id"])
    assert session.model_version == MODEL_VERSION_V4
    # The acceptance criterion: a successful v4 analysis never leaves RUNNING.
    assert session.status == "COMPLETED"
    assert ticket.classification_status is ClassificationStatus.RESOLVED
    assert ticket.category_id == ids["elevator_category_id"]

    db = v4_env.session()
    try:
        run = db.scalar(select(AIAnalysisRun).where(AIAnalysisRun.ticket_id == ids["ticket_id"]))
        assert run.contract_version == "v4"
        assert run.analysis_session_id == session.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scenario 2: ask a resident, answer, resume the same session, finalize
# ---------------------------------------------------------------------------


def test_a_resident_question_pauses_and_the_same_session_resumes_and_finalizes(v4_env, v4_contract):
    ids = _seed(v4_env)
    llm = ScriptedAnalysisLLMV4(
        text_categories=[ids["elevator"]],
        is_confident=False,
        actions=[
            ActionDecisionV4(
                action="ASK_RESIDENT",
                reason="Cần biết có người mắc kẹt bên trong không.",
                question_text="Hiện có ai đang mắc kẹt trong thang máy không?",
                question_options=["Có", "Không"],
            ),
            ActionDecisionV4(action="CONCLUDE", reason="Đã đủ dữ liệu sau khi cư dân trả lời."),
        ],
    )

    first = run_analysis(ids["ticket_id"], llm=llm)
    assert first is not None
    assert first.awaiting_resident is True
    assert first.result is None

    session = _session_for(v4_env, ids["ticket_id"])
    assert session.status == "RUNNING"

    # The resident answers.
    db = v4_env.session()
    try:
        question = db.scalar(select(AIAgentQuestion).where(AIAgentQuestion.session_id == session.id))
        assert question is not None
        question.status = "ANSWERED"
        question.answer_type = "OPTION"
        question.answer_text = "Không"
        question.answered_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()

    second = resume_analysis(session.id, llm=llm)

    assert second is not None
    assert second.result is not None
    assert second.finalized
    # The same session, not a new one.
    assert second.session_id == session.id

    resumed = _session_for(v4_env, ids["ticket_id"])
    assert resumed.id == session.id
    assert resumed.status == "COMPLETED"

    db = v4_env.session()
    try:
        runs = db.scalars(select(AIAnalysisRun).where(AIAnalysisRun.ticket_id == ids["ticket_id"])).all()
        assert len(runs) == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scenario 3: DUPLICATE_EXISTING end to end
# ---------------------------------------------------------------------------


def test_duplicate_existing_runs_end_to_end_and_links_the_master(v4_env, v4_contract):
    db = v4_env.session()
    try:
        world = build_world(db)
        master = make_ticket(
            world,
            resident=world.resident(1),
            location=world.elevator_a,
            category=world.elevator,
            status=TicketStatus.IN_PROGRESS,
        )
        ticket = make_ticket(
            world,
            resident=world.resident(0),
            location=world.elevator_a,
            description="Thang máy A lại dừng giữa tầng.",
        )
        master_id, ticket_id = master.id, ticket.id
        elevator_name = world.elevator.display_name
    finally:
        db.close()

    llm = ScriptedAnalysisLLMV4(
        text_categories=[elevator_name],
        duplicate_verdicts=["SAME_INCIDENT"],
        duplicate_reason="Cùng thang máy A, cùng hiện tượng dừng giữa tầng.",
    )

    outcome = run_analysis(ticket_id, llm=llm)

    assert outcome is not None
    assert outcome.result is not None
    assert outcome.result.exit_reason.value == "DUPLICATE_EXISTING"
    assert outcome.finalized

    db = v4_env.session()
    try:
        linked = db.get(Ticket, ticket_id)
        assert linked.status is TicketStatus.LINKED_DUPLICATE
        assert linked.duplicate_of_ticket_id == master_id
        assert linked.priority is None
        assert not linked.assignments
    finally:
        db.close()


def test_duplicate_existing_keeps_a_coordinator_note_even_when_the_model_gives_no_reason(v4_env, v4_contract):
    """Regression: `exit_duplicate_existing` used to fall through to
    `state.get("duplicate_reason") or None` for `confidence_notes`, silently
    losing the note whenever `duplicate_reason` was empty -- even though
    `duplicate.reason` a few lines above had a real fallback string. The "Ghi
    chu phan tich cua AI" box in the ticket panel would then just not render."""
    db = v4_env.session()
    try:
        world = build_world(db)
        make_ticket(
            world,
            resident=world.resident(1),
            location=world.elevator_a,
            category=world.elevator,
            status=TicketStatus.IN_PROGRESS,
        )
        ticket = make_ticket(
            world,
            resident=world.resident(0),
            location=world.elevator_a,
            description="Thang máy A lại dừng giữa tầng.",
        )
        ticket_id = ticket.id
        elevator_name = world.elevator.display_name
    finally:
        db.close()

    llm = ScriptedAnalysisLLMV4(
        text_categories=[elevator_name],
        duplicate_verdicts=["SAME_INCIDENT"],
        duplicate_reason="",
    )

    outcome = run_analysis(ticket_id, llm=llm)

    assert outcome.result.exit_reason.value == "DUPLICATE_EXISTING"
    assert outcome.result.confidence_notes
    assert outcome.result.confidence_notes == outcome.result.duplicate.reason


def test_insufficient_input_always_carries_a_note_even_when_severity_is_established(db_session):
    """Regression: `_route_conclude` reaches `exit_insufficient` from two
    different conditions -- a missing severity, or `is_input_insufficient`
    (an irrelevant image, or unintelligible text with no usable photo). The
    old fallback only covered the missing-severity case, so a ticket that
    failed on image relevance while severity was already established from the
    text lost its explanation entirely (`confidence_notes` stayed None).

    Calls the exit node directly, bypassing the LLM, because the extraction
    schema now requires a non-empty `notes` from the model -- the scenario
    this guards against (an empty `confidence_notes` reaching this exit) can
    still happen from other state paths, so the node's own fallback is what is
    under test here, not the schema."""
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a)
    session = AIAnalysisSession(
        ticket_id=ticket.id,
        status="RUNNING",
        model_version=MODEL_VERSION_V4,
        category_catalog_version="v-test",
    )
    db_session.add(session)
    db_session.commit()

    nodes = AgentNodesV4.__new__(AgentNodesV4)
    nodes.db = db_session
    nodes.backend = AgentBackendService(db_session)
    nodes.clock = lambda: datetime.now(UTC)

    state = {
        "session_id": str(session.id),
        "ticket_id": str(ticket.id),
        "model_version": MODEL_VERSION_V4,
        "image_urls": ["https://example/scene.jpg"],
        "is_relevant": False,
        "severity": "MEDIUM",
        "confidence_notes": None,
    }

    updates = nodes.exit_insufficient(state)

    assert updates["exit_reason"] == "INSUFFICIENT_INPUT"
    assert updates["result"]["confidence_notes"]


# ---------------------------------------------------------------------------
# Scenario 4: v3 sessions keep resuming on v3
# ---------------------------------------------------------------------------


def test_contract_is_read_from_the_session_not_the_current_default():
    assert contract_version_for_model(MODEL_VERSION_V4) is AnalysisContractVersion.V4
    assert contract_version_for_model("fixit-agent-v3-langgraph-1") is AnalysisContractVersion.V3
    # An unrecognised marker predates v4, so it must not be resumed on v4.
    assert contract_version_for_model(None) is AnalysisContractVersion.V3
    assert contract_version_for_model("something-else") is AnalysisContractVersion.V3


def test_a_v3_session_resumes_on_v3_even_when_v4_is_the_default(v4_env, v4_contract, monkeypatch):
    """§18.2 / acceptance: old sessions stay resumable on the contract they
    started under."""
    db = v4_env.session()
    try:
        world = build_world(db)
        ticket = make_ticket(world, location=world.elevator_a)
        session = AIAnalysisSession(
            ticket_id=ticket.id,
            status="RUNNING",
            model_version="fixit-agent-v3-langgraph-1",
        )
        db.add(session)
        db.commit()
        session_id = session.id
    finally:
        db.close()

    resumed_on: list[str] = []
    monkeypatch.setattr(
        "src.agents.analysis_dispatch.resume_ticket_analysis",
        lambda sid, llm=None: resumed_on.append("v3"),
    )
    monkeypatch.setattr(
        "src.agents.analysis_dispatch.resume_analysis_v4",
        lambda *args, **kwargs: pytest.fail("A v3 session must never resume on the v4 graph."),
    )

    assert default_contract_version() is AnalysisContractVersion.V4
    assert resume_analysis(session_id) is None
    assert resumed_on == ["v3"]


def test_a_new_ticket_uses_v3_when_the_switch_is_v3(v3_contract, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(
        "src.agents.analysis_dispatch.run_ticket_analysis",
        lambda ticket_id, llm=None: started.append("v3"),
    )
    monkeypatch.setattr(
        "src.agents.analysis_dispatch.start_analysis_v4",
        lambda *args, **kwargs: pytest.fail("The v3 switch must not start a v4 session."),
    )

    assert run_analysis(uuid4()) is None
    assert started == ["v3"]


# ---------------------------------------------------------------------------
# Technical failures are never downgraded
# ---------------------------------------------------------------------------


def test_a_v4_technical_failure_goes_to_manual_review_and_never_falls_back_to_v3(
    v4_env, v4_contract, monkeypatch
):
    ids = _seed(v4_env)

    class _BrokenLLM(ScriptedAnalysisLLMV4):
        def extract_text(self, **kwargs):
            raise ExtractionContractError("TextExtractionV4", ["model kept omitting severity"])

    monkeypatch.setattr(
        "src.agents.analysis_dispatch.run_ticket_analysis",
        lambda *args, **kwargs: pytest.fail("A v4 technical failure must not be retried on v3."),
    )

    outcome = run_analysis(ids["ticket_id"], llm=_BrokenLLM(text_categories=[ids["elevator"]]))

    assert outcome is not None
    assert outcome.failed_technically
    assert outcome.result is None
    assert not outcome.finalized

    ticket = _ticket(v4_env, ids["ticket_id"])
    session = _session_for(v4_env, ids["ticket_id"])
    # No invented business result: a human decides.
    assert session.status == "FAILED"
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW
    assert ticket.status is TicketStatus.NEW

    db = v4_env.session()
    try:
        assert db.scalar(select(AIAnalysisRun).where(AIAnalysisRun.ticket_id == ids["ticket_id"])) is None
    finally:
        db.close()


def test_a_backend_rejection_closes_the_session_instead_of_leaving_it_running(v4_env, v4_contract, monkeypatch):
    ids = _seed(v4_env)

    from src.models.api.errors import DomainError

    def _reject(self, result, *, idempotency_key=None):
        raise DomainError("CONTRACT_VALIDATION_ERROR", "Refused for the test.", 400)

    monkeypatch.setattr("src.services.agent_result_v4_service.AgentResultV4Service.finalize_v4", _reject)

    outcome = run_analysis(ids["ticket_id"], llm=ScriptedAnalysisLLMV4(text_categories=[ids["elevator"]]))

    assert outcome is not None
    assert outcome.result is not None
    assert not outcome.finalized
    assert outcome.finalize_error["code"] == "CONTRACT_VALIDATION_ERROR"

    session = _session_for(v4_env, ids["ticket_id"])
    ticket = _ticket(v4_env, ids["ticket_id"])
    assert session.status == "FAILED"
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW


def test_the_ticket_routes_call_the_dispatcher(monkeypatch):
    """The routes must not hold a direct reference to the v3 entry points."""
    import inspect

    import src.api.routes.tickets as routes

    source = inspect.getsource(routes)
    assert "run_ticket_analysis" not in source
    assert "resume_ticket_analysis" not in source
    assert "background_tasks.add_task(run_analysis" in source
    assert "background_tasks.add_task(resume_analysis" in source
