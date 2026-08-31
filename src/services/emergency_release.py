"""Taking work back when a ticket becomes an emergency.

`docs/risk_scoring_v2.md` §8 covers the case the guards cannot: a ticket that was
legitimately assigned as a P4 and is then re-scored to P5. Refusing *new*
assignments does nothing for the technician already on their way to it, and the
invariant is "no active assignment on a P5 ticket", not "no new one".

Four things happen, and the two that do **not** happen matter as much:

* the active assignment ends with `EMERGENCY_MANUAL_ESCALATION`;
* the technician's remaining queue is re-planned, because a job left it;
* any open dispatch event is superseded;
* an audit row records all of it.

**No new assignment is sent.** The work has not moved to another technician; it
has left the dispatch system entirely, and requeueing it would immediately hit
the guard and escalate again.

**Nobody is recorded as handling it.** Building Management deals with a P5 by
walking there. Writing a coordinator's name into an assignment row to represent
that would put a fact in the database that nothing in the system observed.

The end reason is its own value for the same reason `TECHNICIAN_REJECTED` is:
the technician did not refuse this work and must not appear on any exclusion
list for a decision they had no part in.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.domain.assignment_guard import ticket_assignment_allowed
from src.models.enums import AssignmentEndReason, AssignmentStatus

logger = logging.getLogger(__name__)

RELEASE_REASON = "Phản ánh được nâng lên mức khẩn cấp P5; Ban quản lý xử lý thủ công."


def release_assignments_for_emergency(db: Session, ticket: Ticket, *, now: datetime | None = None) -> bool:
    """End any active assignment on a ticket that has just become a P5.

    Returns whether anything was released. Safe to call on a ticket that is not
    a P5 and on one that was never assigned: both are no-ops, which is what lets
    the single place a priority changes call it unconditionally.

    Does not commit. The caller owns the transaction, because this is always a
    consequence of a re-score that is itself mid-transaction.
    """
    if ticket_assignment_allowed(ticket):
        return False

    now = now or datetime.now(UTC)
    active = list(
        db.scalars(
            select(TicketAssignment)
            .where(TicketAssignment.ticket_id == ticket.id, TicketAssignment.is_active.is_(True))
            .with_for_update()
        )
    )
    if not active:
        _supersede_event(db, ticket, now)
        return False

    technicians = set()
    for assignment in active:
        # History is kept, not deleted: somebody drove to this building, and the
        # record of that is not the system's to erase.
        assignment.is_active = False
        assignment.ended_at = now
        assignment.end_reason = AssignmentEndReason.EMERGENCY_MANUAL_ESCALATION.value
        assignment.updated_at = now
        if assignment.status is AssignmentStatus.ASSIGNED:
            assignment.status = AssignmentStatus.REASSIGNED
        technicians.add(assignment.technician_id)
    db.flush()

    _supersede_event(db, ticket, now)
    _replan(db, technicians, now)
    _audit(db, ticket, technicians)
    return True


def _supersede_event(db: Session, ticket: Ticket, now: datetime) -> None:
    from src.services.dispatch_reassignment import supersede_open_event

    supersede_open_event(db, ticket.id, now=now)


def _replan(db: Session, technician_ids: set, now: datetime) -> None:
    """Re-plan whoever lost a job, so the rest of their day closes the gap.

    Imported late: `src.dispatch.planning` pulls in the scheduler, and this
    module is called from the risk-assessment write path, which must stay
    importable without it.
    """
    if not technician_ids:
        return
    try:
        from src.dispatch.planning import reindex_technicians

        reindex_technicians(db, technician_ids, now)
    except Exception:  # pragma: no cover - defensive
        # A failed re-plan leaves a gap in somebody's day. That is a scheduling
        # inconvenience; leaving a technician assigned to an emergency would be
        # a broken invariant, so the release stands either way.
        logger.exception("Could not re-plan technicians after an emergency release.")


def _audit(db: Session, ticket: Ticket, technician_ids: set) -> None:
    from src.services.assignment_support import AssignmentSideEffects

    AssignmentSideEffects(db).audit(
        None,
        "ASSIGNMENT_ENDED_BY_EMERGENCY_ESCALATION",
        "TICKET",
        ticket.id,
        None,
        {
            "reason": AssignmentEndReason.EMERGENCY_MANUAL_ESCALATION.value,
            "technician_ids": sorted(str(item) for item in technician_ids),
            "priority": ticket.priority.value if ticket.priority else None,
        },
        RELEASE_REASON,
        "SYSTEM",
    )


__all__ = ["RELEASE_REASON", "release_assignments_for_emergency"]
