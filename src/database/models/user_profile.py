"""Application user profile backed by Supabase Auth identity."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base
from src.models.enums import UserRole

if TYPE_CHECKING:
    from src.database.models.resident_profile import ResidentProfile
    from src.database.models.technician import TechnicianProfile


def enum_values(enum_class: type[UserRole]) -> list[str]:
    return [member.value for member in enum_class]


class UserProfile(Base):
    """Business authorization profile. Passwords/OTPs remain in Supabase Auth."""

    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint(
            r"phone_e164 IS NULL OR phone_e164 ~ '^\+[1-9][0-9]{6,14}$'",
            name="ck_user_profiles_phone_e164",
        ).ddl_if(dialect="postgresql"),
        Index("ix_user_profiles_phone_e164", "phone_e164", unique=True),
        Index("ix_user_profiles_role_active", "role", "is_active"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    phone_e164: Mapped[str | None] = mapped_column(String(20), nullable=True, unique=True)
    full_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        SQLEnum(UserRole, name="user_role_enum", native_enum=True, values_callable=enum_values),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    resident_profile: Mapped[ResidentProfile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    technician_profile: Mapped[TechnicianProfile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
