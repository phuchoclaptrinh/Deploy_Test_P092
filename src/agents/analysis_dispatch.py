"""Chooses which analysis contract handles a ticket, and finishes the round.

Two responsibilities, and the second is the one that matters operationally:

1. **Route.** A new ticket starts on the configured contract. A paused session
   resumes on the contract it *started* on, read from
   `ai_analysis_sessions.model_version` — never from the current default. A
   session is bound for life to its contract because the checkpoint, the state
   shape and the result schema all differ, so a v3 session resuming on the v4
   graph would either be lost or produce a v3-shaped run under v4 rules.

2. **Finish.** The v4 graph deliberately does not write anything: it returns an
   `AnalysisOutcomeV4` and this module hands the result to Backend
   `AgentResultV4Service.finalize_v4()`. Whatever happens after that, the
   session must stop being RUNNING:

   * business exit accepted -> `finalize_v4()` completes the session;
   * technical failure -> the session is failed and the ticket goes to manual
     review;
   * Backend rejects the payload -> same, with the rejection recorded.

   A v4 technical failure is **never** retried on v3. Downgrading would turn "we
   do not know what happened" into a confident v3 business result, applied to a
   ticket the resident is waiting on.

`run_analysis` and `resume_analysis` are what the ticket routes call. They are
synchronous and open their own database sessions, so they are meant to be
invoked from a background task rather than inline in a request.
"""

from __future__ import annotations

import logging
from enum import Enum
from uuid import UUID

from src.agents.service import MODEL_VERSION as MODEL_VERSION_V3
from src.agents.service import resume_ticket_analysis, run_ticket_analysis
from src.agents.v4.service import (
    MODEL_VERSION_V4,
    AnalysisOutcomeV4,
    resume_analysis_v4,
    start_analysis_v4,
)
from src.agents.v4.tools import AnalysisToolPortV4
from src.config import get_settings
from src.database.models.ai_agent_session import AIAnalysisSession
from src.database.session import SessionLocal
from src.models.api.errors import DomainError
from src.observability import annotate, root_span
from src.services.agent_backend_service import AgentBackendService

logger = logging.getLogger(__name__)


class AnalysisContractVersion(str, Enum):  # noqa: UP042
    V3 = "v3"
    V4 = "v4"


_MODEL_VERSION_BY_CONTRACT = {
    AnalysisContractVersion.V3: MODEL_VERSION_V3,
    AnalysisContractVersion.V4: MODEL_VERSION_V4,
}


def default_contract_version() -> AnalysisContractVersion:
    """Which contract a *new* ticket starts on.

    Read per call rather than captured at import time, so the rollout switch can
    be flipped without a code change and tests can pin it.
    """
    return AnalysisContractVersion(get_settings().analysis_contract_version)


def contract_version_for_model(model_version: str | None) -> AnalysisContractVersion:
    """Map a session `model_version` back onto its contract.

    Unknown values fall back to V3: every session that predates v4 is a v3
    session, and guessing v4 for one of them would resume it on a graph whose
    checkpoint it does not have.
    """
    if model_version == MODEL_VERSION_V4:
        return AnalysisContractVersion.V4
    return AnalysisContractVersion.V3


def contract_version_of_session(session: AIAnalysisSession) -> AnalysisContractVersion:
    return contract_version_for_model(session.model_version)


def model_version_for(contract_version: AnalysisContractVersion) -> str:
    return _MODEL_VERSION_BY_CONTRACT[contract_version]


# ---------------------------------------------------------------------------
# Entry points used by the ticket routes.
# ---------------------------------------------------------------------------


def run_analysis(
    ticket_id: UUID,
    *,
    contract_version: AnalysisContractVersion | None = None,
    llm=None,
    tools: AnalysisToolPortV4 | None = None,
) -> AnalysisOutcomeV4 | None:
    """Start analysis for a ticket and carry it through to a written result.

    `llm` must match the chosen version: `AgentLLMClient` for v3,
    `AnalysisLLMClientV4` for v4. v3 finalizes inside its own graph and returns
    `None`; v4 returns the outcome, already finalized or already accounted for.
    `tools` is v4-only.
    """
    contract_version = contract_version or default_contract_version()
    # One trace per analysis run. Every LLM call and tool call the run makes
    # lands underneath this span, which is what turns a pile of model calls
    # into a readable picture of what the agent decided and why.
    with root_span(
        "analysis.run",
        ticket_id=str(ticket_id),
        contract_version=contract_version.value,
        entry="start",
    ) as active:
        if contract_version is not AnalysisContractVersion.V4:
            run_ticket_analysis(ticket_id, llm)
            return None
        outcome = _settle(start_analysis_v4(ticket_id, llm, tools))
        annotate(active, output=_trace_output(outcome))
        return outcome


