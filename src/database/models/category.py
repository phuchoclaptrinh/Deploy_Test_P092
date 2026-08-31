"""The category catalog.

Deliberately thin since risk scoring v2. A category is a routing and reporting
label: which skill the work needs, and which bucket the monthly report counts it
in. It carries no `base_score` and no `priority_ceiling` any more, because
priority is now computed from the five criteria in `docs/risk_scoring_v2.md` and
nothing about the bucket a ticket was filed under may move it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.ticket import Ticket


class CategoryCatalog(Base):
    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint(
            "code = upper(code) AND code ~ '^[A-Z][A-Z0-9_]{1,79}$'",
            name="ck_categories_code_machine_format",
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    tickets: Mapped[list[Ticket]] = relationship(back_populates="category")
