"""One definition of who may see a ticket, shared by every read path.

A report goes through a **private AI phase** before anybody but its sender may
look at it. The phase is derived from `classification_status` alone — there is
no separate "published" flag to keep in sync — and it covers both the time the
Agent is analysing and the time it is waiting for an answer to a follow-up
question:

    private  <=>  classification_status IN (PENDING, PROCESSING)

Everything else is published: `RESOLVED`, `MANUAL_REVIEW`, `FAILED`, and any
invalid terminal outcome regardless of which of those two the v3/v4 path
records. Publication is what hands the report to Building Management and shares
it with the rest of the apartment, so the same predicate answers both
questions.

Both forms live here — a SQL predicate for list queries and a Python check for
a single loaded row — because a list that filtered differently from a detail
endpoint is exactly how a private ticket leaks: absent from the list, readable
by direct URL.

These helpers describe **human** API access. Internal Agent and worker reads go
through the unscoped repository methods and are deliberately not filtered.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ColumnElement, or_

from src.database.models.ticket import Ticket
from src.models.enums import ClassificationStatus

#: Classification states during which only the reporter may see the ticket.
PRIVATE_AI_PHASE: frozenset[ClassificationStatus] = frozenset(
    {ClassificationStatus.PENDING, ClassificationStatus.PROCESSING}
)

_PRIVATE_VALUES = tuple(status.value for status in PRIVATE_AI_PHASE)


def is_private_ai_phase(ticket: Ticket) -> bool:
    """True while the report belongs to its sender alone."""
    return ticket.classification_status in PRIVATE_AI_PHASE


def is_published(ticket: Ticket) -> bool:
    """True once classification finished, however it finished."""
    return not is_private_ai_phase(ticket)


def published_predicate() -> ColumnElement[bool]:
    """SQL for "classification has finished"."""
    return Ticket.classification_status.notin_(_PRIVATE_VALUES)


def resident_visibility_predicate(source_unit_id: UUID, viewer_user_id: UUID) -> ColumnElement[bool]:
    """SQL for what one resident account may see of their apartment's reports.

    Applied before `count`, `offset` and `limit` so an invisible row never
    consumes a page slot and `total` describes only what the caller may read.
    """
    return (Ticket.source_unit_id == source_unit_id) & or_(
        Ticket.reporter_user_id == viewer_user_id,
        published_predicate(),
    )


def resident_can_view(ticket: Ticket, source_unit_id: UUID, viewer_user_id: UUID) -> bool:
    """The loaded-row twin of :func:`resident_visibility_predicate`."""
    if ticket.source_unit_id != source_unit_id:
        return False
    return ticket.reporter_user_id == viewer_user_id or is_published(ticket)


def is_reporter(ticket: Ticket, viewer_user_id: UUID) -> bool:
    """Reporter-only actions — cancel, reading and answering AI questions."""
    return ticket.reporter_user_id == viewer_user_id
