"""Entry points that run/resume one ticket's Agent v3 analysis.

There is no worker/queue/outbox yet (see README §11) — these functions run
synchronously, in-process, each opening its own short-lived DB session. They
are meant to be invoked from a FastAPI `BackgroundTasks` callback so the
resident-facing request itself is not blocked on LLM latency.
"""

from __future__ import annotations

import logging
from uuid import UUID

from langgraph.types import Command
from sqlalchemy.orm import joinedload

from src.agents.graph import build_graph
from src.agents.llm_client import AgentLLMClient, OpenAIAgentLLMClient
from src.agents.state import AgentState
from src.agents.trace import Tracer, get_tracer
from src.database.models.ai_agent_session import AIAnalysisSession
from src.database.models.location import Location
from src.database.models.ticket import Ticket
from src.database.session import SessionLocal
from src.models.api.errors import DomainError
from src.services.agent_backend_service import AgentBackendService

logger = logging.getLogger(__name__)

MODEL_VERSION = "fixit-agent-v3-langgraph-1"


def _thread_config(session_id: UUID) -> dict[str, object]:
    return {"configurable": {"thread_id": str(session_id)}}


def _build_initial_state(backend: AgentBackendService, session: AIAnalysisSession, ticket: Ticket) -> AgentState:
    catalog = backend.get_category_catalog(session.id)
    image_urls: list[str] = []
    image_paths: list[str] = []
    for attachment in backend.attachments.list_issue_original(ticket.id):
        image_paths.append(attachment.object_path)
        try:
            image_urls.append(backend.storage.create_signed_download_url(attachment.object_path))
        except DomainError:
            logger.warning("Storage not configured; analyzing ticket %s text-only.", ticket.id)

    return AgentState(
        ticket_id=str(ticket.id),
        session_id=str(session.id),
        description=ticket.description or "",
        floor_label=ticket.location.floor.floor_code if ticket.location and ticket.location.floor else "",
        location_label=ticket.location.label if ticket.location else "",
        image_paths=image_paths,
        image_urls=image_urls,
        model_version=MODEL_VERSION,
        catalog=[item.model_dump() for item in catalog.categories],
        catalog_version=catalog.catalog_version,
        related_tickets=[],
        grouping=None,
        iterations=0,
        answer_notes=[],
        reextraction=False,
    )


def _fail_session_quietly(db, session_id: UUID | None, reason: str) -> None:
    """Đóng session sau khi graph lỗi, không để lỗi dọn dẹp che lỗi gốc."""
    if session_id is None:
        return
    try:
        # Transaction hiện tại có thể đang hỏng (nhất là khi lỗi gốc là lỗi DB),
        # nên phải rollback trước khi ghi bất cứ thứ gì.
        db.rollback()
        AgentBackendService(db).fail_session(session_id, reason)
    except Exception:
        logger.exception("Không đóng được session %s sau khi graph lỗi.", session_id)


def _emit_run_outcome(tracer: Tracer, result: object, *, kind: str) -> None:
    """Record how one invoke() returned: paused at a question, or finished.

    A run that stops at `tool_ask_wait` returns normally with an `__interrupt__`
    entry instead of an `exit_reason`; without this distinction a paused trace
    and a crashed trace both just stop, which is exactly the case worth telling
    apart when a ticket appears stuck.
    """
    state = result if isinstance(result, dict) else {}
    if state.get("__interrupt__"):
        tracer.emit(
            "run_paused",
            kind=kind,
            pending_question_id=state.get("pending_question_id"),
            iterations=state.get("iterations"),
        )
        return
    tracer.emit(
        "run_end",
        kind=kind,
        exit_reason=state.get("exit_reason"),
        iterations=state.get("iterations"),
        is_confident=state.get("is_confident"),
        severity=state.get("severity"),
    )


def run_ticket_analysis(ticket_id: UUID, llm: AgentLLMClient | None = None) -> None:
    """Start (or restart, after a resident supplement) analysis for a ticket."""
    db = SessionLocal()
    session = None
    tracer: Tracer | None = None
    try:
        backend = AgentBackendService(db)
        ticket = db.get(
            Ticket,
            ticket_id,
            options=[joinedload(Ticket.location).joinedload(Location.floor)],
        )
        if ticket is None:
            logger.error("run_ticket_analysis: ticket %s not found.", ticket_id)
            return
        session = backend.start_session(ticket_id, model_version=MODEL_VERSION)
        tracer = get_tracer(session_id=str(session.id), ticket_id=str(ticket_id))
        state = _build_initial_state(backend, session, ticket)
        tracer.emit(
            "run_start",
            kind="run",
            model_version=MODEL_VERSION,
            catalog_version=state.get("catalog_version"),
            catalog_size=len(state.get("catalog") or []),
            image_count=len(state.get("image_urls") or []),
            floor_label=state.get("floor_label"),
            location_label=state.get("location_label"),
            description=state.get("description"),
        )
        graph = build_graph(db, llm or OpenAIAgentLLMClient(), tracer)
        result = graph.invoke(state, config=_thread_config(session.id))
        _emit_run_outcome(tracer, result, kind="run")
    except Exception as exc:
        logger.exception("Agent analysis failed for ticket %s.", ticket_id)
        if tracer is not None:
            tracer.emit("run_error", kind="run", error_type=type(exc).__name__, error=str(exc))
        _fail_session_quietly(db, session.id if session else None, "Agent runtime error.")
    finally:
        db.close()


def resume_ticket_analysis(session_id: UUID, llm: AgentLLMClient | None = None) -> None:
    """Continue a paused analysis after `AgentQuestionService.answer_question`."""
    db = SessionLocal()
    tracer: Tracer | None = None
    try:
        session = db.get(AIAnalysisSession, session_id)
        if session is None or session.status != "RUNNING":
            return
        # Appends to the same session file the original run opened, so one
        # ticket's whole conversation stays in one place across requests.
        tracer = get_tracer(session_id=str(session.id), ticket_id=str(session.ticket_id))
        tracer.emit("run_start", kind="resume", model_version=session.model_version)
        graph = build_graph(db, llm or OpenAIAgentLLMClient(), tracer)
        result = graph.invoke(Command(resume={"resumed": True}), config=_thread_config(session.id))
        _emit_run_outcome(tracer, result, kind="resume")
    except Exception as exc:
        logger.exception("Agent analysis resume failed for session %s.", session_id)
        if tracer is not None:
            tracer.emit("run_error", kind="resume", error_type=type(exc).__name__, error=str(exc))
        _fail_session_quietly(db, session_id, "Agent resume runtime error.")
    finally:
        db.close()
