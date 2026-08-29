"""Entry points that run, resume and follow up one ticket's analysis.

Three functions, and the split between them is the latency contract:

* `run_analysis` / `resume_analysis` are the **foreground** round. They classify,
  ask, retrieve duplicate candidates, judge them, and finalize. The resident's
  result and notification come out of here, so nothing that can be deferred is
  done inside them.
* `run_case_grouping` is the **background** stage. It only ever starts after
  duplicate processing is final -- either the round ended DIFFERENT_INCIDENT, or
  management confirmed "not duplicate" -- and it must never delay the resident.

`resume_after_p3_downgrade` is the fourth entry point and the only way back
into the pipeline from the emergency gate. It re-enters the same graph at
`search_duplicates` with the classification a coordinator has just reviewed:
re-classifying would only produce the P3 they overruled.

Nobody schedules grouping speculatively. `run_analysis` / `resume_analysis`
start it themselves once their result is written and the persisted grouping
state says `PENDING`; the management "not duplicate" route starts it once that
decision is committed. An uncertain duplicate is parked in
`WAITING_DUPLICATE_DECISION` and reaches neither path until a human rules on it.

There is no worker or queue yet: these run synchronously, in-process, each
opening its own short-lived database session, and are meant to be invoked from
a FastAPI `BackgroundTasks` callback so the resident-facing request is not
blocked on model latency.

A technical failure never becomes a business outcome here either. When the graph
aborts or finalize is rejected, the session is closed with an explicit error
code and the ticket goes to manual review, where it can be retried.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from langgraph.types import Command
from sqlalchemy.orm import joinedload

from src.agents.graph import build_graph
from src.agents.llm_client import AgentLLMClient, OpenAIAgentLLMClient
from src.agents.state import NEVER_RAN, AgentState, advance_evidence_revision
from src.agents.trace import Tracer, get_tracer
from src.database.models.ai_agent_session import AIAnalysisSession
from src.database.models.location import Location
from src.database.models.ticket import Ticket
from src.database.session import SessionLocal
from src.models.agent_schemas import (
    AgentAnalysisResult,
    AgentGroupingResult,
    AgentSearchPurpose,
)
from src.models.api.errors import DomainError
from src.observability import annotate, root_span
from src.services.agent_backend_service import AgentBackendService
from src.services.agent_result_service import (
    GROUPING_BLOCKED,
    GROUPING_NO_MATCH,
    GROUPING_NOT_ELIGIBLE,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "fixit-agent-langgraph-1"


@dataclass
class AnalysisOutcome:
    """What one foreground round produced. Exactly one of the first three is
    meaningful.

    `analysis_run_id` is filled in when the write succeeded and `finalize_error`
    when Backend rejected the result. A rejected result is emphatically not a
    business exit, so it is kept apart from `technical_failure` too.
    """

    result: AgentAnalysisResult | None = None
    technical_failure: dict[str, object] | None = None
    awaiting_resident: bool = False
    session_id: UUID | None = None
    ticket_id: UUID | None = None
    analysis_run_id: UUID | None = None
    finalize_error: dict[str, object] | None = None
    notes: list[str] = field(default_factory=list)

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


def _build_initial_state(backend: AgentBackendService, session: AIAnalysisSession, ticket: Ticket) -> AgentState:
    """Assemble the one evidence package the model is given.

    Everything the Agent is ever allowed to see about this ticket is gathered
    here, once: the resident's words, every attached photo, the location they
    picked with its floor and apartment context, and the pinned Category
    catalog. The conversation starts empty and grows with each answer.
    """
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
    return AgentState(
        ticket_id=str(ticket.id),
        session_id=str(session.id),
        description=ticket.description or "",
        image_urls=image_urls,
        image_paths=image_paths,
        # The resident picked this from the fixed selector. The Agent may ask
        # them to re-pick it and never infers one.
        location_id=str(ticket.location_id) if ticket.location_id else None,
        location_label=(location.label if location else ""),
        location_type_code=(location.location_type.code if location and location.location_type else None),
        floor_label=(location.floor.floor_code if location and location.floor else ""),
        unit_code=(location.unit.unit_code if location and location.unit else None),
        model_version=MODEL_VERSION,
        # Display names only. The Backend-internal Category `code` stays out of
        # both the graph state and every prompt.
        catalog=[item.model_dump() for item in catalog.categories],
        catalog_version=catalog.catalog_version,
        # The scoring view of the same catalog. Separate from `catalog` because
        # it carries the Backend-internal `code`, which the P3 check needs and
        # no prompt is allowed to see.
        scoring_catalog=backend._snapshot_by_id(session),
        conversation=[],
        incident_facts=[],
        # Set only by the resident answering a Category question, and fixed for
        # the rest of the round once it is.
        confirmed_category_id=None,
        confirmed_category_name=None,
        red_flag=False,
        understandable=True,
        duplicate_candidates=[],
        duplicate_candidates_revision=NEVER_RAN,
        duplicate_searched_revision=NEVER_RAN,
        duplicate_verdict=None,
        duplicate_master_ticket_id=None,
        recent_completion_master_id=None,
        recent_completion_answer=None,
        requested_question=None,
        pending_question_id=None,
        pending_question_kind=None,
        ask_prepare_failed=False,
        iterations=0,
        tool_calls_used=0,
        ask_rounds_used=0,
        ask_elapsed_seconds=0,
        evidence_revision=0,
        technical_failure=None,
        result=None,
        exit_reason=None,
    )


def _outcome_from_state(
    state: dict[str, object] | None,
    session_id: UUID | None,
    ticket_id: UUID | None,
) -> AnalysisOutcome:
    state = state or {}
    failure = state.get("technical_failure")
    if failure:
        return AnalysisOutcome(technical_failure=dict(failure), session_id=session_id, ticket_id=ticket_id)

    payload = state.get("result")
    if payload:
        # Re-validating on the way out is not redundant: it is the last place
        # the payload is checked against the contract before Backend sees it.
        return AnalysisOutcome(
            result=AgentAnalysisResult.model_validate(payload),
            session_id=session_id,
            ticket_id=ticket_id,
        )

    return AnalysisOutcome(
        awaiting_resident=bool(state.get("pending_question_id") or state.get("__interrupt__")),
        session_id=session_id,
        ticket_id=ticket_id,
    )


# ---------------------------------------------------------------------------
# Foreground round.
# ---------------------------------------------------------------------------


def run_analysis(ticket_id: UUID, llm: AgentLLMClient | None = None) -> AnalysisOutcome:
    """Start analysis for a ticket and carry it through to a written result."""
    # One trace per run. Every model call and tool call lands underneath this
    # span, which is what turns a pile of calls into a readable picture of what
    # the Agent decided and why.
    with root_span("analysis.run", ticket_id=str(ticket_id), entry="start") as active:
        outcome = _settle(_start(ticket_id, llm))
        annotate(active, output=_trace_output(outcome))
    _grouping_follow_up(outcome, llm)
    return outcome


def resume_analysis(session_id: UUID, llm: AgentLLMClient | None = None) -> AnalysisOutcome:
    """Continue a paused analysis after the resident answered."""
    with root_span("analysis.resume", session_id=str(session_id), entry="resume") as active:
        outcome = _settle(_resume(session_id, llm))
        annotate(active, output=_trace_output(outcome))
    _grouping_follow_up(outcome, llm)
    return outcome


def resume_after_p3_downgrade(ticket_id: UUID, llm: AgentLLMClient | None = None) -> AnalysisOutcome:
    """Continue a ticket a coordinator has just downgraded out of P3.

    The classification is already settled and already reviewed by a human, so
    this re-enters the graph at `search_duplicates` rather than at `classify`.
    Re-classifying would be worse than wasteful: the severity has not changed,
    so it would score P3 again and re-open the gate the coordinator just closed.

    A fresh analysis session is opened for it. That is what gives the duplicate
    pass its own tool budget, its own catalog snapshot and its own audit trail,
    and it is why the resulting run is a second run on the ticket rather than
    an edit to the first.
    """
    with root_span("analysis.p3_downgrade", ticket_id=str(ticket_id), entry="p3_downgrade") as active:
        outcome = _settle(_start_duplicate_stage(ticket_id, llm))
        annotate(active, output=_trace_output(outcome))
    _grouping_follow_up(outcome, llm)
    return outcome


def _start_duplicate_stage(ticket_id: UUID, llm: AgentLLMClient | None) -> AnalysisOutcome:
    db = SessionLocal()
    session = None
    tracer: Tracer | None = None
    try:
        backend = AgentBackendService(db)
        ticket = db.get(
            Ticket,
            ticket_id,
            options=[
                joinedload(Ticket.location).joinedload(Location.floor),
                joinedload(Ticket.location).joinedload(Location.unit),
                joinedload(Ticket.location).joinedload(Location.location_type),
            ],
        )
        if ticket is None or ticket.category_id is None or ticket.severity is None:
            return AnalysisOutcome(
                technical_failure={
                    "stage": "p3_downgrade",
                    "error_code": "TicketNotClassified",
                    "detail": str(ticket_id),
                },
                ticket_id=ticket_id,
            )
        previous_reason = _incident_facts(db, ticket_id)
        session = backend.start_session(ticket_id, model_version=MODEL_VERSION)
        tracer = get_tracer(session_id=str(session.id), ticket_id=str(ticket_id))
        state = _build_initial_state(backend, session, ticket)
        # Hand the graph the reviewed classification instead of asking for a
        # new one. `severity_source` is TEXT because the value came off the
        # ticket, not out of a fresh look at the photos.
        state["category_id"] = str(ticket.category_id)
        state["severity"] = ticket.severity.value
        state["severity_source"] = "TEXT"
        state["ai_reason"] = previous_reason[0] if previous_reason else None
        state["incident_facts"] = previous_reason
        state.update(advance_evidence_revision(state))
        tracer.emit(
            "run_start",
            kind="p3_downgrade",
            model_version=MODEL_VERSION,
            catalog_version=state.get("catalog_version"),
            category_id=state.get("category_id"),
            severity=state.get("severity"),
        )
        graph = build_graph(db, llm or OpenAIAgentLLMClient(), tracer, entry_point="search_duplicates")
        final_state = graph.invoke(state, config=_thread_config(session.id))
        _emit_run_outcome(tracer, final_state, kind="p3_downgrade")
        return _outcome_from_state(final_state, session.id, ticket_id)
    except Exception as exc:
        logger.exception("Duplicate stage after a P3 downgrade failed for ticket %s.", ticket_id)
        if tracer is not None:
            tracer.emit("run_error", kind="p3_downgrade", error_type=type(exc).__name__, error=str(exc))
        return AnalysisOutcome(
            technical_failure={"stage": "graph", "error_code": type(exc).__name__, "detail": str(exc)},
            session_id=session.id if session else None,
            ticket_id=ticket_id,
        )
    finally:
        db.close()


def _grouping_follow_up(outcome: AnalysisOutcome, llm: AgentLLMClient | None) -> None:
    """Start the background grouping stage, but only if the written result
    authorises one.

    Callers used to queue grouping alongside the analysis, before anything was
    known about the ticket. That is scheduling it unconditionally, and it is
    wrong for the one case that matters: a `DUPLICATE_UNCERTAIN` ticket may
    still be ruled a duplicate, and a duplicate must not have been folded into
    an incident case while management was deciding.

    So the decision is taken here, after finalize has committed, from the
    persisted grouping state -- `PENDING` and nothing else. This still runs
    outside the resident's request (the whole entry point is a background
    task) and strictly after their result and notification were written.
    """
    ticket_id = outcome.ticket_id
    if ticket_id is None or not outcome.finalized:
        return
    db = SessionLocal()
    try:
        if not AgentBackendService(db).grouping_is_pending(ticket_id):
            return
    except Exception:  # noqa: BLE001 - never let the follow-up disturb the result
        logger.exception("Could not check the grouping stage for ticket %s.", ticket_id)
        return
    finally:
        db.close()
    run_case_grouping(ticket_id, llm)


def _start(ticket_id: UUID, llm: AgentLLMClient | None) -> AnalysisOutcome:
    db = SessionLocal()
    session = None
    tracer: Tracer | None = None
    try:
        backend = AgentBackendService(db)
        ticket = db.get(
            Ticket,
            ticket_id,
            options=[
                joinedload(Ticket.location).joinedload(Location.floor),
                joinedload(Ticket.location).joinedload(Location.unit),
                joinedload(Ticket.location).joinedload(Location.location_type),
            ],
        )
        if ticket is None:
            logger.error("run_analysis: ticket %s not found.", ticket_id)
            return AnalysisOutcome(
                technical_failure={"stage": "start", "error_code": "TicketNotFound", "detail": str(ticket_id)},
                ticket_id=ticket_id,
            )
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
            location_label=state.get("location_label"),
            floor_label=state.get("floor_label"),
            description=state.get("description"),
        )
        graph = build_graph(db, llm or OpenAIAgentLLMClient(), tracer)
        final_state = graph.invoke(state, config=_thread_config(session.id))
        _emit_run_outcome(tracer, final_state, kind="run")
        return _outcome_from_state(final_state, session.id, ticket_id)
    except Exception as exc:
        logger.exception("Agent analysis failed for ticket %s.", ticket_id)
        if tracer is not None:
            tracer.emit("run_error", kind="run", error_type=type(exc).__name__, error=str(exc))
        return AnalysisOutcome(
            technical_failure={"stage": "graph", "error_code": type(exc).__name__, "detail": str(exc)},
            session_id=session.id if session else None,
            ticket_id=ticket_id,
        )
    finally:
        db.close()


def _resume(session_id: UUID, llm: AgentLLMClient | None) -> AnalysisOutcome:
    db = SessionLocal()
    tracer: Tracer | None = None
    try:
        session = db.get(AIAnalysisSession, session_id)
        if session is None or session.status != "RUNNING":
            return AnalysisOutcome(
                technical_failure={
                    "stage": "resume",
                    "error_code": "SessionNotResumable",
                    "detail": str(session_id),
                },
                session_id=session_id,
            )
        # Appends to the same session file the original run opened, so one
        # ticket's whole conversation stays in one place across requests.
        tracer = get_tracer(session_id=str(session.id), ticket_id=str(session.ticket_id))
        tracer.emit("run_start", kind="resume", model_version=session.model_version)
        graph = build_graph(db, llm or OpenAIAgentLLMClient(), tracer)
        final_state = graph.invoke(Command(resume={"resumed": True}), config=_thread_config(session.id))
        _emit_run_outcome(tracer, final_state, kind="resume")
        return _outcome_from_state(final_state, session.id, session.ticket_id)
    except Exception as exc:
        logger.exception("Agent analysis resume failed for session %s.", session_id)
        if tracer is not None:
            tracer.emit("run_error", kind="resume", error_type=type(exc).__name__, error=str(exc))
        return AnalysisOutcome(
            technical_failure={"stage": "graph", "error_code": type(exc).__name__, "detail": str(exc)},
            session_id=session_id,
        )
    finally:
        db.close()


def _emit_run_outcome(tracer: Tracer, result: object, *, kind: str) -> None:
    """Record how one invoke() returned: paused at a question, or finished.

    A run that stops at `ask_wait` returns normally with an `__interrupt__`
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
            pending_question_kind=state.get("pending_question_kind"),
        )
        return
    tracer.emit(
        "run_end",
        kind=kind,
        exit_reason=state.get("exit_reason"),
        category_id=state.get("category_id"),
        severity=state.get("severity"),
        duplicate_verdict=state.get("duplicate_verdict"),
    )


