"""Ticket attachment metadata persistence."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models.attachment import TicketAttachment
from src.database.models.ticket_attachment_upload_session import TicketAttachmentUploadSession
from src.models.enums import AttachmentType, ImageQualityStatus


class AttachmentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_from_upload_sessions(
        self,
        ticket_id: UUID,
        owner_user_id: UUID,
        upload_sessions: list[TicketAttachmentUploadSession],
        *,
        attachment_type: AttachmentType = AttachmentType.ISSUE_ORIGINAL,
    ) -> list[TicketAttachment]:
        rows = [
            TicketAttachment(
                ticket_id=ticket_id,
                attachment_type=attachment_type,
                storage_bucket="ticket-attachments",
                object_path=session.storage_path,
                mime_type=session.mime_type,
                size_bytes=session.file_size,
                uploaded_by=owner_user_id,
                image_quality_status=ImageQualityStatus.PENDING,
            )
            for session in upload_sessions
        ]
        self.db.add_all(rows)
        self.db.flush()
        return rows

    def get_attachment(self, ticket_id: UUID, attachment_id: UUID) -> TicketAttachment | None:
        return self.db.scalar(
            select(TicketAttachment).where(
                TicketAttachment.ticket_id == ticket_id,
                TicketAttachment.id == attachment_id,
            )
        )

    def list_issue_original(self, ticket_id: UUID) -> list[TicketAttachment]:
        return list(
            self.db.scalars(
                select(TicketAttachment)
                .where(
                    TicketAttachment.ticket_id == ticket_id,
                    TicketAttachment.attachment_type == AttachmentType.ISSUE_ORIGINAL,
                )
                .order_by(TicketAttachment.created_at, TicketAttachment.id)
            )
        )

    def get_latest_resident_supplement(self, ticket_id: UUID) -> TicketAttachment | None:
        return self.db.scalar(
            select(TicketAttachment)
            .where(
                TicketAttachment.ticket_id == ticket_id,
                TicketAttachment.attachment_type == AttachmentType.RESIDENT_SUPPLEMENT,
            )
            .order_by(TicketAttachment.created_at.desc(), TicketAttachment.id.desc())
            .limit(1)
        )
