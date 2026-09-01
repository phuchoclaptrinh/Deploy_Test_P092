"""align users with supabase auth

Revision ID: a1b2c3d4e5f6
Revises: f4d1c8b2a609
Create Date: 2026-08-05 13:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f4d1c8b2a609"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=True)
    op.alter_column("users", "full_name", existing_type=sa.String(length=255), nullable=True)
    op.add_column("users", sa.Column("phone_number", sa.String(length=32), nullable=True))

    op.drop_index("ix_users_email", table_name="users")
    op.create_index(
        "ix_users_email_not_null",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.create_index(
        "ix_users_phone_number_not_null",
        "users",
        ["phone_number"],
        unique=True,
        postgresql_where=sa.text("phone_number IS NOT NULL"),
    )
    op.create_check_constraint("ck_users_email_or_phone", "users", "email IS NOT NULL OR phone_number IS NOT NULL")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'auth' AND table_name = 'users'
            ) THEN
                ALTER TABLE public.users
                ADD CONSTRAINT fk_users_id_auth_users
                FOREIGN KEY (id) REFERENCES auth.users(id)
                ON DELETE RESTRICT
                NOT VALID;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE public.users DROP CONSTRAINT IF EXISTS fk_users_id_auth_users")
    op.drop_constraint("ck_users_email_or_phone", "users", type_="check")
    op.drop_index("ix_users_phone_number_not_null", table_name="users")
    op.drop_index("ix_users_email_not_null", table_name="users")
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.drop_column("users", "phone_number")
    op.alter_column("users", "full_name", existing_type=sa.String(length=255), nullable=False)
    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=False)
