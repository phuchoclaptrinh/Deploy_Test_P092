"""One-time signed upload session owned by a resident Auth user."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.database.base import Base


class TicketAttachmentUploadSession(Base):
    __tablename__ = "ticket_attachment_upload_sessions"
    __table_args__ = (
        CheckConstraint("file_size > 0", name="ck_ticket_attachment_upload_sessions_file_size_positive"),
        CheckConstraint(
            "mime_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="ck_ticket_attachment_upload_sessions_mime_type",
        ),
        CheckConstraint("status IN ('pending', 'consumed', 'expired')", name="ck_ticket_attachment_upload_sessions_status"),
        UniqueConstraint("storage_path", name="uq_ticket_attachment_upload_sessions_storage_path"),
        Index("ix_ticket_attachment_upload_sessions_owner_status", "owner_user_id", "status"),
        Index("ix_ticket_attachment_upload_sessions_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="RESTRICT"), nullable=False
    )
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    object_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
