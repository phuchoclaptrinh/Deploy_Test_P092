"""The coordinator proposal batch (§4.3a, §4.6, §5.2, §7.5, §7.6).

PROPOSAL is what happens when a coordinator turns auto-assignment on while a
queue already exists: instead of silently assigning everything, Backend builds
one preview table a human confirms.

The lifecycle is deliberately explicit, because the global switch hangs off it:

    BUILDING -> READY -> CONFIRMED | EXPIRED | CANCELLED

`BUILDING` exists so the switch can stay OFF while the model is working.
Opening a batch, cancelling it, letting it expire, or confirming one that turned
out to hand nothing out all leave `enabled=false`. **Only** a confirm that
actually assigns work turns DIRECT on, and it does so as a consequence rather
than on request: there is no flag a caller can set to ask for it (§4.6 items
6-8).

Three more things the contract is specific about and this module implements
literally:

* **One decision round for the whole batch** (§4.3a), not one per ticket, so
  the engine can balance projected load across the rows instead of assigning
  every ticket to whoever was least busy at the start.
* **A per-row failure is a row, not a batch failure** (§5.2 items 1, 5, 7). No
  candidates, a broken decision, or `NO_SUITABLE_CANDIDATE` all become `EMPTY`
  with a reason, and never pause the ticket or block the other rows.
* **PROPOSAL does not lock tickets** (§5.1). A coordinator may keep assigning by
  hand while the table is open; those rows become `SKIPPED_MANUAL_WON` at
  confirm time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.orm import Session, joinedload, selectinload

from src.assignment_agent.config import AssignmentConfigurationError
from src.assignment_agent.schemas import (
    AssignmentDecisionType,
    AssignmentProposalBatchRequestV4,
    CandidateSnapshotV4,
    ProposalWorkItemRequestV4,
    WorkItemType,
    WorkItemV4,
)
from src.assignment_rules.config import AssignmentRuleConfigError
from src.assignment_rules.engine import UNDATED
from src.assignment_rules.service import priority_rank
from src.config import get_settings
from src.database.models.assignment_proposal import (
    AIAssignmentJob,
    AssignmentProposalBatch,
    AssignmentProposalItem,
    AssignmentProposalItemMember,
)
from src.database.models.assignment_schedule import AssignmentProposalSchedule
from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.technician import TechnicianProfile
from src.database.models.ticket import Ticket
from src.database.models.user_profile import UserProfile
from src.models.api.errors import (
    CONFLICT_VERSION,
    INVALID_STATUS_TRANSITION,
    PROPOSAL_EXPIRED,
    PROPOSAL_NOT_READY,
    PROPOSAL_NOTHING_TO_ASSIGN,
    TECHNICIAN_NOT_ELIGIBLE,
    TECHNICIAN_NOT_FOUND,
    TICKET_NOT_FOUND,
    DomainError,
)
from src.models.display import ticket_display_code
from src.models.enums import (
    ActivationDelay,
    AssignmentJobMode,
    AssignmentSource,
    Priority,
    ProposalBatchCreatedBy,
    ProposalBatchStatus,
    ProposalItemStatus,
)
from src.repositories.assignment_repository import AssignmentRepository
from src.services.assignment_candidates import AssignmentCandidateService, WorkItemDraft, as_utc
from src.services.assignment_decision_engine import AssignmentDecisionEngine, build_decision_engine
from src.services.assignment_job_service import AssignmentJobService
from src.services.assignment_service import set_acceptance_deadlines
from src.services.assignment_support import AssignmentSideEffects
from src.services.assignment_trigger_service import parse_activation_delay

logger = logging.getLogger(__name__)

NO_CANDIDATES_REASON = "Không còn kỹ thuật viên phù hợp cho hạng mục này."
MODEL_FAILED_REASON = "Mô hình không trả được quyết định hợp lệ cho dòng này."
# 4.3: the coordinator may have picked someone who has since been deactivated.
# The row is dropped with a reason a human can act on; the rest still confirm.
INACTIVE_TECHNICIAN_REASON = "Kỹ thuật viên đã ngừng hoạt động trước khi bảng được duyệt."


@dataclass
class ProposalRoundReport:
    batches_built: int = 0
    items_proposed: int = 0
    items_empty: int = 0
    batches_expired: int = 0
    errors: list[str] = field(default_factory=list)


class AssignmentProposalService:
    def __init__(self, db: Session, engine: AssignmentDecisionEngine | None = None) -> None:
        self.db = db
        self.settings = get_settings()
        self.candidates = AssignmentCandidateService(db)
        self.jobs = AssignmentJobService(db)
        self.assignments = AssignmentRepository(db)
        self.side_effects = AssignmentSideEffects(db)
        self._engine = engine

    @property
    def engine(self) -> AssignmentDecisionEngine:
        if self._engine is None:
            # Same rule as DIRECT: the factory reads the switch, and on `AI` it
            # is still strict about failover in production.
            self._engine = build_decision_engine(self.settings)
        return self._engine

    # ------------------------------------------------------------------
    # §4.6 item 1: open a batch. The switch stays OFF.
    # ------------------------------------------------------------------

    def create_batch(
        self,
        actor_user_id: UUID | None,
        limit: int | None = None,
        *,
        created_by_type: str = ProposalBatchCreatedBy.COORDINATOR.value,
        skip_if_empty: bool = False,
        build_immediately: bool = False,
    ) -> AssignmentProposalBatch | None:
        """Open a batch. The switch stays OFF whoever opened it.

        `created_by_type` is recorded rather than inferred from
        `actor_user_id`: the recurring schedule opens batches with no
        coordinator behind them, and §8.1 keeps SYSTEM and a named actor apart.

        `skip_if_empty` is what the scheduler passes. A coordinator who presses
        the button and gets nothing still needs the batch, because confirming an
        empty one is how they turn the V4 switch on (§4.2) -- but a schedule
        firing at 3am against an empty queue would otherwise leave a table
        nobody asked for on the screen every interval. It returns `None`
        instead, and the caller advances the next run.
        """
        limit = min(limit or self.settings.direct_request_max_ticket_count, 20)
        try:
            now = datetime.now(UTC)
            drafts = self._collect_drafts(limit)
            if skip_if_empty and not drafts:
                self.db.rollback()
                return None
            batch = AssignmentProposalBatch(
                requested_by_user_id=actor_user_id,
                created_by_type=created_by_type,
                status=ProposalBatchStatus.BUILDING.value,
                # Explicitly undecided. A `false` here would answer a question
                # the coordinator has not been asked yet (§4.6 item 6).
                continue_auto_assignment=None,
                activation_delay=None,
                created_at=now,
            )
            self.db.add(batch)
            self.db.flush()

            for draft in drafts:
                item = AssignmentProposalItem(
                    batch_id=batch.id,
                    decision_id=draft.decision_id,
                    status=ProposalItemStatus.PENDING.value,
                    work_item_type=draft.work_item_type,
                    work_item_id=draft.work_item_id,
                    ticket_id=draft.work_item_id if draft.work_item_type == "TICKET" else None,
                    incident_case_id=draft.work_item_id if draft.work_item_type == "INCIDENT_CASE" else None,
                )
                self.db.add(item)
                self.db.flush()
                for ticket_id in draft.ticket_ids:
                    # §7.5: batch_id on the member is what makes
                    # UNIQUE (batch_id, ticket_id) enforceable.
                    self.db.add(
                        AssignmentProposalItemMember(item_id=item.id, batch_id=batch.id, ticket_id=ticket_id)
                    )

            job: AIAssignmentJob | None = None
            if drafts:
                job = self.jobs.schedule_proposal(proposal_batch_id=batch.id)
                batch.batch_decision_id = job.batch_decision_id
            else:
                # 4.2: nothing is eligible right now. Calling the model with an
                # empty request would be a round trip that can only answer
                # "nothing", so the batch goes straight to READY on the same
                # ten-minute window. It still has to be confirmed, because
                # confirming is how the coordinator turns the switch on for the
                # tickets that arrive next -- the switch stays off until then.
                batch.status = ProposalBatchStatus.READY.value
                batch.ready_at = now
                batch.expires_at = now + timedelta(seconds=self.settings.proposal_ttl_seconds)
                batch.updated_at = now

            self.side_effects.audit(
                actor_user_id,
                "CREATE_ASSIGNMENT_PROPOSAL_BATCH",
                "ASSIGNMENT_PROPOSAL_BATCH",
                batch.id,
                None,
                {
                    "status": batch.status,
                    "item_count": len(drafts),
                    "ticket_count": sum(draft.ticket_count for draft in drafts),
                    # The switch is untouched by opening a batch.
                    "auto_assignment_enabled": bool(self._settings_row().enabled),
                },
                None,
                created_by_type,
            )
            # RULE is deterministic and local.  Keeping it in BUILDING until a
            # polling worker notices the row makes the coordinator wait for the
            # worker cadence even though no model call is involved.  The API
            # opts into this path only for RULE; AI retains the durable worker
            # queue and its existing timeout/failover behaviour.
            if build_immediately and job is not None:
                self.jobs.claim_job(job, now=now)
                self._build_batch(job, ProposalRoundReport())
            else:
                self.db.commit()
            return self.get_batch(batch.id)
        except Exception:
            self.db.rollback()
            raise

    def _collect_drafts(self, limit: int) -> list[WorkItemDraft]:
        """§4.3a: at most 20 distinct tickets, ordered by Priority then age.

        A case that would overflow the remaining room is deferred whole rather
        than split across two batches (§4.3a).
        """
        priority_order = case(
            (Ticket.priority == Priority.P3, 0),
            (Ticket.priority == Priority.P2, 1),
            (Ticket.priority == Priority.P1, 2),
            else_=3,
        )
        tickets = list(
            self.db.scalars(
                self.candidates.eligible_ticket_query(include_paused=True)
                .order_by(priority_order, Ticket.created_at.asc())
                .limit(limit * 3)
            )
        )

        drafts: list[WorkItemDraft] = []
        used = 0
        seen_cases: set[UUID] = set()
        seen_tickets: set[UUID] = set()
        for ticket in tickets:
            if ticket.id in seen_tickets:
                continue
            case_row = self._open_case_for(ticket)
            if case_row is not None:
                if case_row.id in seen_cases:
                    continue
                seen_cases.add(case_row.id)
                draft = self.candidates.case_draft(
                    case_row, max_members=self.settings.incident_case_max_ticket_count, include_paused=True
                )
                if draft is None:
                    continue
            else:
                draft = self.candidates.ticket_draft(ticket)

            if used + draft.ticket_count > limit:
                # Defer the whole work item; do not slice a case.
                continue
            drafts.append(draft)
            used += draft.ticket_count
            seen_tickets.update(draft.ticket_ids)
            if used >= limit:
                break
        return drafts

    def _open_case_for(self, ticket: Ticket) -> IncidentCase | None:
        case_id = self.db.scalar(select(IncidentCaseMember.case_id).where(IncidentCaseMember.ticket_id == ticket.id))
        if case_id is None:
            return None
        row = self.db.get(IncidentCase, case_id)
        return row if row is not None and row.status == "OPEN" else None

    # ------------------------------------------------------------------
    # §4.6 items 2-3: the worker fills the batch and makes it READY.
    # ------------------------------------------------------------------

    def run_due_batches(self, *, now: datetime | None = None, limit: int | None = None) -> ProposalRoundReport:
        now = now or datetime.now(UTC)
        report = ProposalRoundReport()
        report.batches_expired = self.expire_due_batches(now=now)

        jobs = self.jobs.claim_due_jobs(mode=AssignmentJobMode.PROPOSAL, now=now, limit=limit)
        self.db.commit()
        for job in jobs:
            try:
                self._build_batch(job, report)
            except (AssignmentConfigurationError, AssignmentRuleConfigError):
                # Not a bad batch — a bad deployment. Failing every row to EMPTY
                # would hand the coordinator a table to fill in by hand and hide
                # the reason it is empty.
                raise
            except Exception as exc:  # noqa: BLE001 - one bad batch must not stop the worker
                logger.exception("Proposal batch job %s failed.", job.id)
                report.errors.append(f"{type(exc).__name__}: {exc}")
                self.db.rollback()
                self._fail_batch(job, exc)
        return report

    def _build_batch(self, job: AIAssignmentJob, report: ProposalRoundReport) -> None:
        batch = self.db.get(AssignmentProposalBatch, job.proposal_batch_id)
        if batch is None or batch.status != ProposalBatchStatus.BUILDING.value:
            self.jobs.mark_completed(job, selected_technician_id=None, reason="Batch no longer building.", completed_model=None)
            self.db.commit()
            return

        items = list(
            self.db.scalars(
                select(AssignmentProposalItem)
                .where(AssignmentProposalItem.batch_id == batch.id)
                .options(selectinload(AssignmentProposalItem.members))
            )
        )
        drafts: dict[UUID, WorkItemDraft] = {}
        request_items: list[ProposalWorkItemRequestV4] = []
        now = datetime.now(UTC)

        for item in items:
            draft = self._draft_for_item(item)
            if draft is None or not draft.has_candidates:
                # §5.2 item 1: an EMPTY row, not a pause and not a blocked batch.
                self._mark_empty(item, NO_CANDIDATES_REASON, now)
                report.items_empty += 1
                continue
            drafts[item.id] = draft
            request_items.append(
                ProposalWorkItemRequestV4(
                    decision_id=item.decision_id,
                    work_item=WorkItemV4(
                        work_item_type=WorkItemType(draft.work_item_type),
                        work_item_id=draft.work_item_id,
                        ticket_ids=draft.ticket_ids,
                        category_id=draft.category_id,
                        priority=draft.priority,
                        location_labels=draft.location_labels,
                        issue_summary=draft.issue_summary,
                        required_skills=draft.required_skills,
                        current_due_at=draft.current_due_at,
                        created_at=draft.created_at,
                    ),
                    excluded_technician_ids=draft.excluded_technician_ids,
                    candidates=[CandidateSnapshotV4(**candidate) for candidate in draft.candidates],
                )
            )

        job.candidate_snapshot = [
            {"decision_id": str(item.decision_id), "candidates": drafts[item.id].candidates}
            for item in items
            if item.id in drafts
        ]
        job.primary_model = self.engine.engine_version
        job.fallback_model = self.engine.fallback_version

        if request_items:
            # §4.3a: Backend sorts by Priority descending, then submission time
            # ascending, before the engine sees the batch. `_collect_drafts`
            # already picked the rows in that order, but the item rows come back
            # from an unordered select, so the order is re-established here — it
            # decides who gets the least loaded technician.
            request_items.sort(
                key=lambda item: (
                    priority_rank(item.work_item.priority),
                    as_utc(item.work_item.created_at) or UNDATED,
                    str(item.work_item.work_item_id),
                )
            )
            # One request for the whole batch, so the engine can spread
            # projected load across rows instead of re-reading one stale count.
            request = AssignmentProposalBatchRequestV4(
                batch_decision_id=job.batch_decision_id,
                proposal_batch_id=batch.id,
                work_items=request_items,
                requested_at=now,
            )
            outcome = self.engine.decide_proposal(request)
            job.raw_model_output = {
                "batch_decision_id": str(outcome.result.batch_decision_id),
                "fallback_used": outcome.fallback_used,
                "decisions": [decision.model_dump(mode="json") for decision in outcome.result.decisions],
            }
            by_decision = {decision.decision_id: decision for decision in outcome.result.decisions}
            for item in items:
                if item.id not in drafts:
                    continue
                decision = by_decision.get(item.decision_id)
                if decision is None or decision.decision is AssignmentDecisionType.NO_SUITABLE_CANDIDATE:
                    # §5.2 items 5 and 7 land in the same visible place: a row a
                    # coordinator can fill in by hand.
                    reason = decision.reason if decision else MODEL_FAILED_REASON
                    self._mark_empty(item, reason, now, completed_model=decision.model_version if decision else None)
                    report.items_empty += 1
                    continue
                item.status = ProposalItemStatus.PROPOSED.value
                item.proposed_technician_id = decision.selected_technician_id
                item.final_technician_id = decision.selected_technician_id
                item.reason = decision.reason
                item.completed_model = decision.model_version
                item.decided_at = decision.decided_at
                item.decision_job_id = job.id
                item.updated_at = now
                report.items_proposed += 1

        batch.status = ProposalBatchStatus.READY.value
        batch.ready_at = now
        # §4.6 item 3: ten minutes, and the check constraint refuses a READY
        # batch without one.
        batch.expires_at = now + timedelta(seconds=self.settings.proposal_ttl_seconds)
        batch.updated_at = now
        self.jobs.mark_completed(
            job,
            selected_technician_id=None,
            reason=f"{report.items_proposed} dòng có đề xuất, {report.items_empty} dòng trống.",
            completed_model=job.primary_model,
        )
        report.batches_built += 1
        self.db.commit()

    def _draft_for_item(self, item: AssignmentProposalItem) -> WorkItemDraft | None:
        if item.work_item_type == "TICKET":
            ticket = self.db.get(Ticket, item.ticket_id)
            if ticket is None or not self.candidates.is_ticket_eligible(ticket, include_paused=True):
                return None
            draft = self.candidates.ticket_draft(ticket)
        else:
            case_row = self.db.get(IncidentCase, item.incident_case_id)
            if case_row is None:
                return None
            draft = self.candidates.case_draft(
                case_row, max_members=self.settings.incident_case_max_ticket_count, include_paused=True
            )
            if draft is None:
                return None
        draft.decision_id = item.decision_id
        return draft

    def _mark_empty(
        self,
        item: AssignmentProposalItem,
        reason: str,
        now: datetime,
        *,
        completed_model: str | None = None,
    ) -> None:
        item.status = ProposalItemStatus.EMPTY.value
        item.proposed_technician_id = None
        item.final_technician_id = None
        item.reason = reason[:500]
        item.completed_model = completed_model
        item.updated_at = now

    def _fail_batch(self, job: AIAssignmentJob, exc: Exception) -> None:
        """A batch-wide technical failure still leaves a usable table: every row
        is EMPTY with a sanitized reason, and the switch is untouched."""
        batch = self.db.get(AssignmentProposalBatch, job.proposal_batch_id)
        now = datetime.now(UTC)
        if batch is not None and batch.status == ProposalBatchStatus.BUILDING.value:
            for item in batch.items:
                if item.status == ProposalItemStatus.PENDING.value:
                    self._mark_empty(item, MODEL_FAILED_REASON, now)
            batch.status = ProposalBatchStatus.READY.value
            batch.ready_at = now
            batch.expires_at = now + timedelta(seconds=self.settings.proposal_ttl_seconds)
            batch.updated_at = now
        self.jobs.mark_failed(job, error_code="MODEL_FAILED", error_detail=type(exc).__name__)
        self.db.commit()

    # ------------------------------------------------------------------
    # §4.6 item 4: the coordinator edits the table.
    # ------------------------------------------------------------------

    def update_item(
        self,
        actor_user_id: UUID,
        batch_id: UUID,
        item_id: UUID,
        *,
        selected: bool | None = None,
        technician_id: UUID | None = None,
    ) -> AssignmentProposalBatch:
        try:
            batch = self._locked_batch(batch_id)
            self._require_ready(batch)
            item = next((row for row in batch.items if row.id == item_id), None)
            if item is None:
                raise DomainError(TICKET_NOT_FOUND, "Dòng đề xuất không tồn tại.", 404)

            if technician_id is not None:
                # Decision 3: the coordinator may move a row to any *active*
                # technician, including one the AI never saw. That widens who
                # may be chosen, not who the AI considered -- the candidate
                # snapshot and `proposed_technician_id` are left untouched so
                # the audit still separates the model's suggestion from this.
                self._require_active_technician(technician_id)

            before = {"status": item.status, "final_technician_id": str(item.final_technician_id or "")}
            if technician_id is not None:
                item.final_technician_id = technician_id
                if item.status in {ProposalItemStatus.EMPTY.value, ProposalItemStatus.DESELECTED.value}:
                    item.status = ProposalItemStatus.PROPOSED.value
            if selected is False:
                item.status = ProposalItemStatus.DESELECTED.value
            elif selected is True and item.final_technician_id is not None:
                item.status = ProposalItemStatus.PROPOSED.value
            item.updated_at = datetime.now(UTC)
            batch.version += 1
            self.side_effects.audit(
                actor_user_id,
                "EDIT_ASSIGNMENT_PROPOSAL_ITEM",
                "ASSIGNMENT_PROPOSAL_ITEM",
                item.id,
                before,
                {"status": item.status, "final_technician_id": str(item.final_technician_id or "")},
                None,
                "COORDINATOR",
            )
            self.db.commit()
            return self.get_batch(batch_id)
        except Exception:
            self.db.rollback()
            raise

    # ------------------------------------------------------------------
    # §4.6 items 5-6: confirm.
    # ------------------------------------------------------------------

    def confirm_batch(
        self,
        actor_user_id: UUID,
        batch_id: UUID,
        *,
        activation_delay: str = ActivationDelay.IMMEDIATE.value,
        expected_version: int | None = None,
    ) -> AssignmentProposalBatch:
        """Assign the placed rows, and -- if this is the first one -- start DIRECT.

        There is no flag asking for activation, deliberately. DIRECT turning on
        is a *consequence* of a named coordinator confirming real work, not a
        separate thing they can request: a request could be forged, replayed, or
        sent with no proposal behind it at all, which is exactly what this rule
        exists to prevent. The decision is made here instead, from state the
        caller cannot influence -- a READY, unexpired batch that actually
        produced assignments, confirmed by the authenticated coordinator.

        `activation_delay` is not an activation lever. It says how long a ticket
        waits *once* DIRECT is on, and it is applied only in the moment the
        switch flips; a confirm while DIRECT is already running leaves the
        configured delay alone.
        """
        delay = parse_activation_delay(activation_delay)
        try:
            batch = self._locked_batch(batch_id)
            self._require_ready(batch)
            now = datetime.now(UTC)

            if batch.expires_at is not None and _aware(batch.expires_at) <= now:
                # §4.6 item 8: an expired snapshot is never revived.
                batch.status = ProposalBatchStatus.EXPIRED.value
                batch.updated_at = now
                self.db.commit()
                raise DomainError(PROPOSAL_EXPIRED, "Bảng đề xuất đã hết hạn, vui lòng tạo đợt mới.", 409)

            if expected_version is not None and expected_version != batch.version:
                raise DomainError(CONFLICT_VERSION, "Bảng đề xuất đã thay đổi, vui lòng tải lại.", 409)

            live_rows = [
                item
                for item in batch.items
                if item.status == ProposalItemStatus.PROPOSED.value and item.final_technician_id is not None
            ]
            if not live_rows:
                # An empty batch used to be confirmable as a way of turning
                # DIRECT on. It is not any more: an empty table is no evidence
                # that a coordinator reviewed any work, so confirming one would
                # authorise autonomous assignment on the strength of nothing.
                raise DomainError(
                    PROPOSAL_NOTHING_TO_ASSIGN,
                    "Chua co ticket nao duoc dat vao ky thuat vien. "
                    "Hay keo it nhat mot ticket vao mot ky thuat vien, hoac huy de xuat.",
                    409,
                )

            assigned = 0
            skipped = 0
            stale_technicians = 0
            for item in batch.items:
                if item.status != ProposalItemStatus.PROPOSED.value or item.final_technician_id is None:
                    continue
                if not self._technician_is_active(item.final_technician_id):
                    # 4.3: the technician was deactivated after the batch was
                    # built or edited. No assignment for this row, a reason a
                    # coordinator can act on, and every other row still goes
                    # through. `proposed_technician_id` is kept so the audit
                    # still shows what the model had suggested.
                    item.status = ProposalItemStatus.EMPTY.value
                    item.final_technician_id = None
                    item.reason = INACTIVE_TECHNICIAN_REASON
                    item.updated_at = now
                    stale_technicians += 1
                    self.side_effects.audit(
                        actor_user_id,
                        "SKIP_ASSIGNMENT_PROPOSAL_ITEM_INACTIVE_TECHNICIAN",
                        "ASSIGNMENT_PROPOSAL_ITEM",
                        item.id,
                        {"status": ProposalItemStatus.PROPOSED.value},
                        {
                            "status": item.status,
                            "proposed_technician_id": str(item.proposed_technician_id or ""),
                            "reason": INACTIVE_TECHNICIAN_REASON,
                        },
                        None,
                        "COORDINATOR",
                    )
                    continue
                ticket_ids = sorted((member.ticket_id for member in item.members), key=str)
                tickets = self._locked_tickets(ticket_ids)
                assignable = [ticket for ticket in tickets if self.candidates.is_ticket_eligible(ticket, include_paused=True)]
                if not assignable:
                    # §4.6 item 5: a coordinator already assigned it by hand.
                    item.status = ProposalItemStatus.SKIPPED_MANUAL_WON.value
                    item.updated_at = now
                    skipped += 1
                    continue

                member_count = len(assignable)
                factor = self._sla_factor(member_count, assignable[0].priority)
                for ticket in assignable:
                    # A confirmed proposal is a human decision on this ticket,
                    # the same as the reject/timeout flow in AssignmentService --
                    # the pause that let it into this batch (AUTO_ASSIGNMENT_DISABLED
                    # or NO_CANDIDATES) is resolved now, and must not block the
                    # next DIRECT round if this assignment is later rejected.
                    if ticket.auto_assignment_paused:
                        ticket.auto_assignment_paused = False
                        ticket.auto_assignment_pause_reason = None
                        ticket.version += 1
                    assignment = self.assignments.create_assignment(
                        ticket_id=ticket.id,
                        technician_id=item.final_technician_id,
                        # §4.5: a confirmed proposal is the coordinator's act.
                        assigned_by_user_id=actor_user_id,
                        assignment_source=AssignmentSource.AI_PROPOSAL_CONFIRMED.value,
                        assignment_job_id=item.decision_job_id,
                    )
                    assignment.case_member_count_snapshot = (
                        member_count if item.work_item_type == "INCIDENT_CASE" else None
                    )
                    assignment.completion_sla_extension_factor = factor
                    set_acceptance_deadlines(ticket, assignment)
                    self.side_effects.notify_technician(
                        assignment,
                        "ASSIGNMENT_CREATED",
                        "Bạn có công việc mới",
                        "Ban quản lý đã xác nhận bảng đề xuất và phân công phản ánh cho bạn.",
                    )
                    self.side_effects.notify_unit(
                        ticket,
                        "TICKET_ASSIGNED",
                        "Phản ánh đã được gán kỹ thuật viên",
                        "Ban quản lý đã phân công kỹ thuật viên xử lý phản ánh.",
                    )
                    self.side_effects.audit(
                        actor_user_id,
                        "AI_PROPOSAL_ASSIGNMENT_CONFIRMED",
                        "TICKET_ASSIGNMENT",
                        assignment.id,
                        None,
                        {
                            "ticket_id": str(ticket.id),
                            "technician_id": str(item.final_technician_id),
                            "proposed_technician_id": str(item.proposed_technician_id or ""),
                            "batch_id": str(batch.id),
                        },
                        None,
                        "COORDINATOR",
                    )
                    assigned += 1
                item.status = ProposalItemStatus.ASSIGNED.value
                item.updated_at = now

            batch.status = ProposalBatchStatus.CONFIRMED.value
            batch.confirmed_by_user_id = actor_user_id
            batch.confirmed_at = now
            batch.updated_at = now
            batch.version += 1

            # §4.6 item 6: the ONLY path that turns the switch on, running in
            # the same transaction as the assignments above. Either the work was
            # handed out and DIRECT is on, or neither happened.
            activated = self._activate_direct(actor_user_id, batch, delay, now, assigned)
            switch_enabled = bool(self._settings_row().enabled)
            # What this confirmation actually did to the switch, recorded on the
            # batch. Not a request the client made -- there is no such request.
            batch.continue_auto_assignment = activated
            batch.activation_delay = delay.value if activated else None

            # Frozen inside the same transaction as the assignments it
            # describes. A snapshot written afterwards could disagree with what
            # was committed; one written from live rows at read time would let a
            # renamed category rewrite what a human approved months ago.
            batch.confirmation_snapshot = self._confirmation_snapshot(batch, now, switch_enabled)

            self.side_effects.audit(
                actor_user_id,
                "CONFIRM_ASSIGNMENT_PROPOSAL_BATCH",
                "ASSIGNMENT_PROPOSAL_BATCH",
                batch.id,
                {"status": ProposalBatchStatus.READY.value},
                {
                    "status": batch.status,
                    "assigned_count": assigned,
                    "skipped_manual_won": skipped,
                    "skipped_inactive_technician": stale_technicians,
                    "auto_assignment_enabled": switch_enabled,
                    # The audit says which confirmation authorised autonomous
                    # assignment, and which coordinator made it.
                    "direct_activated_by_this_confirmation": activated,
                    "activation_delay": delay.value if activated else None,
                },
                None,
                "COORDINATOR",
            )
            self.db.commit()
            return self.get_batch(batch.id)
        except Exception:
            self.db.rollback()
            raise

    # ------------------------------------------------------------------
    # The confirmation snapshot: what history renders, forever.
    # ------------------------------------------------------------------

    SNAPSHOT_VERSION = 1

    def _confirmation_snapshot(
        self, batch: AssignmentProposalBatch, now: datetime, switch_enabled: bool
    ) -> dict[str, object]:
        """Everything a history record needs, copied out of the live rows once.

        Nothing here may be re-derived at read time. A ticket can change
        category, a technician can be deactivated and a coordinator can be
        renamed; a record of what was approved has to survive all three. The
        one thing deliberately absent is anything the model produced verbatim
        -- §9 keeps prompts and raw responses out of every read path, and
        `item.reason` is the sanitized one-line business reason, capped by its
        column.
        """
        schedule = self.db.get(AssignmentProposalSchedule, 1)
        # Names are looked up by id rather than read off the eager-loaded
        # relationships. Those were resolved when the batch was queried, before
        # confirm changed `final_technician_id` on some rows and set
        # `confirmed_by_user_id` on the batch, so they can be a step behind the
        # very values being frozen.
        names = self._names_for(
            {batch.confirmed_by_user_id}
            | {item.proposed_technician_id for item in batch.items}
            | {item.final_technician_id for item in batch.items}
        )
        return {
            "version": self.SNAPSHOT_VERSION,
            "confirmed_at": now.isoformat(),
            "confirmed_by_user_id": str(batch.confirmed_by_user_id) if batch.confirmed_by_user_id else None,
            # Copied, not joined: the name as it read at confirmation time.
            "confirmed_by_name": names.get(batch.confirmed_by_user_id),
            "created_by_type": batch.created_by_type,
            # The V4 DIRECT switch. Not the repeat schedule -- see below.
            "auto_assignment": {
                "continue": bool(batch.continue_auto_assignment),
                "activation_delay": batch.activation_delay,
                "enabled_after": switch_enabled,
            },
            # The recurring *draft* schedule as it stood at this moment. What
            # the coordinator picks in the result modal afterwards lands on
            # `followup_schedule`, because that is a later decision.
            "schedule_at_confirmation": {
                "enabled": bool(schedule.enabled) if schedule else False,
                "interval": schedule.interval_code if schedule else None,
            },
            "items": [self._snapshot_item(item, names) for item in batch.items],
        }

    def _names_for(self, user_ids: set[UUID | None]) -> dict[UUID, str | None]:
        wanted = [user_id for user_id in user_ids if user_id is not None]
        if not wanted:
            return {}
        rows = self.db.execute(
            select(UserProfile.user_id, UserProfile.full_name).where(UserProfile.user_id.in_(wanted))
        )
        return {user_id: full_name for user_id, full_name in rows}

    def _snapshot_item(
        self, item: AssignmentProposalItem, names: dict[UUID, str | None]
    ) -> dict[str, object]:
        return {
            "item_id": str(item.id),
            "status": item.status,
            "work_item_type": item.work_item_type,
            "work_item_id": str(item.work_item_id),
            # §4.6 item 4: the model's suggestion and the coordinator's choice
            # are two different facts, and history has to keep showing both.
            "proposed_technician_id": str(item.proposed_technician_id) if item.proposed_technician_id else None,
            "proposed_technician_name": names.get(item.proposed_technician_id),
            "final_technician_id": str(item.final_technician_id) if item.final_technician_id else None,
            "final_technician_name": names.get(item.final_technician_id),
            "coordinator_override": bool(
                item.final_technician_id is not None
                and item.final_technician_id != item.proposed_technician_id
            ),
            "reason": item.reason,
            "members": [self._snapshot_member(member) for member in item.members],
        }

    @staticmethod
    def _snapshot_member(member: AssignmentProposalItemMember) -> dict[str, object]:
        ticket = member.ticket
        return {
            "ticket_id": str(member.ticket_id),
            "display_code": ticket_display_code(member.ticket_id),
            "category": ticket.category.code if ticket and ticket.category else None,
            "location_label": ticket.location.label if ticket and ticket.location else None,
            "priority": ticket.priority.value if ticket and ticket.priority else None,
            "created_at": ticket.created_at.isoformat() if ticket and ticket.created_at else None,
            "sla_due_at": ticket.sla_due_at.isoformat() if ticket and ticket.sla_due_at else None,
        }

    def _activate_direct(
        self,
        actor_user_id: UUID,
        batch: AssignmentProposalBatch,
        delay: ActivationDelay,
        now: datetime,
        assigned: int,
    ) -> bool:
        """Turn DIRECT on, if this confirmation is what earned it.

        Returns whether *this* confirmation flipped the switch, which is a
        narrower fact than "the switch is on": a later batch confirmed while
        DIRECT is already running authorised nothing new, and saying it did
        would put the wrong batch id on the provenance.
        """
        row = self._settings_row(lock=True)
        if row.enabled:
            # Already running. The delay and the original provenance are left
            # alone -- this coordinator confirmed work, not autonomy.
            return False
        if assigned <= 0:
            # Every live row was taken by a manual assignment or a technician
            # who had just been deactivated. No work was handed out, so nothing
            # here is evidence that a human approved autonomous assignment.
            return False
        row.enabled = True
        row.activation_delay = delay.value
        row.updated_by_user_id = actor_user_id
        # The provenance this rule exists for: which proposal, whose
        # confirmation, and when. `updated_by_user_id` cannot carry it -- a
        # later delay change overwrites it with someone who authorised nothing.
        row.activated_by_batch_id = batch.id
        row.activated_by_user_id = actor_user_id
        row.activated_at = now
        row.version += 1
        row.updated_at = now
        self.side_effects.audit(
            actor_user_id,
            "ACTIVATE_DIRECT_AUTO_ASSIGNMENT",
            "AUTO_ASSIGNMENT_SETTING",
            batch.id,
            {"enabled": False},
            {
                "enabled": True,
                "activation_delay": delay.value,
                "activated_by_batch_id": str(batch.id),
                "activated_by_user_id": str(actor_user_id),
                "assigned_count": assigned,
            },
            None,
            "COORDINATOR",
        )
        return True

    # ------------------------------------------------------------------
    # §4.6 items 7-8: cancel and expire.
    # ------------------------------------------------------------------

    def cancel_batch(self, actor_user_id: UUID, batch_id: UUID, reason: str | None = None) -> AssignmentProposalBatch:
        try:
            batch = self._locked_batch(batch_id)
            if batch.status not in {ProposalBatchStatus.READY.value, ProposalBatchStatus.BUILDING.value}:
                raise DomainError(INVALID_STATUS_TRANSITION, "Chỉ có thể hủy bảng đề xuất đang mở.", 409)
            now = datetime.now(UTC)
            batch.status = ProposalBatchStatus.CANCELLED.value
            batch.cancelled_at = now
            batch.updated_at = now
            batch.version += 1
            self.side_effects.audit(
                actor_user_id,
                "CANCEL_ASSIGNMENT_PROPOSAL_BATCH",
                "ASSIGNMENT_PROPOSAL_BATCH",
                batch.id,
                {"status": ProposalBatchStatus.READY.value},
                # Stated in the audit because "cancel does not enable auto" is a
                # rule someone will otherwise have to re-derive from the code.
                {"status": batch.status, "auto_assignment_enabled": bool(self._settings_row().enabled)},
                reason,
                "COORDINATOR",
            )
            self.db.commit()
            return self.get_batch(batch.id)
        except Exception:
            self.db.rollback()
            raise

    def expire_due_batches(self, *, now: datetime | None = None) -> int:
        """§4.6 item 8. Runs in the worker, not on a request."""
        now = now or datetime.now(UTC)
        rows = list(
            self.db.scalars(
                select(AssignmentProposalBatch)
                .where(
                    AssignmentProposalBatch.status == ProposalBatchStatus.READY.value,
                    AssignmentProposalBatch.expires_at.is_not(None),
                    AssignmentProposalBatch.expires_at <= now,
                )
                .with_for_update(of=AssignmentProposalBatch, skip_locked=True)
            )
        )
        for batch in rows:
            batch.status = ProposalBatchStatus.EXPIRED.value
            batch.updated_at = now
            batch.version += 1
            self.side_effects.audit(
                None,
                "EXPIRE_ASSIGNMENT_PROPOSAL_BATCH",
                "ASSIGNMENT_PROPOSAL_BATCH",
                batch.id,
                {"status": ProposalBatchStatus.READY.value},
                {"status": batch.status, "auto_assignment_enabled": bool(self._settings_row().enabled)},
                None,
                "SYSTEM",
            )
        if rows:
            self.db.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # Reads.
    # ------------------------------------------------------------------

    def list_batches(self, limit: int = 20) -> list[AssignmentProposalBatch]:
        return list(
            self.db.scalars(
                self._batch_query()
                .order_by(
                    case(
                        (AssignmentProposalBatch.status == ProposalBatchStatus.READY.value, 0),
                        (AssignmentProposalBatch.status == ProposalBatchStatus.BUILDING.value, 1),
                        (AssignmentProposalBatch.status == ProposalBatchStatus.CONFIRMED.value, 2),
                        else_=3,
                    ),
                    AssignmentProposalBatch.created_at.desc(),
                )
                .limit(limit)
            )
        )

    def get_batch(self, batch_id: UUID) -> AssignmentProposalBatch:
        batch = self.db.scalar(self._batch_query().where(AssignmentProposalBatch.id == batch_id))
        if batch is None:
            raise DomainError(TICKET_NOT_FOUND, "Bảng đề xuất không tồn tại.", 404)
        return batch

    # ------------------------------------------------------------------
    # Internals.
    # ------------------------------------------------------------------

    def _locked_batch(self, batch_id: UUID) -> AssignmentProposalBatch:
        batch = self.db.scalar(
            self._batch_query()
            .where(AssignmentProposalBatch.id == batch_id)
            .with_for_update(of=AssignmentProposalBatch)
        )
        if batch is None:
            raise DomainError(TICKET_NOT_FOUND, "Bảng đề xuất không tồn tại.", 404)
        return batch

    @staticmethod
    def _require_ready(batch: AssignmentProposalBatch) -> None:
        if batch.status != ProposalBatchStatus.READY.value:
            raise DomainError(
                PROPOSAL_EXPIRED if batch.status == ProposalBatchStatus.EXPIRED.value else PROPOSAL_NOT_READY,
                "Bảng đề xuất không ở trạng thái có thể thao tác.",
                409,
            )

    def _locked_tickets(self, ticket_ids: list[UUID]) -> list[Ticket]:
        return list(
            self.db.scalars(
                select(Ticket)
                .where(Ticket.id.in_(ticket_ids))
                .options(selectinload(Ticket.assignments))
                .order_by(Ticket.id)
                .with_for_update(of=Ticket)
            )
        )

    def _settings_row(self, *, lock: bool = False) -> AutoAssignmentSetting:
        row = self.db.get(AutoAssignmentSetting, 1, with_for_update=lock)
        if row is None:
            row = AutoAssignmentSetting(
                id=1, enabled=False, activation_delay=ActivationDelay.IMMEDIATE.value, version=1
            )
            self.db.add(row)
            self.db.flush()
        return row

    def _technician_is_active(self, technician_id: UUID) -> bool:
        technician = self.db.get(TechnicianProfile, technician_id, with_for_update=True)
        return technician is not None and bool(technician.is_active)

    def _require_active_technician(self, technician_id: UUID) -> TechnicianProfile:
        """Decision 3 / 4.3: an inactive technician is never assignable.

        Checked in the service, under a row lock, because the roster the browser
        rendered may be minutes old and a client can post any UUID it likes.
        """
        technician = self.db.get(TechnicianProfile, technician_id, with_for_update=True)
        if technician is None:
            raise DomainError(TECHNICIAN_NOT_FOUND, "Kỹ thuật viên không tồn tại.", 404)
        if not technician.is_active:
            raise DomainError(
                TECHNICIAN_NOT_ELIGIBLE,
                "Kỹ thuật viên này đã ngừng hoạt động, vui lòng chọn người khác.",
                409,
            )
        return technician

    def _sla_factor(self, member_count: int, priority: Priority | None) -> Decimal:
        if priority is Priority.P3 or member_count <= 1:
            return Decimal("1.00")
        step = Decimal(str(self.settings.incident_case_sla_extension_per_extra_ticket))
        return min(Decimal("1.00") + step * (member_count - 1), Decimal("2.00"))

    def _batch_query(self):
        return select(AssignmentProposalBatch).options(
            joinedload(AssignmentProposalBatch.confirmed_by),
            selectinload(AssignmentProposalBatch.items).options(
                joinedload(AssignmentProposalItem.ticket).options(
                    joinedload(Ticket.category),
                    joinedload(Ticket.location),
                    joinedload(Ticket.source_unit),
                ),
                joinedload(AssignmentProposalItem.proposed_technician).joinedload(TechnicianProfile.user),
                joinedload(AssignmentProposalItem.final_technician).joinedload(TechnicianProfile.user),
                # Every member ticket, with what the board renders for it: a
                # case row is up to five tickets, not one.
                selectinload(AssignmentProposalItem.members).joinedload(
                    AssignmentProposalItemMember.ticket
                ).options(
                    joinedload(Ticket.category),
                    joinedload(Ticket.location),
                ),
            )
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


__all__ = [
    # Re-exported so callers keep importing the codes from the service that
    # raises them; the definitions now live in `src.models.api.errors`.
    "CONFLICT_VERSION",
    "PROPOSAL_EXPIRED",
    "PROPOSAL_NOTHING_TO_ASSIGN",
    "PROPOSAL_NOT_READY",
    "AssignmentProposalService",
    "ProposalRoundReport",
]
