"""replace the proposal architecture with durable dispatch

Revision ID: 8e9f0a1b2c3d
Revises: 7d8e9f0a1b2c
Create Date: 2026-08-26 00:00:00.000000

§9's removal, and §4/§6/§8's replacement, in one revision. Ordered so that
nothing is dropped while something still points at it:

1. detach `ticket_assignments` from `ai_assignment_jobs`;
2. detach `auto_assignment_settings` from `assignment_proposal_batches`;
3. drop the six proposal/job tables, children first;
4. create `dispatch_events` and `at_risk_decisions`;
5. add the §4 scheduling columns and the §3 one-IN_PROGRESS index;
6. rewrite `assignment_source` onto the new vocabulary.

**Forward-only.** `downgrade` raises rather than pretending: the proposal
batches, their items and their confirmation snapshots are deleted here, and a
downgrade that recreated the empty tables would report success while the data
they existed to hold was gone. This is a test environment and §9 authorises the
deletion; what it does not authorise is a migration that lies about being
reversible.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "8e9f0a1b2c3d"
down_revision: str | Sequence[str] | None = "7d8e9f0a1b2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    # ---------------------------------------------------------------- 1 & 2
    # Detach the two survivors before their targets are dropped.
    if "ticket_assignments" in existing:
        columns = {column["name"] for column in inspector.get_columns("ticket_assignments")}
        if "assignment_job_id" in columns:
            for fk in inspector.get_foreign_keys("ticket_assignments"):
                if fk.get("referred_table") == "ai_assignment_jobs" and fk.get("name"):
                    op.drop_constraint(fk["name"], "ticket_assignments", type_="foreignkey")
            op.drop_column("ticket_assignments", "assignment_job_id")

    if "auto_assignment_settings" in existing:
        for fk in inspector.get_foreign_keys("auto_assignment_settings"):
            if fk.get("referred_table") == "assignment_proposal_batches" and fk.get("name"):
                op.drop_constraint(fk["name"], "auto_assignment_settings", type_="foreignkey")

    # ------------------------------------------------------------------- 3
    # Children before parents; every drop guarded, so a database that never had
    # the proposal architecture migrates cleanly too.
    for table in (
        "assignment_proposal_item_members",
        "assignment_proposal_items",
        "ai_assignment_job_members",
        "assignment_proposal_schedules",
        "ai_assignment_jobs",
        "assignment_proposal_batches",
    ):
        if table in existing:
            op.drop_table(table)

    # ------------------------------------------------------------------- 4
    op.create_table(
        "dispatch_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("priority", sa.String(length=4), nullable=False),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ticket_submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score_total", sa.Float(), nullable=True),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=120), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("risk_state", sa.String(length=10), nullable=True),
        sa.Column("decision_source", sa.String(length=30), nullable=True),
        sa.Column(
            "selected_technician_id",
            sa.Uuid(),
            sa.ForeignKey("technician_profiles.user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Not a foreign key: `ticket_assignments.dispatch_event_id` holds that
        # reference, and declaring both would make the two tables mutually
        # dependent.
        sa.Column("assignment_id", sa.Uuid(), nullable=True),
        sa.Column("planned_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("planned_finish_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("slack_seconds", sa.Integer(), nullable=True),
        sa.Column("escalation_reason", sa.String(length=40), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "(is_open = true) = (status IN ('PENDING', 'CLAIMED'))",
            name="ck_dispatch_events_open_matches_status",
        ),
        sa.CheckConstraint("priority <> 'P3'", name="ck_dispatch_events_no_p3"),
        sa.CheckConstraint(
            "status <> 'CLAIMED' OR claim_expires_at IS NOT NULL",
            name="ck_dispatch_events_claim_has_expiry",
        ),
        sa.CheckConstraint(
            "status <> 'ASSIGNED' OR (assignment_id IS NOT NULL AND selected_technician_id IS NOT NULL)",
            name="ck_dispatch_events_assigned_shape",
        ),
        sa.CheckConstraint(
            "status <> 'ESCALATED' OR escalation_reason IS NOT NULL",
            name="ck_dispatch_events_escalated_shape",
        ),
    )
    # The idempotency guarantee: one open event per ticket, many closed ones.
    op.create_index(
        "uq_dispatch_events_open_ticket",
        "dispatch_events",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("is_open"),
        sqlite_where=sa.text("is_open = 1"),
    )
    op.create_index("ix_dispatch_events_claimable", "dispatch_events", ["status", "available_at"])
    op.create_index("ix_dispatch_events_batch", "dispatch_events", ["batch_id"])
    op.create_index("ix_dispatch_events_ticket_created", "dispatch_events", ["ticket_id", "created_at"])

    op.create_table(
        "at_risk_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "dispatch_event_id",
            sa.Uuid(),
            sa.ForeignKey("dispatch_events.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column(
            "technician_id",
            sa.Uuid(),
            sa.ForeignKey("technician_profiles.user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision_source", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("model_name", sa.String(length=120), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("candidate_technician_ids", JSON_TYPE, nullable=True),
        sa.Column("slack_seconds", sa.Integer(), nullable=True),
        sa.Column("tool_snapshot", JSON_TYPE, nullable=True),
        sa.Column("raw_model_output", JSON_TYPE, nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "decision_source IN ('AGENT', 'SCHEDULER_FALLBACK')",
            name="ck_at_risk_decisions_source",
        ),
    )
    op.create_index("ix_at_risk_decisions_batch", "at_risk_decisions", ["batch_id"])
    op.create_index("ix_at_risk_decisions_created", "at_risk_decisions", ["created_at"])
    op.create_index("ix_at_risk_decisions_ticket_id", "at_risk_decisions", ["ticket_id"])

    # Internal operational tables, same treatment the agent's internal tables
    # get: no PostgREST reach, no anonymous grant.
    if _is_postgres():
        for table in ("dispatch_events", "at_risk_decisions"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")

    # ------------------------------------------------------------------- 5
    # §4's scheduling columns. `acceptance_due_at` carries over whatever
    # `acceptance_reassign_at` held: it is the same instant under a name that
    # describes the promise rather than the consequence, so in-flight
    # assignments keep their deadline across the deploy instead of losing it.
    op.add_column("ticket_assignments", sa.Column("acceptance_due_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE ticket_assignments SET acceptance_due_at = acceptance_reassign_at")
    op.add_column("ticket_assignments", sa.Column("planned_start_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ticket_assignments", sa.Column("planned_finish_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ticket_assignments", sa.Column("planned_order", sa.Integer(), nullable=True))
    op.add_column("ticket_assignments", sa.Column("risk_state", sa.String(length=10), nullable=True))
    op.add_column("ticket_assignments", sa.Column("slack_seconds", sa.Integer(), nullable=True))
    op.add_column(
        "ticket_assignments",
        sa.Column(
            "dispatch_event_id",
            sa.Uuid(),
            sa.ForeignKey("dispatch_events.id", ondelete="SET NULL", name="fk_ticket_assignments_dispatch_event"),
            nullable=True,
        ),
    )

    with op.batch_alter_table("ticket_assignments") as batch:
        batch.drop_index("ix_ticket_assignments_acceptance_reassign_at")
        batch.drop_column("acceptance_reassign_at")
    op.create_index("ix_ticket_assignments_acceptance_due_at", "ticket_assignments", ["acceptance_due_at"])
    op.create_index(
        "ix_ticket_assignments_technician_planned",
        "ticket_assignments",
        ["technician_id", "planned_order"],
    )
    # §3: a technician may hold only one IN_PROGRESS ticket at a time. In the
    # database because it is the rule two concurrent `start` calls would
    # otherwise race past -- neither transaction can see the other's write.
    op.create_index(
        "uq_ticket_assignments_one_in_progress_per_technician",
        "ticket_assignments",
        ["technician_id"],
        unique=True,
        postgresql_where=sa.text("status = 'IN_PROGRESS' AND is_active"),
        sqlite_where=sa.text("status = 'IN_PROGRESS' AND is_active = 1"),
    )

    # ------------------------------------------------------------------- 6
    # The new `assignment_source` vocabulary. Rewritten rather than widened, so
    # there is one spelling in the column and the constraint below can enumerate
    # exactly which sources are allowed to have no human actor.
    op.execute(
        "UPDATE ticket_assignments SET assignment_source = 'COORDINATOR_MANUAL' "
        "WHERE assignment_source IN ('MANUAL', 'COORDINATOR_MANUAL')"
    )
    op.execute(
        "UPDATE ticket_assignments SET assignment_source = 'AUTO_SCHEDULER' WHERE assignment_source = 'AI_AUTO'"
    )
    # A proposal-confirmed assignment was a coordinator approving a table of
    # placements, which is what Visual Assignment is now. Mapping it to an
    # automatic source instead would retroactively record a human decision as
    # one nobody made.
    op.execute(
        "UPDATE ticket_assignments SET assignment_source = 'COORDINATOR_VISUAL' "
        "WHERE assignment_source = 'AI_PROPOSAL_CONFIRMED'"
    )
    if _is_postgres():
        op.drop_constraint("ck_ticket_assignments_human_source_has_actor", "ticket_assignments", type_="check")
        op.create_check_constraint(
            "ck_ticket_assignments_human_source_has_actor",
            "ticket_assignments",
            "assignment_source IN ('AUTO_SCHEDULER', 'AUTO_AGENT', 'AUTO_FALLBACK')"
            " OR assigned_by_user_id IS NOT NULL",
        )

    # ---------------------------------------------------- the toggle, reduced
    with op.batch_alter_table("auto_assignment_settings") as batch:
        for column in ("activation_delay", "activated_by_batch_id", "activated_by_user_id", "activated_at"):
            batch.drop_column(column)
        batch.add_column(sa.Column("enabled_by_user_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_auto_assignment_settings_enabled_by_user",
        "auto_assignment_settings",
        "user_profiles",
        ["enabled_by_user_id"],
        ["user_id"],
        ondelete="SET NULL",
    )
    if _is_postgres():
        op.execute(
            "ALTER TABLE auto_assignment_settings "
            "DROP CONSTRAINT IF EXISTS ck_auto_assignment_settings_delay_enum"
        )
        # An enabled switch that cannot name who enabled it is unauditable. The
        # switch is reset to off first so an existing ON row -- which has no
        # provenance under the old shape -- cannot fail the new constraint;
        # re-enabling is one click and now records a person.
        op.execute("UPDATE auto_assignment_settings SET enabled = false")
        op.create_check_constraint(
            "ck_auto_assignment_settings_enabled_has_actor",
            "auto_assignment_settings",
            "enabled = false OR (enabled_by_user_id IS NOT NULL AND enabled_at IS NOT NULL)",
        )
    else:
        op.execute("UPDATE auto_assignment_settings SET enabled = 0")


def downgrade() -> None:
    raise RuntimeError(
        "8e9f0a1b2c3d is forward-only: it deletes assignment proposal batches, items and "
        "confirmation snapshots, which a downgrade cannot restore."
    )
