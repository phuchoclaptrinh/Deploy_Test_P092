"""Versioned scoring configuration."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.database.base import Base

JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")


class ScoringRuleVersion(Base):
    __tablename__ = "scoring_rule_versions"
    __table_args__ = (
        Index("ix_scoring_rule_versions_active", "is_active"),
        Index(
            "uq_scoring_rule_versions_one_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    version: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    config: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
