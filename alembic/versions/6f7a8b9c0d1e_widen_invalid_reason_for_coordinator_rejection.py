"""Allow COORDINATOR_REJECTED as a ticket invalid_reason.

Building Management no longer asks a resident for more information after a
report arrives. A rejected report ends as INVALID and the resident creates a new
one, so manual-review rejection now records its own reason code instead of
borrowing the agent's `CONTENT_INSUFFICIENT`.

Additive only: the check constraint gains a third permitted value and no
existing row changes. The downgrade folds any `COORDINATOR_REJECTED` row back to
`CONTENT_INSUFFICIENT` before narrowing the constraint again, because leaving it
would make the old constraint unsatisfiable.

Revision ID: 6f7a8b9c0d1e
Revises: 5e6f7a8b9c0d
"""

from collections.abc import Sequence

from alembic import op

revision: str = "6f7a8b9c0d1e"
down_revision: str | Sequence[str] | None = "5e6f7a8b9c0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_tickets_invalid_reason_enum"
_OLD = "invalid_reason IS NULL OR invalid_reason IN ('CONTENT_INSUFFICIENT', 'RESIDENT_RESPONSE_TIMEOUT')"
_NEW = (
    "invalid_reason IS NULL OR invalid_reason IN "
    "('CONTENT_INSUFFICIENT', 'RESIDENT_RESPONSE_TIMEOUT', 'COORDINATOR_REJECTED')"
)


def upgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _NEW)


def downgrade() -> None:
    op.execute(
        "UPDATE tickets SET invalid_reason = 'CONTENT_INSUFFICIENT' "
        "WHERE invalid_reason = 'COORDINATOR_REJECTED'"
    )
    with op.batch_alter_table("tickets") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _OLD)
