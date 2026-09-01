"""Resident-specific profile binding one Auth identity to one unit."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.unit import Unit
    from src.database.models.user_profile import UserProfile


class ResidentProfile(Base):
    __tablename__ = "resident_profiles"
    __table_args__ = (
        Index("ix_resident_profiles_unit", "unit_id"),
        Index(
            "uq_resident_profiles_one_primary_per_unit",
            "unit_id",
            unique=True,
            postgresql_where=text("is_primary = true"),
            sqlite_where=text("is_primary = 1"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    unit_id: Mapped[UUID] = mapped_column(ForeignKey("units.id", ondelete="RESTRICT"), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[UserProfile] = relationship(back_populates="resident_profile")
    unit: Mapped[Unit] = relationship(back_populates="resident_profiles")
