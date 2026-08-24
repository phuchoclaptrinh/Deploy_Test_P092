"""Auditable history of a Technician's readiness to take work.

Business spec §2.13 counts "Ngày hoạt động" as the number of days a Technician
had readiness switched on during the reporting period. `technician_profiles`
only stores the *current* flag, so the count cannot be recovered from it after
the fact. This table records every transition, which is the smallest amount of
persistence that makes the metric computable and auditable.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.technician import TechnicianProfile


class TechnicianAvailabilityEvent(Base):
    """One row per readiness transition, newest state last."""

    __tablename__ = "technician_availability_events"
    __table_args__ = (
        Index("ix_technician_availability_events_technician_changed", "technician_id", "changed_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    technician_id: Mapped[UUID] = mapped_column(
        ForeignKey("technician_profiles.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Null when the system recorded the transition rather than a person: the
    # audit trail still needs to say which is which.
    changed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="SYSTEM", server_default="SYSTEM")
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    technician: Mapped[TechnicianProfile] = relationship(back_populates="availability_events")
