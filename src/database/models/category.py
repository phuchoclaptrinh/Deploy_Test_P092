"""Category catalog and priority ceiling."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base
from src.models.enums import Priority

if TYPE_CHECKING:
    from src.database.models.ticket import Ticket


def enum_values(enum_class):
    return [member.value for member in enum_class]


class CategoryCatalog(Base):
    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint(
            "code = upper(code) AND code ~ '^[A-Z][A-Z0-9_]{1,79}$'",
            name="ck_categories_code_machine_format",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "is_active = false OR base_score IS NOT NULL",
            name="ck_categories_active_base_score_required",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    base_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority_ceiling: Mapped[Priority | None] = mapped_column(
        SQLEnum(Priority, name="priority_level_enum", native_enum=True, values_callable=enum_values),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    tickets: Mapped[list[Ticket]] = relationship(back_populates="category")
