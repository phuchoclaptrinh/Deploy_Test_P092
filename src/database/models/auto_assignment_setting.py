"""The DIRECT auto-assignment switch.

Asymmetric on purpose. A coordinator may turn it **off** at any moment through
the ordinary settings path -- stopping autonomous assignment is never something
to make someone work for. Turning it **on** means future eligible tickets get
assigned with no human looking at them, so it happens in exactly one place: as
a consequence of confirming a real proposal batch, where a person has just seen
the concrete work being handed out.

The three `activated_*` columns record that authorisation. `updated_by_user_id`
cannot: it names whoever last touched the row, which after a later delay change
is no longer the person who approved autonomy.

Not to be confused with `assignment_proposal_schedules`, which decides how often
a *draft* table is built for review and assigns nothing.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class AutoAssignmentSetting(Base):
    __tablename__ = "auto_assignment_settings"
    __table_args__ = (
        # §7.6: a singleton the coordinator owns. Only a confirmed proposal
        # batch may flip `enabled` from false to true.
        CheckConstraint("id = 1", name="ck_auto_assignment_settings_singleton"),
        CheckConstraint(
            "activation_delay IN ('IMMEDIATE', '2_HOURS', '5_HOURS', '1_DAY', '3_DAYS')",
            name="ck_auto_assignment_settings_delay_enum",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    activation_delay: Mapped[str] = mapped_column(String(30), nullable=False, default="IMMEDIATE", server_default="IMMEDIATE")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    # Where the current ON state came from. Written together, only by
    # `AssignmentProposalService.confirm_batch`, and cleared when DIRECT is
    # switched off so a stale provenance cannot outlive the state it explained.
    activated_by_batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "assignment_proposal_batches.id",
            ondelete="SET NULL",
            name="fk_auto_assignment_settings_activated_by_batch",
        ),
        nullable=True,
    )
    activated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "user_profiles.user_id",
            ondelete="SET NULL",
            name="fk_auto_assignment_settings_activated_by_user",
        ),
        nullable=True,
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