def resume_analysis(
    session_id: UUID,
    *,
    llm=None,
    tools: AnalysisToolPortV4 | None = None,
) -> AnalysisOutcomeV4 | None:
    """Resume a paused session on the contract it started on."""
    db = SessionLocal()
    try:
        session = db.get(AIAnalysisSession, session_id)
        if session is None:
            logger.warning("resume_analysis: session %s not found.", session_id)
            return None
        contract_version = contract_version_of_session(session)
    finally:
        db.close()

    with root_span(
        "analysis.resume",
        session_id=str(session_id),
        contract_version=contract_version.value,
        entry="resume",
    ) as active:
        if contract_version is not AnalysisContractVersion.V4:
            resume_ticket_analysis(session_id, llm)
            return None
        outcome = _settle(resume_analysis_v4(session_id, llm, tools))
        annotate(active, output=_trace_output(outcome))
        return outcome


def _trace_output(outcome: AnalysisOutcomeV4) -> dict[str, object]:
    """The shape of the run, not its contents.

    No description, no question text, no resident detail: the exit reason and
    the ids are what a trace needs, and `src/agents/trace.py` already holds the
    sanitized payloads for anyone debugging a specific run.
    """
    return {
        "session_id": str(outcome.session_id),
        "exit_reason": outcome.result.exit_reason.value if outcome.result else None,
        "awaiting_resident": outcome.awaiting_resident,
        "finalized": outcome.finalized,
        "analysis_run_id": str(outcome.analysis_run_id) if outcome.analysis_run_id else None,
        "failed_technically": outcome.failed_technically,
        "dependency_gaps": list(outcome.dependency_gaps),
    }


# ---------------------------------------------------------------------------
# Turning a v4 outcome into a written result.
# ---------------------------------------------------------------------------


def _settle(outcome: AnalysisOutcomeV4) -> AnalysisOutcomeV4:
    if outcome.dependency_gaps:
        # Only reachable when a narrower tool port was injected on purpose.
        logger.warning(
            "Agent v4 session %s reported dependency gaps: %s",
            outcome.session_id,
            outcome.dependency_gaps,
        )

    if outcome.failed_technically:
        _fail_session(outcome.session_id, outcome.technical_failure or {})
        return outcome

    if outcome.awaiting_resident or outcome.result is None:
        # Parked on a question, or nothing to write. The session stays RUNNING
        # on purpose in the first case; the resident timeout sweep owns it now.
        return outcome

    return _finalize(outcome)


def _finalize(outcome: AnalysisOutcomeV4) -> AnalysisOutcomeV4:
    result = outcome.result
    assert result is not None
    db = SessionLocal()
    try:
        run = AgentBackendService(db).finalize_v4(
            result,
            # The session is the natural idempotency key: one session finalizes
            # once, and a retry of this background task must not write twice.
            idempotency_key=str(result.analysis_session_id),
        )
        outcome.analysis_run_id = run.id
        logger.info(
            "Agent v4 session %s finalized as %s (run %s).",
            result.analysis_session_id,
            result.exit_reason.value,
            run.id,
        )
    except DomainError as exc:
        logger.error(
            "Backend rejected the v4 result for session %s: %s %s",
            result.analysis_session_id,
            exc.code,
            exc.message,
        )
        outcome.finalize_error = {"code": exc.code, "message": exc.message, "status_code": exc.status_code}
        _fail_session(
            result.analysis_session_id,
            {"stage": "finalize", "error_type": exc.code, "detail": exc.message},
        )
    except Exception as exc:  # noqa: BLE001 - the session must still be closed
        logger.exception("finalize_v4 failed for session %s.", result.analysis_session_id)
        outcome.finalize_error = {"code": "INTERNAL_ERROR", "message": type(exc).__name__}
        _fail_session(
            result.analysis_session_id,
            {"stage": "finalize", "error_type": type(exc).__name__, "detail": str(exc)},
        )
    finally:
        db.close()
    return outcome


def _fail_session(session_id: UUID | None, failure: dict[str, object]) -> None:
    """Close a session that cannot produce a written result.

    `fail_session` marks the session FAILED and moves the ticket to
    MANUAL_REVIEW, which is the honest outcome: a human decides, and nothing is
    invented on the resident's behalf.
    """
    if session_id is None:
        return
    db = SessionLocal()
    try:
        AgentBackendService(db).fail_session(
            session_id,
            f"Agent v4 {failure.get('stage', 'run')} failure: {failure.get('error_type', 'unknown')}",
        )
    except Exception:
        logger.exception("Could not close v4 session %s after a failure.", session_id)
    finally:
        db.close()


__all__ = [
    "AnalysisContractVersion",
    "AnalysisOutcomeV4",
    "contract_version_for_model",
    "contract_version_of_session",
    "default_contract_version",
    "model_version_for",
    "resume_analysis",
    "run_analysis",
]
