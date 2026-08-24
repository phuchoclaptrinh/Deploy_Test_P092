"""When a DIRECT assignment job is created (§4.2, §6.2).

Four triggers, and the differences between them are the business rules:

* **A newly eligible ticket** waits out `auto_assignment_settings.activation_delay`
  from `approved_at`, so a coordinator has a window to act first.
* **A P3 ticket** skips that delay entirely. P3 exists because five minutes is
  the promise; making it queue behind a three-day activation delay would empty
  the word of meaning.
* **A rejection** starts the §6.2 grace window — immediate for P3, 300 seconds
  for P1/P2 so the coordinator can cancel the job and assign by hand.
* **An acceptance timeout** behaves like a rejection for scheduling purposes,
  but records `REASSIGN_SILENT` so the audit can tell "said no" from "said
  nothing".

Two gates apply to all of them. The global switch being off means no AI job is
created at all — the ticket waits in the manual queue. And past the
reassignment cap the ticket is paused with a reason instead of being handed to
the model for a fourth try (§11 assumption 4).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.database.models.assignment_proposal import AIAssignmentJob
from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.notification import Notification
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.database.models.user_profile import UserProfile
from src.models.enums import (
    ActivationDelay,
    AssignmentJobTrigger,
    NotificationChannel,
    NotificationStatus,
    Priority,
    UserRole,
)
from src.services.assignment_candidates import AssignmentCandidateService
from src.services.assignment_job_service import AssignmentJobService
from src.services.assignment_support import AssignmentSideEffects

logger = logging.getLogger(__name__)

ACTIVATION_DELAYS = {
    ActivationDelay.IMMEDIATE: timedelta(0),
    ActivationDelay.HOURS_2: timedelta(hours=2),
    ActivationDelay.HOURS_5: timedelta(hours=5),
    ActivationDelay.DAY_1: timedelta(days=1),
    ActivationDelay.DAYS_3: timedelta(days=3),
}

# Pre-v4 spellings kept readable so an existing settings row still resolves.
LEGACY_DELAY_ALIASES = {
    "2H": ActivationDelay.HOURS_2,
    "5H": ActivationDelay.HOURS_5,
    "1D": ActivationDelay.DAY_1,
    "3D": ActivationDelay.DAYS_3,
}

REASSIGNMENT_CAP_REACHED = "REASSIGNMENT_CAP_REACHED"
AUTO_ASSIGNMENT_DISABLED = "AUTO_ASSIGNMENT_DISABLED"


def parse_activation_delay(value: str | None) -> ActivationDelay:
    if not value:
        return ActivationDelay.IMMEDIATE
    if value in LEGACY_DELAY_ALIASES:
        return LEGACY_DELAY_ALIASES[value]
    try:
        return ActivationDelay(value)
    except ValueError:
        logger.warning("Unknown activation_delay %r; treating as IMMEDIATE.", value)
        return ActivationDelay.IMMEDIATE


class AssignmentTriggerService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.candidates = AssignmentCandidateService(db)
        self.jobs = AssignmentJobService(db)
        self.side_effects = AssignmentSideEffects(db)

    # ------------------------------------------------------------------
    # The global switch (§7.6).
    # ------------------------------------------------------------------

    def auto_settings(self) -> AutoAssignmentSetting:
        row = self.db.get(AutoAssignmentSetting, 1)
        if row is None:
            row = AutoAssignmentSetting(id=1, enabled=False, activation_delay=ActivationDelay.IMMEDIATE.value, version=1)
            self.db.add(row)
            self.db.flush()
        return row

    def auto_enabled(self) -> bool:
        return bool(self.auto_settings().enabled)

    def activation_delay(self) -> timedelta:
        return ACTIVATION_DELAYS[parse_activation_delay(self.auto_settings().activation_delay)]

    # ------------------------------------------------------------------
    # Trigger 1 and 2: newly eligible tickets.
    # ------------------------------------------------------------------

    def enqueue_newly_eligible(self, *, now: datetime | None = None, limit: int = 50) -> list[AIAssignmentJob]:
        """§4.2 rows 1-2. Returns the jobs actually created."""
        now = now or datetime.now(UTC)
        if not self.auto_enabled():
            return []

        delay = self.activation_delay()
        created: list[AIAssignmentJob] = []
        cases_done: set[UUID] = set()

        for ticket in self.db.scalars(
            self.candidates.eligible_ticket_query().order_by(Ticket.created_at.asc()).limit(limit)
        ):
            if self._cap_reached(ticket):
                continue
            # §4.2: P3 ignores the configured delay; everything else waits.
            if ticket.priority is not Priority.P3:
                approved_at = ticket.approved_at or ticket.created_at
                if approved_at.tzinfo is None:
                    approved_at = approved_at.replace(tzinfo=UTC)
                if approved_at + delay > now:
                    continue

            case = self._open_case_for(ticket)
            if case is not None:
                if case.id in cases_done:
                    continue
                cases_done.add(case.id)
                draft = self.candidates.case_draft(
                    case, max_members=self.settings.incident_case_max_ticket_count
                )
                if draft is None:
                    continue
            else:
                draft = self.candidates.ticket_draft(ticket)

            job = self.jobs.schedule_direct(
                draft,
                trigger=AssignmentJobTrigger.INITIAL_AUTO,
                execute_after=now,
                reassignment_count=ticket.reassignment_count,
            )
            if job is not None:
                created.append(job)

        self.db.commit()
        return created

    def _open_case_for(self, ticket: Ticket) -> IncidentCase | None:
        """§4.2: a case is only a work item once Backend created it officially."""
        case_id = self.db.scalar(
            select(IncidentCaseMember.case_id).where(IncidentCaseMember.ticket_id == ticket.id)
        )
        if case_id is None:
            return None
        case = self.db.get(IncidentCase, case_id)
        if case is None or case.status != "OPEN":
            return None
        return case

    # ------------------------------------------------------------------
    # Trigger 3: technician rejected (§6.1-§6.2).
    # ------------------------------------------------------------------

    def on_rejected(self, assignment: TicketAssignment) -> AIAssignmentJob | None:
        return self._reassignment_job(assignment, trigger=AssignmentJobTrigger.REASSIGN_REJECTED)

    # ------------------------------------------------------------------
    # Trigger 4: acceptance timeout (§6.3).
    # ------------------------------------------------------------------

    def on_acceptance_timeout(self, assignment: TicketAssignment) -> AIAssignmentJob | None:
        return self._reassignment_job(assignment, trigger=AssignmentJobTrigger.REASSIGN_SILENT)

    def _reassignment_job(
        self,
        assignment: TicketAssignment,
        *,
        trigger: AssignmentJobTrigger,
    ) -> AIAssignmentJob | None:
        ticket = assignment.ticket or self.db.get(Ticket, assignment.ticket_id)
        if ticket is None:
            return None

        if self._cap_reached(ticket):
            return None
        if not self.auto_enabled():
            self._pause(ticket, AUTO_ASSIGNMENT_DISABLED)
            return None
        if not self.candidates.is_ticket_eligible(ticket):
            return None

        draft = self.candidates.ticket_draft(ticket)
        if not draft.has_candidates:
            # §4.3 rule 5: exclusions emptied the list, so a human takes over.
            self._pause(ticket, "NO_CANDIDATES")
            self._alert_coordinators(ticket, "NO_CANDIDATES")
            return None

        # §6.2: P3 runs now; P1/P2 get the 300-second window the coordinator can
        # use to cancel the job and assign by hand.
        execute_after = self.jobs.grace_execute_after(ticket.priority)
        job = self.jobs.schedule_direct(
            draft,
            trigger=trigger,
            execute_after=execute_after,
            previous_assignment_id=assignment.id,
            reassignment_count=ticket.reassignment_count,
        )
        if job is not None:
            self._notify_coordinators_scheduled(ticket, job, trigger)
        return job

    # ------------------------------------------------------------------
    # Caps and pausing.
    # ------------------------------------------------------------------

    def _cap_reached(self, ticket: Ticket) -> bool:
        """§11 assumption 4: three reassignments are allowed; the fourth stops
        automatic handling for this ticket only."""
        if not self.jobs.reassignment_cap_reached(ticket.reassignment_count):
            return False
        self._pause(ticket, REASSIGNMENT_CAP_REACHED)
        self._alert_coordinators(ticket, REASSIGNMENT_CAP_REACHED)
        return True

    def _pause(self, ticket: Ticket, reason: str) -> None:
        if ticket.auto_assignment_paused and ticket.auto_assignment_pause_reason == reason:
            return
        ticket.auto_assignment_paused = True
        ticket.auto_assignment_pause_reason = reason
        ticket.version += 1
        self.side_effects.audit(
            None,
            "AUTO_ASSIGNMENT_PAUSED",
            "TICKET",
            ticket.id,
            None,
            {"reason": reason, "reassignment_count": ticket.reassignment_count},
            None,
            "SYSTEM",
        )

    def _alert_coordinators(self, ticket: Ticket, reason: str) -> None:
        for user_id in self._coordinator_ids():
            self.db.add(
                Notification(
                    recipient_user_id=user_id,
                    ticket_id=ticket.id,
                    notification_type="ASSIGNMENT_MANUAL_REQUIRED",
                    channel=NotificationChannel.IN_APP,
                    title="Cần phân việc thủ công",
                    body="Một phản ánh không còn đủ điều kiện phân việc tự động.",
                    payload={"ticket_id": str(ticket.id), "reason": reason},
                    status=NotificationStatus.PENDING,
                )
            )

    def _notify_coordinators_scheduled(
        self,
        ticket: Ticket,
        job: AIAssignmentJob,
        trigger: AssignmentJobTrigger,
    ) -> None:
        """§6.2: during the window the coordinator sees why, and when the AI
        will act, so cancelling is a real option rather than a race."""
        for user_id in self._coordinator_ids():
            self.db.add(
                Notification(
                    recipient_user_id=user_id,
                    ticket_id=ticket.id,
                    notification_type="ASSIGNMENT_JOB_SCHEDULED",
                    channel=NotificationChannel.IN_APP,
                    title="Hệ thống sẽ phân lại kỹ thuật viên",
                    body="Một phản ánh cần đổi người xử lý. Bạn có thể hủy job để phân công thủ công.",
                    payload={
                        "ticket_id": str(ticket.id),
                        "job_id": str(job.id),
                        "trigger": trigger.value,
                        "execute_after": job.execute_after.isoformat() if job.execute_after else None,
                    },
                    status=NotificationStatus.PENDING,
                )
            )

    def _coordinator_ids(self) -> list[UUID]:
        return list(
            self.db.scalars(
                select(UserProfile.user_id).where(
                    UserProfile.role == UserRole.COORDINATOR,
                    UserProfile.is_active.is_(True),
                )
            )
        )


__all__ = [
    "ACTIVATION_DELAYS",
    "AUTO_ASSIGNMENT_DISABLED",
    "REASSIGNMENT_CAP_REACHED",
    "AssignmentTriggerService",
    "parse_activation_delay",
]
