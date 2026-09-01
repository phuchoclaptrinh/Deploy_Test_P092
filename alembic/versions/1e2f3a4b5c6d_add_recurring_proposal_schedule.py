"""add the recurring proposal schedule and the confirmation snapshot

Revision ID: 1e2f3a4b5c6d
Revises: 0d1e2f3a4b5c
Create Date: 2026-08-24 12:00:00.000000

Two features that both need durable storage, and one shared reason for the
migration: neither can be faked from data that already exists.

**The recurring proposal schedule** (`assignment_proposal_schedules`) is a new
product behaviour, not a relabelling of the V4 delay. `auto_assignment_settings`
says whether Backend may assign an approved ticket by itself and after how long;
this says how often Backend opens a new *draft* table for a coordinator to
review. A due run of the schedule creates a batch and assigns nothing. Storing
the repeat interval in `auto_assignment_settings.activation_delay` would put two
different meanings in one column, so it gets its own singleton table.

**The confirmation snapshot** (`assignment_proposal_batches.confirmation_snapshot`)
freezes what was confirmed. Assignment history currently renders from live
tickets and profiles, which means renaming a category or deactivating a
technician silently rewrites a record of what a human approved last month. A
JSON snapshot written inside the confirm transaction is the only way that record
can stay true.

Alongside them, two small columns on the same table:

* `created_by_type` — §8.1 keeps SYSTEM apart from a named actor, and a batch
  the scheduler opened has no coordinator behind it. Existing rows are
  coordinator-created, which is what the server default backfills.
* `followup_schedule` / `followup_schedule_set_at` — the repeat interval chosen
  in the result modal *after* a confirmation. Deliberately not part of the
  snapshot: it is the next thing the coordinator asked for, not part of what
  they approved.

Every column is additive, and the three on `assignment_proposal_batches` are
either nullable or carry a server default, so no existing row is rewritten.
Batches confirmed before this revision keep `confirmation_snapshot IS NULL`; the
history endpoint reports them as pre-snapshot rather than reconstructing them
from live data, because reconstructing them is exactly the bug being fixed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "1e2f3a4b5c6d"
down_revision: str | Sequence[str] | None = "0d1e2f3a4b5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The proposal tables already store the candidate snapshot as JSONB on
# PostgreSQL and JSON on SQLite; the snapshot column follows them.
_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "assignment_proposal_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("interval_code", sa.String(length=20), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_batch_id", sa.Uuid(), nullable=True),
        sa.Column("configured_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="ck_assignment_proposal_schedules_singleton"),
        sa.CheckConstraint(
            "interval_code IS NULL OR interval_code IN ('2_HOURS', '1_DAY', '3_DAYS')",
            name="ck_assignment_proposal_schedules_interval_enum",
        ),
        # An enabled schedule with no interval could never become due; one with
        # no next run would never fire. Both look on and do nothing.
        sa.CheckConstraint(
            "enabled = false OR (interval_code IS NOT NULL AND next_run_at IS NOT NULL)",
            name="ck_assignment_proposal_schedules_enabled_shape",
        ),
        sa.ForeignKeyConstraint(
            ["last_batch_id"], ["assignment_proposal_batches.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["configured_by_user_id"], ["user_profiles.user_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    with op.batch_alter_table("assignment_proposal_batches") as batch:
        batch.add_column(
            sa.Column(
                "created_by_type",
                sa.String(length=20),
                nullable=False,
                server_default="COORDINATOR",
            )
        )
        batch.add_column(sa.Column("confirmation_snapshot", _JSON, nullable=True))
        batch.add_column(sa.Column("followup_schedule", sa.String(length=20), nullable=True))
        batch.add_column(
            sa.Column("followup_schedule_set_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_check_constraint(
            "ck_assignment_proposal_batches_created_by_type",
            "created_by_type IN ('COORDINATOR', 'SYSTEM')",
        )
        batch.create_check_constraint(
            "ck_assignment_proposal_batches_followup_schedule",
            "followup_schedule IS NULL OR followup_schedule IN ('NONE', '2_HOURS', '1_DAY', '3_DAYS')",
        )


def downgrade() -> None:
    with op.batch_alter_table("assignment_proposal_batches") as batch:
        batch.drop_constraint(
            "ck_assignment_proposal_batches_followup_schedule", type_="check"
        )
        batch.drop_constraint("ck_assignment_proposal_batches_created_by_type", type_="check")
        batch.drop_column("followup_schedule_set_at")
        batch.drop_column("followup_schedule")
        batch.drop_column("confirmation_snapshot")
        batch.drop_column("created_by_type")

    op.drop_table("assignment_proposal_schedules")
