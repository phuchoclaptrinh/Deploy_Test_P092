"""add technician availability events

Revision ID: 9c0d1e2f3a4b
Revises: 8b9c0d1e2f3a
Create Date: 2026-08-23 12:00:00.000000

Business spec §2.13 reports "Ngày hoạt động" per Technician: the number of days
readiness was switched on inside the reporting period. `technician_profiles`
keeps only the current flag, so that count is unrecoverable once the flag moves.

This revision adds the smallest auditable source for it — one row per readiness
transition, with the actor who caused it — and seeds one row per existing
Technician so the current state has a start point instead of an empty history.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9c0d1e2f3a4b"
down_revision: str | Sequence[str] | None = "8b9c0d1e2f3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "technician_availability_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "technician_id",
            sa.Uuid(),
            sa.ForeignKey("technician_profiles.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column(
            "changed_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("user_profiles.user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="SYSTEM"),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_technician_availability_events_technician_id",
        "technician_availability_events",
        ["technician_id"],
    )
    op.create_index(
        "ix_technician_availability_events_technician_changed",
        "technician_availability_events",
        ["technician_id", "changed_at"],
    )

    # Without a seed row an existing Technician has no history at all, and the
    # report would read "0 active days" for someone who has been available the
    # whole time. The seed states what the profile already says, dated from when
    # the profile was created, and is attributed to no actor.
    is_postgres = op.get_bind().dialect.name == "postgresql"
    new_id = "gen_random_uuid()" if is_postgres else "lower(hex(randomblob(16)))"
    op.execute(
        sa.text(
            f"""
            INSERT INTO technician_availability_events (id, technician_id, is_available, source, changed_at)
            SELECT {new_id}, user_id, is_available, 'MIGRATION_BACKFILL', created_at
            FROM technician_profiles
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_technician_availability_events_technician_changed", table_name="technician_availability_events")
    op.drop_index("ix_technician_availability_events_technician_id", table_name="technician_availability_events")
    op.drop_table("technician_availability_events")
