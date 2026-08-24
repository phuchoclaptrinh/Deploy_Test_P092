"""The durable assignment job store (§5.1, §7.4).

Every AI assignment round is a row, not a timer. §5 is explicit that a 5-10
minute window may not live in a FastAPI `BackgroundTasks` callback or an
in-process scheduler: a restart during a grace window would silently drop the
ticket, and nobody would know which ones were lost.

So a job is created, persisted, and later *claimed* by a worker process:

* `SCHEDULED_GRACE` with `execute_after` — waiting out the §6.2 window.
* claimed under `FOR UPDATE SKIP LOCKED` (PostgreSQL) so several workers can run
  without handing the same job to two of them.
* `claimed_at` plus a claim timeout, so a worker that dies mid-job releases its
  work instead of parking it forever.

`ai_assignment_job_members.is_active` is the other half. §5.1 defines the
concurrency rule in terms of persistence, and the partial unique index on
`(ticket_id) WHERE is_active` is what actually enforces it: a ticket cannot be
inside two unfinished DIRECT jobs, whether or not one of them represents a whole
incident case. PROPOSAL jobs never set it, because manual assignment must stay
possible while the preview table is open.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from src.assignment_agent.config import ASSIGNMENT_MODEL_TIMEOUT_SECONDS
from src.config import get_settings
from src.database.models.assignment_proposal import AIAssignmentJob, AIAssignmentJobMember
from src.models.api.errors import INVALID_STATUS_TRANSITION, DomainError
from src.models.enums import (
    TERMINAL_JOB_STATUSES,
    AssignmentJobMode,
    AssignmentJobStatus,
    AssignmentJobTrigger,
    Priority,
)
from src.services.assignment_candidates import WorkItemDraft
from src.services.assignment_support import AssignmentSideEffects

logger = logging.getLogger(__name__)

ASSIGNMENT_JOB_ALREADY_ACTIVE = "ASSIGNMENT_JOB_ALREADY_ACTIVE"


class AssignmentJobService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.side_effects = AssignmentSideEffects(db)

    # ------------------------------------------------------------------
    # Creating jobs.
    # ------------------------------------------------------------------

    def schedule_direct(
        self,
        draft: WorkItemDraft,
        *,
        trigger: AssignmentJobTrigger,
        execute_after: datetime | None = None,
        previous_assignment_id: UUID | None = None,
        reassignment_count: int = 0,
    ) -> AIAssignmentJob | None:
        """Create one DIRECT job, or return None when a ticket is already claimed.

        The unique partial index is the real check; the pre-read only turns the
        common case into a clean skip instead of an integrity error.
        """
        now = datetime.now(UTC)
        if self._any_ticket_locked(draft.ticket_ids):
            logger.info(
                "Skipping DIRECT job for work item %s: a ticket is already in an unfinished job.",
                draft.work_item_id,
            )
            return None

        job = AIAssignmentJob(
            mode=AssignmentJobMode.DIRECT.value,
            status=AssignmentJobStatus.SCHEDULED_GRACE.value,
            trigger=trigger.value,
            decision_id=draft.decision_id,
            work_item_type=draft.work_item_type,
            work_item_id=draft.work_item_id,
            ticket_id=draft.ticket_ids[0] if draft.work_item_type == "TICKET" else None,
            incident_case_id=draft.work_item_id if draft.work_item_type == "INCIDENT_CASE" else None,
            previous_assignment_id=previous_assignment_id,
            reassignment_count_snapshot=reassignment_count,
            execute_after=execute_after or now,
            excluded_technician_ids=[str(item) for item in draft.excluded_technician_ids],
            created_at=now,
        )
        self.db.add(job)
        try:
            self.db.flush()
            for ticket_id in draft.ticket_ids:
                self.db.add(AIAssignmentJobMember(job_id=job.id, ticket_id=ticket_id, is_active=True))
            self.db.flush()
        except IntegrityError:
            # Another transaction claimed one of these tickets between the read
            # above and this write. Losing that race is the correct outcome.
            self.db.rollback()
            logger.info("DIRECT job for work item %s lost the race for its tickets.", draft.work_item_id)
            return None

        self.side_effects.audit(
            None,
            "ASSIGNMENT_JOB_SCHEDULED",
            "AI_ASSIGNMENT_JOB",
            job.id,
            None,
            {
                "mode": job.mode,
                "trigger": job.trigger,
                "work_item_id": str(draft.work_item_id),
                "ticket_ids": [str(item) for item in draft.ticket_ids],
                "execute_after": job.execute_after.isoformat() if job.execute_after else None,
            },
            None,
            "SYSTEM",
        )
        return job

    def schedule_proposal(self, *, proposal_batch_id: UUID) -> AIAssignmentJob:
        """§7.4: one PROPOSAL job stands for one model call over the whole batch.

        No members, no `is_active`, no ticket lock — a coordinator has to be able
        to keep assigning by hand while the preview table is open (§5.1).
        """
        job = AIAssignmentJob(
            mode=AssignmentJobMode.PROPOSAL.value,
            status=AssignmentJobStatus.SCHEDULED_GRACE.value,
            trigger=AssignmentJobTrigger.COORDINATOR_PROPOSAL.value,
            batch_decision_id=uuid4(),
            proposal_batch_id=proposal_batch_id,
            execute_after=datetime.now(UTC),
        )
        self.db.add(job)
        self.db.flush()
        return job

    def _any_ticket_locked(self, ticket_ids: list[UUID]) -> bool:
        return (
            self.db.scalar(
                select(AIAssignmentJobMember.ticket_id).where(
                    AIAssignmentJobMember.ticket_id.in_(ticket_ids),
                    AIAssignmentJobMember.is_active.is_(True),
                )
            )
            is not None
        )

    # ------------------------------------------------------------------
    # Claiming work.
    # ------------------------------------------------------------------

    def claim_due_jobs(
        self,
        *,
        mode: AssignmentJobMode,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[AIAssignmentJob]:
        """Take ownership of up to `limit` jobs that are ready to run.

        `SKIP LOCKED` is what makes several workers safe: a row another worker is
        already holding is passed over rather than waited on. SQLite ignores both
        hints, which is fine — the test suite runs one worker.

        A job whose claim has gone stale is picked up again. That is deliberate:
        the alternative is a crashed worker holding a ticket hostage, and the
        write side is idempotent per `decision_id`.
        """
        now = now or datetime.now(UTC)
        limit = limit or self.settings.assignment_worker_batch_size
        stale_before = now - timedelta(seconds=self.settings.assignment_job_claim_timeout_seconds)

        query = (
            select(AIAssignmentJob)
            .where(
                AIAssignmentJob.mode == mode.value,
                AIAssignmentJob.status.in_(
                    [
                        AssignmentJobStatus.SCHEDULED_GRACE.value,
                        AssignmentJobStatus.PRIMARY_RUNNING.value,
                        AssignmentJobStatus.FALLBACK_RUNNING.value,
                    ]
                ),
                AIAssignmentJob.execute_after <= now,
                (AIAssignmentJob.claimed_at.is_(None)) | (AIAssignmentJob.claimed_at <= stale_before),
            )
            .options(selectinload(AIAssignmentJob.members))
            .order_by(AIAssignmentJob.execute_after.asc(), AIAssignmentJob.created_at.asc())
            .limit(limit)
            .with_for_update(of=AIAssignmentJob, skip_locked=True)
        )
        jobs = list(self.db.scalars(query))
        for job in jobs:
            self.claim_job(job, now=now)
        self.db.flush()
        return jobs

    def claim_job(self, job: AIAssignmentJob, *, now: datetime | None = None) -> None:
        """Mark a job already owned by this transaction as running.

        Proposal creation uses this for the RULE engine: there is no remote
        model call to wait for, so sending a freshly-created preview through
        the polling worker only adds 15+ seconds of avoidable latency.  The
        worker still uses the same transition through ``claim_due_jobs``.
        """
        now = now or datetime.now(UTC)
        job.claimed_at = now
        job.started_at = job.started_at or now
        job.attempt_count = (job.attempt_count or 0) + 1
        job.status = AssignmentJobStatus.PRIMARY_RUNNING.value
        job.primary_deadline_at = now + timedelta(seconds=ASSIGNMENT_MODEL_TIMEOUT_SECONDS)
        job.fallback_deadline_at = job.primary_deadline_at + timedelta(seconds=ASSIGNMENT_MODEL_TIMEOUT_SECONDS)
        job.updated_at = now
        self.db.flush()

    # ------------------------------------------------------------------
    # Terminal transitions.
    # ------------------------------------------------------------------

    def mark_completed(
        self,
        job: AIAssignmentJob,
        *,
        selected_technician_id: UUID | None,
        reason: str | None,
        completed_model: str | None,
    ) -> None:
        """§5.1: COMPLETED means a valid business answer, NO_SUITABLE_CANDIDATE
        included."""
        job.status = AssignmentJobStatus.COMPLETED.value
        job.selected_technician_id = selected_technician_id
        job.decision_reason = reason
        job.completed_model = completed_model
        self._finish(job)

    def mark_manual_required(self, job: AIAssignmentJob, *, error_code: str, error_detail: str | None = None) -> None:
        """§5.2 item 5: DIRECT has run out of AI options and a human takes over."""
        job.status = AssignmentJobStatus.MANUAL_REQUIRED.value
        job.error_code = error_code
        job.error_detail = error_detail
        self._finish(job)

    def mark_failed(self, job: AIAssignmentJob, *, error_code: str, error_detail: str | None = None) -> None:
        job.status = AssignmentJobStatus.FAILED.value
        job.error_code = error_code
        job.error_detail = error_detail
        self._finish(job)

    def mark_manual_won(self, job: AIAssignmentJob) -> None:
        """§4.5: a coordinator assigned by hand while this job was in flight."""
        job.status = AssignmentJobStatus.CANCELLED_MANUAL_WON.value
        self._finish(job)

    def cancel_by_coordinator(self, job: AIAssignmentJob, actor_user_id: UUID) -> None:
        """§6.2: the coordinator took the ticket out of the AI path on purpose.

        The window this button belongs to is narrow and specific: a P1/P2
        reassignment after a technician rejected, waiting out its 300 seconds.
        Nothing else is cancellable -- a P3 reassignment runs immediately and has
        no window, an initial-delay job is the switch working as configured, and
        a job already talking to the model would leave a half-written decision.
        Manual assignment stays the way to take any other ticket back, and it
        still wins the race (§4.5).
        """
        self._require_cancellable(job)
        job.status = AssignmentJobStatus.CANCELLED_BY_COORDINATOR.value
        job.cancelled_by_user_id = actor_user_id
        self._finish(job)
        self.side_effects.audit(
            actor_user_id,
            "ASSIGNMENT_JOB_CANCELLED",
            "AI_ASSIGNMENT_JOB",
            job.id,
            None,
            {"status": job.status},
            None,
            "COORDINATOR",
        )

    def _finish(self, job: AIAssignmentJob) -> None:
        now = datetime.now(UTC)
        job.completed_at = now
        job.updated_at = now
        job.claimed_at = None
        # §7.4: a terminal DIRECT job releases its tickets so the next round can
        # claim them.
        for member in job.members:
            member.is_active = False
        self.db.flush()

    @staticmethod
    def _require_cancellable(job: AIAssignmentJob) -> None:
        cancellable = (
            job.mode == AssignmentJobMode.DIRECT.value
            and job.trigger == AssignmentJobTrigger.REASSIGN_REJECTED.value
            and job.status == AssignmentJobStatus.SCHEDULED_GRACE.value
        )
        if not cancellable:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Chỉ hủy được lượt AI đang trong cửa sổ chờ 5 phút sau khi kỹ thuật viên từ chối. "
                "Với các trường hợp khác, hãy phân tay trực tiếp trên ticket.",
                409,
            )

    # ------------------------------------------------------------------
    # Manual-wins race (§4.5).
    # ------------------------------------------------------------------

    def cancel_open_jobs_for_ticket(self, ticket_id: UUID, *, reason: str = "manual assignment won") -> int:
        """Called when a human assigns a ticket an AI job was holding.

        Only the job whose *every* remaining ticket has been taken is cancelled.
        A case job that still has assignable members keeps running, which is what
        §4.5 means by skipping the taken member and assigning the rest.
        """
        job_ids = list(
            self.db.scalars(
                select(AIAssignmentJobMember.job_id).where(
                    AIAssignmentJobMember.ticket_id == ticket_id,
                    AIAssignmentJobMember.is_active.is_(True),
                )
            )
        )
        if not job_ids:
            return 0

        cancelled = 0
        for job_id in job_ids:
            job = self.db.scalar(
                select(AIAssignmentJob)
                .where(AIAssignmentJob.id == job_id)
                .options(selectinload(AIAssignmentJob.members))
                .with_for_update(of=AIAssignmentJob)
            )
            if job is None or job.status in {status.value for status in TERMINAL_JOB_STATUSES}:
                continue
            member = next((item for item in job.members if item.ticket_id == ticket_id), None)
            if member is not None:
                member.is_active = False
            if any(item.is_active for item in job.members):
                # A case job with members still free stays alive.
                self.db.flush()
                continue
            job.status = AssignmentJobStatus.CANCELLED_MANUAL_WON.value
            job.error_detail = reason
            self._finish(job)
            cancelled += 1
            self.side_effects.audit(
                None,
                "ASSIGNMENT_JOB_CANCELLED_MANUAL_WON",
                "AI_ASSIGNMENT_JOB",
                job.id,
                None,
                {"ticket_id": str(ticket_id), "reason": reason},
                None,
                "SYSTEM",
            )
        return cancelled

    # ------------------------------------------------------------------
    # Snapshots and audit payloads (§7.4, §8.1).
    # ------------------------------------------------------------------

    @staticmethod
    def record_request(job: AIAssignmentJob, draft: WorkItemDraft, *, model_request_id: UUID, primary: str, fallback: str | None) -> None:
        job.model_request_id = model_request_id
        job.candidate_snapshot = list(draft.candidates)
        job.excluded_technician_ids = [str(item) for item in draft.excluded_technician_ids]
        job.primary_model = primary
        job.fallback_model = fallback
        job.input_hash = _hash_payload(
            {
                "work_item_id": str(draft.work_item_id),
                "ticket_ids": sorted(str(item) for item in draft.ticket_ids),
                "candidates": [item["technician_id"] for item in draft.candidates],
                "excluded": [str(item) for item in draft.excluded_technician_ids],
            }
        )

    @staticmethod
    def record_raw_output(job: AIAssignmentJob, payload: dict[str, object]) -> None:
        """§9: raw model output is audit data, never part of a client error."""
        job.raw_model_output = payload

    # ------------------------------------------------------------------
    # Deadlines (§6.2).
    # ------------------------------------------------------------------

    def grace_execute_after(self, priority: Priority | None, *, now: datetime | None = None) -> datetime:
        """§6.2: P3 runs immediately; P1/P2 wait out the grace window so a
        coordinator can step in first."""
        now = now or datetime.now(UTC)
        if priority is Priority.P3:
            return now
        return now + timedelta(seconds=self.settings.assignment_grace_seconds)

    def reassignment_cap_reached(self, count: int) -> bool:
        """§11 assumption 4: three changes are allowed; the fourth stops auto."""
        return count > self.settings.assignment_reassignment_cap


def _hash_payload(payload: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def job_already_active_error() -> DomainError:
    return DomainError(
        ASSIGNMENT_JOB_ALREADY_ACTIVE,
        "Work item đã có job phân việc chưa kết thúc.",
        409,
    )


__all__ = [
    "ASSIGNMENT_JOB_ALREADY_ACTIVE",
    "AssignmentJobService",
    "job_already_active_error",
]
