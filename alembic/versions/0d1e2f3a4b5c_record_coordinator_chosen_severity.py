"""record a coordinator-chosen severity on the ticket

Revision ID: 0d1e2f3a4b5c
Revises: 9c0d1e2f3a4b
Create Date: 2026-08-24 09:00:00.000000

§8.3 lets the Coordinator settle a report the analysis could not classify. Some
of those reports never reached a severity at all — a failed agent session moves
the ticket to MANUAL_REVIEW without one — so the Coordinator now supplies it and
Backend scores from that value.

A severity a human chose is not a severity the vision or the text model
produced, and `severity_source_enum` could only say those two things. This
revision:

* adds `COORDINATOR_MANUAL` to `severity_source_enum`, and
* adds `tickets.severity_source`, written only when a human supplied the value.

Additive and nullable: an AI-derived `tickets.severity` leaves the new column
NULL, and its real source stays where it already is, on
`ai_analysis_runs.severity_source`. No existing row changes.

PostgreSQL cannot drop a value from an enum type, so the downgrade drops the
column and leaves the extra label in place; nothing reads it once the column is
gone.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0d1e2f3a4b5c"
down_revision: str | Sequence[str] | None = "9c0d1e2f3a4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEVERITY_SOURCE = postgresql.ENUM(
    "VISION",
    "TEXT_FALLBACK",
    "COORDINATOR_MANUAL",
    name="severity_source_enum",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE severity_source_enum ADD VALUE IF NOT EXISTS 'COORDINATOR_MANUAL'")
        column_type = _SEVERITY_SOURCE
    else:
        column_type = sa.Enum(
            "VISION",
            "TEXT_FALLBACK",
            "COORDINATOR_MANUAL",
            name="severity_source_enum",
        )

    with op.batch_alter_table("tickets") as batch:
        batch.add_column(sa.Column("severity_source", column_type, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.drop_column("severity_source")
