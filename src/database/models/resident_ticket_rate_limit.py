"""Resident ticket creation rate-limit state."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class ResidentTicketRateLimit(Base):
    __tablename__ = "resident_ticket_rate_limits"

    reporter_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_ticket_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ai_rejection_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_rejection_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    block_reason: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
