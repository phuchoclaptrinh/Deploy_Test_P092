"""Apartment unit catalog."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.building import Building
    from src.database.models.floor import Floor
    from src.database.models.resident_profile import ResidentProfile
    from src.database.models.ticket import Ticket


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("building_id", "unit_code", name="uq_units_building_code"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    building_id: Mapped[UUID] = mapped_column(ForeignKey("buildings.id", ondelete="RESTRICT"), nullable=False)
    floor_id: Mapped[UUID] = mapped_column(ForeignKey("floors.id", ondelete="RESTRICT"), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE", server_default="ACTIVE")

    building: Mapped[Building] = relationship(back_populates="units")
    floor: Mapped[Floor] = relationship(back_populates="units")
    resident_profiles: Mapped[list[ResidentProfile]] = relationship(back_populates="unit")
    tickets: Mapped[list[Ticket]] = relationship(back_populates="source_unit")

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    @property
    def building_code(self) -> str:
        return self.building.code

    @property
    def floor_code(self) -> str:
        return self.floor.floor_code
