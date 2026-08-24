"""Technician productivity reporting (business spec §2.13).

One row per Technician per reporting period:

| Column                           | Definition                                            |
| -------------------------------- | ----------------------------------------------------- |
| Ngày hoạt động                   | days readiness was switched on during the period       |
| Số ticket đã xử lý               | assignments completed inside the period                |
| Số ticket trễ SLA                | measured against the ticket's *latest* assignment      |
| Số ticket nhận lại từ người khác | handed over from another Technician, not a first grant |

Every number comes from persisted rows. Nothing here estimates.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from src.database.models.technician import TechnicianProfile
from src.database.models.technician_availability import TechnicianAvailabilityEvent
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.models.enums import AssignmentStatus

REPORT_PERIODS = ("week", "month")


def resolve_period(period: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Calendar-aligned window: the ISO week, or the calendar month, holding `now`."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    if period == "week":
        start = datetime.combine(moment.date() - timedelta(days=moment.weekday()), datetime.min.time(), tzinfo=UTC)
        return start, start + timedelta(days=7)
    start = datetime.combine(moment.date().replace(day=1), datetime.min.time(), tzinfo=UTC)
    end_month = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end_month


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _active_days(events: list[TechnicianAvailabilityEvent], start: datetime, end: datetime) -> int:
    """Distinct UTC dates on which readiness was on at any moment of the period.

    A Technician with no recorded history counts as zero rather than as
    available: the report must not assert readiness nobody wrote down.
    """
    ordered = sorted(events, key=lambda item: _as_utc(item.changed_at) or start)
    state = False
    cursor = start
    days: set[date] = set()

    def cover(from_at: datetime, to_at: datetime) -> None:
        if not state or to_at <= from_at:
            return
        day = from_at.date()
        while day <= (to_at - timedelta(microseconds=1)).date():
            days.add(day)
            day += timedelta(days=1)

    for event in ordered:
        changed_at = _as_utc(event.changed_at) or start
        if changed_at <= start:
            state = event.is_available
            continue
        if changed_at >= end:
            break
        cover(cursor, changed_at)
        state = event.is_available
        cursor = changed_at
    cover(cursor, end)
    return len(days)


class TechnicianReportService:
    def __init__(self, db: Session):
        self.db = db

    def productivity(self, period: str, now: datetime | None = None) -> dict:
        start, end = resolve_period(period if period in REPORT_PERIODS else "month", now)
        technicians = list(
            self.db.scalars(
                select(TechnicianProfile)
                .options(
                    joinedload(TechnicianProfile.user),
                    selectinload(TechnicianProfile.availability_events),
                )
                .order_by(TechnicianProfile.created_at.asc())
            ).unique()
        )
        assignments = list(
            self.db.scalars(
                select(TicketAssignment)
                .options(joinedload(TicketAssignment.ticket).load_only(Ticket.id, Ticket.sla_due_at))
                .order_by(TicketAssignment.assigned_at.asc())
            ).unique()
        )

        by_ticket: dict[object, list[TicketAssignment]] = {}
        for assignment in assignments:
            by_ticket.setdefault(assignment.ticket_id, []).append(assignment)

        rows = []
        for technician in technicians:
            completed = 0
            late = 0
            reassigned_in = 0
            for assignment in assignments:
                if assignment.technician_id != technician.user_id:
                    continue
                completed_at = _as_utc(assignment.completed_at)
                if assignment.status == AssignmentStatus.COMPLETED and completed_at and start <= completed_at < end:
                    completed += 1
                    history = by_ticket.get(assignment.ticket_id, [])
                    latest = history[-1] if history else None
                    due_at = _as_utc(assignment.ticket.sla_due_at) if assignment.ticket else None
                    # §2.13: lateness is judged on the latest hand-over, so an
                    # earlier technician's expired clock is not counted twice.
                    if latest is not None and latest.id == assignment.id and due_at and completed_at > due_at:
                        late += 1
                assigned_at = _as_utc(assignment.assigned_at)
                if assigned_at and start <= assigned_at < end:
                    history = by_ticket.get(assignment.ticket_id, [])
                    earlier = [item for item in history if (_as_utc(item.assigned_at) or assigned_at) < assigned_at]
                    if any(item.technician_id != technician.user_id for item in earlier):
                        reassigned_in += 1
            rows.append(
                {
                    "technician_id": technician.user_id,
                    "full_name": technician.user.full_name if technician.user else None,
                    "is_active": technician.is_active,
                    "active_days": _active_days(list(technician.availability_events), start, end),
                    "completed_tickets": completed,
                    "sla_late_tickets": late,
                    "reassigned_from_other_tickets": reassigned_in,
                }
            )

        return {"period": period if period in REPORT_PERIODS else "month", "period_start": start, "period_end": end, "rows": rows}


def record_availability(
    db: Session,
    technician_id,
    *,
    is_available: bool,
    changed_by_user_id=None,
    source: str = "SYSTEM",
    changed_at: datetime | None = None,
) -> TechnicianAvailabilityEvent:
    """Append one readiness transition. Callers own the surrounding transaction."""
    event = TechnicianAvailabilityEvent(
        technician_id=technician_id,
        is_available=is_available,
        changed_by_user_id=changed_by_user_id,
        source=source,
        changed_at=changed_at or datetime.now(UTC),
    )
    db.add(event)
    return event
