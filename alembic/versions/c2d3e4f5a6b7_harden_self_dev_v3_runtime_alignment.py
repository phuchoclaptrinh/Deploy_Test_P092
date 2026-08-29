"""harden self dev v3 runtime alignment

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-08-11 11:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'TECHNICIAN'")
    op.execute("ALTER TYPE attachment_type_enum ADD VALUE IF NOT EXISTS 'TECHNICIAN_COMPLETION'")
    _ensure_assignment_status_enum()
    _harden_categories()
    _ensure_technician_profiles()
    _ensure_technician_skills()
    _ensure_ticket_assignments()
    _ensure_rls()


def _ensure_assignment_status_enum() -> None:
    status = postgresql.ENUM(
        "ASSIGNED",
        "ACCEPTED",
        "IN_PROGRESS",
        "COMPLETED",
        "UNABLE_TO_HANDLE",
        name="assignment_status_enum",
    )
    status.create(op.get_bind(), checkfirst=True)


def _harden_categories() -> None:
    op.execute(
        """
        ALTER TABLE public.categories
            ADD COLUMN IF NOT EXISTS base_score integer;

        DO $$
        BEGIN
            ALTER TABLE public.categories
                ADD CONSTRAINT ck_categories_code_machine_format
                CHECK (code = upper(code) AND code ~ '^[A-Z][A-Z0-9_]{1,79}$');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )


def _ensure_technician_profiles() -> None:
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
        if_not_exists=True,
    )
    op.create_index(
        "ix_technician_profiles_active_available",
        "technician_profiles",
        ["is_active", "is_available"],
        if_not_exists=True,
    )


def _ensure_technician_skills() -> None:
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
        if_not_exists=True,
    )
    op.create_index("ix_technician_skills_category_id", "technician_skills", ["category_id"], if_not_exists=True)
    op.create_index("ix_technician_skills_technician_id", "technician_skills", ["technician_id"], if_not_exists=True)


def _ensure_ticket_assignments() -> None:
    status = postgresql.ENUM(name="assignment_status_enum", create_type=False)
    op.create_table(
        "ticket_assignments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("technician_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", status, server_default=sa.text("'ASSIGNED'"), nullable=False),
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
        if_not_exists=True,
    )
    op.execute(
        """
        ALTER TABLE public.ticket_assignments
            ADD COLUMN IF NOT EXISTS completion_note text,
            ADD COLUMN IF NOT EXISTS completed_at timestamptz;
        """
    )
    op.create_index(
        "ix_ticket_assignments_technician_active",
        "ticket_assignments",
        ["technician_id", "is_active"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_ticket_assignments_ticket_assigned_at",
        "ticket_assignments",
        ["ticket_id", "assigned_at"],
        if_not_exists=True,
    )
    op.create_index(
        "uq_ticket_assignments_one_active_per_ticket",
        "ticket_assignments",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        if_not_exists=True,
    )


def _ensure_rls() -> None:
    op.execute(
        """
        ALTER TABLE public.technician_profiles ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.technician_profiles FORCE ROW LEVEL SECURITY;
        ALTER TABLE public.technician_skills ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.technician_skills FORCE ROW LEVEL SECURITY;
        ALTER TABLE public.ticket_assignments ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.ticket_assignments FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS rls_technician_skills_select_own ON public.technician_skills;
        CREATE POLICY rls_technician_skills_select_own ON public.technician_skills
        FOR SELECT TO authenticated USING (technician_id = (SELECT auth.uid()));

        DROP POLICY IF EXISTS rls_technician_skills_select_coordinator ON public.technician_skills;
        CREATE POLICY rls_technician_skills_select_coordinator ON public.technician_skills
        FOR SELECT TO authenticated USING (
            EXISTS (
                SELECT 1 FROM public.user_profiles up
                WHERE up.user_id = (SELECT auth.uid())
                  AND up.role = 'COORDINATOR'
                  AND up.is_active = true
            )
        );

        DROP POLICY IF EXISTS rls_ticket_attachments_technician_assigned_read ON public.ticket_attachments;
        CREATE POLICY rls_ticket_attachments_technician_assigned_read ON public.ticket_attachments
        FOR SELECT TO authenticated USING (
            attachment_type IN ('ISSUE_ORIGINAL', 'RESIDENT_SUPPLEMENT', 'TECHNICIAN_COMPLETION')
            AND EXISTS (
                SELECT 1
                FROM public.ticket_assignments assignment
                JOIN public.technician_profiles technician
                  ON technician.user_id = assignment.technician_id
                WHERE assignment.ticket_id = ticket_attachments.ticket_id
                  AND assignment.technician_id = (SELECT auth.uid())
                  AND technician.is_active = true
            )
        );
        """
    )


def downgrade() -> None:
    raise RuntimeError("c2d3e4f5a6b7 is a forward-only v3 corrective migration.")
