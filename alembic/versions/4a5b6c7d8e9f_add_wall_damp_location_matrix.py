"""add the approved wall-damp matrix without rewriting an active rule version

Revision ID: 4a5b6c7d8e9f
Revises: 3f4a5b6c7d8e
Create Date: 2026-08-24 18:45:00.000000

`THAM_TUONG` is a BQL-managed catalog code, not a Python enum member.  Its
base score and ceiling come from the category row; this immutable scoring-rule
version supplies only the approved location bonuses.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4a5b6c7d8e9f"
down_revision: str | Sequence[str] | None = "3f4a5b6c7d8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS_VERSION = "self-dev-v4.1.0-location-matrix"
RULE_VERSION = "self-dev-v4.1.1-location-matrix"


def upgrade() -> None:
    op.execute(sa.text("UPDATE scoring_rule_versions SET is_active = false WHERE is_active"))
    op.execute(
        sa.text(
            """
            INSERT INTO scoring_rule_versions (version, config, is_active)
            SELECT
              :new_version,
              jsonb_set(
                config,
                '{location_bonus,THAM_TUONG}',
                '{"ROOFTOP": 15, "EXTERIOR_FACADE": 15, "BASEMENT_PARKING": 10, "TECHNICAL_ROOM": 10}'::jsonb,
                true
              ),
              true
            FROM scoring_rule_versions
            WHERE version = :previous_version
            """
        ).bindparams(new_version=RULE_VERSION, previous_version=PREVIOUS_VERSION)
    )


def downgrade() -> None:
    op.execute(sa.text("UPDATE scoring_rule_versions SET is_active = false WHERE version = :version").bindparams(version=RULE_VERSION))
    op.execute(
        sa.text(
            """
            UPDATE scoring_rule_versions SET is_active = true
            WHERE version = :previous_version
              AND NOT EXISTS (SELECT 1 FROM scoring_rule_versions WHERE is_active)
            """
        ).bindparams(previous_version=PREVIOUS_VERSION)
    )
    op.execute(sa.text("DELETE FROM scoring_rule_versions WHERE version = :version").bindparams(version=RULE_VERSION))
