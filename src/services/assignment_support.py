"""Shared support for assignment workflow side effects."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models.audit_log import AuditLog
from src.database.models.notification import Notification
from src.database.models.resident_profile import ResidentProfile
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.models.api.errors import COMPLETION_EVIDENCE_REQUIRED, STORAGE_NOT_CONFIGURED, DomainError
from src.models.enums import AssignmentStatus, NotificationChannel, NotificationStatus
from src.repositories.upload_session_repository import UploadSessionRepository
from src.request_context import request_id_context
from src.services.storage_service import StorageService

#: The one thing a technician is told when work lands on them, shared by all
#: three paths that create an assignment -- manual, Visual Assignment board, and
#: automatic dispatch -- so the wording cannot drift between them.
#:
#: What it does *not* say is the point. There is no acknowledgement step to ask
#: for any more, so it never says "xác nhận nhận việc"; and no start deadline
#: has been approved, so it promises no time, threatens no timeout and implies
#: no automatic reassignment. It points at the queue and leaves the timing to
#: the schedule the technician can already see.
NEW_ASSIGNMENT_TITLE = "Bạn có công việc mới"
_NEW_ASSIGNMENT_GUIDANCE = (
    "Hãy xem thứ tự hàng việc và bắt đầu công việc “Làm ngay” khi sẵn sàng."
)
NEW_ASSIGNMENT_BODY_COORDINATOR = f"BQL đã phân công một phản ánh cho bạn. {_NEW_ASSIGNMENT_GUIDANCE}"
NEW_ASSIGNMENT_BODY_AUTOMATIC = f"Hệ thống đã phân công một phản ánh cho bạn. {_NEW_ASSIGNMENT_GUIDANCE}"


class AssignmentEvidenceVerifier:
    def __init__(self, db: Session, storage: StorageService) -> None:
        self.db = db
        self.uploads = UploadSessionRepository(db)
        self.storage = storage

    def lock_and_verify_completion_sessions(self, technician_id: UUID, upload_ids: list[UUID]):
        sessions = self.uploads.lock_upload_sessions(upload_ids)
        by_id = {row.id: row for row in sessions}
        if set(by_id) != set(upload_ids):
            raise DomainError(COMPLETION_EVIDENCE_REQUIRED, "Upload session không hợp lệ.", 400)
        ordered = [by_id[upload_id] for upload_id in upload_ids]
        now = datetime.now(UTC)
        for session in ordered:
            if session.owner_user_id != technician_id:
                raise DomainError(COMPLETION_EVIDENCE_REQUIRED, "Upload session không thuộc Technician.", 400)
            if session.status != "pending" or session.consumed_at is not None:
                raise DomainError(COMPLETION_EVIDENCE_REQUIRED, "Upload session đã được sử dụng.", 400)
            expires_at = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                raise DomainError(COMPLETION_EVIDENCE_REQUIRED, "Upload session đã hết hạn.", 400)
            if not self.storage.is_owned_completion_evidence_path(session.storage_path, technician_id):
                raise DomainError(COMPLETION_EVIDENCE_REQUIRED, "Upload session không hợp lệ.", 400)
            try:
                verified = self.storage.verify_uploaded_object(
                    session.storage_path,
                    expected_mime_type=session.mime_type,
                    expected_file_size=session.file_size,
                )
            except DomainError as exc:
                if exc.code == STORAGE_NOT_CONFIGURED:
                    raise
                raise DomainError(COMPLETION_EVIDENCE_REQUIRED, exc.message, 400) from exc
            session.object_verified_at = verified.verified_at
            session.updated_at = verified.verified_at
        self.db.flush()
        return ordered


class AssignmentSideEffects:
    def __init__(self, db: Session) -> None:
        self.db = db

    def audit_assignment_transition(
        self,
        actor_user_id: UUID,
        assignment: TicketAssignment,
        old_status: AssignmentStatus,
        action: str,
        reason: str | None = None,
    ) -> None:
        self.audit(
            actor_user_id,
            action,
            "TICKET_ASSIGNMENT",
            assignment.id,
            {"status": old_status.value},
            {"status": assignment.status.value, "ticket_id": str(assignment.ticket_id)},
            reason,
            "TECHNICIAN",
        )

    def audit(
        self,
        actor_user_id: UUID,
        action: str,
        entity_type: str,
        entity_id: UUID,
        before_data: dict[str, object] | None,
        after_data: dict[str, object] | None,
        reason: str | None,
        actor_role: str,
    ) -> None:
        self.db.add(
            AuditLog(
                actor_user_id=actor_user_id,
                actor_role=actor_role,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                before_data=before_data,
                after_data=after_data,
                reason=reason,
                request_id=UUID(request_id) if (request_id := request_id_context.get()) else None,
            )
        )

    def unit_recipients(self, tickets: list[Ticket]) -> dict[UUID, list[UUID]]:
        """Residents to notify, for several tickets, in one statement.

        The per-ticket `notify_unit` is fine for a single lifecycle action. A
        dispatch micro-batch writes up to twenty at once, and twenty recipient
        lookups is exactly the per-ticket query §8 rules out -- so the batch
        path resolves them together and passes them in.
        """
        unit_ids = {ticket.source_unit_id for ticket in tickets}
        if not unit_ids:
            return {}
        rows = self.db.execute(
            select(ResidentProfile.unit_id, ResidentProfile.user_id).where(
                ResidentProfile.unit_id.in_(unit_ids)
            )
        ).all()
        grouped: dict[UUID, list[UUID]] = {}
        for unit_id, user_id in rows:
            grouped.setdefault(unit_id, []).append(user_id)
        return grouped

    def notify_unit(
        self,
        ticket: Ticket,
        event: str,
        title: str,
        body: str,
        recipients: list[UUID] | None = None,
    ) -> None:
        if recipients is None:
            recipients = list(
                self.db.scalars(
                    select(ResidentProfile.user_id).where(ResidentProfile.unit_id == ticket.source_unit_id)
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
                    payload={"ticket_id": str(ticket.id), "status": ticket.status.value},
                    status=NotificationStatus.PENDING,
                )
            )

    def notify_coordinators(
        self,
        ticket: Ticket,
        event: str,
        title: str,
        body: str,
        payload_extra: dict[str, object] | None = None,
    ) -> None:
        """§8.1: coordinators are told about anything that needs a human.

        `payload_extra` carries operational facts — a job id, a rejection reason
        — and never a raw model response (§9).
        """
        from src.database.models.user_profile import UserProfile
        from src.models.enums import UserRole

        recipients = list(
            self.db.scalars(
                select(UserProfile.user_id).where(
                    UserProfile.role == UserRole.COORDINATOR,
                    UserProfile.is_active.is_(True),
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
                    payload={"ticket_id": str(ticket.id), **(payload_extra or {})},
                    status=NotificationStatus.PENDING,
                )
            )

    def notify_technician(self, assignment: TicketAssignment, event: str, title: str, body: str) -> None:
        self.db.add(
            Notification(
                recipient_user_id=assignment.technician_id,
                ticket_id=assignment.ticket_id,
                notification_type=event,
                channel=NotificationChannel.IN_APP,
                title=title,
                body=body,
                payload={"ticket_id": str(assignment.ticket_id), "assignment_id": str(assignment.id)},
                status=NotificationStatus.PENDING,
            )
        )
