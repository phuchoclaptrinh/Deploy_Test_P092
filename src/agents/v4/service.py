"""Entry points that run/resume one ticket analysis on the Agent v4 graph.

Separate from `src.agents.service` (V3) and additive: the V3 entry points keep
their behaviour so sessions already in flight finish on the contract they
started with.

What these functions deliberately do **not** do: finalize. V3 calls
`AgentResultService.finalize()` from inside its terminal nodes; V4 returns an
`AnalysisOutcomeV4` to the caller instead, and `src.agents.analysis_dispatch`
hands it to Backend `AgentResultV4Service.finalize_v4()`. Keeping the write out
of the graph is what lets Backend re-derive every claim inside its own
transaction (contract §1.7, §3) instead of trusting the model that produced it.

The outcome is a three-way answer, never collapsed into one:

* `result` — the Agent reached one of the six business exits.
* `technical_failure` — a tool or adapter failed. There is no business exit and
  none may be inferred; the caller decides what to do.
* `awaiting_resident` — the graph is parked on a question and will finish in
  `resume_analysis_v4`.

`dependency_gaps` rides along on all three: capabilities the contract requires
that this Backend cannot serve yet, reported rather than silently worked around.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from langgraph.types import Command
from sqlalchemy.orm import joinedload

from src.agents.v4.graph import build_analysis_graph_v4
from src.agents.v4.llm_client import AnalysisLLMClientV4, OpenAIAnalysisLLMClientV4
from src.agents.v4.state import NEVER_RAN, AgentStateV4
from src.agents.v4.tools import BACKEND_DEPENDENCY_NOTES, AnalysisToolPortV4
from src.database.models.ai_agent_session import AIAnalysisSession
from src.database.models.location import Location
from src.database.models.ticket import Ticket
from src.database.session import SessionLocal
from src.models.agent_schemas_v4 import AGENT_MODEL_VERSION_V4, AgentAnalysisResultV4
from src.models.api.errors import DomainError
from src.services.agent_backend_service import AgentBackendService

logger = logging.getLogger(__name__)

# Must differ from the V3 value so a session can be attributed to the contract
# it ran under, and so `resume_analysis_v4` can refuse a V3 session. Defined in
# `src.models.agent_schemas_v4` so Backend `finalize_v4()` can recognise a v4
# session without importing the graph.
MODEL_VERSION_V4 = AGENT_MODEL_VERSION_V4

__all__ = [
    "MODEL_VERSION_V4",
    "BACKEND_DEPENDENCY_NOTES",
    "AnalysisOutcomeV4",
    "build_analysis_graph_v4",
    "start_analysis_v4",
    "resume_analysis_v4",
    "is_v4_session",
]


@dataclass
class AnalysisOutcomeV4:
    """What one v4 run produced. Exactly one of the first three is meaningful.

    The last two are filled in later, by whoever hands `result` to Backend
    `finalize_v4()`: `analysis_run_id` when the write succeeded, `finalize_error`
    when Backend rejected the result. A rejected result is emphatically not a
    business exit, so it is kept separate from `technical_failure` too.
    """

    result: AgentAnalysisResultV4 | None = None
    technical_failure: dict[str, object] | None = None
    awaiting_resident: bool = False
    dependency_gaps: list[str] = field(default_factory=list)
    session_id: UUID | None = None
    analysis_run_id: UUID | None = None
    finalize_error: dict[str, object] | None = None

    @property
    def succeeded(self) -> bool:
        return self.result is not None

    @property
    def failed_technically(self) -> bool:
        return self.technical_failure is not None

    @property
    def finalized(self) -> bool:
        return self.analysis_run_id is not None


def _thread_config(session_id: UUID) -> dict[str, object]:
    return {"configurable": {"thread_id": str(session_id)}}


def _build_initial_state(
    backend: AgentBackendService,
    session: AIAnalysisSession,
    ticket: Ticket,
) -> AgentStateV4:
    catalog = backend.get_category_catalog(session.id)
    image_urls: list[str] = []
    image_paths: list[str] = []
    for attachment in backend.attachments.list_issue_original(ticket.id):
        image_paths.append(attachment.object_path)
        try:
            image_urls.append(backend.storage.create_signed_download_url(attachment.object_path))
        except DomainError:
            logger.warning("Storage not configured; analyzing ticket %s text-only.", ticket.id)

    location = ticket.location
    return AgentStateV4(
        ticket_id=str(ticket.id),
        session_id=str(session.id),
        description=ticket.description or "",
        building_label=(location.building.name if location and location.building else ""),
        floor_label=(location.floor.floor_code if location and location.floor else ""),
        location_label=(location.label if location else ""),
        # The resident picked this from a fixed catalogue; the Agent never
        # infers or overrides a location.
        location_id=str(ticket.location_id) if ticket.location_id else None,
        image_paths=image_paths,
        image_urls=image_urls,
        model_version=MODEL_VERSION_V4,
        # Display names only. The Backend-internal Category `code` stays out of
        # both the graph state and every prompt.
        catalog=[item.model_dump() for item in catalog.categories],
        catalog_version=catalog.catalog_version,
        # Fingerprints stay unset so the first merge_extraction establishes
        # revision 0 for all three levels without counting as a change.
        search_revision=0,
        incident_revision=0,
        judgement_revision=0,
        duplicate_candidates=[],
        duplicate_candidates_revision=NEVER_RAN,
        duplicate_searched_revision=NEVER_RAN,
        duplicate_judged_revision=NEVER_RAN,
        duplicate_verdict=None,
        grouping_candidates=[],
        grouping_candidates_revision=NEVER_RAN,
        grouping_searched_revision=NEVER_RAN,
        grouping_result_revision=NEVER_RAN,
        grouping_blocked_revision=NEVER_RAN,
        grouping_capability_blocked=False,
        grouping=None,
        symptom_facts=[],
        text_symptom_facts=[],
        image_symptom_facts=[],
        answer_notes=[],
        reextraction=False,
        red_flag_evidence=[],
        invalid_action_notes=[],
        technical_failure=None,
        dependency_gaps=[],
        tool_calls_used=0,
        ask_rounds_used=0,
        ask_elapsed_seconds=0,
        iterations=0,
    )


def _outcome_from_state(state: dict[str, object] | None, session_id: UUID | None) -> AnalysisOutcomeV4:
    state = state or {}
    # Only what the run actually hit. Backend implements every v4 capability, so
    # a gap here means a narrower tool port was injected deliberately.
    gaps = list(state.get("dependency_gaps") or [])

    failure = state.get("technical_failure")
    if failure:
        return AnalysisOutcomeV4(technical_failure=dict(failure), dependency_gaps=gaps, session_id=session_id)

    payload = state.get("result")
    if payload:
        # Re-validating on the way out is not redundant: it is the last place
        # the payload is checked against the contract before Backend sees it.
        return AnalysisOutcomeV4(
            result=AgentAnalysisResultV4.model_validate(payload),
            dependency_gaps=gaps,
            session_id=session_id,
        )

    return AnalysisOutcomeV4(
        awaiting_resident=bool(state.get("pending_question_id")),
        dependency_gaps=gaps,
        session_id=session_id,
    )


def is_v4_session(session: AIAnalysisSession) -> bool:
    return (session.model_version or "") == MODEL_VERSION_V4


def _fail_session_quietly(db, session_id: UUID | None, reason: str) -> None:
    """Close the session after a graph error without letting the cleanup error
    hide the original one."""
    if session_id is None:
        return
    try:
        db.rollback()
        AgentBackendService(db).fail_session(session_id, reason)
    except Exception:
        logger.exception("Không đóng được session %s sau khi graph v4 lỗi.", session_id)


def start_analysis_v4(
    ticket_id: UUID,
    llm: AnalysisLLMClientV4 | None = None,
    tools: AnalysisToolPortV4 | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AnalysisOutcomeV4:
    """Run a v4 analysis for one ticket and return what it produced."""
    db = SessionLocal()
    session = None
    try:
        backend = AgentBackendService(db)
        ticket = db.get(
            Ticket,
            ticket_id,
            options=[
                joinedload(Ticket.location).joinedload(Location.floor),
                joinedload(Ticket.location).joinedload(Location.building),
            ],
        )
        if ticket is None:
            logger.error("start_analysis_v4: ticket %s not found.", ticket_id)
            return AnalysisOutcomeV4(
                technical_failure={"stage": "start", "error_type": "TicketNotFound", "detail": str(ticket_id)},
            )
        session = backend.start_session(ticket_id, model_version=MODEL_VERSION_V4)
        state = _build_initial_state(backend, session, ticket)
        graph = build_analysis_graph_v4(db, llm or OpenAIAnalysisLLMClientV4(), tools, clock)
        final_state = graph.invoke(state, config=_thread_config(session.id))
        return _outcome_from_state(final_state, session.id)
    except Exception as exc:
        logger.exception("Agent v4 analysis failed for ticket %s.", ticket_id)
        _fail_session_quietly(db, session.id if session else None, "Agent v4 runtime error.")
        return AnalysisOutcomeV4(
            technical_failure={"stage": "graph", "error_type": type(exc).__name__, "detail": str(exc)},
            session_id=session.id if session else None,
        )
    finally:
        db.close()


def resume_analysis_v4(
    session_id: UUID,
    llm: AnalysisLLMClientV4 | None = None,
    tools: AnalysisToolPortV4 | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AnalysisOutcomeV4:
    """Continue a paused v4 analysis after the resident answered.

    Refuses sessions that did not start on v4: their checkpoint lives in the V3
    graph with a V3 state shape, and resuming it here would either lose the
    session or produce a V3-shaped run under a V4 contract.
    """
    db = SessionLocal()
    try:
        session = db.get(AIAnalysisSession, session_id)
        if session is None or session.status != "RUNNING":
            return AnalysisOutcomeV4(
                technical_failure={"stage": "resume", "error_type": "SessionNotResumable", "detail": str(session_id)},
                session_id=session_id,
            )
        if not is_v4_session(session):
            logger.warning(
                "resume_analysis_v4 refused session %s: it started on model_version=%r, not v4.",
                session_id,
                session.model_version,
            )
            return AnalysisOutcomeV4(
                technical_failure={
                    "stage": "resume",
                    "error_type": "ContractVersionMismatch",
                    "detail": f"session model_version={session.model_version!r} is not v4",
                },
                session_id=session_id,
            )
        graph = build_analysis_graph_v4(db, llm or OpenAIAnalysisLLMClientV4(), tools, clock)
        final_state = graph.invoke(Command(resume={"resumed": True}), config=_thread_config(session.id))
        return _outcome_from_state(final_state, session.id)
    except Exception as exc:
        logger.exception("Agent v4 analysis resume failed for session %s.", session_id)
        _fail_session_quietly(db, session_id, "Agent v4 resume runtime error.")
        return AnalysisOutcomeV4(
            technical_failure={"stage": "graph", "error_type": type(exc).__name__, "detail": str(exc)},
            session_id=session_id,
        )
    finally:
        db.close()
