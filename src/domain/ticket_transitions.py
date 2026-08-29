"""Ticket workflow rules shared by coordinator and technician services."""

from src.models.api.errors import INVALID_STATUS_TRANSITION, DomainError
from src.models.enums import TicketStatus


def require_assignable_ticket(status: TicketStatus) -> None:
    if status != TicketStatus.APPROVED:
        raise DomainError(INVALID_STATUS_TRANSITION, "Chỉ ticket đã duyệt mới có thể phân công.", 409)


def require_ticket_status(status: TicketStatus, expected: TicketStatus, message: str) -> None:
    if status != expected:
        raise DomainError(INVALID_STATUS_TRANSITION, message, 409)
