"""`get_candidate_dispatch_history` -- the at-risk agent's one tool (§7).

**One query, one look-back.** Every window §7 asks for (30, 60 and 90 days) is
computed in Python from a single pull covering the widest of them. Three
windows times N technicians would otherwise be 3N statements per micro-batch,
which is precisely the shape §8 rules out -- and the same rows answer every
question the tool is asked, so pulling them once is not even a trade.

**What cannot come out of here.** §7 restricts the tool to aggregated
operational data. The projection below names its columns explicitly and none of
them is `tickets.description`, a resident profile, a phone number, an email or
an address. There is no `SELECT *` in this module and no ORM relationship walk,
because either one would let a future column arrive in the payload without
anyone deciding it should.

Handling and waiting times are measured in **working** seconds (§6's window),
not wall-clock. A job started at 16:00 and finished at 09:00 the next morning
took three working hours, not seventeen, and an agent comparing a technician's
history against a P80 estimate expressed in working time needs both sides in
the same unit. The same applies to the wait between being assigned and
starting, which now routinely spans an overnight gap: a technician third in a
valid queue is not idle for fifteen hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.database.models.category import CategoryCatalog
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.dispatch.agent.schemas import CategoryHandlingStats, HistoryWindow
from src.dispatch.shift import as_utc, working_seconds_between
from src.models.enums import AssignmentEndReason, AssignmentStatus


@dataclass(frozen=True)
class _AssignmentFact:
    """One historical assignment, reduced to the fields §7 permits."""

    technician_id: UUID
    category_code: str | None
    status: str
    end_reason: str | None
    assigned_at: datetime
    planned_start_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None


def percentile(values: list[int], fraction: float) -> int | None:
    """Nearest-rank percentile over a small sample.

    Nearest-rank rather than interpolating: these samples are often three or
    four completed jobs, and interpolating between two of them invents a
    precision the data does not have. `p80` of four values is the fourth.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-len(ordered) * fraction // 1))))
    return ordered[rank - 1]


def get_candidate_dispatch_history(
    db: Session,
    candidate_technician_ids: list[UUID],
    category_id: UUID | None,
    current_time: datetime,
    *,
    window_days: list[int] | None = None,
) -> dict[UUID, list[HistoryWindow]]:
    """§7's tool, as a plain function over an open session.

    Returns one `HistoryWindow` per requested window per technician, always in
    ascending window order, and always with an entry for every candidate --
    including candidates with no history at all, whose windows come back zeroed.
    A missing key would force the caller to distinguish "new technician" from
    "lookup failed", and those must not look the same.

    `category_id` selects which category's P50/P80 is guaranteed present. Other
    categories the technician worked in are included too: §7 asks for
    "historical P50/P80 handling time by category", and the trade-off the agent
    is weighing is often "this person is slow at *this* category but has
    capacity", which needs both sides.
    """
    settings = get_settings()
    windows = sorted(set(window_days or settings.parsed_at_risk_history_windows))
    if not candidate_technician_ids or not windows:
        return {tid: [] for tid in candidate_technician_ids}

    facts = _load_facts(db, candidate_technician_ids, current_time, max(windows))
    by_technician: dict[UUID, list[_AssignmentFact]] = {tid: [] for tid in candidate_technician_ids}
    for fact in facts:
        by_technician.setdefault(fact.technician_id, []).append(fact)

    required_code = _category_code(db, category_id) if category_id is not None else None
    return {
        technician_id: [
            _window(rows, current_time, days, required_code=required_code) for days in windows
        ]
        for technician_id, rows in by_technician.items()
    }


# ---------------------------------------------------------------------------


def _load_facts(
    db: Session,
    technician_ids: list[UUID],
    current_time: datetime,
    max_window_days: int,
) -> list[_AssignmentFact]:
    """The single statement. Column list is the privacy boundary -- see module docstring."""
    since = current_time - timedelta(days=max_window_days)
    rows = db.execute(
        select(
            TicketAssignment.technician_id,
            CategoryCatalog.code,
            TicketAssignment.status,
            TicketAssignment.end_reason,
            TicketAssignment.assigned_at,
            TicketAssignment.planned_start_at,
            TicketAssignment.started_at,
            TicketAssignment.completed_at,
        )
        .join(Ticket, Ticket.id == TicketAssignment.ticket_id)
        .outerjoin(CategoryCatalog, CategoryCatalog.id == Ticket.category_id)
        .where(
            TicketAssignment.technician_id.in_(technician_ids),
            TicketAssignment.assigned_at >= since,
        )
    ).all()
    return [
        _AssignmentFact(
            technician_id=row.technician_id,
            category_code=row.code,
            status=row.status.value if hasattr(row.status, "value") else str(row.status),
            end_reason=row.end_reason,
            assigned_at=as_utc(row.assigned_at),
            planned_start_at=as_utc(row.planned_start_at),
            started_at=as_utc(row.started_at),
            completed_at=as_utc(row.completed_at),
        )
        for row in rows
    ]


def _category_code(db: Session, category_id: UUID) -> str | None:
    return db.scalar(select(CategoryCatalog.code).where(CategoryCatalog.id == category_id))


def _window(
    facts: list[_AssignmentFact],
    current_time: datetime,
    days: int,
    *,
    required_code: str | None,
) -> HistoryWindow:
    cutoff = current_time - timedelta(days=days)
    rows = [fact for fact in facts if fact.assigned_at >= cutoff]

    start_delays: list[int] = []
    started_on_time = 0
    durations_by_code: dict[str, list[int]] = {}
    completed = rejected = unable = reassigned = started = 0

    for fact in rows:
        if fact.started_at is not None:
            started += 1
            start_delays.append(max(0, working_seconds_between(fact.assigned_at, fact.started_at)))
            # No planned start recorded means no schedule was promised, so it
            # cannot have been missed. Counting it as late would punish
            # assignments written by a path that never simulated.
            if fact.planned_start_at is None or fact.started_at <= fact.planned_start_at:
                started_on_time += 1
        if fact.status == AssignmentStatus.COMPLETED.value:
            completed += 1
            if fact.started_at is not None and fact.completed_at is not None:
                code = fact.category_code or "UNKNOWN"
                durations_by_code.setdefault(code, []).append(
                    max(0, working_seconds_between(fact.started_at, fact.completed_at))
                )
        if fact.status == AssignmentStatus.UNABLE_TO_HANDLE.value:
            unable += 1
        if fact.end_reason == AssignmentEndReason.TECHNICIAN_REJECTED.value:
            rejected += 1
        if fact.end_reason == AssignmentEndReason.COORDINATOR_REASSIGNED.value:
            reassigned += 1

    # The requested category always appears, even at zero, so the agent can tell
    # "never worked this category" from "the tool did not look".
    if required_code and required_code not in durations_by_code:
        durations_by_code[required_code] = []

    return HistoryWindow(
        window_days=days,
        completed_count=completed,
        assigned_count=len(rows),
        started_count=started,
        started_on_time_count=started_on_time,
        median_assignment_to_start_seconds=percentile(start_delays, 0.5),
        rejected_count=rejected,
        unable_to_handle_count=unable,
        reassigned_away_count=reassigned,
        by_category=[
            CategoryHandlingStats(
                category_code=code,
                completed_count=len(values),
                p50_working_seconds=percentile(values, 0.5),
                p80_working_seconds=percentile(values, 0.8),
            )
            for code, values in sorted(durations_by_code.items())
        ],
    )


__all__ = ["get_candidate_dispatch_history", "percentile"]
