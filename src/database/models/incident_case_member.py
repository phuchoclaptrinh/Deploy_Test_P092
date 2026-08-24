"""Membership of tickets in a spreading-incident case."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.incident_case import IncidentCase
    from src.database.models.ticket import Ticket


class IncidentCaseMember(Base):
    __tablename__ = "incident_case_members"
    __table_args__ = (
        UniqueConstraint("case_id", "ticket_id", name="uq_incident_case_members_case_ticket"),
    )

    case_id: Mapped[UUID] = mapped_column(ForeignKey("incident_cases.id", ondelete="CASCADE"), primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    source_unit_id: Mapped[UUID] = mapped_column(ForeignKey("units.id", ondelete="RESTRICT"), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    case: Mapped[IncidentCase] = relationship(back_populates="members")
    ticket: Mapped[Ticket] = relationship()
