"""The Automatic Assignment switch: one boolean, one singleton row.

Deliberately smaller than what it replaces. The previous version carried an
`activation_delay` and a set of `activated_by_batch_*` columns, because turning
the switch on used to be reachable only as a consequence of confirming a
proposal batch. §2 and §9 remove both ideas: the toggle is now a plain ON/OFF
that Building Management flips directly, after a confirmation modal, and it
depends on nothing else having happened first.

What survives is provenance. `enabled_by_user_id` and `enabled_at` record who
authorised autonomous assignment and when -- separately from `updated_by_user_id`,
which merely names whoever last wrote the row. Turning the switch off clears
them, because an explanation for a state that no longer holds is worse than no
explanation at all.

Turning it off stops *future* automatic assignment only. It never unwinds an
assignment already made; there is no column here that could express that, and
that absence is intentional.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class AutoAssignmentSetting(Base):
    __tablename__ = "auto_assignment_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_auto_assignment_settings_singleton"),
        # An enabled switch must name who enabled it. Without this the audit
        # question "who turned autonomous assignment on?" has no answer for any
        # row written by a path that forgot to set it.
        CheckConstraint(
            "enabled = false OR (enabled_by_user_id IS NOT NULL AND enabled_at IS NOT NULL)",
            name="ck_auto_assignment_settings_enabled_has_actor",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    #: Optimistic concurrency, so two coordinators cannot both think they won.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    enabled_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "user_profiles.user_id",
            ondelete="SET NULL",
            name="fk_auto_assignment_settings_enabled_by_user",
        ),
        nullable=True,
    )
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


__all__ = ["AutoAssignmentSetting"]
