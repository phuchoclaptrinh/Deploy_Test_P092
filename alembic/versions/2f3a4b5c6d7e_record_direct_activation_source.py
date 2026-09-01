"""record which confirmed proposal activated DIRECT auto-assignment

Revision ID: 2f3a4b5c6d7e
Revises: 1e2f3a4b5c6d
Create Date: 2026-08-24 15:00:00.000000

DIRECT auto-assignment can be switched off by a coordinator at any moment, but
it can only be switched **on** as a consequence of confirming a real proposal
batch. That asymmetry is the point: turning it on means future tickets get
assigned with no human in the loop, so a person has to have seen a concrete
table of work and approved it first.

`auto_assignment_settings.updated_by_user_id` cannot express that. It records
whoever last touched the row, which after a later delay change is no longer the
person who authorised autonomy. So activation gets its own three columns,
written together and only by the confirm path:

* `activated_by_batch_id` — the proposal that was confirmed,
* `activated_by_user_id` — the named coordinator who confirmed it,
* `activated_at` — when the switch actually flipped.

All three are nullable and stay NULL while DIRECT is off. Rows that are already
enabled when this runs keep NULL: the activation that turned them on happened
before anything recorded it, and inventing a batch id for them would be worse
than admitting it is unknown.

`ondelete='SET NULL'` on the batch reference rather than RESTRICT — losing the
provenance of an old activation must never block deleting a batch, and the
audit log carries the same facts independently.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2f3a4b5c6d7e"
down_revision: str | Sequence[str] | None = "1e2f3a4b5c6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("auto_assignment_settings") as batch:
        batch.add_column(sa.Column("activated_by_batch_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("activated_by_user_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_auto_assignment_settings_activated_by_batch",
            "assignment_proposal_batches",
            ["activated_by_batch_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_auto_assignment_settings_activated_by_user",
            "user_profiles",
            ["activated_by_user_id"],
            ["user_id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("auto_assignment_settings") as batch:
        batch.drop_constraint("fk_auto_assignment_settings_activated_by_user", type_="foreignkey")
        batch.drop_constraint("fk_auto_assignment_settings_activated_by_batch", type_="foreignkey")
        batch.drop_column("activated_at")
        batch.drop_column("activated_by_user_id")
        batch.drop_column("activated_by_batch_id")
