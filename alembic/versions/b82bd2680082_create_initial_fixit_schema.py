"""create initial FixIt schema

Revision ID: b82bd2680082
Revises:
Create Date: 2026-08-05 10:00:30.723831

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b82bd2680082"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

role_enum = postgresql.ENUM("resident", "coordinator", "technician", "admin", name="role_enum" , create_type=False,)
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
severity_enum = postgresql.ENUM("low", "medium", "high", "critical", name="severity_enum", create_type=False,)
priority_enum = postgresql.ENUM("p1", "p2", "p3", "p4", name="priority_enum", create_type=False,)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    role_enum.create(bind, checkfirst=True)
    ticket_status_enum.create(bind, checkfirst=True)
    category_enum.create(bind, checkfirst=True)
    severity_enum.create(bind, checkfirst=True)
    priority_enum.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("role", role_enum, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "units",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("building_code", sa.String(length=50), nullable=False),
        sa.Column("floor", sa.String(length=50), nullable=False),
        sa.Column("unit_number", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resident_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", ticket_status_enum, server_default=sa.text("'new'"), nullable=False),
        sa.Column("category", category_enum, nullable=True),
        sa.Column("severity", severity_enum, nullable=True),
        sa.Column("priority", priority_enum, nullable=True),
        sa.Column("location_description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["resident_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tickets_resident_id", "tickets", ["resident_id"], unique=False)
    op.create_index("ix_tickets_unit_id", "tickets", ["unit_id"], unique=False)

    op.create_table(
        "ticket_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("file_url", sa.String(length=2048), nullable=False),
        sa.Column("file_type", sa.String(length=100), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ticket_attachments_ticket_id", "ticket_attachments", ["ticket_id"], unique=False)

    op.create_table(
        "ai_analysis_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("category", category_enum, nullable=False),
        sa.Column("severity", severity_enum, nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("red_flags", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column(
            "text_categories",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("image_category", category_enum, nullable=True),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("recommended_department", sa.String(length=100), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_ai_analysis_runs_confidence_range"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_analysis_runs_ticket_id", "ai_analysis_runs", ["ticket_id"], unique=False)

    op.create_table(
        "ticket_scoring_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("ai_analysis_run_id", sa.Uuid(), nullable=True),
        sa.Column("severity_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("red_flag_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("impact_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("density_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("age_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("total_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("priority", priority_enum, nullable=False),
        sa.Column(
            "scoring_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("severity_score >= 0 AND severity_score <= 40", name="ck_ticket_scoring_severity_range"),
        sa.CheckConstraint("red_flag_score >= 0 AND red_flag_score <= 30", name="ck_ticket_scoring_red_flag_range"),
        sa.CheckConstraint("impact_score >= 0 AND impact_score <= 15", name="ck_ticket_scoring_impact_range"),
        sa.CheckConstraint("density_score >= 0 AND density_score <= 10", name="ck_ticket_scoring_density_range"),
        sa.CheckConstraint("age_score >= 0 AND age_score <= 5", name="ck_ticket_scoring_age_range"),
        sa.CheckConstraint("total_score >= 0 AND total_score <= 100", name="ck_ticket_scoring_total_range"),
        sa.CheckConstraint(
            "total_score = severity_score + red_flag_score + impact_score + density_score + age_score",
            name="ck_ticket_scoring_total_equals_components",
        ),
        sa.ForeignKeyConstraint(["ai_analysis_run_id"], ["ai_analysis_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ticket_scoring_results_ai_analysis_run_id",
        "ticket_scoring_results",
        ["ai_analysis_run_id"],
        unique=False,
    )
    op.create_index("ix_ticket_scoring_results_ticket_id", "ticket_scoring_results", ["ticket_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("ticket_scoring_results")
    op.drop_table("ai_analysis_runs")
    op.drop_table("ticket_attachments")
    op.drop_table("tickets")
    op.drop_table("units")
    op.drop_table("users")

    bind = op.get_bind()
    priority_enum.drop(bind, checkfirst=True)
    severity_enum.drop(bind, checkfirst=True)
    category_enum.drop(bind, checkfirst=True)
    ticket_status_enum.drop(bind, checkfirst=True)
    role_enum.drop(bind, checkfirst=True)
