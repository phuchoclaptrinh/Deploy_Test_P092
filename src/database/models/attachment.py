"""Private ticket attachment metadata."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base
from src.models.enums import AttachmentType, ImageQualityStatus

if TYPE_CHECKING:
    from src.database.models.ticket import Ticket


def enum_values(enum_class):
    return [member.value for member in enum_class]


class TicketAttachment(Base):
    __tablename__ = "ticket_attachments"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    attachment_type: Mapped[AttachmentType] = mapped_column(
        SQLEnum(AttachmentType, name="attachment_type_enum", native_enum=True, values_callable=enum_values),
        nullable=False,
        default=AttachmentType.ISSUE_ORIGINAL,
        server_default=AttachmentType.ISSUE_ORIGINAL.value,
    )
    storage_bucket: Mapped[str] = mapped_column(String(100), nullable=False, default="ticket-attachments", server_default="ticket-attachments")
    object_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_quality_status: Mapped[ImageQualityStatus | None] = mapped_column(
        SQLEnum(ImageQualityStatus, name="image_quality_status_enum", native_enum=True, values_callable=enum_values),
        nullable=True,
    )
    uploaded_by: Mapped[UUID] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    ticket: Mapped[Ticket] = relationship(back_populates="attachments")

    # Compatibility accessors used by the Storage service/API.
    @property
    def file_url(self) -> str:
        return self.object_path

    @property
    def file_size(self) -> int | None:
        return self.size_bytes
