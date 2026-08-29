"""Resident notification persistence model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base
from src.models.enums import NotificationChannel, NotificationStatus

if TYPE_CHECKING:
    from src.database.models.ticket import Ticket

JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")


def enum_values(enum_class):
    return [member.value for member in enum_class]


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_recipient_status_created_at", "recipient_user_id", "status", "created_at"),
        Index("ix_notifications_ticket_id", "ticket_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    recipient_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="RESTRICT"), nullable=False
    )
    ticket_id: Mapped[UUID | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    notification_type: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[NotificationChannel] = mapped_column(
        SQLEnum(NotificationChannel, name="notification_channel_enum", native_enum=True, values_callable=enum_values),
        nullable=False,
        default=NotificationChannel.IN_APP,
        server_default=NotificationChannel.IN_APP.value,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict, server_default="{}")
    status: Mapped[NotificationStatus] = mapped_column(
        SQLEnum(NotificationStatus, name="notification_status_enum", native_enum=True, values_callable=enum_values),
        nullable=False,
        default=NotificationStatus.PENDING,
        server_default=NotificationStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ticket: Mapped[Ticket | None] = relationship(back_populates="notifications")
