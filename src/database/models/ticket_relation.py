"""Explicit relations between tickets discovered by Agent or Coordinator."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.database.base import Base


class TicketRelation(Base):
    __tablename__ = "ticket_relations"
    __table_args__ = (
        UniqueConstraint("source_ticket_id", "target_ticket_id", "relation_type", name="uq_ticket_relations_pair_type"),
        # §7.7: evidence pointing at itself is not evidence.
        CheckConstraint("source_ticket_id <> target_ticket_id", name="ck_ticket_relations_not_self"),
        Index("ix_ticket_relations_source_type", "source_ticket_id", "relation_type"),
        Index("ix_ticket_relations_target_type", "target_ticket_id", "relation_type"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_ticket_id: Mapped[UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_ticket_id: Mapped[UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    analysis_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
