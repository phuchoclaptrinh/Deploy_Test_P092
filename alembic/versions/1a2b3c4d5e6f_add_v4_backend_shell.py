"""add v4 backend shell tables and columns

Revision ID: 1a2b3c4d5e6f
Revises: 0f1a2b3c4d5e
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1a2b3c4d5e6f"
down_revision: str | Sequence[str] | None = "0f1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE ticket_status_v2_enum ADD VALUE IF NOT EXISTS 'LINKED_DUPLICATE'")
        op.execute("ALTER TYPE assignment_status_enum ADD VALUE IF NOT EXISTS 'REJECTED'")

    with op.batch_alter_table("tickets") as batch:
        batch.add_column(sa.Column("duplicate_of_ticket_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("invalid_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("reassignment_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("auto_assignment_paused", sa.Boolean(), nullable=False, server_default=sa.text("false")))
        batch.add_column(sa.Column("auto_assignment_pause_reason", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_tickets_duplicate_of_ticket_id_tickets",
            "tickets",
            ["duplicate_of_ticket_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_tickets_duplicate_of_ticket_id", ["duplicate_of_ticket_id"])

    with op.batch_alter_table("ticket_assignments") as batch:
        batch.alter_column("assigned_by_user_id", existing_type=sa.Uuid(), nullable=True)
        batch.add_column(sa.Column("assignment_source", sa.String(length=40), nullable=False, server_default="MANUAL"))
        batch.add_column(sa.Column("reject_reason", sa.String(length=500), nullable=True))
        batch.add_column(sa.Column("acceptance_warning_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("acceptance_reassign_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "duplicate_disputes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("master_ticket_id_at_request", sa.Uuid(), nullable=True),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="OPEN"),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("result_notification_id", sa.Uuid(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["user_profiles.user_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["master_ticket_id_at_request"], ["tickets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["user_profiles.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["result_notification_id"], ["notifications.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_duplicate_disputes_ticket_id", "duplicate_disputes", ["ticket_id"])
    op.create_index(
        "ix_duplicate_disputes_master_ticket_id_at_request",
        "duplicate_disputes",
        ["master_ticket_id_at_request"],
    )
    op.create_index(
        "ix_duplicate_disputes_requested_by_user_id",
        "duplicate_disputes",
        ["requested_by_user_id"],
    )
    op.create_index("ix_duplicate_disputes_status_created_at", "duplicate_disputes", ["status", "requested_at"])
    op.create_index(
        "uq_duplicate_disputes_one_open_per_ticket",
        "duplicate_disputes",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
        sqlite_where=sa.text("status = 'OPEN'"),
    )

    op.create_table(
        "ticket_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_ticket_id", sa.Uuid(), nullable=False),
        sa.Column("target_ticket_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=50), nullable=False),
        sa.Column("analysis_run_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["ai_analysis_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_ticket_id", "target_ticket_id", "relation_type", name="uq_ticket_relations_pair_type"),
    )
    op.create_index("ix_ticket_relations_source_ticket_id", "ticket_relations", ["source_ticket_id"])
    op.create_index("ix_ticket_relations_target_ticket_id", "ticket_relations", ["target_ticket_id"])
    op.create_index("ix_ticket_relations_source_type", "ticket_relations", ["source_ticket_id", "relation_type"])
    op.create_index("ix_ticket_relations_target_type", "ticket_relations", ["target_ticket_id", "relation_type"])

    op.create_table(
        "auto_assignment_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("activation_delay", sa.String(length=30), nullable=False, server_default="IMMEDIATE"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["user_profiles.user_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO auto_assignment_settings (id, enabled, activation_delay, version) "
            "VALUES (1, false, 'IMMEDIATE', 1)"
        )
    )


def downgrade() -> None:
    op.drop_table("auto_assignment_settings")
    op.drop_index("ix_ticket_relations_target_type", table_name="ticket_relations")
    op.drop_index("ix_ticket_relations_source_type", table_name="ticket_relations")
    op.drop_index("ix_ticket_relations_target_ticket_id", table_name="ticket_relations")
    op.drop_index("ix_ticket_relations_source_ticket_id", table_name="ticket_relations")
    op.drop_table("ticket_relations")
    op.drop_index("uq_duplicate_disputes_one_open_per_ticket", table_name="duplicate_disputes")
    op.drop_index("ix_duplicate_disputes_status_created_at", table_name="duplicate_disputes")
    op.drop_index("ix_duplicate_disputes_requested_by_user_id", table_name="duplicate_disputes")
    op.drop_index("ix_duplicate_disputes_master_ticket_id_at_request", table_name="duplicate_disputes")
    op.drop_index("ix_duplicate_disputes_ticket_id", table_name="duplicate_disputes")
    op.drop_table("duplicate_disputes")

    with op.batch_alter_table("ticket_assignments") as batch:
        batch.drop_column("acceptance_reassign_at")
        batch.drop_column("acceptance_warning_at")
        batch.drop_column("reject_reason")
        batch.drop_column("assignment_source")
        batch.alter_column("assigned_by_user_id", existing_type=sa.Uuid(), nullable=False)

    with op.batch_alter_table("tickets") as batch:
        batch.drop_index("ix_tickets_duplicate_of_ticket_id")
        batch.drop_constraint("fk_tickets_duplicate_of_ticket_id_tickets", type_="foreignkey")
        batch.drop_column("auto_assignment_pause_reason")
        batch.drop_column("auto_assignment_paused")
        batch.drop_column("reassignment_count")
        batch.drop_column("invalid_reason")
        batch.drop_column("duplicate_of_ticket_id")
