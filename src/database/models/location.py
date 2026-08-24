"""Canonical incident location selected by residents."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.building import Building
    from src.database.models.floor import Floor
    from src.database.models.location_type import LocationType
    from src.database.models.ticket import Ticket
    from src.database.models.unit import Unit


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (Index("ix_locations_floor_type_active", "floor_id", "location_type_id", "is_active"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    building_id: Mapped[UUID] = mapped_column(ForeignKey("buildings.id", ondelete="CASCADE"), nullable=False)
    floor_id: Mapped[UUID] = mapped_column(ForeignKey("floors.id", ondelete="CASCADE"), nullable=False)
    location_type_id: Mapped[UUID] = mapped_column(
        ForeignKey("location_types.id", ondelete="RESTRICT"), nullable=False
    )
    unit_id: Mapped[UUID | None] = mapped_column(ForeignKey("units.id", ondelete="SET NULL"), nullable=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    building: Mapped[Building] = relationship()
    floor: Mapped[Floor] = relationship(back_populates="locations")
    location_type: Mapped[LocationType] = relationship(back_populates="locations")
    unit: Mapped[Unit | None] = relationship()
    tickets: Mapped[list[Ticket]] = relationship(back_populates="location")
