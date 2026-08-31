"""widen grouping_status for the emergency gate

Revision ID: d4e5f6a7b9ca
Revises: c3d4e5f6a8b9
Create Date: 2026-08-30 00:00:00.000000

``ai_analysis_runs.grouping_status`` was created as ``VARCHAR(30)``, which fit
every value the column had when it was written. The emergency gate then added
``WAITING_EMERGENCY_MANAGEMENT_REVIEW`` -- 35 characters -- and PostgreSQL
refused it with ``StringDataRightTruncation``.

The damage was not a truncated string. The insert is part of the finalize
transaction, so the refusal rolled back the *entire* result: a report correctly
scored P5, with ``emergency_review_status = PENDING``, ended up on a ticket with
a null priority, no risk assessment, no pending emergency, and an ordinary
MANUAL_REVIEW status. The one classification that must never be lost was the
only one the column could not store.

SQLite ignores ``VARCHAR`` limits, so the whole backend suite stored the
35-character value happily and every emergency-gate test passed. Only
PostgreSQL ever saw this.

50 leaves room above the current longest value without inviting another guess;
the next longest, ``WAITING_DUPLICATE_DECISION``, is 26.

Widening is additive: every existing value still fits, so there is nothing to
migrate and the upgrade cannot fail on data. The downgrade narrows back to 30
and is only safe once no row holds a longer value, so it clears the ones that
would block it first.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b9ca"
down_revision: str | Sequence[str] | None = "c3d4e5f6a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "ai_analysis_runs"
COLUMN = "grouping_status"
OLD_LENGTH = 30
NEW_LENGTH = 50


def upgrade() -> None:
    # Batch mode so the same revision applies on SQLite, which cannot ALTER a
    # column type in place.
    with op.batch_alter_table(TABLE) as batch:
        batch.alter_column(
            COLUMN,
            existing_type=sa.String(OLD_LENGTH),
            type_=sa.String(NEW_LENGTH),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Narrowing while a 35-character row exists would fail the way the original
    # bug did, so the rows that no longer fit are cleared first. NULL is the
    # honest value here: "this run has no grouping status the old column can
    # express" rather than a silently truncated one that still reads as a state.
    op.execute(sa.text("UPDATE ai_analysis_runs SET grouping_status = NULL WHERE length(grouping_status) > 30"))
    with op.batch_alter_table(TABLE) as batch:
        batch.alter_column(
            COLUMN,
            existing_type=sa.String(NEW_LENGTH),
            type_=sa.String(OLD_LENGTH),
            existing_nullable=True,
        )
