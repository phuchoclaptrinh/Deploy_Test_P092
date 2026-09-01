"""add v4 timeout support

Revision ID: 3c4d5e6f7a8b
Revises: 2b3c4d5e6f7a
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3c4d5e6f7a8b"
down_revision: str | Sequence[str] | None = "2b3c4d5e6f7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE assignment_status_enum ADD VALUE IF NOT EXISTS 'REASSIGNED'")

    with op.batch_alter_table("ticket_assignments") as batch:
        batch.add_column(sa.Column("warning_sent_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_ticket_assignments_acceptance_warning_at", ["acceptance_warning_at"])
        batch.create_index("ix_ticket_assignments_acceptance_reassign_at", ["acceptance_reassign_at"])

    with op.batch_alter_table("resident_ticket_rate_limits") as batch:
        batch.add_column(sa.Column("ai_rejection_window_started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("ai_rejection_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("resident_ticket_rate_limits") as batch:
        batch.drop_column("ai_rejection_count")
        batch.drop_column("ai_rejection_window_started_at")

    with op.batch_alter_table("ticket_assignments") as batch:
        batch.drop_index("ix_ticket_assignments_acceptance_reassign_at")
        batch.drop_index("ix_ticket_assignments_acceptance_warning_at")
        batch.drop_column("warning_sent_at")
