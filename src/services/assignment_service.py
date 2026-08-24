"""Coordinator and Technician assignment workflow operations."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from src.database.models.attachment import TicketAttachment
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.domain.assignment_transitions import require_assignment_transition
from src.domain.ticket_transitions import require_assignable_ticket, require_ticket_status
from src.models.api.errors import (
    ACTIVE_ASSIGNMENT_EXISTS,
    ASSIGNMENT_NOT_FOUND,
    COMPLETION_EVIDENCE_REQUIRED,
    INVALID_ATTACHMENT,
    TECHNICIAN_NOT_ELIGIBLE,
    TECHNICIAN_NOT_FOUND,
    TICKET_NOT_FOUND,
    DomainError,
)
from src.models.enums import (
    AssignmentEndReason,
    AssignmentStatus,
    AttachmentType,
    ImageQualityStatus,
    Priority,
    TicketStatus,
)
from src.repositories.assignment_repository import AssignmentRepository
from src.repositories.technician_repository import TechnicianRepository
from src.repositories.ticket_repository import TicketRepository
from src.repositories.upload_session_repository import UploadSessionRepository
from src.services.assignment_job_service import AssignmentJobService
from src.services.assignment_support import AssignmentEvidenceVerifier, AssignmentSideEffects
from src.services.assignment_trigger_service import AssignmentTriggerService
from src.services.storage_service import StorageService


def set_acceptance_deadlines(ticket: Ticket, assignment: TicketAssignment, *, is_reassignment: bool = False) -> None:
    """Stamp the §6.4 acceptance clock onto one assignment.

    The `MAX(...)` in every row of the §6.4 table is the point: a ticket approved
    or assigned long after it was reported still owes its technician the full
    floor — one hour on P1, thirty minutes on P2, five minutes on P3 — before a
    worker may take the work away (§12 scenario 23).

    A reassignment starts a *new* cycle at its own `assigned_at`; the previous
    technician's expired clock is never inherited, or the replacement would be
    reassigned the instant they were given the job.
    """
    assigned_at = assignment.assigned_at or datetime.now(UTC)
    if assigned_at.tzinfo is None:
        assigned_at = assigned_at.replace(tzinfo=UTC)
    if is_reassignment:
        cycle_started_at = assigned_at
    else:
        cycle_started_at = ticket.sla_started_at or ticket.created_at or assigned_at
    if cycle_started_at.tzinfo is None:
        cycle_started_at = cycle_started_at.replace(tzinfo=UTC)
    assignment.cycle_started_at = cycle_started_at

    if ticket.priority == Priority.P1:
        assignment.acceptance_warning_at = max(cycle_started_at + timedelta(hours=48), assigned_at)
        assignment.acceptance_reassign_at = max(cycle_started_at + timedelta(hours=49), assigned_at + timedelta(hours=1))
    elif ticket.priority == Priority.P2:
        assignment.acceptance_warning_at = max(cycle_started_at + timedelta(hours=2), assigned_at)
        assignment.acceptance_reassign_at = max(cycle_started_at + timedelta(hours=2, minutes=30), assigned_at + timedelta(minutes=30))
    elif ticket.priority == Priority.P3:
        assignment.acceptance_warning_at = None
        assignment.acceptance_reassign_at = max(cycle_started_at + timedelta(minutes=5), assigned_at + timedelta(minutes=5))
    else:
        # A ticket in manual review can still be assigned by hand before its
        # Priority is settled. It gets the shortest defined window rather than no
        # deadline at all, so the acceptance worker never loses track of it.
        assignment.acceptance_warning_at = None
        assignment.acceptance_reassign_at = assigned_at + timedelta(minutes=5)


class AssignmentService:
    def __init__(self, db: Session, storage_service: StorageService | None = None) -> None:
        self.db = db
        self.assignments = AssignmentRepository(db)
        self.tickets = TicketRepository(db)
        self.uploads = UploadSessionRepository(db)
        self.technicians = TechnicianRepository(db)
        self.storage = storage_service or StorageService()
        self.evidence = AssignmentEvidenceVerifier(db, self.storage)
        self.side_effects = AssignmentSideEffects(db)

    def assign(self, coordinator_user_id: UUID, ticket_id: UUID, technician_id: UUID) -> TicketAssignment:
        try:
            ticket = self._locked_ticket(ticket_id)
            technician = self.technicians.get_technician(technician_id, lock=True)
            if technician is None:
                raise DomainError(TECHNICIAN_NOT_FOUND, "Technician không tồn tại.", 404)
            if not technician.is_active or not technician.is_available:
                raise DomainError(TECHNICIAN_NOT_ELIGIBLE, "Technician không sẵn sàng nhận việc.", 409)
            require_assignable_ticket(ticket.status)
            if self.assignments.get_active_for_ticket(ticket.id, lock=True) is not None:
                raise DomainError(ACTIVE_ASSIGNMENT_EXISTS, "Ticket đã có assignment đang hoạt động.", 409)
            if ticket.category_id and not self.technicians.technician_has_skill(technician.user_id, ticket.category_id):
                raise DomainError(TECHNICIAN_NOT_ELIGIBLE, "Technician không có kỹ năng phù hợp.", 409)

            assignment = self.assignments.create_assignment(
                ticket_id=ticket.id,
                technician_id=technician.user_id,
                assigned_by_user_id=coordinator_user_id,
            )
            set_acceptance_deadlines(ticket, assignment)
            # §4.5: a human just took this ticket. Any AI job still holding it
            # becomes CANCELLED_MANUAL_WON rather than overwriting this decision.
            AssignmentJobService(self.db).cancel_open_jobs_for_ticket(ticket.id)
            self.side_effects.audit(
                coordinator_user_id,
                "ASSIGN_TECHNICIAN",
                "TICKET_ASSIGNMENT",
                assignment.id,
                None,
                {"ticket_id": str(ticket.id), "technician_id": str(technician.user_id)},
                None,
                "COORDINATOR",
            )
            self.side_effects.notify_technician(assignment, "ASSIGNMENT_CREATED", "Bạn có assignment mới", "BQL đã phân công ticket cho bạn.")
            self.side_effects.notify_unit(
                ticket,
                "TICKET_ASSIGNED",
                "Phản ánh đã được gán kỹ thuật viên",
                "Ban quản lý đã phân công kỹ thuật viên xử lý phản ánh.",
            )
            self.db.commit()
            return self.assignments.get_for_technician(assignment.id, technician.user_id) or assignment
        except Exception:
            self.db.rollback()
            raise

    def assign_case(self, coordinator_user_id: UUID, ticket_ids: list[UUID], technician_id: UUID) -> list[TicketAssignment]:
        """Assign every ticket of an incident case to one technician, atomically.

        A case is one unit of work (§7.9): handing some members to a
        technician while the rest are left behind because a later one in the
        loop failed eligibility would split a single incident across two
        people, which is exactly what a case exists to prevent. So every
        precondition -- for the technician and for *each* ticket -- is
        checked, under lock, before anything is written, and the whole case
        commits in one transaction. A rejection here always leaves every
        member exactly as it was; there is no `skipped_ticket_ids` because a
        partial outcome is not an allowed outcome.
        """
        try:
            # §4.5: lock every member in UUID order, the same rule the
            # contract gives PROPOSAL confirm, so a concurrent request
            # touching an overlapping set of tickets cannot deadlock against
            # this one.
            tickets = [self._locked_ticket(ticket_id) for ticket_id in sorted(set(ticket_ids), key=str)]
            technician = self.technicians.get_technician(technician_id, lock=True)
            if technician is None:
                raise DomainError(TECHNICIAN_NOT_FOUND, "Technician không tồn tại.", 404)
            if not technician.is_active or not technician.is_available:
                raise DomainError(TECHNICIAN_NOT_ELIGIBLE, "Technician không sẵn sàng nhận việc.", 409)

            for ticket in tickets:
                require_assignable_ticket(ticket.status)
                if self.assignments.get_active_for_ticket(ticket.id, lock=True) is not None:
                    raise DomainError(ACTIVE_ASSIGNMENT_EXISTS, "Ticket đã có assignment đang hoạt động.", 409)
                if ticket.category_id and not self.technicians.technician_has_skill(technician.user_id, ticket.category_id):
                    raise DomainError(TECHNICIAN_NOT_ELIGIBLE, "Technician không có kỹ năng phù hợp.", 409)

            # Every precondition passed for every member -- now write. Restore
            # creation order for the assignments themselves and their
            # notifications, independent of the UUID lock order above.
            assignments: list[TicketAssignment] = []
            for ticket in sorted(tickets, key=lambda item: item.created_at):
                assignment = self.assignments.create_assignment(
                    ticket_id=ticket.id,
                    technician_id=technician.user_id,
                    assigned_by_user_id=coordinator_user_id,
                )
                set_acceptance_deadlines(ticket, assignment)
                AssignmentJobService(self.db).cancel_open_jobs_for_ticket(ticket.id)
                self.side_effects.audit(
                    coordinator_user_id,
                    "ASSIGN_TECHNICIAN",
                    "TICKET_ASSIGNMENT",
                    assignment.id,
                    None,
                    {"ticket_id": str(ticket.id), "technician_id": str(technician.user_id), "case_assignment": True},
                    None,
                    "COORDINATOR",
                )
                self.side_effects.notify_technician(assignment, "ASSIGNMENT_CREATED", "Bạn có assignment mới", "BQL đã phân công ticket cho bạn.")
                self.side_effects.notify_unit(
                    ticket,
                    "TICKET_ASSIGNED",
                    "Phản ánh đã được gán kỹ thuật viên",
                    "Ban quản lý đã phân công kỹ thuật viên xử lý phản ánh.",
                )
                assignments.append(assignment)

            self.db.commit()
            return [
                self.assignments.get_for_technician(assignment.id, technician.user_id) or assignment
                for assignment in assignments
            ]
        except Exception:
            self.db.rollback()
            raise

    def accept(self, technician_id: UUID, assignment_id: UUID) -> TicketAssignment:
        return self._technician_transition(technician_id, assignment_id, AssignmentStatus.ACCEPTED, "ACCEPT_ASSIGNMENT")

    def start(self, technician_id: UUID, assignment_id: UUID) -> TicketAssignment:
        try:
            assignment = self._locked_assignment(assignment_id, technician_id)
            require_assignment_transition(assignment.status, AssignmentStatus.IN_PROGRESS)
            require_ticket_status(assignment.ticket.status, TicketStatus.APPROVED, "Ticket chưa ở trạng thái sẵn sàng xử lý.")
            old_assignment = assignment.status
            old_ticket = assignment.ticket.status
            now = datetime.now(UTC)
            assignment.status = AssignmentStatus.IN_PROGRESS
            assignment.started_at = now
            assignment.updated_at = now
            assignment.ticket.status = TicketStatus.IN_PROGRESS
            assignment.ticket.started_at = now
            assignment.ticket.version += 1
            self.tickets.append_status_history(
                assignment.ticket,
                from_status=old_ticket,
                to_status=TicketStatus.IN_PROGRESS,
                changed_by=technician_id,
                reason="Technician started work.",
            )
            self.side_effects.notify_unit(assignment.ticket, "TICKET_STARTED", "Phản ánh đang được xử lý", "Kỹ thuật viên đã bắt đầu xử lý.")
            self.side_effects.audit_assignment_transition(technician_id, assignment, old_assignment, "START_ASSIGNMENT")
            self.db.commit()
            return self.assignments.get_for_technician(assignment.id, technician_id) or assignment
        except Exception:
            self.db.rollback()
            raise

    def unable_to_handle(self, technician_id: UUID, assignment_id: UUID, reason: str) -> TicketAssignment:
        if not reason.strip():
            raise DomainError(INVALID_ATTACHMENT, "Lý do không xử lý được là bắt buộc.", 400)
        try:
            assignment = self._locked_assignment(assignment_id, technician_id)
            require_assignment_transition(assignment.status, AssignmentStatus.UNABLE_TO_HANDLE)
            old = assignment.status
            now = datetime.now(UTC)
            assignment.status = AssignmentStatus.UNABLE_TO_HANDLE
            assignment.unable_reason = reason.strip()
            assignment.is_active = False
            assignment.ended_at = now
            assignment.end_reason = AssignmentEndReason.UNABLE_TO_HANDLE.value
            assignment.updated_at = now
            previous = assignment.ticket.status
            assignment.ticket.status = TicketStatus.UNRESOLVABLE
            assignment.ticket.version += 1
            self.tickets.append_status_history(
                assignment.ticket,
                from_status=previous,
                to_status=TicketStatus.UNRESOLVABLE,
                changed_by=technician_id,
                reason=reason.strip(),
            )
            self.side_effects.notify_unit(
                assignment.ticket,
                "TICKET_UNRESOLVABLE",
                "Phản ánh không xử lý được",
                "Kỹ thuật viên báo không thể xử lý phản ánh này.",
            )
            self.side_effects.audit_assignment_transition(technician_id, assignment, old, "UNABLE_TO_HANDLE", reason)
            self.db.commit()
            return assignment
        except Exception:
            self.db.rollback()
            raise

    def reject(self, technician_id: UUID, assignment_id: UUID, reason: str) -> TicketAssignment:
        if not reason.strip():
            raise DomainError(INVALID_ATTACHMENT, "Lý do từ chối nhận việc là bắt buộc.", 400)
        try:
            assignment = self._locked_assignment(assignment_id, technician_id)
            require_assignment_transition(assignment.status, AssignmentStatus.REJECTED)
            old_assignment = assignment.status
            now = datetime.now(UTC)
            assignment.status = AssignmentStatus.REJECTED
            assignment.rejection_reason = reason.strip()
            assignment.is_active = False
            assignment.rejected_at = now
            assignment.ended_at = now
            assignment.end_reason = AssignmentEndReason.TECHNICIAN_REJECTED.value
            assignment.updated_at = now
            assignment.ticket.reassignment_count += 1
            if assignment.ticket.status == TicketStatus.IN_PROGRESS:
                assignment.ticket.status = TicketStatus.APPROVED
            # §6.1 step 4-5: the ticket is only paused when the AI path is over.
            # Pausing unconditionally would make every rejection manual and quietly
            # delete the reassignment flow.
            assignment.ticket.auto_assignment_paused = False
            assignment.ticket.auto_assignment_pause_reason = None
            assignment.ticket.version += 1
            self.side_effects.notify_unit(
                assignment.ticket,
                "ASSIGNMENT_REJECTED",
                "BQL sẽ phân lại kỹ thuật viên",
                "Kỹ thuật viên vừa từ chối nhận việc, Ban quản lý sẽ phân công lại.",
            )
            self.side_effects.audit_assignment_transition(
                technician_id,
                assignment,
                old_assignment,
                "REJECT_ASSIGNMENT",
                reason.strip(),
            )
            self.side_effects.notify_coordinators(
                assignment.ticket,
                "ASSIGNMENT_REJECTED",
                "Kỹ thuật viên từ chối nhận việc",
                "Một kỹ thuật viên vừa từ chối nhận việc kèm lý do.",
                {"assignment_id": str(assignment.id), "reason": reason.strip()[:200]},
            )
            self.db.flush()
            # §6.2: either a new AI job inside the grace window, or the ticket is
            # paused for manual assignment. Both decisions live in one place.
            AssignmentTriggerService(self.db).on_rejected(assignment)
            self.db.commit()
            return assignment
        except Exception:
            self.db.rollback()
            raise

    def complete(self, technician_id: UUID, assignment_id: UUID, note: str, upload_ids: list[UUID]) -> TicketAssignment:
        if not note.strip() or not upload_ids:
            raise DomainError(COMPLETION_EVIDENCE_REQUIRED, "Cần ghi chú xử lý và ít nhất một ảnh hoàn thành.", 400)
        try:
            assignment = self._locked_assignment(assignment_id, technician_id)
            require_assignment_transition(assignment.status, AssignmentStatus.COMPLETED)
            sessions = self.evidence.lock_and_verify_completion_sessions(technician_id, upload_ids)
            old_assignment = assignment.status
            old_ticket = assignment.ticket.status
            now = datetime.now(UTC)
            for session in sessions:
                self.db.add(
                    TicketAttachment(
                        ticket_id=assignment.ticket_id,
                        attachment_type=AttachmentType.TECHNICIAN_COMPLETION,
                        storage_bucket="ticket-attachments",
                        object_path=session.storage_path,
                        mime_type=session.mime_type,
                        size_bytes=session.file_size,
                        uploaded_by=technician_id,
                        image_quality_status=ImageQualityStatus.READABLE,
                    )
                )
            self.uploads.mark_consumed(sessions)
            assignment.status = AssignmentStatus.COMPLETED
            assignment.completion_note = note.strip()
            assignment.is_active = False
            assignment.completed_at = now
            assignment.ended_at = now
            assignment.end_reason = AssignmentEndReason.COMPLETED.value
            assignment.updated_at = now
            assignment.ticket.status = TicketStatus.COMPLETED
            assignment.ticket.completed_at = now
            assignment.ticket.version += 1
            self.tickets.append_status_history(
                assignment.ticket,
                from_status=old_ticket,
                to_status=TicketStatus.COMPLETED,
                changed_by=technician_id,
                reason="Technician completed assignment.",
            )
            self.side_effects.notify_unit(assignment.ticket, "TICKET_COMPLETED", "Phản ánh đã hoàn thành", "Kỹ thuật viên đã hoàn thành xử lý.")
            self.side_effects.audit_assignment_transition(technician_id, assignment, old_assignment, "COMPLETE_ASSIGNMENT")
            self.db.commit()
            return self.assignments.get_for_technician(assignment.id, technician_id) or assignment
        except Exception:
            self.db.rollback()
            raise

    def _technician_transition(
        self,
        technician_id: UUID,
        assignment_id: UUID,
        to_status: AssignmentStatus,
        action: str,
    ) -> TicketAssignment:
        try:
            assignment = self._locked_assignment(assignment_id, technician_id)
            require_assignment_transition(assignment.status, to_status)
            old = assignment.status
            now = datetime.now(UTC)
            assignment.status = to_status
            assignment.updated_at = now
            if to_status == AssignmentStatus.ACCEPTED:
                assignment.accepted_at = now
            self.side_effects.audit_assignment_transition(technician_id, assignment, old, action)
            self.db.commit()
            return self.assignments.get_for_technician(assignment.id, technician_id) or assignment
        except Exception:
            self.db.rollback()
            raise

    def _locked_ticket(self, ticket_id: UUID) -> Ticket:
        # Coordinator-initiated assignment only; a private AI-phase ticket is
        # not assignable and must not be discoverable through this path.
        ticket = self.tickets.get_coordinator_visible_ticket(ticket_id, lock=True)
        if ticket is None:
            raise DomainError(TICKET_NOT_FOUND, "Ticket không tồn tại.", 404)
        return ticket

    def _locked_assignment(self, assignment_id: UUID, technician_id: UUID) -> TicketAssignment:
        assignment = self.assignments.get_for_technician(assignment_id, technician_id, lock=True)
        if assignment is None:
            raise DomainError(ASSIGNMENT_NOT_FOUND, "Assignment không tồn tại.", 404)
        return assignment
