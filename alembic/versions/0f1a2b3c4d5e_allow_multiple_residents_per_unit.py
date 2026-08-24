"""allow multiple residents per unit

Revision ID: 0f1a2b3c4d5e
Revises: f5a6b7c8d9e0
Create Date: 2026-08-19 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0f1a2b3c4d5e"
down_revision: str | Sequence[str] | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_resident_profiles_unit_id", table_name="resident_profiles")


def downgrade() -> None:
    op.create_index("uq_resident_profiles_unit_id", "resident_profiles", ["unit_id"], unique=True)
