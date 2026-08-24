"""DIRECT assignment orchestration (§4.2-§4.5, §5.2, §6).

The shape of one round:

1. Claim the due DIRECT jobs. Batch up to 20 distinct tickets into a single
   model request (§4.3) — batching is a cost optimisation and changes nothing
   else: each decision keeps its own `decision_id`, its own job and its own
   write transaction, and no coordinator approval step appears.
2. Build each candidate snapshot **now**, not when the job was scheduled. A P1
   grace window is five minutes long, and a five-minute-old view of who is
   available is a different question from the one being asked.
3. A work item with no candidates never reaches the model (§5.2 item 1): it goes
   straight to MANUAL_REQUIRED with `NO_CANDIDATES` and pauses just that ticket.
4. The configured decision engine resolves the batch. On `RULE_ENGINE_V1`
   that is a ranking over the snapshot and it always answers; on the `AI`
   engine it is primary then partial fallback, where decisions the primary got
   right are kept and only the broken ones are re-asked.
5. Each decision is applied in its own transaction, re-checking eligibility and
   the one-active-assignment rule but **not** re-checking skills or availability
   (§4.1: that was decided before the model was called, and re-deciding it after
   the fact would quietly overrule the business rule).

What "manual wins" means in practice: the unique index on one active assignment
per ticket makes the AI write fail safely, and the job becomes
`CANCELLED_MANUAL_WON` rather than overwriting a human's decision or retrying.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from src.assignment_agent.config import AssignmentConfigurationError
from src.assignment_agent.schemas import (
    AssignmentDecisionType,
    AssignmentDecisionV4,
    AssignmentTrigger,
    CandidateSnapshotV4,
    DirectAssignmentBatchRequestV4,
    DirectWorkItemRequestV4,
    WorkItemType,
    WorkItemV4,
)
from src.assignment_rules.config import AssignmentRuleConfigError
from src.assignment_rules.engine import UNDATED
from src.assignment_rules.service import priority_rank
from src.config import get_settings
from src.database.models.assignment_proposal import AIAssignmentJob
from src.database.models.incident_case import IncidentCase
from src.database.models.notification import Notification
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.database.models.user_profile import UserProfile
from src.models.enums import (
    AssignmentJobMode,
    AssignmentSource,
    NotificationChannel,
    NotificationStatus,
    Priority,
    UserRole,
)
from src.repositories.assignment_repository import AssignmentRepository
from src.services.assignment_candidates import AssignmentCandidateService, WorkItemDraft, as_utc
from src.services.assignment_decision_engine import AssignmentDecisionEngine, build_decision_engine
from src.services.assignment_job_service import AssignmentJobService
from src.services.assignment_service import set_acceptance_deadlines
from src.services.assignment_support import AssignmentSideEffects
from src.services.scoring_service import ScoringService

logger = logging.getLogger(__name__)

NO_CANDIDATES = "NO_CANDIDATES"
MODEL_FAILED = "MODEL_FAILED"
NO_SUITABLE_CANDIDATE = "NO_SUITABLE_CANDIDATE"
WORK_ITEM_NO_LONGER_ELIGIBLE = "WORK_ITEM_NO_LONGER_ELIGIBLE"


@dataclass
class DirectRoundReport:
    """What one worker pass did, for logs and for the diagnostics endpoint."""

    jobs_claimed: int = 0
    model_requests: int = 0
    assignments_created: int = 0
    manual_required: int = 0
    manual_won: int = 0
    no_candidates: int = 0
    errors: list[str] = field(default_factory=list)


class DirectAssignmentService:
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
            # Built lazily so a worker with no DIRECT work never constructs one,
            # and so a test can inject a scripted engine. On `AI` the factory
            # still applies the §5.2 failover rule; on `RULE` there is nothing
            # to fail over.
            self._engine = build_decision_engine(self.settings)
        return self._engine

    # ------------------------------------------------------------------
    # One worker pass.
    # ------------------------------------------------------------------

    def run_due_jobs(self, *, now: datetime | None = None, limit: int | None = None) -> DirectRoundReport:
        now = now or datetime.now(UTC)
        report = DirectRoundReport()

        jobs = self.jobs.claim_due_jobs(mode=AssignmentJobMode.DIRECT, now=now, limit=limit)
        report.jobs_claimed = len(jobs)
        if not jobs:
            self.db.commit()
            return report

        drafts: dict[UUID, WorkItemDraft] = {}
        runnable: list[AIAssignmentJob] = []
        for job in jobs:
            draft = self._draft_for(job)
            if draft is None:
                # The work item stopped being assignable while the job waited —
                # approved elsewhere, cancelled, or manually assigned.
                self.jobs.mark_manual_won(job)
                report.manual_won += 1
                continue
            if not draft.has_candidates:
                # §5.2 item 1: no model call at all.
                self._to_manual(job, draft, error_code=NO_CANDIDATES, detail="Không còn kỹ thuật viên phù hợp.")
                report.no_candidates += 1
                report.manual_required += 1
                continue
            drafts[job.id] = draft
            runnable.append(job)

        self.db.commit()

        for chunk in self._batches(self._ordered(runnable, drafts), drafts):
            report.model_requests += 1
            self._run_batch(chunk, drafts, report)

        return report

    @staticmethod
    def _ordered(jobs: list[AIAssignmentJob], drafts: dict[UUID, WorkItemDraft]) -> list[AIAssignmentJob]:
        """P3, then P2, then P1; inside one priority, the oldest work item first.

        Jobs are claimed in `execute_after` order, which is when they became
        due rather than how urgent they are. Sorting here decides both which
        work items share a request and the order the engine walks them in, and
        the order matters: the engine hands out the projected load as it goes,
        so whatever is placed first gets the least loaded technician.
        """
        return sorted(
            jobs,
            key=lambda job: (
                priority_rank(drafts[job.id].priority),
                as_utc(drafts[job.id].created_at) or UNDATED,
                str(drafts[job.id].work_item_id),
            ),
        )

    def _batches(
        self,
        jobs: list[AIAssignmentJob],
        drafts: dict[UUID, WorkItemDraft],
    ) -> list[list[AIAssignmentJob]]:
        """§4.3: at most 20 distinct tickets per request, and never a case split
        across two requests."""
        cap = self.settings.direct_request_max_ticket_count
        batches: list[list[AIAssignmentJob]] = []
        current: list[AIAssignmentJob] = []
        used = 0
        for job in jobs:
            size = drafts[job.id].ticket_count
            if current and used + size > cap:
                batches.append(current)
                current, used = [], 0
            current.append(job)
            used += size
        if current:
            batches.append(current)
        return batches

    # ------------------------------------------------------------------
    # Model call and application.
    # ------------------------------------------------------------------

    def _run_batch(
        self,
        jobs: list[AIAssignmentJob],
        drafts: dict[UUID, WorkItemDraft],
        report: DirectRoundReport,
    ) -> None:
        model_request_id = uuid4()
        items = []
        for job in jobs:
            draft = drafts[job.id]
            self.jobs.record_request(
                job,
                draft,
                model_request_id=model_request_id,
                primary=self.engine.engine_version,
                fallback=self.engine.fallback_version,
            )
            items.append(self._request_item(job, draft))
        self.db.commit()

        request = DirectAssignmentBatchRequestV4(
            request_id=model_request_id,
            work_items=items,
            requested_at=datetime.now(UTC),
        )

        try:
            outcome = self.engine.decide_direct(request)
        except (AssignmentConfigurationError, AssignmentRuleConfigError):
            # Never MANUAL_REQUIRED. "The engine is misconfigured" and "the
            # engine looked at this ticket and could not place it" are different
            # facts, and a coordinator staring at a manual queue has no way to
            # tell them apart. Let it reach the worker log and the operator.
            raise
        except Exception as exc:  # noqa: BLE001 - an engine outage must not crash the worker
            logger.exception("Assignment decision round %s failed outright.", model_request_id)
            report.errors.append(f"{type(exc).__name__}: {exc}")
            for job in jobs:
                self._to_manual(
                    job,
                    drafts[job.id],
                    error_code=MODEL_FAILED,
                    detail=type(exc).__name__,
                )
                report.manual_required += 1
            self.db.commit()
            return

        by_decision = {decision.decision_id: decision for decision in outcome.result.decisions}
        failures = {failure.decision_id: failure for failure in outcome.failures}

        for job in jobs:
            draft = drafts[job.id]
            decision = by_decision.get(job.decision_id)
            self.jobs.record_raw_output(
                job,
                {
                    "request_id": str(outcome.result.request_id),
                    "fallback_used": outcome.fallback_used,
                    "decision": decision.model_dump(mode="json") if decision else None,
                    "failure": (
                        {"error_code": failures[job.decision_id].error_code, "detail": failures[job.decision_id].error_detail}
                        if job.decision_id in failures
                        else None
                    ),
                },
            )
            if decision is None:
                # §5.2 item 5: primary and fallback both failed this item.
                failure = failures.get(job.decision_id)
                self._to_manual(
                    job,
                    draft,
                    error_code=MODEL_FAILED,
                    detail=failure.error_code if failure else "No decision returned.",
                )
                report.manual_required += 1
                self.db.commit()
                continue

            if decision.decision is AssignmentDecisionType.NO_SUITABLE_CANDIDATE:
                # §5.2 item 7: a valid business answer. No fallback was called
                # and DIRECT goes straight to the manual queue.
                self.jobs.mark_completed(
                    job,
                    selected_technician_id=None,
                    reason=decision.reason,
                    completed_model=decision.model_version,
                )
                self._pause_tickets(draft, reason=NO_SUITABLE_CANDIDATE)
                self._alert_coordinators(draft, NO_SUITABLE_CANDIDATE, decision.reason)
                report.manual_required += 1
                self.db.commit()
                continue

            self._apply_decision(job, draft, decision, report)
            self.db.commit()

    def _request_item(self, job: AIAssignmentJob, draft: WorkItemDraft) -> DirectWorkItemRequestV4:
        return DirectWorkItemRequestV4(
            decision_id=job.decision_id,
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
            trigger=AssignmentTrigger(job.trigger),
            reassignment_count=job.reassignment_count_snapshot or 0,
            excluded_technician_ids=draft.excluded_technician_ids,
            candidates=[CandidateSnapshotV4(**item) for item in draft.candidates],
        )

    def _apply_decision(
        self,
        job: AIAssignmentJob,
        draft: WorkItemDraft,
        decision: AssignmentDecisionV4,
        report: DirectRoundReport,
    ) -> None:
        """§4.5: lock, re-check integrity only, write, then set the deadlines."""
        technician_id = decision.selected_technician_id
        assert technician_id is not None

        # §4.1: the only technician check that survives the model call.
        if str(technician_id) not in {item["technician_id"] for item in draft.candidates}:
            self._to_manual(job, draft, error_code=MODEL_FAILED, detail="Selected technician was not in the snapshot.")
            report.manual_required += 1
            return

        # Locked in UUID order to keep two case jobs from deadlocking on each other.
        tickets = self._locked_tickets(sorted(draft.ticket_ids, key=str))
        assignable = [ticket for ticket in tickets if self.candidates.is_ticket_eligible(ticket)]
        if not assignable:
            self.jobs.mark_manual_won(job)
            report.manual_won += 1
            return

        # §4.5 step 5: the SLA factor follows the members actually written now,
        # not the snapshot the model saw.
        member_count = len(assignable)
        factor = self._sla_factor(member_count, assignable[0].priority)
        created: list[TicketAssignment] = []
        for ticket in assignable:
            try:
                assignment = self.assignments.create_assignment(
                    ticket_id=ticket.id,
                    technician_id=technician_id,
                    # §4.5 step 4: AI_AUTO has no human author, and its audit
                    # actor is SYSTEM.
                    assigned_by_user_id=None,
                    assignment_source=AssignmentSource.AI_AUTO.value,
                    assignment_job_id=job.id,
                )
            except IntegrityError:
                # A coordinator won the race on this member. Skip it and keep
                # the rest of the case (§4.5).
                self.db.rollback()
                logger.info("Manual assignment won ticket %s during AI write.", ticket.id)
                continue
            assignment.case_member_count_snapshot = member_count if draft.work_item_type == "INCIDENT_CASE" else None
            assignment.completion_sla_extension_factor = factor
            set_acceptance_deadlines(ticket, assignment, is_reassignment=bool(job.previous_assignment_id))
            self._extend_completion_sla(ticket, factor)
            created.append(assignment)

        if not created:
            self.jobs.mark_manual_won(job)
            report.manual_won += 1
            return

        self.jobs.mark_completed(
            job,
            selected_technician_id=technician_id,
            reason=decision.reason,
            completed_model=decision.model_version,
        )
        report.assignments_created += len(created)

        for assignment in created:
            self.side_effects.audit(
                None,
                "AI_ASSIGNMENT_CREATED",
                "TICKET_ASSIGNMENT",
                assignment.id,
                None,
                {
                    "ticket_id": str(assignment.ticket_id),
                    "technician_id": str(technician_id),
                    "job_id": str(job.id),
                    "decision_id": str(decision.decision_id),
                    "model_version": decision.model_version,
                    "reason": decision.reason,
                    "assignment_source": AssignmentSource.AI_AUTO.value,
                },
                None,
                "SYSTEM",
            )
            self.side_effects.notify_technician(
                assignment,
                "ASSIGNMENT_CREATED",
                "Bạn có công việc mới",
                "Hệ thống đã phân công một phản ánh cho bạn. Vui lòng xác nhận nhận việc.",
            )
        for ticket in assignable:
            self.side_effects.notify_unit(
                ticket,
                "TICKET_ASSIGNED",
                "Phản ánh đã được gán kỹ thuật viên",
                "Ban quản lý đã phân công kỹ thuật viên xử lý phản ánh của bạn.",
            )

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    def _draft_for(self, job: AIAssignmentJob) -> WorkItemDraft | None:
        """Rebuild the work item at execution time.

        The candidate snapshot must reflect who is available now, and the job
        keeps its own `decision_id` so the idempotency key survives the rebuild.
        """
        if job.work_item_type == "TICKET":
            ticket = self.db.get(Ticket, job.ticket_id)
            if ticket is None or not self.candidates.is_ticket_eligible(ticket):
                return None
            draft = self.candidates.ticket_draft(ticket)
        else:
            case = self.db.get(IncidentCase, job.incident_case_id)
            if case is None:
                return None
            draft = self.candidates.case_draft(case, max_members=self.settings.incident_case_max_ticket_count)
            if draft is None:
                return None
        draft.decision_id = job.decision_id
        return draft

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

    def _sla_factor(self, member_count: int, priority: Priority | None) -> Decimal:
        """§4.5 step 5 / §11 assumption 8.

        1.00 for a single ticket, +0.25 per extra member, capped at double — and
        P3 never stretches, because five minutes is the promise that makes P3
        mean anything.
        """
        if priority is Priority.P3 or member_count <= 1:
            return Decimal("1.00")
        step = Decimal(str(self.settings.incident_case_sla_extension_per_extra_ticket))
        factor = Decimal("1.00") + step * (member_count - 1)
        return min(factor, Decimal("2.00"))

    def _extend_completion_sla(self, ticket: Ticket, factor: Decimal) -> None:
        """Stretch only the completion deadline; acceptance deadlines are fixed."""
        if factor == Decimal("1.00") or ticket.priority is None:
            return
        scoring = ScoringService()
        started = ticket.sla_started_at or ticket.created_at
        base = scoring.sla_duration[ticket.priority]
        ticket.sla_due_at = started + timedelta(seconds=float(base.total_seconds()) * float(factor))
        ticket.version += 1

    def _to_manual(
        self,
        job: AIAssignmentJob,
        draft: WorkItemDraft | None,
        *,
        error_code: str,
        detail: str | None,
    ) -> None:
        self.jobs.mark_manual_required(job, error_code=error_code, error_detail=detail)
        if draft is not None:
            self._pause_tickets(draft, reason=error_code)
            self._alert_coordinators(draft, error_code, detail)

    def _pause_tickets(self, draft: WorkItemDraft, *, reason: str) -> None:
        """§5.2 item 5: pause only the affected tickets, never the global switch."""
        for ticket in self.db.scalars(select(Ticket).where(Ticket.id.in_(draft.ticket_ids))):
            ticket.auto_assignment_paused = True
            ticket.auto_assignment_pause_reason = reason[:100]
            ticket.version += 1

    def _alert_coordinators(self, draft: WorkItemDraft, error_code: str, detail: str | None) -> None:
        recipients = list(
            self.db.scalars(
                select(UserProfile.user_id).where(
                    UserProfile.role == UserRole.COORDINATOR,
                    UserProfile.is_active.is_(True),
                )
            )
        )
        for user_id in recipients:
            self.db.add(
                Notification(
                    recipient_user_id=user_id,
                    ticket_id=draft.ticket_ids[0],
                    notification_type="ASSIGNMENT_MANUAL_REQUIRED",
                    channel=NotificationChannel.IN_APP,
                    title="Cần phân việc thủ công",
                    body="Hệ thống không thể tự phân công cho một phản ánh. Vui lòng phân công thủ công.",
                    # §9: an error code, never a raw model response.
                    payload={
                        "work_item_id": str(draft.work_item_id),
                        "ticket_ids": [str(item) for item in draft.ticket_ids],
                        "error_code": error_code,
                        "detail": (detail or "")[:200],
                    },
                    status=NotificationStatus.PENDING,
                )
            )


__all__ = [
    "MODEL_FAILED",
    "NO_CANDIDATES",
    "DirectAssignmentService",
    "DirectRoundReport",
]
