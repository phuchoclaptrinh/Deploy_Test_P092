"""Backend `finalize_v4()` — the only place a v4 analysis result is applied.

The Agent produces a decision. This module is what makes it true, and it is
written on the assumption that the Agent may be wrong, stale or replayed:

* **Every claim is re-derived.** Declared tool usage is compared with the
  session counters, category ids with the pinned catalog snapshot, the duplicate
  master and grouping members with this session's own tool-call log, and Density
  with `COUNT(DISTINCT source_unit_id)`. Nothing the Agent asserts is written
  because it asserted it.
* **One transaction, with row locks.** The session, the ticket and — for a
  duplicate — the master are locked before anything is read for a decision, so
  a coordinator acting at the same moment either loses the race cleanly or wins
  it cleanly. There is no partial write: §3.1 says a stale master must produce
  `409 DUPLICATE_CANDIDATE_STALE` and leave the ticket untouched.
* **Finalizing is idempotent.** A session finalizes successfully once (§1.7.9).
  Replaying the same payload returns the stored run; a different payload under
  the same session is `409 ANALYSIS_ALREADY_FINALIZED`. A partial unique index
  backs this up if two callers get past the lock.
* **The session always ends.** Whatever branch is taken, the session leaves
  RUNNING before this returns. A successful Agent result that left a session
  RUNNING is the failure mode this whole module exists to prevent.

Red flag wins. §1.7.1-§1.7.2: a red flag on any source forces the emergency
branch, may not close the new ticket as a duplicate, and may not be delayed by
grouping. `red_flag_relation` links evidence to the master and nothing more.
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
from src.database.models.ticket_relation import TicketRelation
from src.models.agent_schemas_v4 import (
    AGENT_MODEL_VERSION_V4,
    ANALYSIS_CONTRACT_VERSION_V4,
    AgentAnalysisResultV4,
    AgentExitReasonV4,
    AgentSearchPurpose,
    AgentToolUsageV4,
)
from src.models.api.errors import (
    ACTIVE_ASSIGNMENT_EXISTS,
    CATEGORY_REQUIRED,
    INVALID_STATUS_TRANSITION,
    DomainError,
)
from src.models.enums import (
    AnalysisRunStatus,
    AssignmentStatus,
    ClassificationStatus,
    InvalidReason,
    Priority,
    SeveritySource,
    TicketRelationType,
    TicketStatus,
)
from src.services.agent_common import GROUPING_CODES, AgentServiceBase
from src.services.coordinator_support import CoordinatorScoringSupport
from src.services.scoring_service import ScoringService
from src.services.ticket_service import TicketService

logger = logging.getLogger(__name__)

ANALYSIS_ALREADY_FINALIZED = "ANALYSIS_ALREADY_FINALIZED"
DUPLICATE_CANDIDATE_STALE = "DUPLICATE_CANDIDATE_STALE"
CONTRACT_VALIDATION_ERROR = "CONTRACT_VALIDATION_ERROR"

# §1.5 item 4: a master is live in one of these. Everything else — completed,
# cancelled, invalid, unresolvable, or itself a duplicate — is not.
MASTER_ACTIVE_STATUSES = {
    TicketStatus.NEW,
    TicketStatus.WAITING_RESIDENT_INFO,
    TicketStatus.APPROVED,
    TicketStatus.IN_PROGRESS,
}

ACTIVE_ASSIGNMENT_STATUSES = {
    AssignmentStatus.ASSIGNED,
    AssignmentStatus.ACCEPTED,
    AssignmentStatus.IN_PROGRESS,
}

# `analyzed_at` far from server time means a replayed or fabricated payload.
MAX_ANALYZED_AT_SKEW = timedelta(hours=1)

MAX_DUPLICATE_CHAIN_DEPTH = 10


class AgentResultV4Service(AgentServiceBase):
    """Applies one `AgentAnalysisResultV4` to the database."""

    # ------------------------------------------------------------------
    # Entry point.
    # ------------------------------------------------------------------

    def finalize_v4(
        self,
        result: AgentAnalysisResultV4,
        *,
        idempotency_key: str | None = None,
    ) -> AIAnalysisRun:
        payload_hash = self._payload_hash(result)

        try:
            session = self._session(result.analysis_session_id, lock=True)
            existing = self._replayed_run(session, payload_hash, idempotency_key)
            if existing is not None:
                # Same decision arriving twice. Return what was written the
                # first time rather than writing it again.
                self.db.rollback()
                return existing

            self._require_running_v4_session(session)
            ticket = self._locked_ticket(result.ticket_id)
            self._validate_result_v4(session, ticket, result)

            backend_usage = self._backend_tool_usage_v4(session)
            candidates = self._duplicate_candidates_from_log(session)
            run = self._new_run(ticket, session, result, backend_usage, candidates)

            handler = {
                AgentExitReasonV4.RED_FLAG: self._apply_red_flag,
                AgentExitReasonV4.DUPLICATE_EXISTING: self._apply_duplicate_existing,
                AgentExitReasonV4.DUPLICATE_UNCERTAIN: self._apply_manual_review,
                AgentExitReasonV4.LIMIT_REACHED: self._apply_manual_review,
                AgentExitReasonV4.ANALYSIS_COMPLETE: self._apply_analysis_complete,
                AgentExitReasonV4.INSUFFICIENT_INPUT: self._apply_insufficient_input,
            }[result.exit_reason]
            handler(session, ticket, result, run)

            run.idempotency_key = idempotency_key
            run.payload_hash = payload_hash
            self._close_session(session, result)
            self.db.add(run)
            self._audit(
                None,
                "FINALIZE_AGENT_RESULT_V4",
                ticket.id,
                {
                    "session_id": str(session.id),
                    "contract_version": ANALYSIS_CONTRACT_VERSION_V4,
                    "exit_reason": result.exit_reason.value,
                    "model_version": result.model_version,
                    "rule_version_id": str(run.rule_version_id) if run.rule_version_id else None,
                    "classification_status": ticket.classification_status.value,
                    "ticket_status": ticket.status.value,
                },
            )
            self.db.commit()
            self.db.refresh(run)
        except DomainError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise

        # §3.3 item 1: the red-flag evidence link is its own transaction, so a
        # failure to attach it can never hold up or roll back the P3 ticket.
        if result.red_flag_relation is not None:
            self._link_red_flag_evidence(result, run.id)
        return run

    # ------------------------------------------------------------------
    # Idempotency and session state.
    # ------------------------------------------------------------------

    @staticmethod
    def _payload_hash(result: AgentAnalysisResultV4) -> str:
        payload = json.loads(result.model_dump_json())
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _replayed_run(
        self,
        session: AIAnalysisSession,
        payload_hash: str,
        idempotency_key: str | None,
    ) -> AIAnalysisRun | None:
        """§1.7.9. Returns the stored run for a true replay, raises for a
        different payload, and returns None when this session has never
        finalized."""
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
    def _require_running_v4_session(session: AIAnalysisSession) -> None:
        """§1.1: only a RUNNING session finalizes, and only on its own contract.

        A v3 session reaching here would be finalized against v4 rules with a v4
        payload it never produced, so it is refused rather than converted.
        """
        if session.status != "RUNNING":
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Analysis session is not running.",
                409,
            )
        if (session.model_version or "") != AGENT_MODEL_VERSION_V4:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Analysis session did not start on the v4 contract.",
                409,
            )

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

    def _close_session(self, session: AIAnalysisSession, result: AgentAnalysisResultV4) -> None:
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
    # §1.7 invariants Backend re-checks.
    # ------------------------------------------------------------------

    def _validate_result_v4(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResultV4,
    ) -> None:
        self._validate_session_ticket(session, result.ticket_id)

        if session.category_catalog_version != result.category_catalog_version:
            raise DomainError(CATEGORY_REQUIRED, "Category catalog version mismatch.", 409)

        # §1.7.8.
        snapshot_ids = set(self._snapshot_by_id(session))
        supplied = {str(item) for item in (result.text_categories or [])}
        supplied |= {str(item) for item in (result.image_categories or [])}
        if not supplied <= snapshot_ids:
            raise DomainError(CATEGORY_REQUIRED, "Agent returned Category outside session snapshot.", 400)

        # §1.7.6: the image group is coherent with what the ticket actually has.
        has_images = bool(self.attachments.list_issue_original(ticket.id))
        if has_images and result.exit_reason is not AgentExitReasonV4.INSUFFICIENT_INPUT:
            if result.image_categories is None:
                raise DomainError(
                    CONTRACT_VALIDATION_ERROR,
                    "Ticket has images but the result reports no image analysis.",
                    400,
                )
        elif not has_images and result.image_categories is not None:
            raise DomainError(
                CONTRACT_VALIDATION_ERROR,
                "Result reports image analysis for a ticket without images.",
                400,
            )

        # §1.6: the declared counters are compared, never trusted.
        backend_usage = self._backend_tool_usage_v4(session)
        if result.tool_usage.model_dump() != backend_usage.model_dump():
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Agent tool usage does not match backend records.",
                409,
            )

        # §1.7.5: the budget must genuinely be spent.
        if result.exit_reason is AgentExitReasonV4.LIMIT_REACHED:
            spent = (
                backend_usage.total_tool_calls >= 5
                or backend_usage.ask_resident_rounds >= 3
                or backend_usage.ask_resident_elapsed_seconds >= 300
            )
            if not spent:
                raise DomainError(
                    CONTRACT_VALIDATION_ERROR,
                    "LIMIT_REACHED without an exhausted tool, ask or wait budget.",
                    400,
                )

        # §1.7.4: an uncertain duplicate must actually have searched for one.
        if result.exit_reason is AgentExitReasonV4.DUPLICATE_UNCERTAIN and not self._searched(
            session, AgentSearchPurpose.DUPLICATE
        ):
            raise DomainError(
                CONTRACT_VALIDATION_ERROR,
                "DUPLICATE_UNCERTAIN requires a search_related_tickets(purpose=DUPLICATE) call.",
                400,
            )

        # §1.3: a payload analyzed hours ago is a replay, not a decision.
        analyzed_at = result.analyzed_at
        skew = abs(datetime.now(UTC) - analyzed_at)
        if skew > MAX_ANALYZED_AT_SKEW:
            raise DomainError(
                CONTRACT_VALIDATION_ERROR,
                "analyzed_at is too far from server time.",
                400,
            )

    def _backend_tool_usage_v4(self, session: AIAnalysisSession) -> AgentToolUsageV4:
        tool_names = set(
            self.db.scalars(select(AIAgentToolCall.tool_name).where(AIAgentToolCall.session_id == session.id))
        )
        return AgentToolUsageV4(
            total_tool_calls=session.total_tool_calls,
            ask_resident_rounds=session.ask_resident_rounds,
            ask_resident_elapsed_seconds=session.ask_resident_elapsed_seconds,
            search_related_tickets_called="search_related_tickets" in tool_names,
            propose_case_grouping_called="propose_case_grouping" in tool_names,
        )

    def _search_calls(self, session: AIAnalysisSession, purpose: AgentSearchPurpose) -> list[AIAgentToolCall]:
        return [
            call
            for call in self.db.scalars(
                select(AIAgentToolCall)
                .where(
                    AIAgentToolCall.session_id == session.id,
                    AIAgentToolCall.tool_name == "search_related_tickets",
                )
                .order_by(AIAgentToolCall.sequence)
            )
            if (call.sanitized_request or {}).get("purpose") == purpose.value
        ]

    def _searched(self, session: AIAnalysisSession, purpose: AgentSearchPurpose) -> bool:
        return bool(self._search_calls(session, purpose))

    def _duplicate_candidates_from_log(self, session: AIAnalysisSession) -> list[dict[str, object]]:
        """The sanitized candidate set this session saw, newest call last.

        §1.7.4 and §3.3 both need it: it is the evidence a coordinator reviews
        for DUPLICATE_UNCERTAIN or LIMIT_REACHED, and the whitelist a
        DUPLICATE_EXISTING master must appear in.
        """
        seen: dict[str, dict[str, object]] = {}
        for call in self._search_calls(session, AgentSearchPurpose.DUPLICATE):
            for row in (call.sanitized_response or {}).get("related_tickets", []):
                seen[str(row.get("ticket_id"))] = row
        return list(seen.values())

    # ------------------------------------------------------------------
    # The analysis run row.
    # ------------------------------------------------------------------

    def _new_run(
        self,
        ticket: Ticket,
        session: AIAnalysisSession,
        result: AgentAnalysisResultV4,
        tool_usage: AgentToolUsageV4,
        candidates: list[dict[str, object]],
    ) -> AIAnalysisRun:
        run_number = (
            int(self.db.scalar(select(func.count(AIAnalysisRun.id)).where(AIAnalysisRun.ticket_id == ticket.id)) or 0) + 1
        )
        return AIAnalysisRun(
            ticket_id=ticket.id,
            run_number=run_number,
            text_categories=[str(item) for item in (result.text_categories or [])],
            image_categories=(
                [str(item) for item in result.image_categories] if result.image_categories is not None else None
            ),
            red_flag_text=result.red_flag_text,
            red_flag_signal=bool(result.red_flag_signal),
            severity=result.severity,
            severity_source=self._severity_source(result),
            status=AnalysisRunStatus.SUCCEEDED,
            completed_at=datetime.now(UTC),
            contract_version=ANALYSIS_CONTRACT_VERSION_V4,
            analysis_session_id=session.id,
            exit_reason=result.exit_reason.value,
            is_relevant=result.is_relevant,
            is_confident=result.is_confident,
            confidence_notes=result.confidence_notes,
            # Backend-owned canonical usage, never the Agent-declared numbers.
            tool_usage=tool_usage.model_dump(),
            duplicate_candidates=candidates or None,
            category_catalog_version=session.category_catalog_version,
            model_version=result.model_version,
            analyzed_at=result.analyzed_at,
        )

    @staticmethod
    def _severity_source(result: AgentAnalysisResultV4) -> SeveritySource | None:
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
        result: AgentAnalysisResultV4,
        run: AIAnalysisRun,
    ) -> None:
        """§3.3: the existing emergency/P3 path, and nothing duplicate about it."""
        ticket.red_flag_detected = True
        ticket.severity = result.severity
        ticket.priority = Priority.P3
        ticket.score_total = None
        ticket.classification_status = ClassificationStatus.RESOLVED
        ticket.version += 1
        # A red flag is answered by speed, not by a score, so no rule version is
        # pinned here: there is no scoring decision to reproduce later.
        CoordinatorScoringSupport(self.db, ScoringService()).recalculate_sla(ticket)
        if result.red_flag_relation is not None:
            run.red_flag_relation = {
                "master_ticket_id": str(result.red_flag_relation.master_ticket_id),
                "reason": result.red_flag_relation.reason,
            }
        self._notify_unit(
            ticket,
            "TICKET_RED_FLAG",
            "Phản ánh được xử lý khẩn cấp",
            "Ban quản lý đã ghi nhận dấu hiệu nguy hiểm và ưu tiên xử lý ngay.",
        )

    def _apply_duplicate_existing(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResultV4,
        run: AIAnalysisRun,
    ) -> None:
        """§3.1, inside the same transaction as everything else."""
        assert result.duplicate is not None  # guaranteed by the payload validator
        master = self._validated_master(session, ticket, result.duplicate.master_ticket_id)

        if self._has_active_assignment(ticket):
            # Somebody is already on their way to this ticket; folding it into
            # another one now would strand that assignment.
            raise DomainError(
                ACTIVE_ASSIGNMENT_EXISTS,
                "Ticket already has an active assignment and cannot be linked as a duplicate.",
                409,
            )

        now = datetime.now(UTC)
        previous_status = ticket.status
        ticket.duplicate_of_ticket_id = master.id
        ticket.duplicate_linked_at = now
        ticket.duplicate_reason = result.duplicate.reason
        ticket.status = TicketStatus.LINKED_DUPLICATE
        ticket.classification_status = ClassificationStatus.RESOLVED
        # §3.1: a duplicate carries no Priority, score or SLA of its own, and
        # never joins the active queue.
        ticket.priority = None
        ticket.score_total = None
        ticket.sla_due_at = None
        ticket.severity = result.severity
        ticket.auto_assignment_paused = True
        ticket.auto_assignment_pause_reason = "Linked duplicate"
        ticket.version += 1

        run.duplicate = {
            "master_ticket_id": str(master.id),
            "reason": result.duplicate.reason,
        }
        # Written after the run is flushed, below, because the column is a FK
        # onto the run this call is creating.
        self.db.add(run)
        self.db.flush()
        ticket.duplicate_analysis_run_id = run.id

        self.tickets.append_status_history(
            ticket,
            from_status=previous_status,
            to_status=TicketStatus.LINKED_DUPLICATE,
            changed_by=None,
            reason=result.duplicate.reason,
        )
        self._audit(
            None,
            "TICKET_LINKED_AS_DUPLICATE",
            ticket.id,
            {
                "master_ticket_id": str(master.id),
                "analysis_run_id": str(run.id),
                "session_id": str(session.id),
                "reason": result.duplicate.reason,
            },
        )
        self._notify_duplicate_linked(ticket, master)

    def _apply_manual_review(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResultV4,
        run: AIAnalysisRun,
    ) -> None:
        """§3.3 for DUPLICATE_UNCERTAIN and LIMIT_REACHED.

        The candidate evidence is already on the run (`duplicate_candidates`),
        which is what a coordinator opens the ticket to look at.
        """
        ticket.severity = result.severity
        ticket.classification_status = ClassificationStatus.MANUAL_REVIEW
        ticket.version += 1
        self._notify_coordinator_review(ticket, result.exit_reason)

    def _apply_analysis_complete(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResultV4,
        run: AIAnalysisRun,
    ) -> None:
        """§3.3: Backend, not the Agent, decides whether the two Category
        sources agree.

        One unambiguous answer — a single text Category with no images, or a
        single Category in the intersection — is scored and prioritized. Anything
        else (no overlap, or several survivors) goes to manual review; the Agent
        is never asked to adjudicate it, which is why `CATEGORY_MISMATCH` no
        longer exists.
        """
        ticket.severity = result.severity
        category_id = self._reconcile_category_v4(session, result)
        if category_id is None:
            ticket.classification_status = ClassificationStatus.MANUAL_REVIEW
            ticket.version += 1
            self._notify_coordinator_review(ticket, result.exit_reason)
            return

        snapshot = self._snapshot_by_id(session).get(str(category_id))
        if snapshot is None:
            raise DomainError(CATEGORY_REQUIRED, "Category not found in session snapshot.", 400)

        grouping = self._validated_grouping_v4(session, ticket, result, category_id)
        ticket.category_id = category_id
        density = int(grouping["density"]) if grouping else 1
        self._apply_scoring_v4(ticket, snapshot, density, run)
        ticket.classification_status = ClassificationStatus.RESOLVED
        ticket.version += 1

        if grouping is not None:
            run.grouping = grouping
            self._persist_incident_grouping_v4(ticket, category_id, grouping)

    def _apply_insufficient_input(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResultV4,
        run: AIAnalysisRun,
    ) -> None:
        """§3.3 / §8.2: closed as invalid, and this one does count against the
        3-AI-rejections-per-day limit."""
        self._invalidate_ticket(ticket, "Agent reported insufficient input.")
        ticket.invalid_reason = InvalidReason.CONTENT_INSUFFICIENT.value
        ticket.classification_status = ClassificationStatus.RESOLVED
        TicketService(self.db).register_ai_insufficient_input_rejection(ticket.reporter_user_id)

    # ------------------------------------------------------------------
    # Duplicate validation (§1.5, §3.1).
    # ------------------------------------------------------------------

    def _validated_master(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        master_ticket_id: UUID,
    ) -> Ticket:
        if master_ticket_id == ticket.id:
            raise DomainError(CONTRACT_VALIDATION_ERROR, "A ticket cannot be its own duplicate master.", 400)

        # §1.5 item 3: only a candidate this session actually saw.
        candidate_ids = {str(row.get("ticket_id")) for row in self._duplicate_candidates_from_log(session)}
        if str(master_ticket_id) not in candidate_ids:
            raise DomainError(
                CONTRACT_VALIDATION_ERROR,
                "Duplicate master did not come from this session's DUPLICATE search.",
                400,
            )

        master = self.db.scalar(
            select(Ticket).where(Ticket.id == master_ticket_id).with_for_update(of=Ticket)
        )
        if master is None:
            raise DomainError(DUPLICATE_CANDIDATE_STALE, "Duplicate master no longer exists.", 409)

        # §1.5 item 7: the master must be the end of the chain, not a duplicate.
        if master.duplicate_of_ticket_id is not None:
            raise DomainError(
                DUPLICATE_CANDIDATE_STALE,
                "Duplicate master is itself linked to another ticket.",
                409,
            )
        # Cheap cycle guard for the other direction.
        if self._would_create_cycle(ticket.id, master):
            raise DomainError(CONTRACT_VALIDATION_ERROR, "Linking would create a duplicate cycle.", 400)

        if master.status not in MASTER_ACTIVE_STATUSES:
            raise DomainError(DUPLICATE_CANDIDATE_STALE, "Duplicate master is no longer active.", 409)

        # §1.5 item 5 / §11 assumption 2: the same shared asset, by id.
        if ticket.location_id is None or master.location_id != ticket.location_id:
            raise DomainError(
                DUPLICATE_CANDIDATE_STALE,
                "Duplicate master is no longer on the same asset/location.",
                409,
            )
        return master

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
    # Grouping (§1.4, §7.9) — Backend owns Density.
    # ------------------------------------------------------------------

    def _validated_grouping_v4(
        self,
        session: AIAnalysisSession,
        ticket: Ticket,
        result: AgentAnalysisResultV4,
        category_id: UUID,
    ) -> dict[str, object] | None:
        if result.grouping is None:
            return None

        accepted = [
            call
            for call in self.db.scalars(
                select(AIAgentToolCall)
                .where(
                    AIAgentToolCall.session_id == session.id,
                    AIAgentToolCall.tool_name == "propose_case_grouping",
                    AIAgentToolCall.success.is_(True),
                )
                .order_by(AIAgentToolCall.sequence.desc())
            )
        ]
        if not accepted:
            raise DomainError(
                CONTRACT_VALIDATION_ERROR,
                "Grouping result requires an accepted propose_case_grouping call in this session.",
                400,
            )

        # §1.4: only water leak and electrical short spread as one case. The
        # tool already refuses anything else, but a Category can be re-resolved
        # between the proposal and finalize, so it is checked again here.
        snapshot = self._snapshot_by_id(session).get(str(category_id))
        if snapshot is None or str(snapshot.get("code")) not in GROUPING_CODES:
            raise DomainError(
                CONTRACT_VALIDATION_ERROR,
                "Grouping is only valid for spreading water-leak and electrical-short categories.",
                400,
            )

        latest = accepted[0].sanitized_response or {}
        expected = {str(item) for item in latest.get("related_ticket_ids", [])}
        actual = {str(item) for item in result.grouping.related_ticket_ids}
        if not latest.get("accepted") or expected != actual:
            raise DomainError(
                CONTRACT_VALIDATION_ERROR,
                "Agent grouping does not match the accepted backend grouping proposal.",
                400,
            )
        if latest.get("category_id") and str(latest["category_id"]) != str(category_id):
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Grouping category does not match the final resolved Category.",
                409,
            )

        related = self._live_grouping_members(ticket, category_id, [UUID(item) for item in actual])
        if len(related) != len(actual):
            raise DomainError(INVALID_STATUS_TRANSITION, "Grouping tickets are no longer valid.", 409)

        # §1.4: Density is Backend's number, recomputed from distinct units at
        # commit time. The Agent does not send one and the tool's earlier value
        # is not reused.
        density = self._density(ticket, related)
        return {
            "grouped": True,
            "related_ticket_ids": sorted(actual),
            "category_id": str(category_id),
            "reason": result.grouping.reason,
            "density": density,
        }

    def _live_grouping_members(self, ticket: Ticket, category_id: UUID, related_ids: list[UUID]) -> list[Ticket]:
        if not related_ids:
            return []
        rows = self.db.scalars(
            select(Ticket)
            .where(Ticket.id.in_(related_ids), Ticket.category_id == category_id)
            .options(joinedload(Ticket.location).joinedload(Location.floor), selectinload(Ticket.assignments))
            .with_for_update(of=Ticket)
        )
        return [
            row
            for row in rows
            if self._can_join_case_v4(row)
            and self._same_building_adjacent_floor(ticket, row)
            and row.created_at >= ticket.created_at - timedelta(days=3)
        ]

    def _can_join_case_v4(self, ticket: Ticket) -> bool:
        if ticket.status in {
            TicketStatus.COMPLETED,
            TicketStatus.CANCELLED,
            TicketStatus.INVALID,
            TicketStatus.UNRESOLVABLE,
            TicketStatus.LINKED_DUPLICATE,
        }:
            return False
        return not any(
            assignment.is_active and assignment.status in ACTIVE_ASSIGNMENT_STATUSES
            for assignment in ticket.assignments
        )

    def _persist_incident_grouping_v4(
        self,
        ticket: Ticket,
        category_id: UUID,
        grouping: dict[str, object],
    ) -> None:
        """§7.9: at most five members per case, overflow opens the next case in
        the same series.

        The series row is locked before counting so two finalizations of two
        tickets cannot both see four members and both commit a fifth and sixth.
        """
        if ticket.location is None:
            raise DomainError(INVALID_STATUS_TRANSITION, "Grouping requires a valid ticket location.", 409)

        related_ids = [UUID(str(item)) for item in grouping["related_ticket_ids"]]
        members = self._live_grouping_members(ticket, category_id, related_ids)
        wanted = [ticket, *members]

        case = self._case_for(ticket, category_id)
        for member in wanted:
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

    def _case_for(self, ticket: Ticket, category_id: UUID) -> IncidentCase:
        """Reuse the open case that already holds one of this ticket's neighbours."""
        window_start = ticket.created_at - timedelta(days=3)
        case = self.db.scalar(
            select(IncidentCase)
            .where(
                IncidentCase.category_id == category_id,
                IncidentCase.building_id == ticket.location.building_id,
                IncidentCase.status == "OPEN",
                IncidentCase.window_end >= window_start,
            )
            .order_by(IncidentCase.sequence_no.desc())
            .with_for_update(of=IncidentCase)
        )
        if case is not None:
            return case
        case = IncidentCase(
            category_id=category_id,
            building_id=ticket.location.building_id,
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
        """§7.9: never commit a sixth member; open the next case in the series."""
        count = int(
            self.db.scalar(select(func.count()).select_from(IncidentCaseMember).where(IncidentCaseMember.case_id == case.id))
            or 0
        )
        if count < 5:
            return case
        successor = IncidentCase(
            category_id=category_id,
            building_id=ticket.location.building_id,
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
        """§7.9: `COUNT(DISTINCT source_unit_id)` — apartments, not tickets."""
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
    # Category reconciliation and scoring (§3.3, §7.10).
    # ------------------------------------------------------------------

    def _reconcile_category_v4(self, session: AIAnalysisSession, result: AgentAnalysisResultV4) -> UUID | None:
        valid = set(self._snapshot_by_id(session))
        text = {str(item) for item in (result.text_categories or [])} & valid
        if result.image_categories is None:
            return UUID(next(iter(text))) if len(text) == 1 else None
        image = {str(item) for item in result.image_categories} & valid
        matched = text & image
        return UUID(next(iter(matched))) if len(matched) == 1 else None

    def _apply_scoring_v4(
        self,
        ticket: Ticket,
        snapshot: dict[str, object],
        density: int,
        run: AIAnalysisRun,
    ) -> None:
        """§7.10: pin exactly one scoring rule version and store it on the run.

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
        run.category_match = ticket.category_id is not None

    # ------------------------------------------------------------------
    # Red-flag evidence relation (§3.3, §7.7) — its own transaction.
    # ------------------------------------------------------------------

    def _link_red_flag_evidence(self, result: AgentAnalysisResultV4, analysis_run_id: UUID) -> None:
        """§3.3 item 1-4.

        Runs after the P3 ticket is already committed, so a master that just
        closed, or a unique-constraint clash on a replay, downgrades to a log
        line instead of losing the resident's report.
        """
        relation = result.red_flag_relation
        if relation is None or relation.master_ticket_id == result.ticket_id:
            return
        try:
            master = self.db.scalar(select(Ticket).where(Ticket.id == relation.master_ticket_id).with_for_update())
            if master is None or master.status not in MASTER_ACTIVE_STATUSES:
                logger.warning(
                    "Red-flag evidence not linked: master %s is missing or closed. Ticket %s continues on P3.",
                    relation.master_ticket_id,
                    result.ticket_id,
                )
                self.db.rollback()
                return

            already = self.db.scalar(
                select(TicketRelation).where(
                    TicketRelation.source_ticket_id == result.ticket_id,
                    TicketRelation.target_ticket_id == master.id,
                    TicketRelation.relation_type == TicketRelationType.RED_FLAG_EVIDENCE.value,
                )
            )
            if already is not None:
                self.db.rollback()
                return

            self.db.add(
                TicketRelation(
                    source_ticket_id=result.ticket_id,
                    target_ticket_id=master.id,
                    relation_type=TicketRelationType.RED_FLAG_EVIDENCE.value,
                    analysis_run_id=analysis_run_id,
                    reason=relation.reason,
                )
            )
            # §3.3 item 3: the master is pushed up for urgent re-review, never
            # down. Its own Priority is only raised, and only to P3.
            if master.priority != Priority.P3:
                master.priority = Priority.P3
                CoordinatorScoringSupport(self.db, ScoringService()).recalculate_sla(master)
            master.classification_status = ClassificationStatus.MANUAL_REVIEW
            master.version += 1
            self._audit(
                None,
                "RED_FLAG_EVIDENCE_LINKED",
                master.id,
                {
                    "source_ticket_id": str(result.ticket_id),
                    "analysis_run_id": str(analysis_run_id),
                    "reason": relation.reason,
                },
            )
            self._notify_unit(
                master,
                "TICKET_RED_FLAG_REVIEW",
                "Phản ánh của bạn được đánh giá lại khẩn cấp",
                "Ban quản lý nhận thêm thông tin cho thấy sự cố có dấu hiệu nguy hiểm và đang xem xét lại mức ưu tiên.",
            )
            self.db.commit()
        except Exception:
            # The new ticket is already committed on the P3 path; a failure here
            # must not take it with it.
            logger.exception("Failed to link red-flag evidence for ticket %s.", result.ticket_id)
            self.db.rollback()

    # ------------------------------------------------------------------
    # Notifications (§8.1) — reduced data only.
    # ------------------------------------------------------------------

    def _notify_duplicate_linked(self, ticket: Ticket, master: Ticket) -> None:
        """§3.1: a reference code, Category, status and due time. No identity."""
        self._notify_unit(
            ticket,
            "TICKET_LINKED_AS_DUPLICATE",
            "Phản ánh đã được gộp với một phản ánh đang xử lý",
            (
                "Ban quản lý xác định phản ánh này trùng với một phản ánh khác tại cùng vị trí "
                "và sẽ cập nhật theo tiến độ của phản ánh gốc."
            ),
            {
                "master_reference_code": _reference_code(master.id),
                "master_status": master.status.value,
                "master_category": master.category.display_name if master.category else None,
                "master_due_at": master.sla_due_at.isoformat() if master.sla_due_at else None,
            },
        )

    def _notify_coordinator_review(self, ticket: Ticket, exit_reason: AgentExitReasonV4) -> None:
        self._notify_unit(
            ticket,
            "TICKET_MANUAL_REVIEW",
            "Phản ánh đang được Ban quản lý xem xét",
            "Ban quản lý sẽ kiểm tra lại phản ánh của bạn và cập nhật sớm.",
        )
        logger.info("Ticket %s moved to MANUAL_REVIEW after %s.", ticket.id, exit_reason.value)


def _reference_code(ticket_id: UUID) -> str:
    return f"PA-{str(ticket_id).replace('-', '')[:6].upper()}"


__all__ = [
    "ANALYSIS_ALREADY_FINALIZED",
    "CONTRACT_VALIDATION_ERROR",
    "DUPLICATE_CANDIDATE_STALE",
    "AgentResultV4Service",
    "GROUPING_CODES",
]
