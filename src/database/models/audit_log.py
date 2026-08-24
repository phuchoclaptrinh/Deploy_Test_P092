"""Append-only audit events for manual review, override, and status-sensitive operations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.database.base import Base

JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")
AUDIT_ID_TYPE = BigInteger().with_variant(Integer(), "sqlite")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id", "created_at"),
        Index("ix_audit_logs_actor_created_at", "actor_user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(AUDIT_ID_TYPE, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    actor_role: Mapped[str] = mapped_column(String(50), nullable=False, default="SYSTEM", server_default="SYSTEM")
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    before_data: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE, nullable=True)
    after_data: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
