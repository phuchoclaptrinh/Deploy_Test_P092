"""Durable timeout sweeps for V4 operational workflow."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from src.database.models.notification import Notification
from src.database.models.ticket_assignment import TicketAssignment
from src.database.models.user_profile import UserProfile
from src.models.enums import AssignmentStatus, NotificationChannel, NotificationStatus, UserRole
from src.services.agent_question_service import AgentQuestionService
from src.services.assignment_support import AssignmentSideEffects
from src.services.assignment_trigger_service import AssignmentTriggerService

REASSIGNMENT_CAP = 3


class OperationalTimeoutService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.side_effects = AssignmentSideEffects(db)

    def sweep(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        resident_question_timeouts = AgentQuestionService(self.db).handle_timeouts(now)
        technician_warnings = self._send_assignment_acceptance_warnings(now)
        technician_reassignments = self._expire_silent_assignments(now)
        return {
            "resident_question_timeouts": resident_question_timeouts,
            "technician_acceptance_warnings": technician_warnings,
            "technician_acceptance_reassignments": technician_reassignments,
        }

    def _send_assignment_acceptance_warnings(self, now: datetime) -> int:
        assignments = list(
            self.db.scalars(
                select(TicketAssignment)
                .where(
                    TicketAssignment.is_active.is_(True),
                    TicketAssignment.status == AssignmentStatus.ASSIGNED,
                    TicketAssignment.accepted_at.is_(None),
                    TicketAssignment.warning_sent_at.is_(None),
                    TicketAssignment.acceptance_warning_at.is_not(None),
                    TicketAssignment.acceptance_warning_at <= now,
                )
                .options(joinedload(TicketAssignment.ticket))
                .with_for_update(of=TicketAssignment, skip_locked=True)
            )
        )

        for assignment in assignments:
            assignment.warning_sent_at = now
            assignment.updated_at = now
            self.side_effects.notify_technician(
                assignment,
                "ASSIGNMENT_ACCEPTANCE_WARNING",
                "Vui lòng xác nhận nhận việc",
                "Công việc đã gần tới hạn xác nhận. Vui lòng nhận việc hoặc từ chối để BQL phân lại.",
            )

        self.db.commit()
        return len(assignments)

    def _expire_silent_assignments(self, now: datetime) -> int:
        assignments = list(
            self.db.scalars(
                select(TicketAssignment)
                .where(
                    TicketAssignment.is_active.is_(True),
                    TicketAssignment.status == AssignmentStatus.ASSIGNED,
                    TicketAssignment.accepted_at.is_(None),
                    TicketAssignment.acceptance_reassign_at.is_not(None),
                    TicketAssignment.acceptance_reassign_at <= now,
                )
                .options(joinedload(TicketAssignment.ticket))
                .with_for_update(of=TicketAssignment, skip_locked=True)
            )
        )

        for assignment in assignments:
            ticket = assignment.ticket
            old_status = assignment.status
            assignment.status = AssignmentStatus.REASSIGNED
            assignment.is_active = False
            assignment.ended_at = now
            assignment.end_reason = "ACCEPTANCE_TIMEOUT"
            assignment.updated_at = now
            ticket.reassignment_count += 1
            ticket.version += 1
            # Whether this pauses the ticket or schedules another AI round is
            # §6.2/§6.3 policy, and it lives in AssignmentTriggerService. Setting
            # `paused` here as well would make every timeout manual.
            ticket.auto_assignment_paused = False
            ticket.auto_assignment_pause_reason = None

            self.side_effects.audit(
                None,
                "ACCEPTANCE_TIMEOUT_REASSIGN",
                "TICKET_ASSIGNMENT",
                assignment.id,
                {"status": old_status.value},
                {"status": assignment.status.value, "ticket_id": str(assignment.ticket_id)},
                "Technician did not accept assignment before deadline.",
                "SYSTEM",
            )
            self.side_effects.notify_unit(
                ticket,
                "ASSIGNMENT_ACCEPTANCE_TIMEOUT",
                "BQL sẽ phân lại kỹ thuật viên",
                "Kỹ thuật viên chưa xác nhận nhận việc đúng hạn, Ban quản lý sẽ phân công lại.",
            )
            self.db.flush()
            # §6.3: auto on and under the cap picks a new technician straight
            # away; otherwise the trigger service pauses the ticket and alerts
            # the coordinator.
            AssignmentTriggerService(self.db).on_acceptance_timeout(assignment)

        self.db.commit()
        return len(assignments)

    def _notify_coordinators(self, ticket_id, event: str, title: str, body: str) -> None:
        coordinator_ids = list(
            self.db.scalars(
                select(UserProfile.user_id).where(
                    UserProfile.role == UserRole.COORDINATOR,
                    UserProfile.is_active.is_(True),
                )
            )
        )
        for user_id in coordinator_ids:
            self.db.add(
                Notification(
                    recipient_user_id=user_id,
                    ticket_id=ticket_id,
                    notification_type=event,
                    channel=NotificationChannel.IN_APP,
                    title=title,
                    body=body,
                    payload={"ticket_id": str(ticket_id)},
                    status=NotificationStatus.PENDING,
                )
            )
