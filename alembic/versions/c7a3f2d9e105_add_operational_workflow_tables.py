"""add operational workflow tables

Revision ID: c7a3f2d9e105
Revises: b82bd2680082
Create Date: 2026-08-05 11:15:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7a3f2d9e105"
down_revision: str | Sequence[str] | None = "b82bd2680082"
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
ticket_status_enum = postgresql.ENUM(
    "new",
    "analyzing",
    "waiting_assignment",
    "assigned",
    "in_progress",
    "resolved",
    "closed",
    "rejected",
    name="ticket_status_enum",
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_unit_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("unlinked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "unit_id", name="uq_user_unit_memberships_user_unit"),
    )
    op.create_index(
        "ix_user_unit_memberships_unit_active",
        "user_unit_memberships",
        ["unit_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_user_unit_memberships_user_active",
        "user_unit_memberships",
        ["user_id", "is_active"],
        unique=False,
    )

    op.create_table(
        "technician_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_available", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("old_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_actor_created_at", "audit_logs", ["actor_user_id", "created_at"], unique=False)
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"], unique=False)

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notifications_recipient_unread_created_at",
        "notifications",
        ["recipient_user_id", "is_read", "created_at"],
        unique=False,
    )
    op.create_index("ix_notifications_ticket_id", "notifications", ["ticket_id"], unique=False)

    op.create_table(
        "technician_skills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("technician_id", sa.Uuid(), nullable=False),
        sa.Column("category", category_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["technician_id"], ["technician_profiles.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("technician_id", "category", name="uq_technician_skills_technician_category"),
    )
    op.create_index("ix_technician_skills_category", "technician_skills", ["category"], unique=False)

    op.create_table(
        "ticket_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("technician_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["technician_id"], ["technician_profiles.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ticket_assignments_technician_active",
        "ticket_assignments",
        ["technician_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_ticket_assignments_ticket_assigned_at",
        "ticket_assignments",
        ["ticket_id", "assigned_at"],
        unique=False,
    )
    op.create_index(
        "uq_ticket_assignments_one_active_per_ticket",
        "ticket_assignments",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "ticket_status_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", ticket_status_enum, nullable=True),
        sa.Column("to_status", ticket_status_enum, nullable=False),
        sa.Column("changed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("change_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ticket_status_history_ticket_created_at",
        "ticket_status_history",
        ["ticket_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("ticket_status_history")
    op.drop_table("ticket_assignments")
    op.drop_table("technician_skills")
    op.drop_table("notifications")
    op.drop_table("audit_logs")
    op.drop_table("technician_profiles")
    op.drop_table("user_unit_memberships")
