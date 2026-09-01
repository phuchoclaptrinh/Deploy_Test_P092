"""Scheduling side effects shared by all three assignment paths.

Manual assignment, Visual Assignment and Automatic Assignment all produce the
same three facts on a `ticket_assignments` row -- `planned_start_at`,
`planned_finish_at` and `planned_order` -- and they must produce them the same
way. A coordinator's manual placement that skipped scheduling would leave a
ticket with no expected start for the resident (§4) and an invisible hole in the
technician's day that the next automatic pass would happily book over.

Two entry points:

* `plan_single` -- simulate one unit onto one named technician. Used when the
  technician is already chosen by a human, so there is nothing to rank.
* `reindex_technicians` -- renumber "Do now" / "Next" across a technician's live
  queue after it changed.

`reindex_technicians` deliberately never rewrites `planned_finish_at`. That
value is the commitment a placement made, and slack is measured against it;
refreshing it on every re-simulation would drive every slack to zero and make
AT_RISK unreachable. Order, expected start and current slack are the only things
that move -- plus `risk_state`, and only ever in the SAFE -> AT_RISK direction:
a re-simulation that pushes a committed unit into negative slack has discovered
a broken commitment, and Building Management is told. It is never moved back,
because "this was late at some point" is the fact an operator needs, and a
queue that healed itself between two refreshes would hide it.

`reindex_technicians` returns the assignments that crossed into AT_RISK on this
call, so the caller can raise them with Building Management. Callers that only
need the renumbering ignore the return value.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from src.config import get_settings
from src.database.models.category import CategoryCatalog
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.dispatch.durations import p80_for_code, p80_for_unit
from src.dispatch.loader import ACTIVE_ASSIGNMENT_STATUSES
from src.dispatch.scheduler import Placement, WorkUnit, place, simulate
from src.dispatch.shift import as_utc
from src.models.enums import AssignmentStatus, DispatchRiskState


def safety_buffer() -> timedelta:
    return timedelta(seconds=get_settings().dispatch_safety_buffer_seconds)


def load_queue(
    db: Session,
    technician_id: UUID,
    now: datetime,
    *,
    exclude_assignment_id: UUID | None = None,
) -> list[WorkUnit]:
    """One technician's live queue as scheduler units.

    Single-technician and therefore a single statement. The batch path does not
    call this -- it bulk-loads every technician at once (§8) -- so there is no
    N+1 hiding here; this is the shape a human-chosen placement genuinely needs.

    `exclude_assignment_id` exists for a specific trap. A caller that writes the
    assignment row first and schedules second would load its own new row back as
    existing work, and `place()` would then add the same unit a second time as
    the candidate -- booking its duration twice and reporting a start time that
    is one job too late. Excluding it is what lets the write happen first, which
    the caller needs because the placement is applied to that very row.
    """
    rows = db.execute(
        select(
            TicketAssignment.id,
            TicketAssignment.status,
            TicketAssignment.started_at,
            TicketAssignment.planned_finish_at,
            Ticket.id.label("ticket_id"),
            Ticket.created_at,
            Ticket.risk_score,
            CategoryCatalog.code,
        )
        .join(Ticket, Ticket.id == TicketAssignment.ticket_id)
        .outerjoin(CategoryCatalog, CategoryCatalog.id == Ticket.category_id)
        .where(
            TicketAssignment.technician_id == technician_id,
            TicketAssignment.is_active.is_(True),
            TicketAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
            *([TicketAssignment.id != exclude_assignment_id] if exclude_assignment_id else []),
        )
    ).all()
    return [
        WorkUnit(
            key=row.id,
            ticket_ids=(row.ticket_id,),
            duration=p80_for_code(row.code),
            score=row.risk_score or 0,
            submitted_at=as_utc(row.created_at) or now,
            deadline=as_utc(row.planned_finish_at),
            assignment_id=row.id,
            in_progress=row.status == AssignmentStatus.IN_PROGRESS,
            started_at=as_utc(row.started_at),
        )
        for row in rows
    ]


def plan_single(
    db: Session,
    *,
    unit_key: UUID,
    ticket_ids: list[UUID],
    category_codes: list[str],
    score: float,
    submitted_at: datetime,
    technician_id: UUID,
    now: datetime,
    exclude_assignment_id: UUID | None = None,
) -> Placement:
    """Where one work unit lands in one named technician's day.

    `category_codes` is a list because a grouped Visual Assignment unit covers
    several tickets on one visit, and §1 requires it to stay one unit. Its cost
    is the sum of its members' P80s (`p80_for_unit`).

    Pass `exclude_assignment_id` when the assignment row already exists -- see
    `load_queue`. Callers that use the new assignment's own id as `unit_key`
    should pass the same value here.
    """
    buffer = safety_buffer()
    unit = WorkUnit(
        key=unit_key,
        ticket_ids=tuple(ticket_ids),
        duration=p80_for_unit(category_codes),
        score=score,
        submitted_at=submitted_at,
    )
    queue = load_queue(db, technician_id, now, exclude_assignment_id=exclude_assignment_id)
    return place(technician_id, queue, unit, now, buffer)


def apply_placement(
    assignment: TicketAssignment,
    placement: Placement,
    *,
    risk: DispatchRiskState | None = None,
) -> None:
    """Copy a simulated placement onto the assignment row it produced."""
    assignment.planned_start_at = placement.candidate.planned_start_at
    assignment.planned_finish_at = placement.committed_deadline
    assignment.planned_order = placement.candidate.order
    assignment.slack_seconds = placement.worst_committed_slack
    if risk is not None:
        assignment.risk_state = risk.value
    elif assignment.risk_state is None:
        assignment.risk_state = (
            DispatchRiskState.SAFE.value if placement.is_safe else DispatchRiskState.AT_RISK.value
        )


def reindex_technicians(
    db: Session, technician_ids: set[UUID] | list[UUID], now: datetime
) -> list[TicketAssignment]:
    """Renumber "Do now" / "Next" for every technician whose queue changed.

    Returns the assignments this pass moved from SAFE to AT_RISK.
    """
    ids = list(technician_ids)
    if not ids:
        return []
    buffer = safety_buffer()
    rows = list(
        db.scalars(
            select(TicketAssignment)
            # Eager: the loop reads `row.ticket` for every assignment, and lazy
            # loading would issue one statement per row.
            .options(joinedload(TicketAssignment.ticket))
            .where(
                TicketAssignment.technician_id.in_(ids),
                TicketAssignment.is_active.is_(True),
                TicketAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
            )
        ).unique()
    )
    if not rows:
        return []

    codes = _category_codes(db, [row.ticket_id for row in rows])
    grouped: dict[UUID, list[TicketAssignment]] = {}
    for row in rows:
        grouped.setdefault(row.technician_id, []).append(row)
    newly_at_risk: list[TicketAssignment] = []

    for assignments in grouped.values():
        index = {row.id: row for row in assignments}
        units = [
            WorkUnit(
                key=row.id,
                ticket_ids=(row.ticket_id,),
                duration=p80_for_code(codes.get(row.ticket_id)),
                score=(row.ticket.risk_score if row.ticket else None) or 0,
                submitted_at=(as_utc(row.ticket.created_at) if row.ticket else None) or now,
                deadline=as_utc(row.planned_finish_at),
                assignment_id=row.id,
                in_progress=row.status == AssignmentStatus.IN_PROGRESS,
                started_at=as_utc(row.started_at),
            )
            for row in assignments
        ]
        for slot in simulate(units, now, buffer):
            assignment = index[slot.unit.key]
            assignment.planned_order = slot.order
            assignment.planned_start_at = slot.planned_start_at
            assignment.slack_seconds = slot.slack_seconds
            if (
                slot.slack_seconds is not None
                and slot.slack_seconds < 0
                and assignment.risk_state != DispatchRiskState.AT_RISK.value
            ):
                assignment.risk_state = DispatchRiskState.AT_RISK.value
                newly_at_risk.append(assignment)

    return newly_at_risk


def _category_codes(db: Session, ticket_ids: list[UUID]) -> dict[UUID, str]:
    if not ticket_ids:
        return {}
    rows = db.execute(
        select(Ticket.id, CategoryCatalog.code)
        .outerjoin(CategoryCatalog, CategoryCatalog.id == Ticket.category_id)
        .where(Ticket.id.in_(ticket_ids))
    ).all()
    return {ticket_id: code for ticket_id, code in rows if code}


__all__ = [
    "apply_placement",
    "load_queue",
    "plan_single",
    "reindex_technicians",
    "safety_buffer",
]
