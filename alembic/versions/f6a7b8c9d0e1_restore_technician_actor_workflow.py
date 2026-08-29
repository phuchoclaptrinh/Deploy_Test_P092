"""restore technician actor workflow

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-06 15:00:00.000000

Additive, corrective revision that restores the Technician actor required by the
product source documents. It never edits an applied revision, never drops an
existing business table, and preflights with count-only checks so no identifier,
email, phone number, description, or storage path can leak into an error message.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

category_enum = postgresql.ENUM(
    "electricity",
    "water",
    "elevator",
    "security",
    "sanitation",
    "fire_safety",
    "infrastructure",
    "other",
    name="category_enum",
    create_type=False,
)

TECHNICIAN_TABLES = (
    "technician_profiles",
    "technician_skills",
    "ticket_assignments",
)


def upgrade() -> None:
    """Restore Technician profiles, skills, and ticket assignments."""
    _preflight()
    _create_assignment_status_enum()
    _create_technician_profiles()
    _create_technician_skills()
    _create_ticket_assignments()
    _extend_actor_profile_conflict_prevention()
    _create_technician_rls_policies()
    _harden_technician_tables()


def _preflight() -> None:
    """Count-only safety checks before any schema change."""
    op.execute(
        """
        DO $$
        DECLARE
            actor_profile_conflict_count integer;
            orphan_resident_profile_count integer;
            orphan_bql_profile_count integer;
            colliding_table_count integer;
            colliding_type_count integer;
            auth_users_present boolean;
        BEGIN
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'auth'
                  AND table_name = 'users'
            )
            INTO auth_users_present;

            SELECT count(*)
            INTO actor_profile_conflict_count
            FROM public.residents resident
            JOIN public.bql_staff staff
              ON staff.id = resident.id;

            IF auth_users_present THEN
                SELECT count(*)
                INTO orphan_resident_profile_count
                FROM public.residents resident
                LEFT JOIN auth.users auth_user
                  ON auth_user.id = resident.id
                WHERE auth_user.id IS NULL;

                SELECT count(*)
                INTO orphan_bql_profile_count
                FROM public.bql_staff staff
                LEFT JOIN auth.users auth_user
                  ON auth_user.id = staff.id
                WHERE auth_user.id IS NULL;
            ELSE
                orphan_resident_profile_count := 0;
                orphan_bql_profile_count := 0;
            END IF;

            SELECT count(*)
            INTO colliding_table_count
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN (
                'technician_profiles',
                'technician_skills',
                'ticket_assignments'
              );

            SELECT count(*)
            INTO colliding_type_count
            FROM pg_type
            JOIN pg_namespace ON pg_namespace.oid = pg_type.typnamespace
            WHERE pg_type.typname = 'assignment_status_enum'
              AND pg_namespace.nspname = 'public';

            IF NOT auth_users_present THEN
                RAISE EXCEPTION
                    'Cannot restore Technician workflow: auth.users was not found. '
                    'Privileged profiles require a verified Auth FK at migration time.';
            END IF;

            IF actor_profile_conflict_count > 0
               OR orphan_resident_profile_count > 0
               OR orphan_bql_profile_count > 0
               OR colliding_table_count > 0
               OR colliding_type_count > 0 THEN
                RAISE EXCEPTION
                    'Cannot restore Technician workflow: actor_profile_conflict_count=%, orphan_resident_profile_count=%, orphan_bql_profile_count=%, colliding_table_count=%, colliding_type_count=%',
                    actor_profile_conflict_count,
                    orphan_resident_profile_count,
                    orphan_bql_profile_count,
                    colliding_table_count,
                    colliding_type_count;
            END IF;
        END $$;
        """
    )


def _create_assignment_status_enum() -> None:
    op.execute(
        """
        CREATE TYPE assignment_status_enum AS ENUM (
            'assigned',
            'accepted',
            'in_progress',
            'completed',
            'unable_to_handle'
        )
        """
    )


def _create_technician_profiles() -> None:
    op.create_table(
        "technician_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("phone_number", sa.String(length=32), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_available", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            r"phone_number IS NULL OR phone_number ~ '^\+[1-9][0-9]{6,14}$'",
            name="ck_technician_profiles_phone_number_e164",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_technician_profiles_email", "technician_profiles", ["email"], unique=True)
    op.create_index(
        "ix_technician_profiles_active_available",
        "technician_profiles",
        ["is_active", "is_available"],
    )

    op.execute(
        """
        ALTER TABLE public.technician_profiles
            ADD CONSTRAINT fk_technician_profiles_id_auth_users
            FOREIGN KEY (id)
            REFERENCES auth.users(id)
            ON DELETE RESTRICT
        """
    )


def _create_technician_skills() -> None:
    op.create_table(
        "technician_skills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("technician_id", sa.Uuid(), nullable=False),
        sa.Column("category", category_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["technician_id"], ["technician_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("technician_id", "category", name="uq_technician_skills_technician_category"),
    )
    op.create_index("ix_technician_skills_category", "technician_skills", ["category"])
    op.create_index("ix_technician_skills_technician_id", "technician_skills", ["technician_id"])


def _create_ticket_assignments() -> None:
    assignment_status = postgresql.ENUM(name="assignment_status_enum", create_type=False)
    op.create_table(
        "ticket_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("technician_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_auth_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", assignment_status, server_default=sa.text("'assigned'"), nullable=False),
        sa.Column("assignment_note", sa.String(length=500), nullable=True),
        sa.Column("unable_reason", sa.String(length=500), nullable=True),
        sa.Column("work_note", sa.String(length=1000), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status <> 'unable_to_handle' OR (unable_reason IS NOT NULL AND length(trim(unable_reason)) > 0)",
            name="ck_ticket_assignments_unable_reason_required",
        ),
        sa.ForeignKeyConstraint(["technician_id"], ["technician_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ticket_assignments_technician_active",
        "ticket_assignments",
        ["technician_id", "is_active"],
    )
    op.create_index(
        "ix_ticket_assignments_ticket_assigned_at",
        "ticket_assignments",
        ["ticket_id", "assigned_at"],
    )
    op.create_index(
        "uq_ticket_assignments_one_active_per_ticket",
        "ticket_assignments",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.execute(
        """
        ALTER TABLE public.ticket_assignments
            ADD CONSTRAINT fk_ticket_assignments_assigned_by_auth_users
            FOREIGN KEY (assigned_by_auth_user_id)
            REFERENCES auth.users(id)
            ON DELETE RESTRICT
        """
    )


def _extend_actor_profile_conflict_prevention() -> None:
    """Extend one-profile-per-Auth-identity enforcement to all three actors."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.prevent_actor_profile_conflict()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_TABLE_NAME = 'residents' THEN
                IF EXISTS (SELECT 1 FROM public.bql_staff WHERE id = NEW.id)
                   OR EXISTS (SELECT 1 FROM public.technician_profiles WHERE id = NEW.id) THEN
                    RAISE EXCEPTION 'AUTH_PROFILE_CONFLICT';
                END IF;
            ELSIF TG_TABLE_NAME = 'bql_staff' THEN
                IF EXISTS (SELECT 1 FROM public.residents WHERE id = NEW.id)
                   OR EXISTS (SELECT 1 FROM public.technician_profiles WHERE id = NEW.id) THEN
                    RAISE EXCEPTION 'AUTH_PROFILE_CONFLICT';
                END IF;
            ELSIF TG_TABLE_NAME = 'technician_profiles' THEN
                IF EXISTS (SELECT 1 FROM public.residents WHERE id = NEW.id)
                   OR EXISTS (SELECT 1 FROM public.bql_staff WHERE id = NEW.id) THEN
                    RAISE EXCEPTION 'AUTH_PROFILE_CONFLICT';
                END IF;
            END IF;

            RETURN NEW;
        END
        $$;

        CREATE TRIGGER trg_technician_profiles_prevent_actor_profile_conflict
        BEFORE INSERT OR UPDATE OF id ON public.technician_profiles
        FOR EACH ROW
        EXECUTE FUNCTION public.prevent_actor_profile_conflict();
        """
    )


def _create_technician_rls_policies() -> None:
    """Read-only, assignment-scoped policies. No client mutation is granted."""
    op.execute(
        """
        CREATE POLICY rls_technician_profiles_select_own_active
        ON technician_profiles
        FOR SELECT
        TO authenticated
        USING (id = (SELECT auth.uid()) AND is_active = true);

        CREATE POLICY rls_technician_profiles_select_bql_roster
        ON technician_profiles
        FOR SELECT
        TO authenticated
        USING (
            EXISTS (
                SELECT 1
                FROM bql_staff
                WHERE bql_staff.id = (SELECT auth.uid())
                  AND bql_staff.is_active = true
            )
        );

        CREATE POLICY rls_technician_skills_select_own
        ON technician_skills
        FOR SELECT
        TO authenticated
        USING (
            technician_id = (SELECT auth.uid())
            AND EXISTS (
                SELECT 1
                FROM technician_profiles technician
                WHERE technician.id = (SELECT auth.uid())
                  AND technician.is_active = true
            )
        );

        CREATE POLICY rls_technician_skills_select_bql_roster
        ON technician_skills
        FOR SELECT
        TO authenticated
        USING (
            EXISTS (
                SELECT 1
                FROM bql_staff
                WHERE bql_staff.id = (SELECT auth.uid())
                  AND bql_staff.is_active = true
            )
        );

        CREATE POLICY rls_ticket_assignments_select_own_active
        ON ticket_assignments
        FOR SELECT
        TO authenticated
        USING (
            technician_id = (SELECT auth.uid())
            AND is_active = true
            AND EXISTS (
                SELECT 1
                FROM technician_profiles technician
                WHERE technician.id = (SELECT auth.uid())
                  AND technician.is_active = true
            )
        );

        CREATE POLICY rls_ticket_assignments_select_bql
        ON ticket_assignments
        FOR SELECT
        TO authenticated
        USING (
            EXISTS (
                SELECT 1
                FROM bql_staff
                WHERE bql_staff.id = (SELECT auth.uid())
                  AND bql_staff.is_active = true
            )
        );

        CREATE POLICY rls_tickets_technician_select_assigned
        ON tickets
        FOR SELECT
        TO authenticated
        USING (
            EXISTS (
                SELECT 1
                FROM ticket_assignments assignment
                JOIN technician_profiles technician
                  ON technician.id = assignment.technician_id
                WHERE assignment.ticket_id = tickets.id
                  AND assignment.technician_id = (SELECT auth.uid())
                  AND assignment.is_active = true
                  AND technician.is_active = true
            )
        );
        """
    )


def _harden_technician_tables() -> None:
    for table_name in TECHNICIAN_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM PUBLIC")


def downgrade() -> None:
    """Remove the Technician workflow.

    This downgrade is structural only. Any Technician profile, skill, or
    assignment recorded after the upgrade is destroyed and cannot be restored by
    re-running the upgrade. Do not treat it as a data-preserving rollback.
    """
    op.execute("DROP POLICY IF EXISTS rls_tickets_technician_select_assigned ON tickets")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_technician_profiles_prevent_actor_profile_conflict "
        "ON public.technician_profiles"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.prevent_actor_profile_conflict()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_TABLE_NAME = 'residents' THEN
                IF EXISTS (SELECT 1 FROM public.bql_staff WHERE id = NEW.id) THEN
                    RAISE EXCEPTION 'AUTH_PROFILE_CONFLICT';
                END IF;
            ELSIF TG_TABLE_NAME = 'bql_staff' THEN
                IF EXISTS (SELECT 1 FROM public.residents WHERE id = NEW.id) THEN
                    RAISE EXCEPTION 'AUTH_PROFILE_CONFLICT';
                END IF;
            END IF;

            RETURN NEW;
        END
        $$;
        """
    )

    op.drop_table("ticket_assignments")
    op.drop_table("technician_skills")
    op.drop_table("technician_profiles")
    op.execute("DROP TYPE IF EXISTS assignment_status_enum")
