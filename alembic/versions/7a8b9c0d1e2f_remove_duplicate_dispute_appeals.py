"""remove duplicate dispute appeals

Revision ID: 7a8b9c0d1e2f
Revises: 6f7a8b9c0d1e
Create Date: 2026-08-23 10:00:00.000000

The resident "Sự cố của tôi khác" appeal and the Building Management
dispute-resolution queue are gone from the product. Duplicate *detection* and
*linking* are untouched: `tickets.duplicate_of_ticket_id`, `duplicate_linked_at`,
`duplicate_reason`, `duplicate_analysis_run_id`, the `LINKED_DUPLICATE` status
and both duplicate check constraints all stay exactly as they are.

What goes is the appeal's own persistence and nothing else:

* the `duplicate_disputes` table, and
* `tickets.duplicate_disputed_at`, which only ever recorded when an appeal was
  opened.

This is a forward corrective migration. The v4 revisions that introduced these
objects (`1a2b3c4d5e6f`, `4d5e6f7a8b9c`, `5e6f7a8b9c0d`) are immutable and still
mention the feature; that is expected and is why this revision exists.

The downgrade rebuilds the table and the column so the chain stays structurally
valid, but the rows themselves are not recoverable — the appeal data is dropped
here, not archived.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7a8b9c0d1e2f"
down_revision: str | Sequence[str] | None = "6f7a8b9c0d1e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "duplicate_disputes" in inspector.get_table_names():
        for index in ("uq_duplicate_disputes_one_open_per_ticket", "ix_duplicate_disputes_status_created_at"):
            op.execute(sa.text(f"DROP INDEX IF EXISTS {index}"))
        op.drop_table("duplicate_disputes")

    if "duplicate_disputed_at" in {column["name"] for column in inspector.get_columns("tickets")}:
        with op.batch_alter_table("tickets") as batch:
            batch.drop_column("duplicate_disputed_at")


def downgrade() -> None:
    """Structural rebuild only: the appeal rows are not restored."""
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(sa.Column("duplicate_disputed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "duplicate_disputes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "master_ticket_id_at_request",
            sa.Uuid(),
            sa.ForeignKey("tickets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "requested_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("user_profiles.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="OPEN"),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column(
            "decided_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("user_profiles.user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "result_notification_id",
            sa.Uuid(),
            sa.ForeignKey("notifications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('OPEN', 'KEEP_LINKED', 'SPLIT_INDEPENDENT')",
            name="ck_duplicate_disputes_status_enum",
        ),
    )
    op.create_index("ix_duplicate_disputes_ticket_id", "duplicate_disputes", ["ticket_id"])
    op.create_index(
        "ix_duplicate_disputes_master_ticket_id_at_request",
        "duplicate_disputes",
        ["master_ticket_id_at_request"],
    )
    op.create_index("ix_duplicate_disputes_requested_by_user_id", "duplicate_disputes", ["requested_by_user_id"])
    op.create_index("ix_duplicate_disputes_status_created_at", "duplicate_disputes", ["status", "requested_at"])
    op.create_index(
        "uq_duplicate_disputes_one_open_per_ticket",
        "duplicate_disputes",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
        sqlite_where=sa.text("status = 'OPEN'"),
    )
