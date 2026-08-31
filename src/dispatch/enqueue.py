"""Creating the durable dispatch event (§2, §8).

§8's first requirement is that a ticket becoming ready for automatic assignment
produces a **durable event**, not an in-process task. This module is the only
place that writes one, and it is deliberately the only place that decides what
"ready" means, because §2's eligibility rules and §8's idempotency rules are the
same decision seen from two angles:

    resident submits -> AI classification -> classification eligible
      -> not duplicate -> not P3 -> dispatch event

A ticket failing any of those does not get an event. It is not an error and it
is not queued for later: it is Building Management's, and the manager ticket
list already shows it. There is nothing to write.

**Idempotency.** `dispatch_events` has a partial unique index on `ticket_id
WHERE is_open`, so two concurrent callers produce one row and one
`IntegrityError`. `enqueue` catches that and returns the existing event rather
than raising -- a duplicate enqueue is a duplicate *request*, not a conflict,
and the caller's intent ("this ticket should be dispatched") is already
satisfied.

**The toggle.** Automatic Assignment being off means no event is written at all,
so turning it off stops future automatic assignment without touching anything
already assigned or already queued (§2). Events already `PENDING` when the
switch goes off are caught by the re-check inside the batch, which escalates
them to Building Management rather than assigning them.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.dispatch import DispatchEvent
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.dispatch.shift import as_utc
from src.domain.assignment_guard import EMERGENCY_PRIORITY, ticket_assignment_allowed
from src.models.enums import ClassificationStatus, DispatchEventStatus, TicketStatus
from src.services.emergency_review_guard import emergency_review_is_pending

logger = logging.getLogger(__name__)


def automatic_assignment_enabled(db: Session) -> bool:
    """§2's toggle. A missing row means off -- autonomy is never the default."""
    row = db.get(AutoAssignmentSetting, 1)
    return bool(row and row.enabled)


def ticket_is_dispatchable(db: Session, ticket: Ticket) -> bool:
    """§2's eligibility chain, evaluated in the order §2 states it.

    Read this as the flow diagram it came from:

    * classification must have *concluded* -- `RESOLVED`, with a category and a
      priority. `MANUAL_REVIEW`, `PENDING`, `PROCESSING` and `FAILED` all mean
      no classification the system may act on;
    * the ticket must not be a duplicate;
    * the ticket must not be P5, the emergency priority, which
      `docs/risk_scoring_v2.md` §8 says is always Building Management's;
    * and it must actually be approved and unassigned, or there is nothing to
      dispatch.

    The emergency gate is checked separately from the priority: a ticket still
    parked at that gate has no settled priority yet, and treating "not yet P5"
    as "not P5" would let an emergency slip into the automatic path during the
    window a human is deciding.
    """
    if ticket.status is not TicketStatus.APPROVED:
        return False
    if ticket.classification_status is not ClassificationStatus.RESOLVED:
        return False
    if ticket.category_id is None or ticket.priority is None:
        return False
    if ticket.duplicate_of_ticket_id is not None:
        return False
    if not ticket_assignment_allowed(ticket):
        return False
    if emergency_review_is_pending(db, ticket.id):
        return False
    return not _has_active_assignment(db, ticket.id)


def _has_active_assignment(db: Session, ticket_id: UUID) -> bool:
    return (
        db.scalar(
            select(TicketAssignment.id).where(
                TicketAssignment.ticket_id == ticket_id,
                TicketAssignment.is_active.is_(True),
            )
        )
        is not None
    )


def open_event_for(db: Session, ticket_id: UUID) -> DispatchEvent | None:
    return db.scalar(
        select(DispatchEvent).where(DispatchEvent.ticket_id == ticket_id, DispatchEvent.is_open.is_(True))
    )


def enqueue(db: Session, ticket: Ticket, *, now: datetime | None = None) -> DispatchEvent | None:
    """Write one dispatch event for a ticket, or return why there is none.

    Returns the event -- new or already open -- or `None` when the ticket is not
    eligible for the automatic path. `None` is a normal answer meaning "this one
    is Building Management's", not a failure.

    Does **not** commit. The caller owns the transaction, because enqueue is
    always a consequence of something else (an approval, a classification
    result) and committing here would split that act in two.
    """
    now = now or datetime.now(UTC)
    if not automatic_assignment_enabled(db):
        return None
    if not ticket_is_dispatchable(db, ticket):
        return None

    existing = open_event_for(db, ticket.id)
    if existing is not None:
        return existing

    event = DispatchEvent(
        ticket_id=ticket.id,
        status=DispatchEventStatus.PENDING.value,
        is_open=True,
        priority=ticket.priority.value,
        category_id=ticket.category_id,
        ticket_submitted_at=as_utc(ticket.created_at) or now,
        score_total=float(ticket.risk_score) if ticket.risk_score is not None else None,
        enqueued_at=now,
        available_at=now,
    )
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        # Lost the race against another writer. The other one's event is the
        # live one and satisfies this caller's intent, so recover it rather
        # than failing the enclosing operation (an approval, say) over a
        # duplicate request.
        db.rollback()
        recovered = open_event_for(db, ticket.id)
        if recovered is None:
            raise
        logger.info("Dispatch event for ticket %s already existed; reusing it.", ticket.id)
        return recovered
    return event


def enqueue_backlog(db: Session, *, now: datetime | None = None, limit: int = 200) -> list[DispatchEvent]:
    """Enqueue every eligible ticket that has no open event.

    The recovery path, run by the worker rather than by a request. It exists for
    two situations: tickets that became eligible while the toggle was off and
    are now eligible with it on, and any ticket whose enqueue was lost to a
    crash between its own commit and this one.

    Bounded by `limit` so one pass cannot spend an unbounded amount of the §8
    session budget catching up on a large backlog; the next pass takes the rest.
    """
    now = now or datetime.now(UTC)
    if not automatic_assignment_enabled(db):
        return []

    open_event = (
        select(DispatchEvent.id)
        .where(DispatchEvent.ticket_id == Ticket.id, DispatchEvent.is_open.is_(True))
        .exists()
    )
    active_assignment = (
        select(TicketAssignment.id)
        .where(TicketAssignment.ticket_id == Ticket.id, TicketAssignment.is_active.is_(True))
        .exists()
    )
    candidates = db.scalars(
        select(Ticket)
        .where(
            Ticket.status == TicketStatus.APPROVED,
            Ticket.classification_status == ClassificationStatus.RESOLVED,
            Ticket.category_id.is_not(None),
            Ticket.priority.is_not(None),
            Ticket.priority != EMERGENCY_PRIORITY,
            Ticket.duplicate_of_ticket_id.is_(None),
            ~open_event,
            ~active_assignment,
        )
        .order_by(Ticket.created_at.asc())
        .limit(limit)
    )
    created = [event for ticket in candidates if (event := enqueue(db, ticket, now=now)) is not None]
    if created:
        db.commit()
    return created


__all__ = [
    "automatic_assignment_enabled",
    "enqueue",
    "enqueue_backlog",
    "open_event_for",
    "ticket_is_dispatchable",
]
