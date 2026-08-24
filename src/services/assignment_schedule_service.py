"""The recurring proposal schedule.

What this is, stated once so it cannot be confused with the thing next to it:

* `AutoAssignmentSettingsService` owns the **V4 DIRECT switch** -- whether
  Backend may assign an approved ticket by itself, and how long it waits first.
  It assigns. A confirmed proposal is the only thing that turns it on (§4.6
  item 6).
* This service owns the **recurring draft schedule** -- how often Backend opens
  a new proposal table for a coordinator to review. It never assigns anything.
  A due run produces a BUILDING/READY batch and stops there; the tickets in it
  stay unassigned until a human confirms.

They are separate rows in separate tables with separate APIs, and neither reads
the other's interval. That separation is the whole point: the previous UI
labelled `activation_delay` as a repeat, which told coordinators the system was
going to show them another table when it was in fact going to start assigning.

A due run is claimed under a row lock and advances `next_run_at` **before** it
builds anything. A worker that dies mid-build therefore skips one round rather
than retrying the same due time forever, and two workers cannot both fire it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models.assignment_proposal import AssignmentProposalBatch
from src.database.models.assignment_schedule import AssignmentProposalSchedule
from src.models.api.errors import (
    CONFLICT_VERSION,
    INVALID_STATUS_TRANSITION,
    TICKET_NOT_FOUND,
    DomainError,
)
from src.models.enums import ProposalBatchCreatedBy, ProposalBatchStatus, ProposalScheduleInterval
from src.services.assignment_proposal_service import AssignmentProposalService
from src.services.assignment_support import AssignmentSideEffects

logger = logging.getLogger(__name__)

#: How long each interval actually is. Kept next to the enum it measures so a
#: new interval cannot be added without deciding what it means.
INTERVALS: dict[str, timedelta] = {
    ProposalScheduleInterval.HOURS_2.value: timedelta(hours=2),
    ProposalScheduleInterval.DAY_1.value: timedelta(days=1),
    ProposalScheduleInterval.DAYS_3.value: timedelta(days=3),
}

#: The result-modal option meaning "do not repeat". Stored on the batch so a
#: history record can say the coordinator was asked and declined, which is a
#: different fact from never having been asked.
NO_REPEAT = "NONE"

ACTIVE_BATCH_STATUSES = (ProposalBatchStatus.BUILDING.value, ProposalBatchStatus.READY.value)

#: `audit_logs.entity_id` is a non-null UUID and this schedule is a singleton
#: with an integer key, so it gets one stable derived id rather than a magic
#: literal. Deterministic, so every deployment's audit trail agrees.
SCHEDULE_ENTITY_ID = uuid5(NAMESPACE_URL, "fixit:assignment-proposal-schedule")


@dataclass
class ScheduleRunReport:
    """What one worker pass did with the schedule."""

    due: bool = False
    batches_created: int = 0
    #: Due, but a batch was already open. Creating a second one would give the
    #: coordinator two tables drawing on the same queue.
    skipped_active_batch: int = 0
    #: Due, but nothing was eligible. Section 2: record the next due time and
    #: wait, rather than leaving an empty table on the screen every interval.
    skipped_no_work: int = 0


class AssignmentScheduleService:
    def __init__(self, db: Session, proposals: AssignmentProposalService | None = None) -> None:
        self.db = db
        self.side_effects = AssignmentSideEffects(db)
        self._proposals = proposals

    @property
    def proposals(self) -> AssignmentProposalService:
        if self._proposals is None:
            self._proposals = AssignmentProposalService(self.db)
        return self._proposals

    # ------------------------------------------------------------------
    # Reads and configuration.
    # ------------------------------------------------------------------

    def get(self) -> AssignmentProposalSchedule:
        return self._row()

    def update(
        self,
        actor_user_id: UUID,
        *,
        enabled: bool,
        interval: str | None,
        expected_version: int | None = None,
        after_batch_id: UUID | None = None,
    ) -> AssignmentProposalSchedule:
        """Configure the repeat, optionally recording which batch it followed.

        `after_batch_id` is the result modal talking: the coordinator has just
        confirmed that batch and is answering "and then what?". It is written to
        the batch once and never rewritten, because a history record claiming a
        different schedule than the one chosen is worse than one saying nothing.
        """
        if enabled and interval not in INTERVALS:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Lịch lặp lại phải chọn một chu kỳ: mỗi 2 giờ, mỗi 1 ngày hoặc mỗi 3 ngày.",
                400,
            )
        try:
            row = self._row(lock=True)
            if expected_version is not None and expected_version != row.version:
                raise DomainError(
                    CONFLICT_VERSION,
                    "Lịch lặp lại vừa được người khác thay đổi, vui lòng tải lại.",
                    409,
                )
            now = datetime.now(UTC)
            before = {"enabled": row.enabled, "interval_code": row.interval_code}

            row.enabled = enabled
            row.interval_code = interval if enabled else None
            # A fresh window starts now: the coordinator has just dealt with the
            # current queue, so the next draft is due one full interval later.
            row.next_run_at = now + INTERVALS[interval] if enabled else None
            row.configured_by_user_id = actor_user_id
            row.version += 1
            row.updated_at = now

            if after_batch_id is not None:
                self._record_followup(after_batch_id, enabled, interval, now)

            self.side_effects.audit(
                actor_user_id,
                "UPDATE_ASSIGNMENT_PROPOSAL_SCHEDULE",
                "ASSIGNMENT_PROPOSAL_SCHEDULE",
                SCHEDULE_ENTITY_ID,
                before,
                {
                    "enabled": row.enabled,
                    "interval_code": row.interval_code,
                    "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
                    "after_batch_id": str(after_batch_id) if after_batch_id else None,
                },
                None,
                "COORDINATOR",
            )
            self.db.commit()
            return self._row()
        except Exception:
            self.db.rollback()
            raise

    def _record_followup(
        self, batch_id: UUID, enabled: bool, interval: str | None, now: datetime
    ) -> None:
        batch = self.db.get(AssignmentProposalBatch, batch_id, with_for_update=True)
        if batch is None:
            raise DomainError(TICKET_NOT_FOUND, "Bảng đề xuất không tồn tại.", 404)
        if batch.status != ProposalBatchStatus.CONFIRMED.value:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Chỉ ghi được lịch lặp lại cho bảng đề xuất đã xác nhận.",
                409,
            )
        chosen = interval if enabled else NO_REPEAT
        if batch.followup_schedule is not None:
            # A double-click on the final button re-sends the same answer; that
            # is not an error. A *different* answer is, because the first one
            # already became part of the record.
            if batch.followup_schedule == chosen:
                return
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Đợt phân việc này đã ghi nhận lịch lặp lại và không sửa lại được.",
                409,
            )
        batch.followup_schedule = chosen
        batch.followup_schedule_set_at = now

    # ------------------------------------------------------------------
    # The worker.
    # ------------------------------------------------------------------

    def run_due(self, *, now: datetime | None = None) -> ScheduleRunReport:
        now = now or datetime.now(UTC)
        report = ScheduleRunReport()

        try:
            row = self.db.get(AssignmentProposalSchedule, 1, with_for_update=True)
            if row is None or not row.enabled or not row.interval_code or row.next_run_at is None:
                self.db.rollback()
                return report
            if _aware(row.next_run_at) > now:
                self.db.rollback()
                return report

            report.due = True
            active_batch = self.db.scalar(
                select(AssignmentProposalBatch.id).where(
                    AssignmentProposalBatch.status.in_(ACTIVE_BATCH_STATUSES)
                )
            )
            # Claim the slot before doing any work: a build that crashes must
            # skip a round, not re-fire the same due time on the next pass.
            row.last_run_at = now
            row.next_run_at = self._advance(_aware(row.next_run_at), row.interval_code, now)
            interval = row.interval_code
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        if active_batch is not None:
            report.skipped_active_batch = 1
            self._audit_run(now, interval, "SKIPPED_ACTIVE_BATCH", batch_id=active_batch)
            return report

        # Section 2: a scheduled batch belongs to the scheduler, not to a
        # coordinator, and it produces a table to review rather than work.
        batch = self.proposals.create_batch(
            None,
            created_by_type=ProposalBatchCreatedBy.SYSTEM.value,
            skip_if_empty=True,
        )
        if batch is None:
            report.skipped_no_work = 1
            self._audit_run(now, interval, "SKIPPED_NO_WORK")
            return report

        report.batches_created = 1
        self._link_batch(batch.id)
        self._audit_run(now, interval, "BATCH_CREATED", batch_id=batch.id)
        return report

    def _link_batch(self, batch_id: UUID) -> None:
        try:
            row = self.db.get(AssignmentProposalSchedule, 1, with_for_update=True)
            if row is not None:
                row.last_batch_id = batch_id
            self.db.commit()
        except Exception:
            # Bookkeeping only. The batch exists and is reviewable either way,
            # so failing to note its id must not undo the run.
            logger.exception("Could not link scheduled batch %s to the schedule row.", batch_id)
            self.db.rollback()

    @staticmethod
    def _advance(previous: datetime, interval: str, now: datetime) -> datetime:
        """The next due time, never in the past.

        A worker that was down for two days would otherwise come back with a
        due time still behind `now` and fire once per pass until it caught up,
        producing a batch nobody asked for every few seconds.
        """
        step = INTERVALS[interval]
        nxt = previous + step
        if nxt <= now:
            missed = int((now - previous) / step) + 1
            nxt = previous + step * missed
        return nxt

    def _audit_run(
        self, now: datetime, interval: str, outcome: str, *, batch_id: UUID | None = None
    ) -> None:
        try:
            self.side_effects.audit(
                None,
                "RUN_ASSIGNMENT_PROPOSAL_SCHEDULE",
                "ASSIGNMENT_PROPOSAL_SCHEDULE",
                batch_id or SCHEDULE_ENTITY_ID,
                None,
                {"outcome": outcome, "interval_code": interval, "ran_at": now.isoformat()},
                None,
                ProposalBatchCreatedBy.SYSTEM.value,
            )
            self.db.commit()
        except Exception:
            logger.exception("Could not audit the proposal schedule run.")
            self.db.rollback()

    # ------------------------------------------------------------------
    # Internals.
    # ------------------------------------------------------------------

    def _row(self, *, lock: bool = False) -> AssignmentProposalSchedule:
        row = self.db.get(AssignmentProposalSchedule, 1, with_for_update=lock)
        if row is None:
            row = AssignmentProposalSchedule(id=1, enabled=False, interval_code=None, version=1)
            self.db.add(row)
            self.db.flush()
        return row


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


__all__ = [
    "INTERVALS",
    "NO_REPEAT",
    "SCHEDULE_ENTITY_ID",
    "AssignmentScheduleService",
    "ScheduleRunReport",
]
