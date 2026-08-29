"""`finalize()` -- the only place an analysis result is applied to the database.

The Agent produces a decision. This module is what makes it true, and it is
written on the assumption that the Agent may be stale or replayed:

* **One transaction, with row locks.** The session, the ticket and -- for a
  duplicate -- the master are locked before anything is read for a decision, so
  a coordinator acting at the same moment either loses the race cleanly or wins
  it cleanly. There is no partial write.
* **Finalizing is idempotent.** A session finalizes successfully once. Replaying
  the same payload returns the stored run; a different payload under the same
  session is `409 ANALYSIS_ALREADY_FINALIZED`, and a partial unique index backs
  that up if two callers get past the lock.
* **The session always ends.** Whatever branch is taken, the session leaves
  RUNNING before this returns. A successful result that left a session RUNNING
  is the failure mode this module exists to prevent.

What it deliberately does **not** do is re-judge the duplicate verdict. Backend
built the candidate snapshot, filtered it, and handed exactly that to the Agent;
re-deriving "is the master still live, still on the same asset" here would be a
second, differently-timed opinion on a question that was already settled. The
master is resolved (it must be one of the candidates this session actually saw,
it must exist, and it must not be the ticket itself) and then linked.

Four things happen strictly outside the foreground round:

* **The P3 gate** (`resolve_p3_review`). P3 is the five-minute-SLA priority, so
  a P3 classification is never published automatically: the run stops before
  the duplicate stage and a coordinator either confirms the emergency or
  downgrades it.

* **Grouping** (`apply_grouping`) runs in the background, after the resident has
  already been told the outcome.
* **A management duplicate decision** (`resolve_duplicate_uncertain`) is how a
  `DUPLICATE_UNCERTAIN` ticket is settled by a human.
* **A technical failure** never arrives here at all: it is recorded by
  `AgentSessionService.fail_session` with an error code and stays retryable.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from src.database.models.ai_agent_session import AIAgentToolCall, AIAnalysisSession
from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.location import Location
from src.database.models.scoring_rule_version import ScoringRuleVersion
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.domain.assignment_transitions import ACTIVE_ASSIGNMENT_STATUSES
from src.models.agent_schemas import (
    ANALYSIS_CONTRACT_VERSION,
    MAX_ASK_ROUNDS,
    MAX_ASK_WAIT_SECONDS,
    MAX_TOOL_CALLS,
    AgentAnalysisResult,
    AgentExitReason,
    AgentGroupingResult,
    AgentToolUsage,
    DuplicateVerdict,
    P3Decision,
    P3ReviewStatus,
)
from src.models.api.errors import (
    ACTIVE_ASSIGNMENT_EXISTS,
    CATEGORY_REQUIRED,
    INVALID_STATUS_TRANSITION,
    TICKET_NOT_FOUND,
    DomainError,
)
from src.models.enums import (
    AnalysisRunStatus,
    ClassificationStatus,
    InvalidReason,
    Priority,
    SeveritySource,
    TicketStatus,
)
from src.services.agent_common import GROUPING_CODES, AgentServiceBase, reference_code
from src.services.auto_approval import auto_approve_and_dispatch
from src.services.coordinator_support import CoordinatorScoringSupport
from src.services.p3_review_guard import p3_review_is_pending
from src.services.scoring_service import ScoringService
from src.services.ticket_service import TicketService

logger = logging.getLogger(__name__)

ANALYSIS_ALREADY_FINALIZED = "ANALYSIS_ALREADY_FINALIZED"
DUPLICATE_CANDIDATE_STALE = "DUPLICATE_CANDIDATE_STALE"
CONTRACT_VALIDATION_ERROR = "CONTRACT_VALIDATION_ERROR"

TERMINAL_TICKET_STATUSES = {
    TicketStatus.COMPLETED,
    TicketStatus.CANCELLED,
    TicketStatus.INVALID,
    TicketStatus.UNRESOLVABLE,
    TicketStatus.LINKED_DUPLICATE,
}

#: `analyzed_at` far from server time means a replayed or fabricated payload.
MAX_ANALYZED_AT_SKEW = timedelta(hours=1)

#: A downgrade may only land below the emergency priority.
DOWNGRADE_PRIORITIES = {Priority.P1, Priority.P2}

MAX_DUPLICATE_CHAIN_DEPTH = 10
GROUPING_LOOKBACK_DAYS = 3
MAX_CASE_MEMBERS = 5

#: `ai_analysis_runs.grouping_status`. A ticket that never became eligible is
#: not the same thing as one where grouping ran and found nothing, and neither
#: is one that is still waiting for a human.
#:
#: PENDING is the *only* value that authorises the background stage: it means
#: duplicate processing is finished and the ticket is a published, independent
#: ticket. WAITING_DUPLICATE_DECISION is deliberately its own state rather than
#: a flavour of PENDING -- an uncertain duplicate may still turn out to be a
#: duplicate, and a duplicate must not already have been grouped by then.
GROUPING_PENDING = "PENDING"
GROUPING_WAITING_DUPLICATE_DECISION = "WAITING_DUPLICATE_DECISION"
#: Grouping is blocked because the emergency gate is still open. Distinct from
#: WAITING_DUPLICATE_DECISION on purpose: they are different gates, held by
#: different people, and a ticket is never in both.
GROUPING_WAITING_P3_REVIEW = "WAITING_P3_MANAGEMENT_REVIEW"
GROUPING_NOT_ELIGIBLE = "NOT_ELIGIBLE"
GROUPING_NO_MATCH = "NO_MATCH"
GROUPING_GROUPED = "GROUPED"
GROUPING_BLOCKED = "BLOCKED"


class AgentResultService(AgentServiceBase):
    """Applies one `AgentAnalysisResult` to the database."""

    # ------------------------------------------------------------------
    # Entry point.
    # ------------------------------------------------------------------

    def finalize(self, result: AgentAnalysisResult, *, idempotency_key: str | None = None) -> AIAnalysisRun:
        payload_hash = self._payload_hash(result)

        try:
            session = self._session(result.analysis_session_id, lock=True)
            existing = self._replayed_run(session, payload_hash, idempotency_key)
            if existing is not None:
                # The same decision arriving twice. Return what was written the
                # first time rather than writing it again.
                self.db.rollback()
                return existing

            self._require_running_session(session)
            ticket = self._locked_ticket(result.ticket_id)
            self._validate_result(session, ticket, result)

            run = self._new_run(ticket, session, result)

            handler = {
                AgentExitReason.RED_FLAG: self._apply_red_flag,
                AgentExitReason.P3_REVIEW_REQUIRED: self._apply_p3_review_required,
                AgentExitReason.DUPLICATE_EXISTING: self._apply_duplicate_existing,
                AgentExitReason.DUPLICATE_UNCERTAIN: self._apply_duplicate_uncertain,
                AgentExitReason.LIMIT_REACHED: self._apply_manual_review,
                AgentExitReason.ANALYSIS_COMPLETE: self._apply_analysis_complete,
                AgentExitReason.INSUFFICIENT_INPUT: self._apply_insufficient_input,
            }[result.exit_reason]
            handler(session, ticket, result, run)

            run.idempotency_key = idempotency_key
            run.payload_hash = payload_hash
            self._close_session(session, result)
            self.db.add(run)
            # §2: with Automatic Assignment on, a confidently classified,
            # non-duplicate, non-P3 ticket is approved by the system and queued
            # for dispatch here -- inside the same transaction that recorded the
            # classification, so a crash cannot leave it classified but stranded.
            # With the switch off this is a no-op and the ticket waits for a
            # human approval exactly as before.
            auto_approve_and_dispatch(self.db, ticket, run)
            self._audit(
                None,
                "FINALIZE_AGENT_RESULT",
                ticket.id,
                {
                    "session_id": str(session.id),
                    "contract_version": ANALYSIS_CONTRACT_VERSION,
                    "exit_reason": result.exit_reason.value,
                    "model_version": result.model_version,
                    "category_id": str(result.category_id) if result.category_id else None,
                    "duplicate_verdict": result.duplicate_verdict.value if result.duplicate_verdict else None,
                    "classification_status": ticket.classification_status.value,
                    "ticket_status": ticket.status.value,
                },
            )
            self.db.commit()
            self.db.refresh(run)
            return run
        except Exception:
            self.db.rollback()
            raise

    # ------------------------------------------------------------------
    # Idempotency and session state.
    # ------------------------------------------------------------------

    @staticmethod
    def _payload_hash(result: AgentAnalysisResult) -> str:
        payload = json.loads(result.model_dump_json())
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _replayed_run(
        self,
        session: AIAnalysisSession,
        payload_hash: str,
        idempotency_key: str | None,
    ) -> AIAnalysisRun | None:
        """Returns the stored run for a true replay, raises for a different
        payload, and returns None when this session has never finalized."""
        run = self.db.scalar(
            select(AIAnalysisRun).where(
                AIAnalysisRun.analysis_session_id == session.id,
                AIAnalysisRun.status == AnalysisRunStatus.SUCCEEDED,
            )
        )
        if run is None:
            return None
        same_payload = run.payload_hash == payload_hash
        same_key = idempotency_key is None or run.idempotency_key == idempotency_key
        if same_payload and same_key:
            return run
        raise DomainError(
            ANALYSIS_ALREADY_FINALIZED,
            "Analysis session has already been finalized with a different result.",
            409,
        )

    @staticmethod
    def _require_running_session(session: AIAnalysisSession) -> None:
        if session.status != "RUNNING":
            raise DomainError(INVALID_STATUS_TRANSITION, "Analysis session is not running.", 409)

    def _locked_ticket(self, ticket_id: UUID) -> Ticket:
        ticket = self.db.scalar(
            select(Ticket)
            .where(Ticket.id == ticket_id)
            .options(
                joinedload(Ticket.category),
                joinedload(Ticket.location).joinedload(Location.floor),
                joinedload(Ticket.location).joinedload(Location.location_type),
                selectinload(Ticket.assignments),
            )
            .with_for_update(of=Ticket)
        )
        if ticket is None:
            raise DomainError(INVALID_STATUS_TRANSITION, "Ticket not found for analysis session.", 404)
        return ticket

    def _close_session(self, session: AIAnalysisSession, result: AgentAnalysisResult) -> None:
        """A finalized session never stays RUNNING."""
        now = datetime.now(UTC)
        session.status = "COMPLETED"
        session.completed_at = now
        session.waiting_deadline_at = None
        session.model_version = result.model_version
        session.updated_at = now
        for question in session.questions:
            if question.status == "PENDING":
                question.status = "CLOSED"

    # ------------------------------------------------------------------
    # What Backend re-checks. Deliberately short: identity and bookkeeping,
    # not a second opinion on the Agent's judgement.
    # ------------------------------------------------------------------

    def _validate_result(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResult,
    ) -> None:
        self._validate_session_ticket(session, result.ticket_id)

        if session.category_catalog_version != result.category_catalog_version:
            raise DomainError(CATEGORY_REQUIRED, "Category catalog version mismatch.", 409)

        snapshot_ids = set(self._snapshot_by_id(session))
        supplied = {
            str(item)
            for item in (result.category_id, result.text_category_id, result.image_category_id)
            if item is not None
        }
        if not supplied <= snapshot_ids:
            raise DomainError(CATEGORY_REQUIRED, "Agent returned a Category outside the session snapshot.", 400)

        # The declared counters are compared, never trusted.
        backend_usage = self._backend_tool_usage(session)
        if result.tool_usage.model_dump() != backend_usage.model_dump():
            raise DomainError(INVALID_STATUS_TRANSITION, "Agent tool usage does not match backend records.", 409)

        if result.exit_reason is AgentExitReason.LIMIT_REACHED:
            spent = (
                backend_usage.total_tool_calls >= MAX_TOOL_CALLS
                or backend_usage.ask_resident_rounds >= MAX_ASK_ROUNDS
                or backend_usage.ask_resident_elapsed_seconds >= MAX_ASK_WAIT_SECONDS
            )
            if not spent:
                raise DomainError(
                    CONTRACT_VALIDATION_ERROR,
                    "LIMIT_REACHED without an exhausted tool, ask or wait budget.",
                    400,
                )

        # A payload analyzed hours ago is a replay, not a decision.
        if abs(datetime.now(UTC) - result.analyzed_at) > MAX_ANALYZED_AT_SKEW:
            raise DomainError(CONTRACT_VALIDATION_ERROR, "analyzed_at is too far from server time.", 400)

        # The resident may have re-picked their location mid-analysis; the
        # Agent must have been looking at the same one Backend now holds.
        if result.location_id is not None and ticket.location_id != result.location_id:
            raise DomainError(
                CONTRACT_VALIDATION_ERROR,
                "Ticket location changed after the analysis was produced.",
                409,
            )

    def _backend_tool_usage(self, session: AIAnalysisSession) -> AgentToolUsage:
        tool_names = set(
            self.db.scalars(select(AIAgentToolCall.tool_name).where(AIAgentToolCall.session_id == session.id))
        )
        return AgentToolUsage(
            total_tool_calls=session.total_tool_calls,
            ask_resident_rounds=session.ask_resident_rounds,
            ask_resident_elapsed_seconds=session.ask_resident_elapsed_seconds,
            search_related_tickets_called="search_related_tickets" in tool_names,
            propose_case_grouping_called="propose_case_grouping" in tool_names,
        )

    # ------------------------------------------------------------------
    # The analysis run row.
    # ------------------------------------------------------------------

    def _new_run(
        self,
        ticket: Ticket,
        session: AIAnalysisSession,
        result: AgentAnalysisResult,
    ) -> AIAnalysisRun:
        run_number = (
            int(self.db.scalar(select(func.count(AIAnalysisRun.id)).where(AIAnalysisRun.ticket_id == ticket.id)) or 0) + 1
        )
        return AIAnalysisRun(
            ticket_id=ticket.id,
            run_number=run_number,
            final_category_id=result.category_id,
            text_category_id=result.text_category_id,
            image_category_id=result.image_category_id,
            ai_reason=result.ai_reason,
            red_flag=result.red_flag,
            red_flag_text=result.red_flag,
            severity=result.severity,
            severity_source=self._severity_source(result),
            status=AnalysisRunStatus.SUCCEEDED,
            completed_at=datetime.now(UTC),
            contract_version=ANALYSIS_CONTRACT_VERSION,
            analysis_session_id=session.id,
            exit_reason=result.exit_reason.value,
            duplicate_verdict=result.duplicate_verdict.value if result.duplicate_verdict else None,
            duplicate_reason=result.duplicate_reason,
            # The exact snapshot the Agent judged. This is what a coordinator
            # reviewing an uncertain duplicate opens the ticket to look at, and
            # the whitelist a DUPLICATE_EXISTING master has to appear in.
            duplicate_candidates=[item.model_dump(mode="json") for item in result.duplicate_candidates] or None,
            # Every exit handler overwrites this. NOT_ELIGIBLE is the safe
            # default: a row that somehow escaped its handler must not be
            # picked up by the background stage.
            grouping_status=GROUPING_NOT_ELIGIBLE,
            # Overwritten by the two handlers that open the gate.
            p3_review_status=P3ReviewStatus.NOT_REQUIRED.value,
            # Backend-owned canonical usage, never the Agent-declared numbers.
            tool_usage=self._backend_tool_usage(session).model_dump(),
            category_catalog_version=session.category_catalog_version,
            model_version=result.model_version,
            analyzed_at=result.analyzed_at,
        )

    @staticmethod
    def _severity_source(result: AgentAnalysisResult) -> SeveritySource | None:
        """Map the contract's IMAGE/TEXT onto the stored VISION/TEXT_FALLBACK."""
        if result.severity_source is None:
            return None
        return SeveritySource.VISION if result.severity_source.value == "IMAGE" else SeveritySource.TEXT_FALLBACK

    # ------------------------------------------------------------------
    # Exit handlers.
    # ------------------------------------------------------------------

    def _apply_red_flag(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResult,
        run: AIAnalysisRun,
    ) -> None:
        """Danger answered by speed, not by a score -- and then by a human.

        A red flag is a P3 by definition, so it goes through the same gate as
        any other P3. The ticket keeps its P3 priority and its five-minute SLA
        immediately; what waits is publication, duplicate work and grouping.
        """
        ticket.red_flag_detected = True
        ticket.severity = result.severity
        if result.category_id is not None:
            ticket.category_id = result.category_id
        ticket.priority = Priority.P3
        ticket.score_total = None
        # No rule version is pinned: there is no scoring decision to reproduce.
        CoordinatorScoringSupport(self.db, ScoringService()).recalculate_sla(ticket)
        self._open_p3_gate(ticket, run, Priority.P3)
        self._notify_unit(
            ticket,
            "TICKET_RED_FLAG",
            "Phản ánh được xử lý khẩn cấp",
            "Ban quản lý đã ghi nhận dấu hiệu nguy hiểm và đang xem xét ngay.",
        )

    def _apply_p3_review_required(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResult,
        run: AIAnalysisRun,
    ) -> None:
        """The classification scored P3 without a danger signal.

        Same gate, different reason. The score is written so a coordinator can
        see how the ticket got there, but the ticket is not published and no
        duplicate lookup ever ran -- the contract rejects a P3 payload that
        carries one.
        """
        category_id = result.category_id
        assert category_id is not None  # guaranteed by the payload validator
        snapshot = self._snapshot_by_id(session).get(str(category_id))
        if snapshot is None:
            raise DomainError(CATEGORY_REQUIRED, "Category not found in session snapshot.", 400)

        ticket.category_id = category_id
        ticket.severity = result.severity
        self._apply_scoring(ticket, snapshot, 1, run)
        self._open_p3_gate(ticket, run, ticket.priority or Priority.P3)
        # Every other exit tells the resident something. This one would
        # otherwise leave them watching a report that has visibly stopped.
        self._notify_unit(
            ticket,
            "TICKET_MANUAL_REVIEW",
            "Phản ánh đang được Ban quản lý xem xét gấp",
            "Phản ánh của bạn được đánh giá ở mức ưu tiên cao và đang chờ Ban quản lý duyệt ngay.",
        )

    def _open_p3_gate(self, ticket: Ticket, run: AIAnalysisRun, priority: Priority) -> None:
        """Park the ticket in front of a coordinator and stop the automation.

        Manual review rather than resolved: `RESOLVED` is what publishes a
        ticket into the operational queue, and the whole point of the gate is
        that an emergency priority is not published on the model's say-so.
        Grouping gets its own waiting state so `grouping_is_pending` -- the
        single gate on the background stage -- stays false throughout.
        """
        ticket.classification_status = ClassificationStatus.MANUAL_REVIEW
        ticket.version += 1
        run.p3_review_status = P3ReviewStatus.PENDING.value
        run.ai_priority_before_review = priority
        run.effective_priority = priority
        run.grouping_status = GROUPING_WAITING_P3_REVIEW
        self._notify_coordinators(
            ticket,
            "TICKET_P3_REVIEW_REQUIRED",
            "Cần duyệt phản ánh mức khẩn cấp",
            "AI xếp phản ánh này ở mức khẩn cấp P3. Vui lòng xác nhận hoặc hạ mức trước khi xử lý tiếp.",
            {
                "ai_priority": priority.value,
                "red_flag": bool(ticket.red_flag_detected),
                "ai_reason": run.ai_reason,
            },
        )

    def _apply_duplicate_existing(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResult,
        run: AIAnalysisRun,
    ) -> None:
        assert result.duplicate is not None  # guaranteed by the payload validator
        master = self._resolved_master(session, ticket, result.duplicate.master_ticket_id)

        if self._has_active_assignment(ticket):
            # Somebody is already on their way to this ticket; folding it into
            # another one now would strand that assignment.
            raise DomainError(
                ACTIVE_ASSIGNMENT_EXISTS,
                "Ticket already has an active assignment and cannot be linked as a duplicate.",
                409,
            )

        run.grouping_status = GROUPING_NOT_ELIGIBLE
        self._link_duplicate(ticket, master, result.duplicate.reason, run, session_id=session.id)

    def _apply_duplicate_uncertain(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResult,
        run: AIAnalysisRun,
    ) -> None:
        """Everything the Agent established is kept; the link is not made.

        The category, severity, both reasons, the candidate snapshot and the
        whole question/answer history stay on the run and the session, because
        that is exactly the evidence management opens the ticket to read before
        deciding. Grouping waits for that decision.

        It waits in its own state, not in PENDING. A ticket management may yet
        rule a duplicate must not be sitting in a queue that would first fold
        it into an incident case; grouping becomes pending only once
        `resolve_duplicate_uncertain` publishes it as an independent ticket.
        """
        ticket.severity = result.severity
        if result.category_id is not None:
            ticket.category_id = result.category_id
        ticket.classification_status = ClassificationStatus.MANUAL_REVIEW
        ticket.version += 1
        run.grouping_status = GROUPING_WAITING_DUPLICATE_DECISION
        self._notify_unit(
            ticket,
            "TICKET_MANUAL_REVIEW",
            "Phản ánh đang được Ban quản lý xem xét",
            "Ban quản lý sẽ kiểm tra lại phản ánh của bạn và cập nhật sớm.",
        )
        self._notify_coordinators(
            ticket,
            "TICKET_DUPLICATE_UNCERTAIN",
            "Cần xác nhận phản ánh có trùng hay không",
            "AI tìm thấy phản ánh tương tự nhưng chưa đủ chắc chắn. Vui lòng xem lại và xác nhận.",
            {
                "candidate_count": len(result.duplicate_candidates),
                "duplicate_reason": result.duplicate_reason,
            },
        )

    def _apply_manual_review(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResult,
        run: AIAnalysisRun,
    ) -> None:
        """LIMIT_REACHED: the conversation ran out of budget before a decision.

        Whatever the round did establish is kept, and nothing it did not is
        overwritten -- a null severity here means "never established", not
        "reset the one the ticket already had".
        """
        if result.severity is not None:
            ticket.severity = result.severity
        if result.category_id is not None:
            ticket.category_id = result.category_id
        ticket.classification_status = ClassificationStatus.MANUAL_REVIEW
        ticket.version += 1
        run.grouping_status = GROUPING_NOT_ELIGIBLE
        self._notify_unit(
            ticket,
            "TICKET_MANUAL_REVIEW",
            "Phản ánh đang được Ban quản lý xem xét",
            "Ban quản lý sẽ kiểm tra lại phản ánh của bạn và cập nhật sớm.",
        )

    def _apply_analysis_complete(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResult,
        run: AIAnalysisRun,
    ) -> None:
        """The normal outcome: one Category, one severity, scored and published.

        Grouping is not attempted here. The resident gets their answer now, and
        the background stage looks for a spreading case afterwards.
        """
        category_id = result.category_id
        assert category_id is not None  # guaranteed by the payload validator
        snapshot = self._snapshot_by_id(session).get(str(category_id))
        if snapshot is None:
            raise DomainError(CATEGORY_REQUIRED, "Category not found in session snapshot.", 400)

        ticket.category_id = category_id
        ticket.severity = result.severity
        # Density starts at one apartment. The background grouping stage
        # rescores nothing: it records the case, and Density is a property of
        # the case rather than a retroactive edit to this ticket's score.
        self._apply_scoring(ticket, snapshot, 1, run)
        self._apply_priority_override(ticket, run)
        ticket.classification_status = ClassificationStatus.RESOLVED
        ticket.version += 1
        run.grouping_status = (
            GROUPING_PENDING if str(snapshot.get("code")) in GROUPING_CODES else GROUPING_NOT_ELIGIBLE
        )
        self._notify_unit(
            ticket,
            "TICKET_CLASSIFIED",
            "Phản ánh của bạn đã được tiếp nhận",
            "Ban quản lý đã tiếp nhận và phân loại phản ánh của bạn.",
        )

    def _apply_insufficient_input(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResult,
        run: AIAnalysisRun,
    ) -> None:
        """Closed as invalid, and this one counts against the daily AI-rejection
        limit for the reporter."""
        self._invalidate_ticket(ticket, "Agent reported insufficient input.")
        ticket.invalid_reason = InvalidReason.CONTENT_INSUFFICIENT.value
        ticket.classification_status = ClassificationStatus.RESOLVED
        run.grouping_status = GROUPING_NOT_ELIGIBLE
        TicketService(self.db).register_ai_insufficient_input_rejection(ticket.reporter_user_id)

    # ------------------------------------------------------------------
    # Duplicate linking.
    # ------------------------------------------------------------------

    def _resolved_master(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        master_ticket_id: UUID,
    ) -> Ticket:
        """Resolve the master the Agent named, without re-judging the verdict.

        Three integrity checks and no more: the id came from the candidate
        snapshot this session actually saw, the row exists, and it is not the
        ticket itself or a cycle. Whether it is "still the same live incident"
        was settled when the snapshot was built.
        """
        if master_ticket_id == ticket.id:
            raise DomainError(CONTRACT_VALIDATION_ERROR, "A ticket cannot be its own duplicate master.", 400)

        candidate_ids = {str(row.get("ticket_id")) for row in self.duplicate_candidates_from_log(session)}
        if str(master_ticket_id) not in candidate_ids:
            raise DomainError(
                CONTRACT_VALIDATION_ERROR,
                "Duplicate master did not come from this session's candidate snapshot.",
                400,
            )

        master = self.db.scalar(select(Ticket).where(Ticket.id == master_ticket_id).with_for_update(of=Ticket))
        if master is None:
            raise DomainError(DUPLICATE_CANDIDATE_STALE, "Duplicate master no longer exists.", 409)
        if self._would_create_cycle(ticket.id, master):
            raise DomainError(CONTRACT_VALIDATION_ERROR, "Linking would create a duplicate cycle.", 400)
        return master

    def _link_duplicate(
        self,
        ticket: Ticket,
        master: Ticket,
        reason: str,
        run: AIAnalysisRun | None,
        *,
        session_id: UUID | None = None,
        actor_user_id: UUID | None = None,
    ) -> None:
        """Fold `ticket` into `master`. Shared by the Agent and management paths."""
        now = datetime.now(UTC)
        previous_status = ticket.status
        # A master that is itself a duplicate would build a chain; point at the
        # root instead so every duplicate is one hop from the canonical ticket.
        resolved_master_id = master.duplicate_of_ticket_id or master.id

        ticket.duplicate_of_ticket_id = resolved_master_id
        ticket.duplicate_linked_at = now
        ticket.duplicate_reason = reason
        ticket.status = TicketStatus.LINKED_DUPLICATE
        ticket.classification_status = ClassificationStatus.RESOLVED
        # A duplicate carries no Priority, score or SLA of its own and never
        # joins the active queue.
        ticket.priority = None
        ticket.score_total = None
        ticket.sla_due_at = None
        ticket.auto_assignment_paused = True
        ticket.auto_assignment_pause_reason = "Linked duplicate"
        ticket.version += 1

        if run is not None:
            run.duplicate = {"master_ticket_id": str(resolved_master_id), "reason": reason}
            # Written after the run is flushed because the column is a FK onto
            # the run this call is creating.
            self.db.add(run)
            self.db.flush()
            ticket.duplicate_analysis_run_id = run.id

        self.tickets.append_status_history(
            ticket,
            from_status=previous_status,
            to_status=TicketStatus.LINKED_DUPLICATE,
            changed_by=actor_user_id,
            reason=reason,
        )
        self._audit(
            actor_user_id,
            "TICKET_LINKED_AS_DUPLICATE",
            ticket.id,
            {
                "master_ticket_id": str(resolved_master_id),
                "analysis_run_id": str(run.id) if run is not None else None,
                "session_id": str(session_id) if session_id else None,
                "reason": reason,
            },
        )
        self._notify_duplicate_linked(ticket, master)

    def _would_create_cycle(self, ticket_id: UUID, master: Ticket) -> bool:
        current = master
        for _ in range(MAX_DUPLICATE_CHAIN_DEPTH):
            if current.id == ticket_id:
                return True
            if current.duplicate_of_ticket_id is None:
                return False
            nxt = self.db.get(Ticket, current.duplicate_of_ticket_id)
            if nxt is None:
                return False
            current = nxt
        return True

    def _has_active_assignment(self, ticket: Ticket) -> bool:
        return (
            self.db.scalar(
                select(TicketAssignment.id).where(
                    TicketAssignment.ticket_id == ticket.id,
                    TicketAssignment.is_active.is_(True),
                    TicketAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                )
            )
            is not None
        )

    # ------------------------------------------------------------------
    # Management decision at the P3 gate.
    # ------------------------------------------------------------------

    def p3_review_is_pending(self, ticket_id: UUID) -> bool:
        """Whether this ticket is currently held at the emergency gate.

        Read by every automatic step that must not run behind it, so a client
        calling an endpoint directly hits the same rule the pipeline does.
        One implementation, in `p3_review_guard`, shared with the coordinator
        services that ask the same question.
        """
        return p3_review_is_pending(self.db, ticket_id)

    def resolve_p3_review(
        self,
        actor_user_id: UUID,
        ticket_id: UUID,
        *,
        decision: P3Decision,
        priority: Priority | None = None,
        reason: str = "",
    ) -> Ticket:
        """Confirm the emergency, or downgrade it.

        Confirming publishes the ticket through the emergency route and stops
        there on purpose: correlating an emergency with other reports is not
        worth the minutes it costs, so neither duplicate work nor grouping
        resumes. Downgrading is the only way back into the pipeline, it demands
        a written reason, and it cannot land on P3 again -- confirming is the
        action for that.
        """
        try:
            ticket = self._locked_ticket(ticket_id)
            run = self._latest_successful_run(ticket_id)
            if run is None or run.p3_review_status != P3ReviewStatus.PENDING.value:
                raise DomainError(
                    INVALID_STATUS_TRANSITION,
                    "Phản ánh này không đang chờ duyệt mức khẩn cấp.",
                    409,
                )

            now = datetime.now(UTC)
            note = reason.strip()

            if decision is P3Decision.CONFIRM_P3:
                self._confirm_p3(ticket, run)
            else:
                if not note:
                    raise DomainError(
                        CONTRACT_VALIDATION_ERROR,
                        "Hạ mức khẩn cấp bắt buộc phải có lý do.",
                        400,
                    )
                if priority not in DOWNGRADE_PRIORITIES:
                    raise DomainError(
                        CONTRACT_VALIDATION_ERROR,
                        "Chỉ được hạ xuống P1 hoặc P2.",
                        400,
                    )
                self._downgrade_from_p3(ticket, run, priority)

            run.p3_reviewed_by = actor_user_id
            run.p3_reviewed_at = now
            run.p3_decision = decision.value
            run.p3_decision_reason = note or None

            self._audit(
                actor_user_id,
                "RESOLVE_P3_REVIEW",
                ticket.id,
                {
                    "decision": decision.value,
                    "ai_priority": run.ai_priority_before_review.value if run.ai_priority_before_review else None,
                    "effective_priority": run.effective_priority.value if run.effective_priority else None,
                    "reason": note or None,
                    "analysis_run_id": str(run.id),
                    "p3_review_status": run.p3_review_status,
                    "grouping_status": run.grouping_status,
                },
            )
            self.db.commit()
            self.db.refresh(ticket)
            return ticket
        except Exception:
            self.db.rollback()
            raise

    def _confirm_p3(self, ticket: Ticket, run: AIAnalysisRun) -> None:
        """Publish the emergency and leave the automation switched off."""
        ticket.priority = Priority.P3
        ticket.classification_status = ClassificationStatus.RESOLVED
        ticket.version += 1
        CoordinatorScoringSupport(self.db, ScoringService()).recalculate_sla(ticket)
        run.p3_review_status = P3ReviewStatus.CONFIRMED.value
        run.effective_priority = Priority.P3
        run.priority_final = Priority.P3
        # Deliberate, not an oversight: a confirmed emergency is never grouped
        # and its duplicates are never chased.
        run.grouping_status = GROUPING_NOT_ELIGIBLE
        self._notify_unit(
            ticket,
            "TICKET_RED_FLAG" if ticket.red_flag_detected else "TICKET_CLASSIFIED",
            "Phản ánh được xử lý khẩn cấp",
            "Ban quản lý đã xác nhận mức khẩn cấp và đang xử lý ngay.",
        )

    def _downgrade_from_p3(self, ticket: Ticket, run: AIAnalysisRun, priority: Priority) -> None:
        """Lower the priority and let the pipeline continue.

        The ticket is deliberately *not* published here. It goes back to the
        duplicate stage first, exactly where a non-P3 ticket would have been,
        and whatever that concludes is what publishes it.
        """
        ticket.priority = priority
        ticket.red_flag_detected = False
        ticket.version += 1
        CoordinatorScoringSupport(self.db, ScoringService()).recalculate_sla(ticket)
        run.p3_review_status = P3ReviewStatus.DOWNGRADED.value
        run.effective_priority = priority
        run.priority_final = priority
        # Still not PENDING: grouping waits for the duplicate stage that is
        # about to run, and that stage sets it if the ticket stays independent.
        run.grouping_status = GROUPING_NOT_ELIGIBLE

    def management_priority_override(self, ticket_id: UUID) -> Priority | None:
        """The priority a coordinator set at the P3 gate, if they set one.

        Read by the runs that come after a downgrade: the severity has not
        changed, so re-scoring the ticket would land on P3 again and undo the
        decision.
        """
        run = self.db.scalar(
            select(AIAnalysisRun)
            .where(
                AIAnalysisRun.ticket_id == ticket_id,
                AIAnalysisRun.p3_review_status == P3ReviewStatus.DOWNGRADED.value,
            )
            .order_by(AIAnalysisRun.run_number.desc())
            .limit(1)
        )
        return run.effective_priority if run is not None else None

    def _apply_priority_override(self, ticket: Ticket, run: AIAnalysisRun) -> None:
        """Re-apply a coordinator's downgrade over a fresh score."""
        override = self.management_priority_override(ticket.id)
        if override is None:
            run.effective_priority = ticket.priority
            return
        ticket.priority = override
        run.priority_final = override
        run.effective_priority = override
        run.ceiling_applied = True
        CoordinatorScoringSupport(self.db, ScoringService()).recalculate_sla(ticket)

    # ------------------------------------------------------------------
    # Management decision on an uncertain duplicate.
    # ------------------------------------------------------------------

    def resolve_duplicate_uncertain(
        self,
        actor_user_id: UUID,
        ticket_id: UUID,
        *,
        is_duplicate: bool,
        master_ticket_id: UUID | None = None,
        reason: str = "",
    ) -> Ticket:
        """Settle a `DUPLICATE_UNCERTAIN` ticket by hand.

        Confirming a duplicate links the ticket and ends it -- a duplicate is
        never also a spreading case, so grouping does not run. Confirming *not*
        duplicate turns it back into an ordinary ticket: it is scored from the
        Category and severity the Agent already established, published, and the
        caller is told to start background grouping.
        """
        try:
            ticket = self._locked_ticket(ticket_id)
            run = self._latest_successful_run(ticket_id)
            if run is None or run.exit_reason != AgentExitReason.DUPLICATE_UNCERTAIN.value:
                raise DomainError(
                    INVALID_STATUS_TRANSITION,
                    "Ticket has no uncertain duplicate result to resolve.",
                    409,
                )
            if ticket.classification_status is not ClassificationStatus.MANUAL_REVIEW:
                raise DomainError(INVALID_STATUS_TRANSITION, "Ticket is not in manual review.", 409)
            if run.grouping_status != GROUPING_WAITING_DUPLICATE_DECISION:
                # Already decided once. Deciding again would either re-link a
                # linked ticket or re-open one that has since been grouped.
                raise DomainError(
                    INVALID_STATUS_TRANSITION,
                    "Phản ánh này đã được xử lý trùng lặp trước đó.",
                    409,
                )

            note = reason.strip() or (
                "Ban quản lý xác nhận trùng phản ánh." if is_duplicate else "Ban quản lý xác nhận không trùng phản ánh."
            )

            if is_duplicate:
                # A confirmed duplicate is never grouped: it is not an
                # independent member of anything.
                master = self._master_from_candidates(ticket, run, master_ticket_id)
                if self._has_active_assignment(ticket):
                    raise DomainError(
                        ACTIVE_ASSIGNMENT_EXISTS,
                        "Ticket already has an active assignment and cannot be linked as a duplicate.",
                        409,
                    )
                run.duplicate_verdict = DuplicateVerdict.SAME_INCIDENT.value
                run.grouping_status = GROUPING_NOT_ELIGIBLE
                self._link_duplicate(ticket, master, note, None, actor_user_id=actor_user_id)
            else:
                # Ruled independent. Only now does the grouping stage become
                # pending, for the background task the caller queues after
                # this returns.
                run.duplicate_verdict = DuplicateVerdict.DIFFERENT_INCIDENT.value
                self._publish_after_not_duplicate(ticket, run, note)

            self._audit(
                actor_user_id,
                "RESOLVE_DUPLICATE_UNCERTAIN",
                ticket.id,
                {
                    "is_duplicate": is_duplicate,
                    "master_ticket_id": str(master_ticket_id) if master_ticket_id else None,
                    "reason": note,
                    "analysis_run_id": str(run.id),
                    "grouping_status": run.grouping_status,
                },
            )
            self.db.commit()
            self.db.refresh(ticket)
            return ticket
        except Exception:
            self.db.rollback()
            raise

    def _master_from_candidates(
        self,
        ticket: Ticket,
        run: AIAnalysisRun,
        master_ticket_id: UUID | None,
    ) -> Ticket:
        """The master must be one management was actually shown.

        With one candidate the choice is unambiguous and may be omitted; with
        several, management has to name which one.
        """
        candidates = [str(item.get("ticket_id")) for item in (run.duplicate_candidates or [])]
        if master_ticket_id is None:
            if len(candidates) != 1:
                raise DomainError(
                    CONTRACT_VALIDATION_ERROR,
                    "Chọn phản ánh gốc cần liên kết.",
                    400,
                )
            master_ticket_id = UUID(candidates[0])
        elif str(master_ticket_id) not in candidates:
            raise DomainError(
                CONTRACT_VALIDATION_ERROR,
                "Phản ánh gốc không nằm trong danh sách ứng viên đã trình bày.",
                400,
            )
        if master_ticket_id == ticket.id:
            raise DomainError(CONTRACT_VALIDATION_ERROR, "A ticket cannot be its own duplicate master.", 400)

        master = self.db.scalar(select(Ticket).where(Ticket.id == master_ticket_id).with_for_update(of=Ticket))
        if master is None:
            raise DomainError(DUPLICATE_CANDIDATE_STALE, "Duplicate master no longer exists.", 409)
        if self._would_create_cycle(ticket.id, master):
            raise DomainError(CONTRACT_VALIDATION_ERROR, "Linking would create a duplicate cycle.", 400)
        return master

    def _publish_after_not_duplicate(self, ticket: Ticket, run: AIAnalysisRun, note: str) -> None:
        """Score and publish a ticket management ruled independent."""
        category_id = run.final_category_id or ticket.category_id
        if category_id is None or ticket.severity is None:
            # Nothing to score from. It stays in manual review and a
            # coordinator sets the Category through the normal override.
            raise DomainError(
                CATEGORY_REQUIRED,
                "Phản ánh chưa có phân loại và mức độ để chấm điểm.",
                409,
            )
        session = self.db.get(AIAnalysisSession, run.analysis_session_id) if run.analysis_session_id else None
        snapshot = self._snapshot_by_id(session).get(str(category_id)) if session else None
        if snapshot is None:
            raise DomainError(CATEGORY_REQUIRED, "Category not found in session snapshot.", 400)

        ticket.category_id = category_id
        self._apply_scoring(ticket, snapshot, 1, run)
        self._apply_priority_override(ticket, run)
        ticket.classification_status = ClassificationStatus.RESOLVED
        ticket.version += 1
        run.grouping_status = (
            GROUPING_PENDING if str(snapshot.get("code")) in GROUPING_CODES else GROUPING_NOT_ELIGIBLE
        )
        self._notify_unit(
            ticket,
            "TICKET_CLASSIFIED",
            "Phản ánh của bạn đã được tiếp nhận",
            note,
        )

    def _latest_successful_run(self, ticket_id: UUID) -> AIAnalysisRun | None:
        return self.db.scalar(
            select(AIAnalysisRun)
            .where(AIAnalysisRun.ticket_id == ticket_id, AIAnalysisRun.status == AnalysisRunStatus.SUCCEEDED)
            .order_by(AIAnalysisRun.run_number.desc())
            .limit(1)
        )

    # ------------------------------------------------------------------
    # Background grouping.
    # ------------------------------------------------------------------

    def grouping_is_pending(self, ticket_id: UUID) -> bool:
        """Whether the background stage is authorised to run for this ticket.

        The single gate on grouping, and false for every state except PENDING.
        In particular it is false for a ticket held at the emergency gate,
        false for an uncertain duplicate awaiting a management decision, and
        false for a ticket that was linked, already grouped, or never eligible.
        """
        run = self._latest_successful_run(ticket_id)
        return run is not None and run.grouping_status == GROUPING_PENDING

    def record_grouping_outcome(self, ticket_id: UUID, status: str) -> None:
        """Close the grouping stage without a case (nothing matched, or blocked)."""
        run = self._latest_successful_run(ticket_id)
        if run is None:
            return
        run.grouping_status = status
        self.db.commit()

    def apply_grouping(
        self,
        session_id: UUID,
        ticket_id: UUID,
        proposal: AgentGroupingResult,
    ) -> IncidentCase | None:
        """Write the incident case for an accepted proposal.

        Runs in the background, in its own transaction, after the resident has
        already been notified. Grouped tickets stay independent tickets: nothing
        here changes their status, their assignment or their SLA -- they simply
        become members of one case.
        """
        try:
            session = self._session(session_id, lock=True)
            ticket = self._locked_ticket(ticket_id)
            run = self._latest_successful_run(ticket_id)
            if run is None:
                raise DomainError(TICKET_NOT_FOUND, "Ticket has no analysis run to group.", 404)

            category_id = run.final_category_id or ticket.category_id
            if category_id is None:
                raise DomainError(CATEGORY_REQUIRED, "Grouping requires a resolved Category.", 409)
            snapshot = self._snapshot_by_id(session).get(str(category_id))
            if snapshot is None or str(snapshot.get("code")) not in GROUPING_CODES:
                raise DomainError(
                    CONTRACT_VALIDATION_ERROR,
                    "Grouping is only valid for the spreading categories.",
                    400,
                )

            accepted = self._latest_accepted_proposal(session)
            if accepted is None:
                raise DomainError(
                    CONTRACT_VALIDATION_ERROR,
                    "Grouping requires an accepted propose_case_grouping call in this session.",
                    400,
                )
            expected = {str(item) for item in accepted.get("related_ticket_ids", [])}
            actual = {str(item) for item in proposal.related_ticket_ids}
            if expected != actual:
                raise DomainError(
                    CONTRACT_VALIDATION_ERROR,
                    "Grouping does not match the accepted backend proposal.",
                    400,
                )

            members = self._live_grouping_members(ticket, category_id, [UUID(item) for item in actual])
            if not members:
                run.grouping_status = GROUPING_NO_MATCH
                self.db.commit()
                return None

            case = self._persist_incident_grouping(ticket, category_id, members)
            run.grouping = {
                "grouped": True,
                "related_ticket_ids": sorted(str(item.id) for item in members),
                "category_id": str(category_id),
                "reason": proposal.reason,
                "density": case.density_value,
                "case_id": str(case.id),
            }
            run.grouping_status = GROUPING_GROUPED
            self._audit(
                None,
                "TICKET_GROUPED_INTO_CASE",
                ticket.id,
                {
                    "case_id": str(case.id),
                    "category_id": str(category_id),
                    "density": case.density_value,
                    "member_ticket_ids": sorted(str(item.id) for item in members),
                },
            )
            self.db.commit()
            self.db.refresh(case)
            return case
        except Exception:
            self.db.rollback()
            raise

    def _latest_accepted_proposal(self, session: AIAnalysisSession) -> dict[str, object] | None:
        call = self.db.scalar(
            select(AIAgentToolCall)
            .where(
                AIAgentToolCall.session_id == session.id,
                AIAgentToolCall.tool_name == "propose_case_grouping",
                AIAgentToolCall.success.is_(True),
            )
            .order_by(AIAgentToolCall.sequence.desc())
            .limit(1)
        )
        if call is None:
            return None
        response = call.sanitized_response or {}
        return response if response.get("accepted") else None

    def _live_grouping_members(self, ticket: Ticket, category_id: UUID, related_ids: list[UUID]) -> list[Ticket]:
        if not related_ids:
            return []
        rows = self.db.scalars(
            select(Ticket)
            .where(Ticket.id.in_(related_ids), Ticket.category_id == category_id)
            .options(joinedload(Ticket.location).joinedload(Location.floor), selectinload(Ticket.assignments))
            .with_for_update(of=Ticket)
        )
        # Same symmetric window the candidate search used: a neighbour who
        # reported the spreading problem after this ticket is still part of it.
        window = timedelta(days=GROUPING_LOOKBACK_DAYS)
        return [
            row
            for row in rows
            if self._can_join_case(row)
            and self._same_building_adjacent_floor(ticket, row)
            and abs(row.created_at - ticket.created_at) <= window
        ]

    def _can_join_case(self, ticket: Ticket) -> bool:
        if ticket.status in TERMINAL_TICKET_STATUSES:
            return False
        return not any(
            assignment.is_active and assignment.status in ACTIVE_ASSIGNMENT_STATUSES
            for assignment in ticket.assignments
        )

    def _persist_incident_grouping(
        self,
        ticket: Ticket,
        category_id: UUID,
        members: list[Ticket],
    ) -> IncidentCase:
        """At most five members per case; overflow opens the next case in the
        same series.

        The series row is locked before counting, so two tickets finalizing at
        once cannot both see four members and both commit a fifth and a sixth.
        """
        if ticket.location is None:
            raise DomainError(INVALID_STATUS_TRANSITION, "Grouping requires a valid ticket location.", 409)

        case = self._case_for(ticket, category_id)
        for member in [ticket, *members]:
            existing = self.db.scalar(
                select(IncidentCaseMember.ticket_id).where(IncidentCaseMember.ticket_id == member.id)
            )
            if existing is not None:
                continue
            case = self._case_with_room(case, ticket, category_id)
            self.db.add(
                IncidentCaseMember(
                    case_id=case.id,
                    ticket_id=member.id,
                    source_unit_id=member.source_unit_id,
                )
            )
            self.db.flush()

        self._recompute_density(case)
        return case

    def _case_for(self, ticket: Ticket, category_id: UUID) -> IncidentCase:
        """Reuse the open case that already holds one of this ticket's neighbours.

        Matching only on category and recency would also match every *other*
        open case for the same category in the building, whatever floor its
        members are on, so two unrelated leaks far apart could land in one case.
        A case is only reused when the new ticket is genuinely adjacent to at
        least one ticket already in it.
        """
        window_start = ticket.created_at - timedelta(days=GROUPING_LOOKBACK_DAYS)
        candidates = self.db.scalars(
            select(IncidentCase)
            .where(
                IncidentCase.category_id == category_id,
                IncidentCase.status == "OPEN",
                IncidentCase.window_end >= window_start,
            )
            .order_by(IncidentCase.sequence_no.desc())
            .with_for_update(of=IncidentCase)
        )
        for case in candidates:
            members = self.db.scalars(
                select(Ticket)
                .join(IncidentCaseMember, IncidentCaseMember.ticket_id == Ticket.id)
                .where(IncidentCaseMember.case_id == case.id)
                .options(joinedload(Ticket.location).joinedload(Location.floor))
            ).all()
            if any(self._same_building_adjacent_floor(ticket, member) for member in members):
                return case

        case = IncidentCase(
            category_id=category_id,
            window_start=window_start,
            window_end=ticket.created_at + timedelta(seconds=1),
            density_value=1,
            sequence_no=1,
        )
        # `series_id` defaults to the case id so a one-case series is still
        # addressable through the same column.
        self.db.add(case)
        self.db.flush()
        case.series_id = case.id
        self.db.flush()
        return case

    def _case_with_room(self, case: IncidentCase, ticket: Ticket, category_id: UUID) -> IncidentCase:
        count = int(
            self.db.scalar(
                select(func.count()).select_from(IncidentCaseMember).where(IncidentCaseMember.case_id == case.id)
            )
            or 0
        )
        if count < MAX_CASE_MEMBERS:
            return case
        successor = IncidentCase(
            category_id=category_id,
            status="OPEN",
            window_start=case.window_start,
            window_end=ticket.created_at + timedelta(seconds=1),
            density_value=1,
            series_id=case.series_id,
            sequence_no=case.sequence_no + 1,
        )
        self.db.add(successor)
        self.db.flush()
        return successor

    def _recompute_density(self, case: IncidentCase) -> None:
        """`COUNT(DISTINCT source_unit_id)` -- apartments, not tickets."""
        density = int(
            self.db.scalar(
                select(func.count(func.distinct(IncidentCaseMember.source_unit_id))).where(
                    IncidentCaseMember.case_id == case.id
                )
            )
            or 1
        )
        case.density_value = max(1, density)

    # ------------------------------------------------------------------
    # Scoring.
    # ------------------------------------------------------------------

    def _apply_scoring(
        self,
        ticket: Ticket,
        snapshot: dict[str, object],
        density: int,
        run: AIAnalysisRun,
    ) -> None:
        """Pin exactly one scoring rule version and store it on the run.

        The pinned id is what makes the run reproducible: a later edit to the
        active rule set must not change what this analysis decided.
        """
        if ticket.severity is None:
            raise DomainError(CATEGORY_REQUIRED, "Agent did not provide Severity for scoring.", 409)

        rule_version = self.db.scalar(
            select(ScoringRuleVersion).where(ScoringRuleVersion.is_active.is_(True)).with_for_update(read=True)
        )
        scoring = ScoringService(rule_version.config if rule_version else None)
        ceiling = snapshot.get("priority_ceiling")
        outcome = scoring.calculate_dynamic(
            category_code=str(snapshot["code"]),
            base_score=int(snapshot["base_score"]),
            severity=ticket.severity,
            location_type_code=(
                ticket.location.location_type.code if ticket.location and ticket.location.location_type else None
            ),
            density_count=density,
            red_flag_detected=ticket.red_flag_detected,
            priority_ceiling=Priority(ceiling) if ceiling in {"P1", "P2", "P3"} else None,
        )

        ticket.score_total = outcome.score_total
        ticket.priority = outcome.priority_final
        CoordinatorScoringSupport(self.db, scoring).recalculate_sla(ticket)

        run.rule_version_id = rule_version.id if rule_version else None
        run.score_components = dict(outcome.components)
        run.score_total = outcome.score_total
        run.priority_raw = outcome.priority_raw
        run.priority_final = outcome.priority_final
        run.ceiling_applied = outcome.ceiling_applied

    # ------------------------------------------------------------------
    # Notifications -- reduced facts only, never another reporter's identity.
    # ------------------------------------------------------------------

    def _notify_duplicate_linked(self, ticket: Ticket, master: Ticket) -> None:
        self._notify_unit(
            ticket,
            "TICKET_LINKED_AS_DUPLICATE",
            "Phản ánh đã được gộp với một phản ánh đang xử lý",
            (
                "Ban quản lý xác định phản ánh này trùng với một phản ánh khác tại cùng vị trí "
                "và sẽ cập nhật theo tiến độ của phản ánh gốc."
            ),
            {
                "master_reference_code": reference_code(master.id),
                "master_status": master.status.value,
                "master_category": master.category.display_name if master.category else None,
                "master_due_at": master.sla_due_at.isoformat() if master.sla_due_at else None,
            },
        )


__all__ = [
    "ANALYSIS_ALREADY_FINALIZED",
    "CONTRACT_VALIDATION_ERROR",
    "DUPLICATE_CANDIDATE_STALE",
    "GROUPING_BLOCKED",
    "GROUPING_CODES",
    "GROUPING_GROUPED",
    "GROUPING_NOT_ELIGIBLE",
    "GROUPING_NO_MATCH",
    "GROUPING_PENDING",
    "GROUPING_WAITING_DUPLICATE_DECISION",
    "GROUPING_WAITING_P3_REVIEW",
    "AgentResultService",
]
