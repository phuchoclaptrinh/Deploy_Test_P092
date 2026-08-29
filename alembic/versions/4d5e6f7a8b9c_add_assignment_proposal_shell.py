"""add assignment proposal shell

Revision ID: 4d5e6f7a8b9c
Revises: 3c4d5e6f7a8b
Create Date: 2026-08-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4d5e6f7a8b9c"
down_revision: str | Sequence[str] | None = "3c4d5e6f7a8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(sa.Column("duplicate_linked_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("duplicate_reason", sa.String(length=500), nullable=True))
        batch.add_column(sa.Column("duplicate_analysis_run_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("duplicate_disputed_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_tickets_duplicate_analysis_run",
            "ai_analysis_runs",
            ["duplicate_analysis_run_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "assignment_proposal_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="READY"),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("continue_auto_assignment", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("activation_delay", sa.String(length=20), nullable=False, server_default="IMMEDIATE"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user_profiles.user_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["user_profiles.user_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assignment_proposal_batches_status_ready", "assignment_proposal_batches", ["status", "ready_at"])

    op.create_table(
        "ai_assignment_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="PENDING"),
        sa.Column("trigger", sa.String(length=50), nullable=True),
        sa.Column("work_item_type", sa.String(length=30), nullable=True),
        sa.Column("work_item_id", sa.Uuid(), nullable=True),
        sa.Column("ticket_id", sa.Uuid(), nullable=True),
        sa.Column("incident_case_id", sa.Uuid(), nullable=True),
        sa.Column("proposal_batch_id", sa.Uuid(), nullable=True),
        sa.Column("execute_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("primary_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fallback_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reassignment_count_snapshot", sa.Integer(), nullable=True),
        sa.Column("selected_technician_id", sa.Uuid(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_case_id"], ["incident_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["proposal_batch_id"], ["assignment_proposal_batches.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["selected_technician_id"], ["technician_profiles.user_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_assignment_jobs_status_execute", "ai_assignment_jobs", ["status", "execute_after"])
    op.create_index("ix_ai_assignment_jobs_ticket_id", "ai_assignment_jobs", ["ticket_id"])

    op.create_table(
        "ai_assignment_job_members",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["job_id"], ["ai_assignment_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "ticket_id"),
    )
    op.create_index("ix_ai_assignment_job_members_ticket", "ai_assignment_job_members", ["ticket_id", "is_active"])

    op.create_table(
        "assignment_proposal_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="PROPOSED"),
        sa.Column("work_item_type", sa.String(length=30), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=True),
        sa.Column("incident_case_id", sa.Uuid(), nullable=True),
        sa.Column("selected_technician_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decision_job_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["batch_id"], ["assignment_proposal_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_case_id"], ["incident_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["selected_technician_id"], ["technician_profiles.user_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decision_job_id"], ["ai_assignment_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assignment_proposal_items_batch_status", "assignment_proposal_items", ["batch_id", "status"])

    op.create_table(
        "assignment_proposal_item_members",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["item_id"], ["assignment_proposal_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id", "ticket_id"),
    )

    with op.batch_alter_table("ticket_assignments") as batch:
        batch.add_column(sa.Column("assignment_job_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("end_reason", sa.String(length=50), nullable=True))
        batch.create_foreign_key(
            "fk_ticket_assignments_assignment_job",
            "ai_assignment_jobs",
            ["assignment_job_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("ticket_assignments") as batch:
        batch.drop_constraint("fk_ticket_assignments_assignment_job", type_="foreignkey")
        batch.drop_column("end_reason")
        batch.drop_column("rejected_at")
        batch.drop_column("assignment_job_id")

    op.drop_table("assignment_proposal_item_members")
    op.drop_index("ix_assignment_proposal_items_batch_status", table_name="assignment_proposal_items")
    op.drop_table("assignment_proposal_items")
    op.drop_index("ix_ai_assignment_job_members_ticket", table_name="ai_assignment_job_members")
    op.drop_table("ai_assignment_job_members")
    op.drop_index("ix_ai_assignment_jobs_ticket_id", table_name="ai_assignment_jobs")
    op.drop_index("ix_ai_assignment_jobs_status_execute", table_name="ai_assignment_jobs")
    op.drop_table("ai_assignment_jobs")
    op.drop_index("ix_assignment_proposal_batches_status_ready", table_name="assignment_proposal_batches")
    op.drop_table("assignment_proposal_batches")

    with op.batch_alter_table("tickets") as batch:
        batch.drop_constraint("fk_tickets_duplicate_analysis_run", type_="foreignkey")
        batch.drop_column("duplicate_disputed_at")
        batch.drop_column("duplicate_analysis_run_id")
        batch.drop_column("duplicate_reason")
        batch.drop_column("duplicate_linked_at")
