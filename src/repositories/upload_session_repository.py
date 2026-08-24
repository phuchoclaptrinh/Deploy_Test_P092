"""Ticket attachment upload-session persistence."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models.ticket_attachment_upload_session import TicketAttachmentUploadSession
from src.services.storage_service import SIGNED_UPLOAD_EXPIRY_SECONDS


class UploadSessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_upload_session(
        self,
        owner_user_id: UUID,
        storage_path: str,
        original_filename: str | None,
        mime_type: str,
        file_size: int,
    ) -> TicketAttachmentUploadSession:
        row = TicketAttachmentUploadSession(
            owner_user_id=owner_user_id,
            storage_path=storage_path,
            original_filename=original_filename,
            mime_type=mime_type,
            file_size=file_size,
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(seconds=SIGNED_UPLOAD_EXPIRY_SECONDS),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def lock_upload_sessions(self, upload_ids: list[UUID]) -> list[TicketAttachmentUploadSession]:
        if not upload_ids:
            return []
        return list(
            self.db.scalars(
                select(TicketAttachmentUploadSession)
                .where(TicketAttachmentUploadSession.id.in_(upload_ids))
                .with_for_update()
            )
        )

    def mark_verified(self, sessions: list[TicketAttachmentUploadSession], verified_at: datetime) -> None:
        for session in sessions:
            session.object_verified_at = verified_at
            session.updated_at = verified_at
        self.db.flush()

    def mark_consumed(self, sessions: list[TicketAttachmentUploadSession]) -> None:
        now = datetime.now(UTC)
        for session in sessions:
            session.status = "consumed"
            session.consumed_at = now
            session.updated_at = now
        self.db.flush()
