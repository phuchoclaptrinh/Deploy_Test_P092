"""The one place that answers "is this ticket held at the emergency gate?".

P5 is the emergency Priority in this system -- a five-minute SLA and a manual
response -- and a ticket classified into it is not published on the model's
say-so. It stops with `ai_analysis_runs.emergency_review_status = 'PENDING'` and
waits for a coordinator to either confirm the emergency or downgrade it.

Renamed from `p3_review_guard` by the v2 rubric. Only the band moved: the gate,
the rule and the reasoning are the ones that were written for P3 when P3 was the
emergency.

While it waits, exactly two management actions are legal, and both go through
`POST /coordinator/tickets/{id}/emergency-review`:

* `CONFIRM_P5` -- Building Management handles it by hand; automation stays off,
  and confirming does *not* unlock assignment;
* `DOWNGRADE_PRIORITY` to P1-P4 with a written reason -- the pipeline resumes.

Everything else is refused. That includes the generic manual-review actions,
which are the reason this module exists: `classification_status =
MANUAL_REVIEW` is where a P5-pending ticket parks *and* where an unclassifiable
ticket parks, so any guard written as "is it in manual review?" lets a
coordinator resolve, reject or link an emergency through the ordinary form and
never see the gate at all.

The check reads the persisted run rather than any in-memory state, so a client
posting directly to an endpoint is stopped by the same fact the pipeline is.
It lives in its own module, taking a plain `Session`, because the callers span
four service families -- coordinator, agent, duplicate workflow and assignment
-- that share no base class between them.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models.ai_analysis import AIAnalysisRun
from src.models.api.errors import EMERGENCY_REVIEW_REQUIRED, DomainError
from src.models.enums import AnalysisRunStatus, EmergencyReviewStatus

#: Distinct from `INVALID_STATUS_TRANSITION` on purpose. A client that gets this
#: back is not being told "wrong state, try later" -- it is being told there is
#: one specific action that applies, and pointed at it.
EMERGENCY_REVIEW_REQUIRED_MESSAGE = (
    "Phản ánh đang chờ Ban quản lý duyệt mức khẩn cấp P5. "
    "Hãy dùng thao tác duyệt khẩn cấp (xác nhận P5 hoặc hạ mức xuống P1-P4 kèm lý do) "
    "trước khi thực hiện bất kỳ thao tác nào khác."
)


def emergency_review_is_pending(db: Session, ticket_id: UUID) -> bool:
    """Whether the latest successful analysis run is parked at the gate.

    The *latest* run, because a ticket can be analysed more than once: a
    coordinator downgrade is followed by a second run, and a retry after a
    technical failure by another. Only the most recent one describes where the
    ticket stands now.

    A FAILED run is not consulted at all. It concluded nothing, so it cannot
    have opened a gate, and letting one answer this question would freeze a
    ticket whose analysis merely errored.
    """
    status = db.scalar(
        select(AIAnalysisRun.emergency_review_status)
        .where(
            AIAnalysisRun.ticket_id == ticket_id,
            AIAnalysisRun.status == AnalysisRunStatus.SUCCEEDED,
        )
        .order_by(AIAnalysisRun.run_number.desc())
        .limit(1)
    )
    return status == EmergencyReviewStatus.PENDING


def emergency_review_pending_ticket_ids(db: Session, ticket_ids: list[UUID]) -> frozenset[UUID]:
    """Which of these tickets are parked at the emergency gate, in one statement.

    The batch counterpart of `emergency_review_is_pending`. Same rule -- only
    the latest successful run per ticket counts -- resolved in Python because
    "latest per group" in SQL costs more than it saves at batch sizes of twenty.
    """
    if not ticket_ids:
        return frozenset()
    rows = db.execute(
        select(AIAnalysisRun.ticket_id, AIAnalysisRun.run_number, AIAnalysisRun.emergency_review_status)
        .where(
            AIAnalysisRun.ticket_id.in_(ticket_ids),
            AIAnalysisRun.status == AnalysisRunStatus.SUCCEEDED,
        )
        .order_by(AIAnalysisRun.ticket_id, AIAnalysisRun.run_number)
    ).all()
    latest: dict[UUID, EmergencyReviewStatus | None] = {}
    for ticket_id, _run_number, status in rows:
        latest[ticket_id] = status
    return frozenset(
        ticket_id for ticket_id, status in latest.items() if status == EmergencyReviewStatus.PENDING
    )


def assert_emergency_review_not_pending(db: Session, ticket_id: UUID) -> None:
    """Refuse an ordinary management mutation on a ticket awaiting review.

    Called at the top of every coordinator action that could change a ticket's
    lifecycle, category, priority, duplicate relationship, assignment or
    publication. `resolve_emergency_review` is deliberately not one of them: it
    is the action this points people at.
    """
    if emergency_review_is_pending(db, ticket_id):
        raise DomainError(EMERGENCY_REVIEW_REQUIRED, EMERGENCY_REVIEW_REQUIRED_MESSAGE, 409)


__all__ = [
    "EMERGENCY_REVIEW_REQUIRED",
    "EMERGENCY_REVIEW_REQUIRED_MESSAGE",
    "assert_emergency_review_not_pending",
    "emergency_review_is_pending",
    "emergency_review_pending_ticket_ids",
]
