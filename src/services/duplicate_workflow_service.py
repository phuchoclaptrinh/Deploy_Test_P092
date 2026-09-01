"""Coordinator-initiated duplicate linking.

The manual counterpart to the Agent's duplicate stage. Where the pipeline links
a duplicate it judged, this is a coordinator linking one they found -- same
outcome on the ticket, different authority behind it, and no analysis session
involved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from src.database.models.ticket import Ticket
from src.models.api.errors import (
    INVALID_STATUS_TRANSITION,
    TICKET_NOT_FOUND,
    DomainError,
)
from src.models.enums import TicketStatus
from src.repositories.ticket_repository import TicketRepository
from src.services.assignment_support import AssignmentSideEffects
from src.services.emergency_review_guard import assert_emergency_review_not_pending

TERMINAL_TICKET_STATUSES = {
    TicketStatus.COMPLETED,
    TicketStatus.CANCELLED,
    TicketStatus.INVALID,
    TicketStatus.UNRESOLVABLE,
    TicketStatus.LINKED_DUPLICATE,
}


class DuplicateWorkflowService:
    """Coordinator-initiated duplicate linking.

    Linking only. There is no resident appeal and no Building Management
    dispute-resolution step: if the link is wrong, a coordinator corrects the
    ticket through the normal review actions.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.tickets = TicketRepository(db)
        self.side_effects = AssignmentSideEffects(db)

    def link_duplicate(self, actor_user_id: UUID, ticket_id: UUID, master_ticket_id: UUID, reason: str) -> Ticket:
        if ticket_id == master_ticket_id:
            raise DomainError(INVALID_STATUS_TRANSITION, "Không thể đánh dấu trùng với chính ticket này.", 409)
        try:
            ticket = self._locked_ticket(ticket_id)
            # The gate sits in front of duplicate processing precisely so an
            # emergency is not folded into another ticket before a human has
            # looked at it. That holds for a manual link too.
            assert_emergency_review_not_pending(self.db, ticket_id)
            master = self._locked_ticket(master_ticket_id)
            if ticket.status in TERMINAL_TICKET_STATUSES:
                raise DomainError(INVALID_STATUS_TRANSITION, "Ticket này không thể liên kết duplicate.", 409)
            if master.status in {TicketStatus.CANCELLED, TicketStatus.INVALID, TicketStatus.UNRESOLVABLE}:
                raise DomainError(INVALID_STATUS_TRANSITION, "Ticket gốc không còn hợp lệ để liên kết.", 409)

            old = ticket.status
            resolved_master_id = master.duplicate_of_ticket_id or master.id
            now = datetime.now(UTC)
            ticket.duplicate_of_ticket_id = resolved_master_id
            ticket.duplicate_linked_at = now
            ticket.duplicate_reason = reason.strip() or "Coordinator linked duplicate ticket."
            ticket.status = TicketStatus.LINKED_DUPLICATE
            ticket.auto_assignment_paused = True
            ticket.auto_assignment_pause_reason = "Linked duplicate"
            ticket.version += 1
            self.tickets.append_status_history(
                ticket,
                from_status=old,
                to_status=TicketStatus.LINKED_DUPLICATE,
                changed_by=actor_user_id,
                reason=reason.strip() or "Coordinator linked duplicate ticket.",
            )
            self.side_effects.notify_unit(
                ticket,
                "TICKET_LINKED_DUPLICATE",
                "Phản ánh đã được gộp với ticket đang xử lý",
                "Ban quản lý xác định phản ánh này trùng với một phản ánh khác và sẽ theo dõi theo ticket gốc.",
            )
            self.side_effects.audit(
                actor_user_id,
                "LINK_DUPLICATE",
                "TICKET",
                ticket.id,
                {"status": old.value, "duplicate_of_ticket_id": None},
                {"status": ticket.status.value, "duplicate_of_ticket_id": str(resolved_master_id)},
                reason,
                "COORDINATOR",
            )
            self.db.commit()
            return self.tickets.get_coordinator_visible_ticket(ticket.id) or ticket
        except Exception:
            self.db.rollback()
            raise

    def _locked_ticket(self, ticket_id: UUID) -> Ticket:
        # Human coordinator action: a ticket still under AI analysis is not
        # visible here and cannot be linked by guessing its ID.
        ticket = self.tickets.get_coordinator_visible_ticket(ticket_id, lock=True)
        if ticket is None:
            raise DomainError(TICKET_NOT_FOUND, "Ticket không tồn tại.", 404)
        return ticket


__all__ = ["TERMINAL_TICKET_STATUSES", "DuplicateWorkflowService"]
