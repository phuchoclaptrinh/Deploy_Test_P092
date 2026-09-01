"""Coordinator SLA, reporting, and side-effect helpers.

`apply_scoring` is gone. A category no longer carries a base score, a location
no longer carries a bonus, and there is no severity to weigh -- so there is
nothing here that could decide a priority, and the ticket's priority is written
only by `RiskAssessmentService.record`. What is left is the deadline that
follows from a priority somebody else already decided.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database.models.audit_log import AuditLog
from src.database.models.category import CategoryCatalog
from src.database.models.notification import Notification
from src.database.models.resident_profile import ResidentProfile
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.domain.sla_clock import counts_toward_compliance
from src.models.enums import NotificationChannel, NotificationStatus, TicketStatus
from src.request_context import request_id_context
from src.services.risk_assessment_service import RiskAssessmentService

#: A ticket in one of these is still somebody's to do, so a passed deadline on
#: it is a violation rather than history.
_OPEN_STATUSES = frozenset({TicketStatus.APPROVED, TicketStatus.IN_PROGRESS})


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class CoordinatorScoringSupport:
    """The deadline half of scoring. Delegates so there is one SLA rule."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.risk = RiskAssessmentService(db)

    def recalculate_sla(self, ticket: Ticket) -> None:
        self.risk.recalculate_sla(ticket)


class CoordinatorSideEffects:
    def __init__(self, db: Session) -> None:
        self.db = db

    def transition(
        self,
        actor_user_id: UUID,
        ticket: Ticket,
        to_status: TicketStatus,
        *,
        action: str,
        notification_title: str,
        notification_body: str,
    ) -> None:
        old = ticket.status
        now = datetime.now(UTC)
        ticket.status = to_status
        ticket.version += 1
        if to_status == TicketStatus.APPROVED:
            ticket.approved_at = now
        self.append_status_history(ticket, old, to_status, actor_user_id, action)
        self.notify_unit(ticket, action, notification_title, notification_body)
        self.audit(actor_user_id, action, ticket, {"status": old.value}, {"status": to_status.value}, None)
        if to_status == TicketStatus.APPROVED:
            # A coordinator approving by hand still hands the ticket to the
            # automatic path when the switch is on -- approving is not the same
            # act as choosing a technician, and §2 does not ask them to do both.
            # Imported here so the coordinator surface does not pull the dispatch
            # package into every request that changes a ticket status.
            from src.dispatch.enqueue import enqueue

            self.db.flush()
            enqueue(self.db, ticket)

    def append_status_history(self, ticket: Ticket, old, new, changed_by: UUID, reason: str) -> None:
        from src.database.models.ticket_status_history import TicketStatusHistory

        self.db.add(TicketStatusHistory(ticket_id=ticket.id, from_status=old, to_status=new, changed_by=changed_by, reason=reason))
        self.db.flush()

    def notify_unit(self, ticket: Ticket, event: str, title: str, body: str) -> None:
        recipients = list(self.db.scalars(select(ResidentProfile.user_id).where(ResidentProfile.unit_id == ticket.source_unit_id)))
        for user_id in recipients:
            self.db.add(
                Notification(
                    recipient_user_id=user_id,
                    ticket_id=ticket.id,
                    notification_type=event,
                    channel=NotificationChannel.IN_APP,
                    title=title,
                    body=body,
                    payload={"ticket_id": str(ticket.id), "status": ticket.status.value},
                    status=NotificationStatus.PENDING,
                )
            )

    def audit(self, actor_user_id: UUID, action: str, ticket: Ticket, before_data, after_data, reason: str | None) -> None:
        self.db.add(
            AuditLog(
                actor_user_id=actor_user_id,
                actor_role="COORDINATOR",
                action=action,
                entity_type="TICKET",
                entity_id=ticket.id,
                before_data=before_data,
                after_data=after_data,
                reason=reason,
                request_id=UUID(request_id) if (request_id := request_id_context.get()) else None,
            )
        )


class CoordinatorReadService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_audit_logs(self, **filters) -> list[AuditLog]:
        query = select(AuditLog)
        for attr in ("actor_user_id", "action", "entity_type", "entity_id"):
            if filters.get(attr) is not None:
                query = query.where(getattr(AuditLog, attr) == filters[attr])
        if filters.get("created_from") is not None:
            query = query.where(AuditLog.created_at >= filters["created_from"])
        if filters.get("created_to") is not None:
            query = query.where(AuditLog.created_at <= filters["created_to"])
        return list(self.db.scalars(query.order_by(AuditLog.created_at.desc()).limit(filters.get("limit", 100))))

    def tickets_summary(self) -> dict[str, object]:
        total = int(self.db.scalar(select(func.count(Ticket.id))) or 0)
        status_rows = self.db.execute(select(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status)).all()
        priority_rows = self.db.execute(select(Ticket.priority, func.count(Ticket.id)).group_by(Ticket.priority)).all()
        category_rows = self.db.execute(
            select(CategoryCatalog.code, func.count(Ticket.id)).join(Ticket, Ticket.category_id == CategoryCatalog.id).group_by(CategoryCatalog.code)
        ).all()
        return {
            "total": total,
            "by_status": {status.value: int(count) for status, count in status_rows},
            "by_priority": {priority.value: int(count) for priority, count in priority_rows if priority is not None},
            "by_category": {str(category): int(count) for category, count in category_rows},
        }

    def sla_performance(self) -> dict[str, object]:
        """Did somebody *start* in time, and is anything still overdue.

        Three deliberate choices, all from `docs/risk_scoring_v2.md` §6:

        **Measured at `started_at`, not `completed_at`.** What Building
        Management promises a resident, and the only thing dispatch controls,
        is that somebody arrives. How long the repair then takes is a property
        of the fault, and judging the promise on it makes a difficult job look
        like a broken process.

        **P5 is reported separately, not counted.** An emergency is handled by
        hand and never dispatched, so scoring it as a technician's pass or
        failure would put a number nobody earned in the denominator.

        **An open ticket past its deadline is already a violation.** Counting
        only finished work would let the worst cases -- the ones nobody has
        started at all -- improve the number by staying unfinished.
        """
        now = datetime.now(UTC)
        rows = list(
            self.db.execute(
                select(Ticket.id, Ticket.priority, Ticket.sla_due_at, Ticket.completed_at, Ticket.status)
                .where(Ticket.priority.is_not(None), Ticket.sla_due_at.is_not(None))
            ).all()
        )
        starts = dict(
            self.db.execute(
                select(TicketAssignment.ticket_id, func.min(TicketAssignment.started_at))
                .where(TicketAssignment.started_at.is_not(None))
                .group_by(TicketAssignment.ticket_id)
            ).all()
        )

        measured = 0
        on_time = 0
        overdue_open = 0
        emergencies = 0
        for ticket_id, priority, due_at, _completed_at, status in rows:
            if not counts_toward_compliance(priority):
                emergencies += 1
                continue
            started_at = starts.get(ticket_id)
            if started_at is not None:
                measured += 1
                if _as_utc(started_at) <= _as_utc(due_at):
                    on_time += 1
                continue
            if status in _OPEN_STATUSES and _as_utc(due_at) < now:
                # Nobody has started it and the deadline has passed. Counted as
                # a measured violation rather than left out.
                measured += 1
                overdue_open += 1

        return {
            "measured_total": measured,
            "started_on_time": on_time,
            "overdue_not_started": overdue_open,
            "compliance_rate": (on_time / measured) if measured else None,
            # Shown beside the rate, never inside it.
            "emergency_manual_total": emergencies,
        }