def _trace_output(outcome: AnalysisOutcome) -> dict[str, object]:
    """The shape of the run, not its contents.

    No description, no question text, no resident detail: the exit reason and
    the ids are what a trace needs, and `src/agents/trace.py` already holds the
    sanitized payloads for anyone debugging a specific run.
    """
    return {
        "session_id": str(outcome.session_id) if outcome.session_id else None,
        "exit_reason": outcome.result.exit_reason.value if outcome.result else None,
        "awaiting_resident": outcome.awaiting_resident,
        "finalized": outcome.finalized,
        "analysis_run_id": str(outcome.analysis_run_id) if outcome.analysis_run_id else None,
        "failed_technically": outcome.failed_technically,
    }


# ---------------------------------------------------------------------------
# Turning an outcome into a written result.
# ---------------------------------------------------------------------------


def _settle(outcome: AnalysisOutcome) -> AnalysisOutcome:
    if outcome.failed_technically:
        _fail_session(outcome.session_id, outcome.technical_failure or {})
        return outcome

    if outcome.awaiting_resident or outcome.result is None:
        # Parked on a question. The session stays RUNNING on purpose; the
        # resident timeout sweep owns it now.
        return outcome

    return _finalize(outcome)


def _finalize(outcome: AnalysisOutcome) -> AnalysisOutcome:
    result = outcome.result
    assert result is not None
    db = SessionLocal()
    try:
        run = AgentBackendService(db).finalize(
            result,
            # The session is the natural idempotency key: one session finalizes
            # once, and a retry of this background task must not write twice.
            idempotency_key=str(result.analysis_session_id),
        )
        outcome.analysis_run_id = run.id
        logger.info(
            "Agent session %s finalized as %s (run %s).",
            result.analysis_session_id,
            result.exit_reason.value,
            run.id,
        )
    except DomainError as exc:
        logger.error(
            "Backend rejected the result for session %s: %s %s",
            result.analysis_session_id,
            exc.code,
            exc.message,
        )
        outcome.finalize_error = {"code": exc.code, "message": exc.message, "status_code": exc.status_code}
        _fail_session(
            result.analysis_session_id,
            {"stage": "finalize", "error_code": exc.code, "detail": exc.message},
        )
    except Exception as exc:  # noqa: BLE001 - the session must still be closed
        logger.exception("finalize failed for session %s.", result.analysis_session_id)
        outcome.finalize_error = {"code": "INTERNAL_ERROR", "message": type(exc).__name__}
        _fail_session(
            result.analysis_session_id,
            {"stage": "finalize", "error_code": type(exc).__name__, "detail": str(exc)},
        )
    finally:
        db.close()
    return outcome


