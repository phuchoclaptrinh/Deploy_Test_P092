"""align self dev v3 human workflow

Revision ID: b1c2d3e4f5a6
Revises: a7b8c9d0e1f2
Create Date: 2026-08-11 09:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'TECHNICIAN'")
    op.execute("ALTER TYPE attachment_type_enum ADD VALUE IF NOT EXISTS 'TECHNICIAN_COMPLETION'")
    assignment_status = postgresql.ENUM(
        "ASSIGNED",
        "ACCEPTED",
        "IN_PROGRESS",
        "COMPLETED",
        "UNABLE_TO_HANDLE",
        name="assignment_status_enum",
        create_type=False,
    )
    assignment_status.create(op.get_bind(), checkfirst=True)
    op.add_column("categories", sa.Column("base_score", sa.Integer(), nullable=True))
    op.create_table(
        "technician_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("phone_number", sa.String(32), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_available", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user_profiles.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_technician_profiles_active_available", "technician_profiles", ["is_active", "is_available"])
    op.create_table(
        "technician_skills",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("technician_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["technician_id"], ["technician_profiles.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("technician_id", "category_id", name="uq_technician_skills_category"),
    )
    op.create_index("ix_technician_skills_category_id", "technician_skills", ["category_id"])
    op.create_index("ix_technician_skills_technician_id", "technician_skills", ["technician_id"])
    op.create_table(
        "ticket_assignments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("technician_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", assignment_status, server_default=sa.text("'ASSIGNED'"), nullable=False),
        sa.Column("assignment_note", sa.String(500), nullable=True),
        sa.Column("unable_reason", sa.String(500), nullable=True),
        sa.Column("completion_note", sa.Text(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status <> 'UNABLE_TO_HANDLE' OR (unable_reason IS NOT NULL AND length(trim(unable_reason)) > 0)",
            name="ck_ticket_assignments_unable_reason_required",
        ),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["user_profiles.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["technician_id"], ["technician_profiles.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ticket_assignments_technician_active", "ticket_assignments", ["technician_id", "is_active"])
    op.create_index("ix_ticket_assignments_ticket_assigned_at", "ticket_assignments", ["ticket_id", "assigned_at"])
    op.create_index(
        "uq_ticket_assignments_one_active_per_ticket",
        "ticket_assignments",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    for table in ("technician_profiles", "technician_skills", "ticket_assignments"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")
    op.execute(
        """
        CREATE POLICY rls_technician_profiles_select_own ON technician_profiles
        FOR SELECT TO authenticated USING (user_id=(SELECT auth.uid()) AND is_active=true);
        CREATE POLICY rls_technician_profiles_select_coordinator ON technician_profiles
        FOR SELECT TO authenticated USING (
          EXISTS (SELECT 1 FROM user_profiles up WHERE up.user_id=(SELECT auth.uid()) AND up.role='COORDINATOR' AND up.is_active=true)
        );
        CREATE POLICY rls_ticket_assignments_select_own ON ticket_assignments
        FOR SELECT TO authenticated USING (technician_id=(SELECT auth.uid()));
        CREATE POLICY rls_ticket_assignments_select_coordinator ON ticket_assignments
        FOR SELECT TO authenticated USING (
          EXISTS (SELECT 1 FROM user_profiles up WHERE up.user_id=(SELECT auth.uid()) AND up.role='COORDINATOR' AND up.is_active=true)
        );
        """
    )


def downgrade() -> None:
    op.drop_table("ticket_assignments")
    op.drop_table("technician_skills")
    op.drop_table("technician_profiles")
    op.drop_column("categories", "base_score")
    op.execute("DROP TYPE IF EXISTS assignment_status_enum")
