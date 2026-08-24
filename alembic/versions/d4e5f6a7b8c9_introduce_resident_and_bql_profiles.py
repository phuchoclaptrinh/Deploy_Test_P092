"""introduce resident and bql profiles

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-06 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add Resident/BQL profiles and copy actor-dependent data safely."""
    op.create_table(
        "residents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            r"phone_number ~ '^\+[1-9][0-9]{6,14}$'",
            name="ck_residents_phone_number_e164",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone_number", name="uq_residents_phone_number"),
    )
    op.create_index("ix_residents_phone_number", "residents", ["phone_number"], unique=True)

    op.create_table(
        "bql_staff",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_bql_staff_email"),
    )
    op.create_index("ix_bql_staff_email", "bql_staff", ["email"], unique=True)

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'auth'
                  AND table_name = 'users'
            ) THEN
                ALTER TABLE public.residents
                    ADD CONSTRAINT fk_residents_id_auth_users
                    FOREIGN KEY (id)
                    REFERENCES auth.users(id)
                    ON DELETE RESTRICT
                    NOT VALID;

                ALTER TABLE public.bql_staff
                    ADD CONSTRAINT fk_bql_staff_id_auth_users
                    FOREIGN KEY (id)
                    REFERENCES auth.users(id)
                    ON DELETE RESTRICT
                    NOT VALID;
            END IF;
        END $$;
        """
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

        CREATE TRIGGER trg_residents_prevent_actor_profile_conflict
        BEFORE INSERT OR UPDATE OF id ON public.residents
        FOR EACH ROW
        EXECUTE FUNCTION public.prevent_actor_profile_conflict();

        CREATE TRIGGER trg_bql_staff_prevent_actor_profile_conflict
        BEFORE INSERT OR UPDATE OF id ON public.bql_staff
        FOR EACH ROW
        EXECUTE FUNCTION public.prevent_actor_profile_conflict();
        """
    )

    op.execute(
        """
        DO $$
        DECLARE
            technician_count integer;
            admin_count integer;
            resident_without_phone_count integer;
            coordinator_without_email_count integer;
            non_resident_membership_count integer;
            non_resident_ticket_count integer;
            non_resident_upload_owner_count integer;
        BEGIN
            SELECT count(*)
            INTO technician_count
            FROM public.users
            WHERE role = 'technician';

            SELECT count(*)
            INTO admin_count
            FROM public.users
            WHERE role = 'admin';

            SELECT count(*)
            INTO resident_without_phone_count
            FROM public.users
            WHERE role = 'resident'
              AND (
                  phone_number IS NULL
                  OR phone_number !~ '^\\+[1-9][0-9]{6,14}$'
              );

            SELECT count(*)
            INTO coordinator_without_email_count
            FROM public.users
            WHERE role = 'coordinator'
              AND (email IS NULL OR btrim(email) = '');

            SELECT count(*)
            INTO non_resident_membership_count
            FROM public.user_unit_memberships membership
            LEFT JOIN public.users app_user
              ON app_user.id = membership.user_id
            WHERE app_user.id IS NULL
               OR app_user.role <> 'resident';

            SELECT count(*)
            INTO non_resident_ticket_count
            FROM public.tickets ticket
            LEFT JOIN public.users app_user
              ON app_user.id = ticket.resident_id
            WHERE app_user.id IS NULL
               OR app_user.role <> 'resident';

            SELECT count(*)
            INTO non_resident_upload_owner_count
            FROM public.ticket_attachment_upload_sessions upload_session
            LEFT JOIN public.users app_user
              ON app_user.id = upload_session.owner_user_id
            WHERE app_user.id IS NULL
               OR app_user.role <> 'resident';

            IF technician_count > 0
               OR admin_count > 0
               OR resident_without_phone_count > 0
               OR coordinator_without_email_count > 0
               OR non_resident_membership_count > 0
               OR non_resident_ticket_count > 0
               OR non_resident_upload_owner_count > 0 THEN
                RAISE EXCEPTION
                    'Cannot migrate actor profiles: technician_count=%, admin_count=%, resident_without_phone_count=%, coordinator_without_email_count=%, non_resident_membership_count=%, non_resident_ticket_count=%, non_resident_upload_owner_count=%',
                    technician_count,
                    admin_count,
                    resident_without_phone_count,
                    coordinator_without_email_count,
                    non_resident_membership_count,
                    non_resident_ticket_count,
                    non_resident_upload_owner_count;
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        INSERT INTO public.residents (
            id,
            phone_number,
            full_name,
            is_active,
            created_at,
            updated_at
        )
        SELECT
            id,
            phone_number,
            full_name,
            is_active,
            created_at,
            updated_at
        FROM public.users
        WHERE role = 'resident'
        ON CONFLICT (id) DO NOTHING;

        INSERT INTO public.bql_staff (
            id,
            email,
            full_name,
            is_active,
            created_at,
            updated_at
        )
        SELECT
            id,
            lower(email),
            full_name,
            is_active,
            created_at,
            updated_at
        FROM public.users
        WHERE role = 'coordinator'
        ON CONFLICT (id) DO NOTHING;
        """
    )

    op.create_table(
        "resident_unit_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resident_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("unlinked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["resident_id"], ["residents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resident_id",
            "unit_id",
            name="uq_resident_unit_memberships_resident_unit",
        ),
    )
    op.create_index(
        "ix_resident_unit_memberships_resident_active",
        "resident_unit_memberships",
        ["resident_id", "is_active"],
    )
    op.create_index(
        "ix_resident_unit_memberships_unit_active",
        "resident_unit_memberships",
        ["unit_id", "is_active"],
    )

    op.execute(
        """
        INSERT INTO public.resident_unit_memberships (
            id,
            resident_id,
            unit_id,
            is_active,
            linked_at,
            unlinked_at,
            created_at,
            updated_at
        )
        SELECT
            id,
            user_id,
            unit_id,
            is_active,
            linked_at,
            unlinked_at,
            created_at,
            updated_at
        FROM public.user_unit_memberships
        ON CONFLICT (resident_id, unit_id) DO NOTHING;
        """
    )

    op.create_foreign_key(
        "fk_tickets_resident_id_residents",
        "tickets",
        "residents",
        ["resident_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "ticket_attachment_upload_sessions",
        sa.Column("resident_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE ticket_attachment_upload_sessions "
        "SET resident_id = owner_user_id"
    )
    op.alter_column(
        "ticket_attachment_upload_sessions",
        "resident_id",
        nullable=False,
    )
    op.create_foreign_key(
        "fk_ticket_attachment_upload_sessions_resident_id_residents",
        "ticket_attachment_upload_sessions",
        "residents",
        ["resident_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_ticket_attachment_upload_sessions_resident_status",
        "ticket_attachment_upload_sessions",
        ["resident_id", "status"],
    )

    op.add_column(
        "ticket_status_history",
        sa.Column("changed_by_auth_user_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE ticket_status_history "
        "SET changed_by_auth_user_id = changed_by_user_id"
    )

    op.add_column(
        "notifications",
        sa.Column("recipient_auth_user_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE notifications "
        "SET recipient_auth_user_id = recipient_user_id"
    )
    op.alter_column("notifications", "recipient_auth_user_id", nullable=False)

    op.add_column(
        "audit_logs",
        sa.Column("actor_auth_user_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE audit_logs "
        "SET actor_auth_user_id = actor_user_id"
    )

    for table_name in ("residents", "bql_staff", "resident_unit_memberships"):
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table_name} FROM PUBLIC")


def downgrade() -> None:
    """Remove additive Resident/BQL structures before final cutover."""
    op.drop_column("audit_logs", "actor_auth_user_id")
    op.drop_column("notifications", "recipient_auth_user_id")
    op.drop_column("ticket_status_history", "changed_by_auth_user_id")

    op.drop_index(
        "ix_ticket_attachment_upload_sessions_resident_status",
        table_name="ticket_attachment_upload_sessions",
    )
    op.drop_constraint(
        "fk_ticket_attachment_upload_sessions_resident_id_residents",
        "ticket_attachment_upload_sessions",
        type_="foreignkey",
    )
    op.drop_column("ticket_attachment_upload_sessions", "resident_id")

    op.drop_constraint(
        "fk_tickets_resident_id_residents",
        "tickets",
        type_="foreignkey",
    )

    op.drop_table("resident_unit_memberships")
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "trg_bql_staff_prevent_actor_profile_conflict "
        "ON public.bql_staff"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "trg_residents_prevent_actor_profile_conflict "
        "ON public.residents"
    )
    op.execute("DROP FUNCTION IF EXISTS public.prevent_actor_profile_conflict()")
    op.drop_table("bql_staff")
    op.drop_table("residents")