def _fail_session(session_id: UUID | None, failure: dict[str, object]) -> None:
    """Close a session that cannot produce a written result.

    The ticket goes to manual review, which is the honest outcome: a human
    decides, nothing is invented on the resident's behalf, and the explicit
    error code on the failed run is what makes a retry meaningful.
    """
    if session_id is None:
        return
    db = SessionLocal()
    try:
        AgentBackendService(db).fail_session(
            session_id,
            f"Agent {failure.get('stage', 'run')} failure: {failure.get('detail', 'unknown')}",
            error_code=str(failure.get("error_code") or "AGENT_RUNTIME_ERROR"),
        )
    except Exception:
        logger.exception("Could not close session %s after a failure.", session_id)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Background grouping stage.
# ---------------------------------------------------------------------------


def run_case_grouping(ticket_id: UUID, llm: AgentLLMClient | None = None) -> None:
    """Look for a spreading incident this ticket belongs to.

    Runs strictly after the foreground round has been finalized and the
    resident notified. Grouping is not duplicate detection: the tickets it
    joins stay independent tickets that happen to share one incident case, so
    nothing here changes a status, an assignment or an SLA.

    Cheap exits first, because most tickets are not part of a spreading case:
    a ticket whose Category is not one of the four that can spread never
    reaches a model call at all, and neither does one with no candidates.
    """
    with root_span("analysis.grouping", ticket_id=str(ticket_id), entry="background"):
        db = SessionLocal()
        try:
            backend = AgentBackendService(db)
            if not backend.grouping_is_pending(ticket_id):
                return
            ticket = db.get(
                Ticket,
                ticket_id,
                options=[joinedload(Ticket.location).joinedload(Location.floor)],
            )
            if ticket is None or ticket.category_id is None:
                backend.record_grouping_outcome(ticket_id, GROUPING_NOT_ELIGIBLE)
                return
            session = _latest_session(db, ticket_id)
            if session is None:
                backend.record_grouping_outcome(ticket_id, GROUPING_BLOCKED)
                return

            response = backend.search_related_tickets(
                session.id,
                ticket_id=ticket_id,
                category_id=ticket.category_id,
                purpose=AgentSearchPurpose.GROUPING.value,
            )
            candidates = list(response.get("candidates") or [])
            if not candidates:
                backend.record_grouping_outcome(ticket_id, GROUPING_NO_MATCH)
                return

            client = llm or OpenAIAgentLLMClient()
            proposal = client.judge_grouping(
                evidence={
                    "category_name": ticket.category.display_name if ticket.category else "",
                    "floor_label": (
                        ticket.location.floor.floor_code if ticket.location and ticket.location.floor else ""
                    ),
                    "location_label": ticket.location.label if ticket.location else "",
                    "incident_facts": _incident_facts(db, ticket_id),
                },
                candidates=candidates,
            )
            if not proposal.grouped:
                backend.record_grouping_outcome(ticket_id, GROUPING_NO_MATCH)
                return

            allowed = {str(item.get("ticket_id")) for item in candidates}
            named = [item for item in dict.fromkeys(proposal.related_ticket_ids) if item in allowed]
            if not named:
                logger.warning("Grouping proposal named tickets outside the candidate list; dropping it.")
                backend.record_grouping_outcome(ticket_id, GROUPING_NO_MATCH)
                return

            accepted = backend.propose_case_grouping(
                session.id,
                ticket_id=ticket_id,
                related_ticket_ids=[UUID(item) for item in named],
                reason=proposal.reason[:300],
            )
            if not accepted.get("accepted"):
                logger.info("propose_case_grouping not accepted: %s", accepted.get("rejected_reason"))
                backend.record_grouping_outcome(ticket_id, GROUPING_NO_MATCH)
                return

            backend.apply_grouping(
                session.id,
                ticket_id,
                AgentGroupingResult(
                    grouped=True,
                    related_ticket_ids=[UUID(item) for item in named],
                    reason=proposal.reason[:300],
                ),
            )
        except Exception:
            # Grouping is an enrichment. It failing must never disturb a ticket
            # the resident has already been told about, so the stage is simply
            # marked blocked and the error logged.
            logger.exception("Background grouping failed for ticket %s.", ticket_id)
            _record_grouping_blocked(ticket_id)
        finally:
            db.close()


