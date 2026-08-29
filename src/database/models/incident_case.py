"""Incident grouping for physically spreading water/electrical issues."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.category import CategoryCatalog
    from src.database.models.incident_case_member import IncidentCaseMember


class IncidentCase(Base):
    __tablename__ = "incident_cases"
    __table_args__ = (
        # §7.9: a case holds at most five members. Overflow opens the next case
        # in the same series instead of moving anyone, so the series needs its
        # own identity and a stable order.
        UniqueConstraint("series_id", "sequence_no", name="uq_incident_cases_series_sequence"),
        CheckConstraint("sequence_no >= 1", name="ck_incident_cases_sequence_positive"),
        CheckConstraint("density_value >= 1", name="ck_incident_cases_density_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    # Defaults to the case id for a first case, so a single-case series is
    # still addressable through the same column.
    series_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True, default=uuid4)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    category_id: Mapped[UUID] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="OPEN", server_default="OPEN")
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    density_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    category: Mapped[CategoryCatalog] = relationship()
    members: Mapped[list[IncidentCaseMember]] = relationship(back_populates="case", cascade="all, delete-orphan")
