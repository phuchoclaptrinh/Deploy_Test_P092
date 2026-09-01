"""add agent v3 backend contract

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-08-11 13:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: str | Sequence[str] | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.execute("ALTER TYPE ticket_status_v2_enum ADD VALUE IF NOT EXISTS 'INVALID'")
    op.create_table(
        "ai_analysis_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(30), server_default="RUNNING", nullable=False),
        sa.Column("category_catalog_version", sa.String(128), nullable=True),
        sa.Column("category_catalog_snapshot", json_type, nullable=True),
        sa.Column("total_tool_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ask_resident_rounds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ask_resident_elapsed_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("waiting_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_analysis_sessions_ticket_id", "ai_analysis_sessions", ["ticket_id"])
    op.create_table(
        "ai_agent_tool_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("sanitized_request", json_type, nullable=False),
        sa.Column("sanitized_response", json_type, nullable=False),
        sa.Column("success", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["ai_analysis_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_ai_agent_tool_calls_session_sequence"),
    )
    op.create_index("ix_ai_agent_tool_calls_session_id", "ai_agent_tool_calls", ["session_id"])
    op.create_table(
        "ai_agent_questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("question_type", sa.String(30), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("options", json_type, nullable=True),
        sa.Column("allow_free_text_fallback", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), server_default="PENDING", nullable=False),
        sa.Column("answer_type", sa.String(30), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("answer_upload_id", sa.Uuid(), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["ai_analysis_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_agent_questions_session_id", "ai_agent_questions", ["session_id"])
    op.create_index("ix_ai_agent_questions_ticket_id", "ai_agent_questions", ["ticket_id"])

    for column in (
        sa.Column("contract_version", sa.String(20), server_default="v2", nullable=False),
        sa.Column("analysis_session_id", sa.Uuid(), nullable=True),
        sa.Column("exit_reason", sa.String(50), nullable=True),
        sa.Column("is_relevant", sa.Boolean(), nullable=True),
        sa.Column("is_confident", sa.Boolean(), nullable=True),
        sa.Column("confidence_notes", sa.String(500), nullable=True),
        sa.Column("grouping", json_type, nullable=True),
        sa.Column("tool_usage", json_type, nullable=True),
        sa.Column("category_catalog_version", sa.String(128), nullable=True),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("ai_analysis_runs", column)
    op.create_index("ix_ai_analysis_runs_analysis_session_id", "ai_analysis_runs", ["analysis_session_id"])
    op.create_foreign_key(
        "fk_ai_analysis_runs_analysis_session_id",
        "ai_analysis_runs",
        "ai_analysis_sessions",
        ["analysis_session_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    raise RuntimeError("e4f5a6b7c8d9 is a forward-only Agent v3 backend contract migration.")
