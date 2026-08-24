"""Resident-facing lifecycle grouping and rejection copy.

The Resident request list groups reports into "Đang theo dõi" (ACTIVE) and
"Đã kết thúc" (FINISHED). The grouping has to exist twice — once as a SQL
predicate so filtering, counting and pagination happen in the database, and
once in Python so the serialized response can report the group it belongs to.
Both live here so they can never drift apart.
"""

from __future__ import annotations

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import aliased

from src.database.models.ticket import Ticket
from src.models.enums import InvalidReason, TicketLifecycleGroup, TicketStatus

FINISHED_STATUSES: frozenset[TicketStatus] = frozenset(
    {
        TicketStatus.COMPLETED,
        TicketStatus.UNRESOLVABLE,
        TicketStatus.CANCELLED,
        TicketStatus.INVALID,
    }
)

#: WAITING_RESIDENT_INFO stays here only for legacy rows: Building Management no
#: longer requests extra information, but reports parked in that state before the
#: workflow was removed must still be visible under "Đang theo dõi".
ACTIVE_STATUSES: frozenset[TicketStatus] = frozenset(
    {
        TicketStatus.NEW,
        TicketStatus.WAITING_RESIDENT_INFO,
        TicketStatus.APPROVED,
        TicketStatus.IN_PROGRESS,
    }
)


def lifecycle_group(ticket: Ticket) -> TicketLifecycleGroup:
    """Group one loaded ticket.

    A linked duplicate has no lifecycle of its own — it follows the report it
    was folded into, so the resident sees it under the same tab as the work that
    actually resolves their issue.
    """
    if ticket.status != TicketStatus.LINKED_DUPLICATE:
        return _group_for_status(ticket.status)

    master = ticket.duplicate_master
    # `link_duplicate` resolves to the canonical master before writing
    # (`master.duplicate_of_ticket_id or master.id`), so a chain is at most one
    # hop. The loop is defensive against rows written before that rule existed.
    for _ in range(8):
        if master is None:
            # The master row is gone; treat the link as still being worked on
            # rather than silently retiring the resident's report.
            return TicketLifecycleGroup.ACTIVE
        if master.status != TicketStatus.LINKED_DUPLICATE:
            return _group_for_status(master.status)
        master = master.duplicate_master
    return TicketLifecycleGroup.ACTIVE


def _group_for_status(status: TicketStatus) -> TicketLifecycleGroup:
    if status in FINISHED_STATUSES:
        return TicketLifecycleGroup.FINISHED
    return TicketLifecycleGroup.ACTIVE


def apply_lifecycle_group_filter(query: Select, group: TicketLifecycleGroup | None) -> Select:
    """Restrict a ticket query to one lifecycle group inside the database.

    Applied before `count`, `offset` and `limit` so `total` and pagination
    describe the filtered set rather than the whole apartment history.
    """
    if group is None:
        return query

    master = aliased(Ticket)
    master_status = (
        select(master.status)
        .where(master.id == Ticket.duplicate_of_ticket_id)
        .correlate(Ticket)
        .scalar_subquery()
    )
    finished = [status.value for status in FINISHED_STATUSES]

    if group is TicketLifecycleGroup.FINISHED:
        return query.where(
            or_(
                Ticket.status.in_(finished),
                (Ticket.status == TicketStatus.LINKED_DUPLICATE.value) & master_status.in_(finished),
            )
        )
    return query.where(
        or_(
            Ticket.status.notin_([*finished, TicketStatus.LINKED_DUPLICATE.value]),
            # A link whose master is missing or still open counts as active.
            (Ticket.status == TicketStatus.LINKED_DUPLICATE.value)
            & or_(master_status.is_(None), master_status.notin_(finished)),
        )
    )


#: Resident-safe explanations. The raw coordinator or agent reason is an internal
#: audit value and is never sent to a resident.
_INVALID_REASON_TEXT: dict[str, str] = {
    InvalidReason.CONTENT_INSUFFICIENT.value: (
        "Phản ánh chưa được tiếp nhận vì thông tin chưa đủ để xác định sự cố."
    ),
    InvalidReason.RESIDENT_RESPONSE_TIMEOUT.value: (
        "Phản ánh chưa được tiếp nhận vì đã hết thời gian trả lời câu hỏi bổ sung."
    ),
    InvalidReason.COORDINATOR_REJECTED.value: (
        "Phản ánh chưa được tiếp nhận sau khi Ban quản lý xem xét."
    ),
}

_INVALID_REASON_FALLBACK = "Phản ánh chưa được tiếp nhận."


def resident_invalid_reason_text(ticket: Ticket) -> str | None:
    """Friendly rejection copy, or None when the report is not INVALID."""
    if ticket.status != TicketStatus.INVALID:
        return None
    return _INVALID_REASON_TEXT.get(ticket.invalid_reason or "", _INVALID_REASON_FALLBACK)


#: Public timeline reasons, keyed by the internal string written to
#: `ticket_status_history.reason`. Anything not listed here is internal: a
#: coordinator note, an agent-authored duplicate reason, or an audit action
#: name. Those are dropped rather than translated, because a resident must
#: never read staff-facing text (COMPONENT_STATES.md C-18).
_PUBLIC_TIMELINE_REASONS: dict[str, str] = {
    "Resident created ticket.": "Bạn đã gửi phản ánh.",
    "Resident cancelled ticket.": "Bạn đã hủy phản ánh.",
    "Resident supplied requested information.": "Bạn đã gửi thông tin bổ sung.",
    "Coordinator requested resident information.": "Ban quản lý yêu cầu bổ sung thông tin.",
    "APPROVE_TICKET": "Ban quản lý đã duyệt phản ánh.",
    "Technician started work.": "Kỹ thuật viên đã bắt đầu xử lý.",
    "Technician completed assignment.": "Kỹ thuật viên đã hoàn thành xử lý.",
    "Coordinator linked duplicate ticket.": "Phản ánh được gộp với một sự cố đã ghi nhận.",
}


def resident_timeline_reason(reason: str | None) -> str | None:
    """Translate a status-history reason, or drop it if it is internal."""
    if not reason:
        return None
    return _PUBLIC_TIMELINE_REASONS.get(reason.strip())
