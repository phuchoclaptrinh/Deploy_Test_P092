"""Add the mandatory human gate in front of the emergency priority.

Revision ID: 7d8e9f0a1b2c
Revises: 6c7d8e9f0a1b

P3 is the five-minute-SLA priority. Until now a P3 classification -- whether
from a red-flag signal or from a high score -- published itself and carried on
into duplicate retrieval and grouping. This adds the columns that let a run
stop and wait for a coordinator instead.

Nothing is dropped and no history is rewritten. Rows written before this
revision get `p3_review_status = 'NOT_REQUIRED'`, which is the truth about
them: they were never held at a gate that did not exist. Their `priority_final`
is copied into `effective_priority` so the column reads correctly for every
row, not only for new ones.

The one live-state question is a ticket that is *currently* mid-flight on a P3
run. Run this read-only audit first to see them:

    SELECT r.ticket_id, r.run_number, r.priority_final, r.red_flag,
           t.classification_status, t.status
    FROM ai_analysis_runs r
    JOIN tickets t ON t.id = r.ticket_id
    WHERE r.status = 'SUCCEEDED'
      AND r.priority_final = 'P3'
      AND t.classification_status = 'RESOLVED'
      AND t.status NOT IN ('COMPLETED', 'CANCELLED', 'INVALID',
                           'UNRESOLVABLE', 'LINKED_DUPLICATE')
    ORDER BY r.completed_at DESC;

Those are already-published emergencies being worked on right now. The
migration deliberately leaves them published and marks them NOT_REQUIRED: they
were handled under the old rule, and retroactively pulling a live emergency
back into a review queue would be the more dangerous of the two options. The
gate applies to everything classified from the deploy onwards.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7d8e9f0a1b2c"
down_revision: str | Sequence[str] | None = "6c7d8e9f0a1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRIORITY_ENUM = sa.Enum("P1", "P2", "P3", name="priority_level_enum", create_type=False)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.add_column("ai_analysis_runs", sa.Column("p3_review_status", sa.String(length=20), nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("p3_reviewed_by", sa.Uuid(), nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("p3_reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("p3_decision", sa.String(length=30), nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("p3_decision_reason", sa.Text(), nullable=True))
    op.add_column(
        "ai_analysis_runs",
        sa.Column("ai_priority_before_review", PRIORITY_ENUM, nullable=True),
    )
    op.add_column("ai_analysis_runs", sa.Column("effective_priority", PRIORITY_ENUM, nullable=True))

    if _is_postgres():
        op.create_foreign_key(
            "fk_ai_analysis_runs_p3_reviewed_by",
            "ai_analysis_runs",
            "user_profiles",
            ["p3_reviewed_by"],
            ["user_id"],
            ondelete="SET NULL",
        )
    else:
        with op.batch_alter_table("ai_analysis_runs") as batch:
            batch.create_foreign_key(
                "fk_ai_analysis_runs_p3_reviewed_by",
                "user_profiles",
                ["p3_reviewed_by"],
                ["user_id"],
                ondelete="SET NULL",
            )

    # Every historical run predates the gate, so NOT_REQUIRED is accurate for
    # all of them -- including the P3 ones, which were published under the rule
    # in force at the time.
    op.execute(
        """
        UPDATE ai_analysis_runs
        SET p3_review_status = 'NOT_REQUIRED'
        WHERE status = 'SUCCEEDED' AND p3_review_status IS NULL
        """
    )
    # `effective_priority` is "what downstream should use", which for every
    # pre-gate row is simply what was decided then.
    op.execute(
        """
        UPDATE ai_analysis_runs
        SET effective_priority = priority_final,
            ai_priority_before_review = priority_final
        WHERE priority_final IS NOT NULL
        """
    )


def downgrade() -> None:
    if _is_postgres():
        op.drop_constraint("fk_ai_analysis_runs_p3_reviewed_by", "ai_analysis_runs", type_="foreignkey")
    else:
        with op.batch_alter_table("ai_analysis_runs") as batch:
            batch.drop_constraint("fk_ai_analysis_runs_p3_reviewed_by", type_="foreignkey")

    op.drop_column("ai_analysis_runs", "effective_priority")
    op.drop_column("ai_analysis_runs", "ai_priority_before_review")
    op.drop_column("ai_analysis_runs", "p3_decision_reason")
    op.drop_column("ai_analysis_runs", "p3_decision")
    op.drop_column("ai_analysis_runs", "p3_reviewed_at")
    op.drop_column("ai_analysis_runs", "p3_reviewed_by")
    op.drop_column("ai_analysis_runs", "p3_review_status")
