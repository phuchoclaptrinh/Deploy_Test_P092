"""Building catalog."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.floor import Floor
    from src.database.models.unit import Unit


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    floors: Mapped[list[Floor]] = relationship(back_populates="building")
    units: Mapped[list[Unit]] = relationship(back_populates="building")
