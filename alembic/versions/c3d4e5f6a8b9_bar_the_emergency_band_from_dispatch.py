"""bar the emergency band from dispatch

Revision ID: c3d4e5f6a8b9
Revises: b2c3d4e5f6a8
Create Date: 2026-08-29 00:00:00.000000

``dispatch_events`` has always refused the emergency band at the database level,
because a check constraint is what makes "manual-only" a fact rather than an
agreement between the ten call sites that enforce it in Python.

Risk scoring v2 inverted the scale, so the constraint was refusing the wrong
band: it said ``priority <> 'P3'``, and P3 is now an ordinary in-shift priority
that belongs in the queue, while P5 is the emergency that must never enter it.
Left alone, the database would have blocked routine work and admitted
emergencies.

The rule did not change. Only which label names it did.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a8b9"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_NAME = "ck_dispatch_events_no_p3"
NEW_NAME = "ck_dispatch_events_no_emergency"


def upgrade() -> None:
    # No open events survive `a1b2c3d4e5f7`, which deleted the whole ticket
    # domain, so nothing has to be moved out of the way first.
    op.execute(sa.text(f"ALTER TABLE dispatch_events DROP CONSTRAINT IF EXISTS {OLD_NAME}"))
    op.create_check_constraint(NEW_NAME, "dispatch_events", "priority <> 'P5'")


def downgrade() -> None:
    op.drop_constraint(NEW_NAME, "dispatch_events", type_="check")
    op.create_check_constraint(OLD_NAME, "dispatch_events", "priority <> 'P3'")