def _latest_session(db, ticket_id: UUID) -> AIAnalysisSession | None:
    return db.query(AIAnalysisSession).filter(AIAnalysisSession.ticket_id == ticket_id).order_by(
        AIAnalysisSession.started_at.desc()
    ).first()


def _incident_facts(db, ticket_id: UUID) -> list[str]:
    """The observable facts the foreground round recorded, for the grouping
    prompt. Read back from the run rather than re-derived, so the two stages
    describe the same incident."""
    from src.database.models.ai_analysis import AIAnalysisRun

    run = (
        db.query(AIAnalysisRun)
        .filter(AIAnalysisRun.ticket_id == ticket_id)
        .order_by(AIAnalysisRun.run_number.desc())
        .first()
    )
    return [run.ai_reason] if run is not None and run.ai_reason else []


def _record_grouping_blocked(ticket_id: UUID) -> None:
    db = SessionLocal()
    try:
        AgentBackendService(db).record_grouping_outcome(ticket_id, GROUPING_BLOCKED)
    except Exception:
        logger.exception("Could not mark grouping blocked for ticket %s.", ticket_id)
    finally:
        db.close()


__all__ = [
    "MODEL_VERSION",
    "AnalysisOutcome",
    "resume_after_p3_downgrade",
    "resume_analysis",
    "run_analysis",
    "run_case_grouping",
]
