"""close incident cases with a reason

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-08-29 00:00:00.000000

Two columns on ``incident_cases``, for one situation the v2 workflow creates and
v1 never could: a case that loses its last member.

Under ``docs/risk_scoring_v2.md`` §7.3, re-scoring a case can push one of its
members to P5, and a P5 ticket is detached from its case immediately -- it is
handled by hand and must not appear in any grouped work. A two-member case whose
members both escalate ends up empty.

An empty case is **closed**, not deleted. The membership it held is why several
tickets were scored the way they were, and a coordinator asking "why was this a
P4 an hour ago" needs the case to still be there to answer. ``closed_reason``
says which of the ways it ended.

Additive and reversible: no data is touched, and dropping the two columns loses
only the closure annotation.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c3d4e5f6a8"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incident_cases", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("incident_cases", sa.Column("closed_reason", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("incident_cases", "closed_reason")
    op.drop_column("incident_cases", "closed_at")
