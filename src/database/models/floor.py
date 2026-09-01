"""Floor catalog with explicit adjacency ordering."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.location import Location
    from src.database.models.unit import Unit


class Floor(Base):
    __tablename__ = "floors"
    __table_args__ = (UniqueConstraint("floor_code", name="uq_floors_code"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    floor_code: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    adjacency_index: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    units: Mapped[list[Unit]] = relationship(back_populates="floor")
    locations: Mapped[list[Location]] = relationship(back_populates="floor")
