"""add resident ticket rate limits

Revision ID: 2b3c4d5e6f7a
Revises: 1a2b3c4d5e6f
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2b3c4d5e6f7a"
down_revision: str | Sequence[str] | None = "1a2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resident_ticket_rate_limits",
        sa.Column("reporter_user_id", sa.Uuid(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ticket_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("block_reason", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["reporter_user_id"], ["user_profiles.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("reporter_user_id"),
    )
    op.create_index("ix_resident_ticket_rate_limits_blocked_until", "resident_ticket_rate_limits", ["blocked_until"])


def downgrade() -> None:
    op.drop_index("ix_resident_ticket_rate_limits_blocked_until", table_name="resident_ticket_rate_limits")
    op.drop_table("resident_ticket_rate_limits")
