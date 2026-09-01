"""complete t006 t007 upload sessions and validation

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-05 14:20:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(
        "ck_users_phone_number_e164",
        "users",
        r"phone_number IS NULL OR phone_number ~ '^\+[1-9][0-9]{6,14}$'",
    )
    op.execute(
        """
        DO $$
        DECLARE
            orphan_count integer;
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'auth' AND table_name = 'users'
            )
            AND EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_users_id_auth_users'
                  AND conrelid = 'public.users'::regclass
            ) THEN
                SELECT count(*)
                INTO orphan_count
                FROM public.users
                LEFT JOIN auth.users ON auth.users.id = public.users.id
                WHERE auth.users.id IS NULL;

                IF orphan_count > 0 THEN
                    RAISE EXCEPTION
                        'Cannot validate fk_users_id_auth_users: % orphan public user profiles exist.',
                        orphan_count;
                END IF;

                ALTER TABLE public.users VALIDATE CONSTRAINT fk_users_id_auth_users;
            END IF;
        END $$;
        """
    )

    op.create_table(
        "ticket_attachment_upload_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("object_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("file_size > 0", name="ck_ticket_attachment_upload_sessions_file_size_positive"),
        sa.CheckConstraint(
            "mime_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="ck_ticket_attachment_upload_sessions_mime_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'consumed', 'expired')",
            name="ck_ticket_attachment_upload_sessions_status",
        ),
        sa.CheckConstraint(
            "length(storage_path) > 0 AND length(storage_path) <= 1024",
            name="ck_ticket_attachment_upload_sessions_storage_path_length",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_ticket_attachment_upload_sessions_expiry_order"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_path", name="uq_ticket_attachment_upload_sessions_storage_path"),
    )
    op.create_index(
        "ix_ticket_attachment_upload_sessions_owner_status",
        "ticket_attachment_upload_sessions",
        ["owner_user_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_ticket_attachment_upload_sessions_expires_at",
        "ticket_attachment_upload_sessions",
        ["expires_at"],
        unique=False,
    )
    op.execute("ALTER TABLE ticket_attachment_upload_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ticket_attachment_upload_sessions FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE ticket_attachment_upload_sessions FROM PUBLIC")
    op.execute(
        """
        CREATE POLICY rls_ticket_attachment_upload_sessions_deny_all_client_access
        ON ticket_attachment_upload_sessions
        FOR ALL TO anon, authenticated
        USING (false)
        WITH CHECK (false)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
        DROP POLICY IF EXISTS
        rls_ticket_attachment_upload_sessions_deny_all_client_access
        ON ticket_attachment_upload_sessions
        """
    )
    op.execute("ALTER TABLE ticket_attachment_upload_sessions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ticket_attachment_upload_sessions DISABLE ROW LEVEL SECURITY")
    op.drop_table("ticket_attachment_upload_sessions")
    op.drop_constraint("ck_users_phone_number_e164", "users", type_="check")
