"""Automatic approval, and the hand-off to dispatch (§2).

§2's flow has a step that used to be a person:

    resident submits -> AI classification -> classification is eligible
      -> not duplicate -> not P3 -> **skip grouping** -> assign

"Automatically approved" is that step. With the switch on, a ticket the AI
classified confidently does not wait in the manager queue for someone to press
Duyệt; it is approved by the system and enqueued for dispatch in the same
transaction that recorded the classification.

Three things this module is careful about:

* **The switch decides, not the classification.** With Automatic Assignment
  off, nothing here fires and every ticket keeps waiting for a human approval,
  exactly as before. §2 makes the toggle the whole gate.

* **Grouping is skipped, not disabled.** `run.grouping_status` is set to
  `GROUPING_SKIPPED_AUTOMATIC` rather than left `PENDING`, so the background
  grouping stage does not later fold an already-assigned ticket into a case.
  Grouping itself is untouched and still runs for everything Building
  Management handles by hand -- §9 preserves it for Visual Assignment.

* **The audit actor is SYSTEM.** An automatic approval has no coordinator
  behind it, and borrowing whoever last touched the ticket would put a person's
  name on a decision they were not present for.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from src.database.models.ticket import Ticket
from src.database.models.ticket_status_history import TicketStatusHistory
from src.dispatch.enqueue import automatic_assignment_enabled, enqueue, ticket_is_dispatchable
from src.models.enums import ClassificationStatus, Priority, TicketStatus
from src.services.assignment_support import AssignmentSideEffects
from src.services.p3_review_guard import p3_review_is_pending

logger = logging.getLogger(__name__)

#: Written to `ai_analysis_runs.grouping_status` for a ticket the automatic path
#: took. Distinct from `GROUPING_NOT_ELIGIBLE`, which means the category never
#: groups: this one means the category *would* have grouped, and §2 chose speed.
GROUPING_SKIPPED_AUTOMATIC = "SKIPPED_AUTOMATIC"


def eligible_for_automatic_approval(db: Session, ticket: Ticket) -> bool:
    """§2's classification gate, before approval rather than after it.

    Deliberately not the same predicate as `ticket_is_dispatchable`: that one
    asks "is this approved and ready to dispatch?", this one asks "should the
    system approve it at all?". They differ on exactly one thing -- ticket
    status -- and collapsing them would make an already-approved ticket look
    like one waiting to be approved.
    """
    if ticket.status is not TicketStatus.NEW:
        return False
    if ticket.classification_status is not ClassificationStatus.RESOLVED:
        return False
    if ticket.category_id is None or ticket.priority is None:
        return False
    if ticket.duplicate_of_ticket_id is not None:
        return False
    if ticket.priority is Priority.P3:
        return False
    return not p3_review_is_pending(db, ticket.id)


def auto_approve_and_dispatch(db: Session, ticket: Ticket, run=None) -> bool:
    """Approve and enqueue one ticket, if §2 says the system may.

    Returns whether it did. Does **not** commit: the caller is inside the
    transaction that produced the classification, and approving in a separate
    one would allow a crash to leave a ticket classified but never approved and
    never queued.
    """
    if not automatic_assignment_enabled(db):
        return False
    if not eligible_for_automatic_approval(db, ticket):
        return False

    now = datetime.now(UTC)
    previous = ticket.status
    ticket.status = TicketStatus.APPROVED
    ticket.approved_at = now
    ticket.version += 1
    db.add(
        TicketStatusHistory(
            ticket_id=ticket.id,
            from_status=previous,
            to_status=TicketStatus.APPROVED,
            changed_by=None,
            reason="Automatic Assignment approved this ticket after AI classification.",
        )
    )
    if run is not None:
        run.grouping_status = GROUPING_SKIPPED_AUTOMATIC

    side_effects = AssignmentSideEffects(db)
    side_effects.audit(
        None,
        "AUTO_APPROVE_TICKET",
        "TICKET",
        ticket.id,
        {"status": previous.value},
        {"status": TicketStatus.APPROVED.value, "grouping": "SKIPPED"},
        None,
        "SYSTEM",
    )
    side_effects.notify_unit(
        ticket,
        "TICKET_APPROVED",
        "Phản ánh đã được duyệt",
        "Phản ánh của bạn đã được duyệt và đang được phân công kỹ thuật viên.",
    )
    db.flush()

    if ticket_is_dispatchable(db, ticket):
        enqueue(db, ticket, now=now)
    return True


__all__ = [
    "GROUPING_SKIPPED_AUTOMATIC",
    "auto_approve_and_dispatch",
    "eligible_for_automatic_approval",
]
