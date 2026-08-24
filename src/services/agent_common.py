"""Shared policy and persistence helpers for Agent v3 backend services."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from src.database.models.ai_agent_session import AIAgentToolCall, AIAnalysisSession
from src.database.models.audit_log import AuditLog
from src.database.models.location import Location
from src.database.models.notification import Notification
from src.database.models.resident_profile import ResidentProfile
from src.database.models.ticket import Ticket
from src.models.api.errors import NO_ACTIVE_UNIT, TICKET_NOT_FOUND, DomainError
from src.models.enums import (
    ClassificationStatus,
    InvalidReason,
    NotificationChannel,
    NotificationStatus,
    TicketStatus,
)
from src.repositories.attachment_repository import AttachmentRepository
from src.repositories.catalog_repository import CatalogRepository
from src.repositories.ticket_repository import TicketRepository
from src.repositories.upload_session_repository import UploadSessionRepository
from src.request_context import request_id_context
from src.services.storage_service import StorageService

MAX_TOOL_CALLS = 5
MAX_ASK_ROUNDS = 3
MAX_WAIT_SECONDS = 300
GROUPING_CODES = {"WATER_LEAK", "ELECTRICAL_SHORT"}


class AgentServiceBase:
    def __init__(self, db: Session, storage: StorageService | None = None) -> None:
        self.db = db
        self.storage = storage or StorageService()
        self.catalog = CatalogRepository(db)
        self.tickets = TicketRepository(db)
        self.uploads = UploadSessionRepository(db)
        self.attachments = AttachmentRepository(db)

    def _session(
        self,
        session_id: UUID,
        *,
        lock: bool = False,
    ) -> AIAnalysisSession:
        query = select(AIAnalysisSession).where(AIAnalysisSession.id == session_id)
        if lock:
            query = query.with_for_update()
        session = self.db.scalar(query)
        if session is None:
            raise DomainError(
                TICKET_NOT_FOUND,
                "Analysis session not found.",
                404,
            )
        return session

    def _ticket(self, ticket_id: UUID) -> Ticket:
        ticket = self.db.scalar(
            select(Ticket)
            .where(Ticket.id == ticket_id)
            .options(
                joinedload(Ticket.category),
                joinedload(Ticket.location).joinedload(Location.floor),
                joinedload(Ticket.location).joinedload(Location.building),
                selectinload(Ticket.ai_analysis_runs),
            )
        )
        if ticket is None:
            raise DomainError(TICKET_NOT_FOUND, "Ticket not found.", 404)
        return ticket

    def _reporter_ticket(
        self,
        resident_profile: ResidentProfile | None,
        viewer_user_id: UUID,
        ticket_id: UUID,
    ) -> Ticket:
        """The AI conversation belongs to the person who started it.

        Reading the pending question and answering it are both reporter-only:
        a housemate must not be able to steer the analysis of a report they did
        not send, and during the private phase they must not even learn it
        exists. Both failures return the same 404 as a missing ticket.
        """
        if resident_profile is None:
            raise DomainError(
                NO_ACTIVE_UNIT,
                "Resident has no active unit.",
                400,
            )
        ticket = self.tickets.get_resident_ticket(
            resident_profile.unit_id,
            ticket_id,
        )
        if ticket is None or ticket.reporter_user_id != viewer_user_id:
            raise DomainError(TICKET_NOT_FOUND, "Ticket not found.", 404)
        return ticket

    @staticmethod
    def _validate_session_ticket(
        session: AIAnalysisSession,
        ticket_id: UUID,
    ) -> None:
        if session.ticket_id != ticket_id:
            raise DomainError(
                TICKET_NOT_FOUND,
                "Ticket does not belong to analysis session.",
                404,
            )

    def _increment_tool(self, session: AIAnalysisSession) -> None:
        from src.models.api.errors import INVALID_STATUS_TRANSITION

        if session.status != "RUNNING":
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Analysis session is not running.",
                409,
            )
        if session.total_tool_calls >= MAX_TOOL_CALLS:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Agent tool budget exceeded.",
                409,
            )
        session.total_tool_calls += 1
        session.updated_at = datetime.now(UTC)
        self.db.flush()

    def _log_tool(
        self,
        session: AIAnalysisSession,
        tool_name: str,
        request: dict[str, object],
        response: dict[str, object],
        success: bool = True,
    ) -> None:
        self.db.add(
            AIAgentToolCall(
                session_id=session.id,
                sequence=session.total_tool_calls,
                tool_name=tool_name,
                sanitized_request=request,
                sanitized_response=response,
                success=success,
            )
        )

    @staticmethod
    def _safe_summary(ticket: Ticket) -> str:
        """A one-line description of another unit's ticket, built only from
        Backend-owned structured data.

        Contract §2.2 forbids returning the reporter, their unit, the full text
        or any photo. It permits a "sanitized summary", and the resident-written
        description is exactly where names, phone numbers and apartment numbers
        turn up, so nothing derived from it is included here. What the Agent
        gets is the Category, the shared asset label, the current state and the
        Priority — enough to judge "same asset, same kind of fault, still being
        worked on", which is what §1.5 items 4-6 actually turn on.

        If a text summary is wanted later, this is the one place to add it, and
        it needs a real redaction step rather than a truncation.
        """
        parts = [ticket.category.display_name if ticket.category else "Chưa phân loại"]
        if ticket.location is not None and ticket.location.label:
            parts.append(ticket.location.label)
        parts.append(f"trạng thái {ticket.status.value}")
        if ticket.priority is not None:
            parts.append(ticket.priority.value)
        return " · ".join(parts)

    @staticmethod
    def _snapshot_by_id(
        session: AIAnalysisSession,
    ) -> dict[str, dict[str, object]]:
        return {
            str(item["category_id"]): item
            for item in (session.category_catalog_snapshot or [])
        }

    @staticmethod
    def _density(ticket: Ticket, related: list[Ticket]) -> int:
        return len(
            {
                ticket.source_unit_id,
                *(item.source_unit_id for item in related),
            }
        )

    @staticmethod
    def _same_building_adjacent_floor(
        current: Ticket,
        candidate: Ticket,
    ) -> bool:
        if current.location is None or candidate.location is None:
            return False
        if current.location.building_id != candidate.location.building_id:
            return False
        if current.location.floor is None or candidate.location.floor is None:
            return False
        return (
            abs(
                current.location.floor.adjacency_index
                - candidate.location.floor.adjacency_index
            )
            <= 1
        )

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    def _invalidate_ticket(
        self,
        ticket: Ticket,
        reason: str,
        *,
        changed_by: UUID | None = None,
        invalid_reason: InvalidReason | None = None,
        notification_body: str = "Phản ánh hiện tại không đủ điều kiện xử lý.",
    ) -> None:
        """End a report as INVALID.

        `reason` is the internal audit string and stays in the status history;
        residents only ever see `notification_body` and the friendly text derived
        from `invalid_reason`.
        """
        old = ticket.status
        ticket.status = TicketStatus.INVALID
        ticket.classification_status = ClassificationStatus.FAILED
        ticket.priority = None
        ticket.score_total = None
        ticket.sla_due_at = None
        if invalid_reason is not None:
            ticket.invalid_reason = invalid_reason.value
        ticket.version += 1

        self.tickets.append_status_history(
            ticket,
            from_status=old,
            to_status=TicketStatus.INVALID,
            changed_by=changed_by,
            reason=reason,
        )
        self._notify_unit(
            ticket,
            "TICKET_INVALID",
            "Vui lòng gửi lại phản ánh",
            notification_body,
        )

    def _notify_unit(
        self,
        ticket: Ticket,
        event: str,
        title: str,
        body: str,
        payload_extra: dict[str, object] | None = None,
    ) -> None:
        """§8.1: every account in the reporting apartment, one notification each.

        `payload_extra` is for reduced facts about *another* ticket — a master
        reference code, its status, its due time. Anything identifying the other
        reporter must never be passed here.
        """
        recipients = list(
            self.db.scalars(
                select(ResidentProfile.user_id).where(
                    ResidentProfile.unit_id == ticket.source_unit_id
                )
            )
        )
        for user_id in recipients:
            self.db.add(
                Notification(
                    recipient_user_id=user_id,
                    ticket_id=ticket.id,
                    notification_type=event,
                    channel=NotificationChannel.IN_APP,
                    title=title,
                    body=body,
                    payload={
                        "ticket_id": str(ticket.id),
                        "status": ticket.status.value,
                        **(payload_extra or {}),
                    },
                    status=NotificationStatus.PENDING,
                )
            )

    def _audit(
        self,
        actor_user_id: UUID | None,
        action: str,
        entity_id: UUID,
        data: dict[str, object],
    ) -> None:
        self.db.add(
            AuditLog(
                actor_user_id=actor_user_id,
                actor_role=(
                    "SYSTEM"
                    if actor_user_id is None
                    else "COORDINATOR"
                ),
                action=action,
                entity_type="TICKET",
                entity_id=entity_id,
                after_data=data,
                request_id=(
                    UUID(request_id)
                    if (request_id := request_id_context.get())
                    else None
                ),
            )
        )
