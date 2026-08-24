"""Append-only business lifecycle history."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base
from src.models.enums import TicketStatus

if TYPE_CHECKING:
    from src.database.models.ticket import Ticket


def enum_values(enum_class):
    return [member.value for member in enum_class]


class TicketStatusHistory(Base):
    __tablename__ = "ticket_status_history"
    __table_args__ = (Index("ix_ticket_status_history_ticket_created_at", "ticket_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    from_status: Mapped[TicketStatus | None] = mapped_column(
        SQLEnum(TicketStatus, name="ticket_status_v2_enum", native_enum=True, values_callable=enum_values), nullable=True
    )
    to_status: Mapped[TicketStatus] = mapped_column(
        SQLEnum(TicketStatus, name="ticket_status_v2_enum", native_enum=True, values_callable=enum_values), nullable=False
    )
    changed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    ticket: Mapped[Ticket] = relationship(back_populates="status_history")
