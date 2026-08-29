"""Coordinator scoring, reporting, and side-effect helpers."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database.models.audit_log import AuditLog
from src.database.models.category import CategoryCatalog
from src.database.models.floor import Floor
from src.database.models.location import Location
from src.database.models.notification import Notification
from src.database.models.resident_profile import ResidentProfile
from src.database.models.ticket import Ticket
from src.models.api.errors import CATEGORY_REQUIRED, DomainError
from src.models.enums import Category, NotificationChannel, NotificationStatus, TicketStatus
from src.request_context import request_id_context
from src.services.scoring_service import ScoringService


class CoordinatorScoringSupport:
    def __init__(self, db: Session, scoring: ScoringService) -> None:
        self.db = db
        self.scoring = scoring

    def apply_scoring(self, ticket: Ticket, category: CategoryCatalog) -> None:
        location = ticket.location
        if category.is_active and category.base_score is None:
            raise DomainError(
                CATEGORY_REQUIRED,
                "Category does not have a valid scoring configuration.",
                409,
            )
        result = self.scoring.calculate_dynamic(
            category_code=category.code,
            base_score=category.base_score,
            severity=ticket.severity,
            location_type_code=location.location_type.code if location else None,
            density_count=self._density_count(ticket, category),
            red_flag_detected=ticket.red_flag_detected,
            priority_ceiling=category.priority_ceiling,
        )
        ticket.score_total = result.score_total
        ticket.priority = result.priority_final
        self.recalculate_sla(ticket)

    def recalculate_sla(self, ticket: Ticket) -> None:
        if ticket.priority is None:
            ticket.sla_due_at = None
            return
        started = ticket.sla_started_at or ticket.created_at
        ticket.sla_started_at = started
        ticket.sla_due_at = started + self.scoring.sla_duration[ticket.priority]

    def _density_count(self, ticket: Ticket, category: CategoryCatalog) -> int:
        if category.code != Category.WATER.value or ticket.location is None:
            return 1
        current_floor = ticket.location.floor
        window_start = ticket.created_at - timedelta(days=3)
        query = (
            select(func.count(func.distinct(Ticket.source_unit_id)))
            .join(Location, Location.id == Ticket.location_id)
            .join(CategoryCatalog, CategoryCatalog.id == Ticket.category_id)
            .join(Floor, Floor.id == Location.floor_id)
            .where(
                CategoryCatalog.code == category.code,
                Ticket.created_at >= window_start,
                Floor.adjacency_index.between(current_floor.adjacency_index - 1, current_floor.adjacency_index + 1),
            )
        )
        return max(1, int(self.db.scalar(query) or 0))


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
        completed = list(self.db.scalars(select(Ticket).where(Ticket.status == TicketStatus.COMPLETED, Ticket.completed_at.is_not(None))))
        on_time = sum(1 for ticket in completed if ticket.sla_due_at and ticket.completed_at <= ticket.sla_due_at)
        total = len(completed)
        return {"completed_total": total, "completed_on_time": on_time, "compliance_rate": (on_time / total) if total else None}
