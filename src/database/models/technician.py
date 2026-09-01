"""Trusted Technician profile and skill metadata."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.category import CategoryCatalog
    from src.database.models.technician_availability import TechnicianAvailabilityEvent
    from src.database.models.ticket_assignment import TicketAssignment
    from src.database.models.user_profile import UserProfile


class TechnicianProfile(Base):
    __tablename__ = "technician_profiles"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[UserProfile] = relationship(back_populates="technician_profile")
    skills: Mapped[list[TechnicianSkill]] = relationship(back_populates="technician", cascade="all, delete-orphan")
    assignments: Mapped[list[TicketAssignment]] = relationship(back_populates="technician")
    availability_events: Mapped[list[TechnicianAvailabilityEvent]] = relationship(
        back_populates="technician", cascade="all, delete-orphan", order_by="TechnicianAvailabilityEvent.changed_at"
    )


class TechnicianSkill(Base):
    __tablename__ = "technician_skills"
    __table_args__ = (UniqueConstraint("technician_id", "category_id", name="uq_technician_skills_category"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    technician_id: Mapped[UUID] = mapped_column(
        ForeignKey("technician_profiles.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[UUID] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    technician: Mapped[TechnicianProfile] = relationship(back_populates="skills")
    category: Mapped[CategoryCatalog] = relationship()
