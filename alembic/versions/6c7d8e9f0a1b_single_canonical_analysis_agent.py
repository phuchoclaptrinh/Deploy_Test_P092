"""Collapse the two analysis pipelines into one canonical agent.

Revision ID: 6c7d8e9f0a1b
Revises: 5b6c7d8e9f0a

Adds what the single pipeline writes and retires -- without dropping -- what the
old dual-runtime era wrote.

Nothing is deleted. `ai_analysis_runs.text_categories` / `image_categories` /
`category_match` / `red_flag_relation` and every row already written under the
old contracts are audit history for tickets that have already been decided, and
this migration is deliberately not the thing that erases them.
`text_categories` is only relaxed to nullable so new rows can stop depending on
it; every existing value stays exactly as it was.

The one piece of live state that has to be dealt with before the switch is a
session still parked on a resident question. Those LangGraph checkpoints live in
memory in the process that started them, so they cannot be resumed after a
deploy under either the old pipeline or the new one. `upgrade()` closes them
explicitly and moves their tickets to manual review, rather than leaving
tickets stuck in PROCESSING behind a question nobody will ever answer.

Run this read-only audit before upgrading, to know in advance which reports the
migration will hand to a coordinator:

    SELECT s.id AS session_id, s.ticket_id, s.model_version, s.started_at,
           t.classification_status,
           (SELECT count(*) FROM ai_agent_questions q
             WHERE q.session_id = s.id AND q.status = 'PENDING') AS open_questions
    FROM ai_analysis_sessions s
    JOIN tickets t ON t.id = s.ticket_id
    WHERE s.status = 'RUNNING'
    ORDER BY s.started_at;

An empty result means nothing is in flight and the switch strands no one.

Written to run on both PostgreSQL and SQLite: SQLite needs batch mode for a
column alteration and for adding a foreign key to an existing table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "6c7d8e9f0a1b"
down_revision: str | Sequence[str] | None = "5b6c7d8e9f0a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")

_CATEGORY_FKS = (
    ("fk_ai_analysis_runs_final_category", "final_category_id"),
    ("fk_ai_analysis_runs_text_category", "text_category_id"),
    ("fk_ai_analysis_runs_image_category", "image_category_id"),
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # --- Questions: what the question is for, and the structured answer that
    # a location confirmation replies with. ---
    op.add_column("ai_agent_questions", sa.Column("question_kind", sa.String(length=40), nullable=True))
    op.add_column("ai_agent_questions", sa.Column("answer_payload", JSON_TYPE, nullable=True))

    # --- Runs: one final category, the two evidence categories, both reasons,
    # the duplicate verdict, and the background grouping stage. ---
    op.add_column("ai_analysis_runs", sa.Column("final_category_id", sa.Uuid(), nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("text_category_id", sa.Uuid(), nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("image_category_id", sa.Uuid(), nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("ai_reason", sa.Text(), nullable=True))
    op.add_column(
        "ai_analysis_runs",
        sa.Column("red_flag", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("ai_analysis_runs", sa.Column("duplicate_verdict", sa.String(length=30), nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("duplicate_reason", sa.Text(), nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("grouping_status", sa.String(length=30), nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("grouping_candidates", JSON_TYPE, nullable=True))

    if _is_postgres():
        for name, column in _CATEGORY_FKS:
            op.create_foreign_key(name, "ai_analysis_runs", "categories", [column], ["id"], ondelete="SET NULL")
        op.alter_column("ai_analysis_runs", "text_categories", existing_type=postgresql.JSONB(), nullable=True)
        op.alter_column("ai_analysis_runs", "text_categories", server_default=None)
    else:
        with op.batch_alter_table("ai_analysis_runs") as batch:
            for name, column in _CATEGORY_FKS:
                batch.create_foreign_key(name, "categories", [column], ["id"], ondelete="SET NULL")
            batch.alter_column("text_categories", existing_type=sa.JSON(), nullable=True, server_default=None)

    # `red_flag` restates what the old pair meant, so historical rows answer the
    # new column honestly instead of reading as "no danger was ever detected".
    op.execute(
        """
        UPDATE ai_analysis_runs
        SET red_flag = (COALESCE(red_flag_text, false) OR COALESCE(red_flag_signal, false))
        """
    )
    # An already-finalized run has no grouping stage waiting for it; only rows
    # written from now on pass through PENDING.
    op.execute(
        """
        UPDATE ai_analysis_runs
        SET grouping_status = CASE WHEN grouping IS NOT NULL THEN 'GROUPED' ELSE 'NOT_ELIGIBLE' END
        WHERE status = 'SUCCEEDED'
        """
    )

    # --- Live sessions from the retired runtimes. ---
    # Audited first: this is the only place the switch can strand a ticket, so
    # the tickets it touches are the ones to look at after the deploy.
    op.execute(
        """
        UPDATE tickets
        SET classification_status = 'MANUAL_REVIEW'
        WHERE classification_status IN ('PENDING', 'PROCESSING')
          AND id IN (SELECT ticket_id FROM ai_analysis_sessions WHERE status = 'RUNNING')
        """
    )
    op.execute(
        """
        UPDATE ai_agent_questions
        SET status = 'EXPIRED'
        WHERE status = 'PENDING'
          AND session_id IN (SELECT id FROM ai_analysis_sessions WHERE status = 'RUNNING')
        """
    )
    op.execute(
        """
        UPDATE ai_analysis_sessions
        SET status = 'FAILED', waiting_deadline_at = NULL
        WHERE status = 'RUNNING'
        """
    )


def downgrade() -> None:
    if _is_postgres():
        for name, _column in _CATEGORY_FKS:
            op.drop_constraint(name, "ai_analysis_runs", type_="foreignkey")
        op.execute("UPDATE ai_analysis_runs SET text_categories = '[]'::jsonb WHERE text_categories IS NULL")
        op.alter_column(
            "ai_analysis_runs",
            "text_categories",
            existing_type=postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        )
    else:
        op.execute("UPDATE ai_analysis_runs SET text_categories = '[]' WHERE text_categories IS NULL")
        with op.batch_alter_table("ai_analysis_runs") as batch:
            for name, _column in _CATEGORY_FKS:
                batch.drop_constraint(name, type_="foreignkey")
            batch.alter_column(
                "text_categories",
                existing_type=sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )

    op.drop_column("ai_analysis_runs", "grouping_candidates")
    op.drop_column("ai_analysis_runs", "grouping_status")
    op.drop_column("ai_analysis_runs", "duplicate_reason")
    op.drop_column("ai_analysis_runs", "duplicate_verdict")
    op.drop_column("ai_analysis_runs", "red_flag")
    op.drop_column("ai_analysis_runs", "ai_reason")
    op.drop_column("ai_analysis_runs", "image_category_id")
    op.drop_column("ai_analysis_runs", "text_category_id")
    op.drop_column("ai_analysis_runs", "final_category_id")

    op.drop_column("ai_agent_questions", "answer_payload")
    op.drop_column("ai_agent_questions", "question_kind")
