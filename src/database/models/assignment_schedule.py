"""The recurring proposal schedule.

This is **not** the V4 auto-assignment switch. `auto_assignment_settings`
answers "may Backend assign an approved ticket by itself, and after how long?";
this table answers "how often should Backend open a new *draft* proposal table
for a coordinator to review?". A due run of this schedule never creates an
assignment and never replaces a human confirmation — it produces a batch that
still has to be confirmed like any other.

Keeping them apart in persistence is the point. Reusing
`auto_assignment_settings.activation_delay` for a repeat interval would leave
the two meanings sharing one column, and the first change to either would
silently move the other.

A singleton, because the schedule is a property of the deployment rather than
of a coordinator: whoever configures it last owns it, and `configured_by_user_id`
records who that was.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class AssignmentProposalSchedule(Base):
    __tablename__ = "assignment_proposal_schedules"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_assignment_proposal_schedules_singleton"),
        CheckConstraint(
            "interval_code IS NULL OR interval_code IN ('2_HOURS', '1_DAY', '3_DAYS')",
            name="ck_assignment_proposal_schedules_interval_enum",
        ),
        # An enabled schedule with no interval could never become due, and an
        # enabled schedule with no next run would never fire. Both are the same
        # bug -- a schedule that looks on and does nothing -- so neither is
        # writable.
        CheckConstraint(
            "enabled = false OR (interval_code IS NOT NULL AND next_run_at IS NOT NULL)",
            name="ck_assignment_proposal_schedules_enabled_shape",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # NULL means "no repeat". The UI's `Không tự động` option.
    interval_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The batch the last due run opened, so a run that produced nothing is
    # distinguishable from one that produced a table.
    last_batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("assignment_proposal_batches.id", ondelete="SET NULL"), nullable=True
    )
    configured_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    # Optimistic concurrency, so two coordinators cannot configure it twice.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


__all__ = ["AssignmentProposalSchedule"]
