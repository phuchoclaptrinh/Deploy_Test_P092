"""Handing a released ticket back to the automatic path, or to a human.

Called when an assignment stops being active without the work being done: a
technician rejected it, or Building Management reassigned it. §9 lists
"Assignment rejection/reassignment behavior" as preserved, and this module is
what preserves it under the new architecture -- the old grace-window AI job is
gone, so the recovery path is simply a fresh dispatch event.

Three outcomes, and they are the same three the old path had:

* **Automatic Assignment is on and the ticket is still eligible** -> a new
  dispatch event. The technician who just said no is already on the ticket's
  exclusion list (`AssignmentEndReason.TECHNICIAN_REJECTED`), so the next pass
  routes around them without any special handling here.
* **Past the reassignment cap** -> paused with a reason and Building Management
  is told. A ticket that three technicians have declined is not a scheduling
  problem any more.
* **Automatic Assignment is off, or the ticket is no longer eligible** ->
  nothing. It sits in the manager queue, which is where §2 says tickets that do
  not meet the automatic conditions belong.

No commit here. The caller is inside a lifecycle transaction (a rejection, a
timeout sweep) and splitting that in two would let a crash leave the assignment
closed with nothing scheduled to replace it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.database.models.dispatch import DispatchEvent
from src.database.models.ticket import Ticket
from src.dispatch.enqueue import automatic_assignment_enabled, enqueue
from src.models.enums import DispatchEventStatus
from src.services.assignment_support import AssignmentSideEffects

logger = logging.getLogger(__name__)

REASSIGNMENT_CAP_REACHED = "REASSIGNMENT_CAP_REACHED"


def supersede_open_event(db: Session, ticket_id: UUID, *, now: datetime | None = None) -> bool:
    """Close the open dispatch event for a ticket a human has just taken.

    Returns whether one was closed. `SUPERSEDED` rather than `ESCALATED`: the
    ticket was handled, by a person, which is a success for the queue and not
    something to notify anyone about.
    """
    now = now or datetime.now(UTC)
    event = db.scalar(
        select(DispatchEvent).where(DispatchEvent.ticket_id == ticket_id, DispatchEvent.is_open.is_(True))
    )
    if event is None:
        return False
    event.status = DispatchEventStatus.SUPERSEDED.value
    event.is_open = False
    event.decided_at = now
    return True


def requeue_after_release(db: Session, ticket: Ticket, *, now: datetime | None = None) -> DispatchEvent | None:
    """Put a released ticket back on the automatic path, if it belongs there."""
    now = now or datetime.now(UTC)
    settings = get_settings()

    if ticket.reassignment_count > settings.assignment_reassignment_cap:
        _pause(db, ticket, REASSIGNMENT_CAP_REACHED)
        return None
    if not automatic_assignment_enabled(db):
        return None
    return enqueue(db, ticket, now=now)


def _pause(db: Session, ticket: Ticket, reason: str) -> None:
    if ticket.auto_assignment_paused and ticket.auto_assignment_pause_reason == reason:
        return
    ticket.auto_assignment_paused = True
    ticket.auto_assignment_pause_reason = reason
    ticket.version += 1
    side_effects = AssignmentSideEffects(db)
    side_effects.audit(
        None,
        "AUTO_ASSIGNMENT_PAUSED",
        "TICKET",
        ticket.id,
        None,
        {"reason": reason, "reassignment_count": ticket.reassignment_count},
        None,
        "SYSTEM",
    )
    side_effects.notify_coordinators(
        ticket,
        "ASSIGNMENT_MANUAL_REQUIRED",
        "Cần phân việc thủ công",
        "Một phản ánh đã vượt số lần phân công lại cho phép và cần Ban quản lý xử lý.",
        {"reason": reason, "reassignment_count": ticket.reassignment_count},
    )


__all__ = [
    "REASSIGNMENT_CAP_REACHED",
    "requeue_after_release",
    "supersede_open_event",
]
