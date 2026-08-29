"""Add the v4 Agent/Backend persistence contract (§7.1-§7.9).

Everything the v4 contract needs that the v4 *shell* migrations did not already
provide:

* `tickets`   - the invariants §7.1 asks the database to guarantee.
* `ai_analysis_runs` - the validated duplicate/red-flag payload, the finalize
  idempotency key, the sanitized candidate evidence, and one-success-per-session.
* `ticket_assignments` - `cycle_started_at`, the case SLA factor, and the
  rename to the contract name `rejection_reason`.
* `ai_assignment_jobs` - the durable job store §7.4 describes, with the mode
  check constraints and the partial unique index that stops a ticket sitting in
  two live DIRECT jobs.
* `assignment_proposal_*` - per-row decision ids, proposed vs final technician,
  and the composite key that makes `UNIQUE (batch_id, ticket_id)` real.
* `incident_cases` - series identity so a sixth member opens the next case.

Written to run on both PostgreSQL and SQLite: the SQLite path is what the test
suite creates its schema with, and several statements (check constraints on an
existing table, column drops) need batch mode there.

Revision ID: 5e6f7a8b9c0d
Revises: 4d5e6f7a8b9c
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "5e6f7a8b9c0d"
down_revision: str | Sequence[str] | None = "4d5e6f7a8b9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    _upgrade_tickets()
    _upgrade_analysis_runs()
    _upgrade_assignments()
    _upgrade_jobs()
    _upgrade_proposals()
    _upgrade_incident_cases()
    _upgrade_relations_and_disputes()


# ---------------------------------------------------------------------------
# §7.1 tickets
# ---------------------------------------------------------------------------


def _upgrade_tickets() -> None:
    # A ticket that already points at itself would make the new constraint
    # unsatisfiable, and it is meaningless data either way.
    op.execute("UPDATE tickets SET duplicate_of_ticket_id = NULL WHERE duplicate_of_ticket_id = id")
    op.execute(
        "UPDATE tickets SET invalid_reason = 'CONTENT_INSUFFICIENT' "
        "WHERE invalid_reason IS NOT NULL "
        "AND invalid_reason NOT IN ('CONTENT_INSUFFICIENT', 'RESIDENT_RESPONSE_TIMEOUT')"
    )
    with op.batch_alter_table("tickets") as batch:
        batch.create_check_constraint(
            "ck_tickets_duplicate_not_self",
            "duplicate_of_ticket_id IS NULL OR duplicate_of_ticket_id <> id",
        )
        batch.create_check_constraint(
            "ck_tickets_linked_duplicate_needs_master",
            "status <> 'LINKED_DUPLICATE' OR duplicate_of_ticket_id IS NOT NULL",
        )
        batch.create_check_constraint(
            "ck_tickets_invalid_reason_enum",
            "invalid_reason IS NULL OR invalid_reason IN ('CONTENT_INSUFFICIENT', 'RESIDENT_RESPONSE_TIMEOUT')",
        )


# ---------------------------------------------------------------------------
# §7.2 ai_analysis_runs
# ---------------------------------------------------------------------------


def _upgrade_analysis_runs() -> None:
    op.add_column("ai_analysis_runs", sa.Column("duplicate", JSON_TYPE, nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("red_flag_relation", JSON_TYPE, nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("duplicate_candidates", JSON_TYPE, nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.add_column("ai_analysis_runs", sa.Column("payload_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_ai_analysis_runs_idempotency_key", "ai_analysis_runs", ["idempotency_key"])
    # §1.7.9: a session finalizes successfully once. Two racing finalize calls
    # both pass the row lock only if one of them is replaying, and a replay must
    # return the stored run rather than write a second one.
    if _is_postgres():
        op.execute(
            "CREATE UNIQUE INDEX uq_ai_analysis_runs_one_success_per_session "
            "ON ai_analysis_runs (analysis_session_id) "
            "WHERE status = 'SUCCEEDED' AND analysis_session_id IS NOT NULL"
        )
    else:
        op.execute(
            "CREATE UNIQUE INDEX uq_ai_analysis_runs_one_success_per_session "
            "ON ai_analysis_runs (analysis_session_id) "
            "WHERE status = 'SUCCEEDED' AND analysis_session_id IS NOT NULL"
        )


# ---------------------------------------------------------------------------
# §7.3 ticket_assignments
# ---------------------------------------------------------------------------


def _upgrade_assignments() -> None:
    op.alter_column("ticket_assignments", "reject_reason", new_column_name="rejection_reason")
    op.add_column(
        "ticket_assignments",
        sa.Column("cycle_started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("ticket_assignments", sa.Column("case_member_count_snapshot", sa.Integer(), nullable=True))
    op.add_column(
        "ticket_assignments",
        sa.Column(
            "completion_sla_extension_factor",
            sa.Numeric(precision=3, scale=2),
            nullable=False,
            server_default="1.00",
        ),
    )
    # Historic rows started their acceptance clock when they were assigned.
    op.execute("UPDATE ticket_assignments SET cycle_started_at = assigned_at")
    # §7.3 spells the manual source `COORDINATOR_MANUAL`; pre-v4 rows wrote
    # `MANUAL`. One spelling only, so the source is queryable.
    op.execute("UPDATE ticket_assignments SET assignment_source = 'COORDINATOR_MANUAL' WHERE assignment_source = 'MANUAL'")
    # §6: "rejected" and "could not handle" are different outcomes; only the
    # first two below put a technician on a work item's exclusion list.
    op.execute("UPDATE ticket_assignments SET end_reason = 'TECHNICIAN_REJECTED' WHERE end_reason = 'REJECTED_BY_TECHNICIAN'")

    with op.batch_alter_table("ticket_assignments") as batch:
        batch.create_check_constraint(
            "ck_ticket_assignments_sla_factor_range",
            "completion_sla_extension_factor >= 1.00 AND completion_sla_extension_factor <= 2.00",
        )
        batch.create_check_constraint(
            "ck_ticket_assignments_case_member_count",
            "case_member_count_snapshot IS NULL OR (case_member_count_snapshot >= 1 AND case_member_count_snapshot <= 5)",
        )
        batch.create_check_constraint(
            "ck_ticket_assignments_human_source_has_actor",
            "assignment_source = 'AI_AUTO' OR assigned_by_user_id IS NOT NULL",
        )


# ---------------------------------------------------------------------------
# §7.4 ai_assignment_jobs
# ---------------------------------------------------------------------------


def _upgrade_jobs() -> None:
    # The shell table only ever held placeholder rows produced by the
    # deterministic proposal builder this migration retires. Keeping them would
    # violate the mode shape constraints below for no benefit.
    op.execute("DELETE FROM ai_assignment_job_members")
    op.execute("DELETE FROM ai_assignment_jobs")

    for column in (
        sa.Column("decision_id", sa.Uuid(), nullable=True),
        sa.Column("batch_decision_id", sa.Uuid(), nullable=True),
        sa.Column("model_request_id", sa.Uuid(), nullable=True),
        sa.Column("previous_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_snapshot", JSON_TYPE, nullable=True),
        sa.Column("excluded_technician_ids", JSON_TYPE, nullable=True),
        sa.Column("raw_model_output", JSON_TYPE, nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("primary_model", sa.String(length=100), nullable=True),
        sa.Column("fallback_model", sa.String(length=100), nullable=True),
        sa.Column("completed_model", sa.String(length=100), nullable=True),
        sa.Column("decision_reason", sa.String(length=500), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("ai_assignment_jobs", column)

    op.drop_column("ai_assignment_jobs", "failure_reason")
    op.alter_column("ai_assignment_jobs", "status", server_default="SCHEDULED_GRACE")

    op.create_unique_constraint("uq_ai_assignment_jobs_decision_id", "ai_assignment_jobs", ["decision_id"])
    op.create_unique_constraint("uq_ai_assignment_jobs_batch_decision_id", "ai_assignment_jobs", ["batch_decision_id"])
    op.create_index("ix_ai_assignment_jobs_model_request_id", "ai_assignment_jobs", ["model_request_id"])
    op.create_index("ix_ai_assignment_jobs_mode_status", "ai_assignment_jobs", ["mode", "status"])
    # The shell table used ON DELETE SET NULL, which the PROPOSAL shape check
    # now makes impossible: a PROPOSAL job with a null proposal_batch_id is not
    # a writable row. Deleting a batch has to take its job with it.
    op.drop_constraint("ai_assignment_jobs_proposal_batch_id_fkey", "ai_assignment_jobs", type_="foreignkey")
    op.create_foreign_key(
        "fk_ai_assignment_jobs_proposal_batch",
        "ai_assignment_jobs",
        "assignment_proposal_batches",
        ["proposal_batch_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_ai_assignment_jobs_previous_assignment",
        "ai_assignment_jobs",
        "ticket_assignments",
        ["previous_assignment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_ai_assignment_jobs_cancelled_by",
        "ai_assignment_jobs",
        "user_profiles",
        ["cancelled_by_user_id"],
        ["user_id"],
        ondelete="SET NULL",
    )

    with op.batch_alter_table("ai_assignment_jobs") as batch:
        batch.create_check_constraint("ck_ai_assignment_jobs_mode", "mode IN ('DIRECT', 'PROPOSAL')")
        batch.create_check_constraint(
            "ck_ai_assignment_jobs_direct_shape",
            "mode <> 'DIRECT' OR ("
            " decision_id IS NOT NULL"
            " AND batch_decision_id IS NULL"
            " AND proposal_batch_id IS NULL"
            " AND work_item_type IS NOT NULL"
            " AND work_item_id IS NOT NULL"
            " AND ((work_item_type = 'TICKET' AND ticket_id IS NOT NULL AND incident_case_id IS NULL)"
            "   OR (work_item_type = 'INCIDENT_CASE' AND incident_case_id IS NOT NULL AND ticket_id IS NULL))"
            ")",
        )
        batch.create_check_constraint(
            "ck_ai_assignment_jobs_proposal_shape",
            "mode <> 'PROPOSAL' OR ("
            " batch_decision_id IS NOT NULL"
            " AND proposal_batch_id IS NOT NULL"
            " AND decision_id IS NULL"
            " AND work_item_type IS NULL"
            " AND work_item_id IS NULL"
            " AND ticket_id IS NULL"
            " AND incident_case_id IS NULL"
            ")",
        )

    # §5.1: the DIRECT concurrency guarantee, expressed where it cannot be
    # forgotten. PROPOSAL rows never set is_active, so they do not collide.
    op.execute(
        "CREATE UNIQUE INDEX uq_ai_assignment_job_members_active_ticket "
        "ON ai_assignment_job_members (ticket_id) WHERE is_active"
        if _is_postgres()
        else "CREATE UNIQUE INDEX uq_ai_assignment_job_members_active_ticket "
        "ON ai_assignment_job_members (ticket_id) WHERE is_active = 1"
    )


# ---------------------------------------------------------------------------
# §7.5 proposal batches and items
# ---------------------------------------------------------------------------


def _upgrade_proposals() -> None:
    op.execute("DELETE FROM assignment_proposal_item_members")
    op.execute("DELETE FROM assignment_proposal_items")
    op.execute("DELETE FROM assignment_proposal_batches")

    op.alter_column("assignment_proposal_batches", "created_by_user_id", new_column_name="requested_by_user_id")
    op.add_column("assignment_proposal_batches", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assignment_proposal_batches", sa.Column("batch_decision_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        "uq_assignment_proposal_batches_batch_decision_id", "assignment_proposal_batches", ["batch_decision_id"]
    )
    with op.batch_alter_table("assignment_proposal_batches") as batch:
        # §4.6 item 6: null means "not decided yet", which is exactly what an
        # open or cancelled batch is. A false default would silently answer a
        # question the coordinator never got to.
        batch.alter_column("continue_auto_assignment", existing_type=sa.Boolean(), nullable=True, server_default=None)
        batch.alter_column("activation_delay", existing_type=sa.String(length=20), nullable=True, server_default=None)
        batch.alter_column("status", existing_type=sa.String(length=30), server_default="BUILDING")
        batch.alter_column("ready_at", existing_type=sa.DateTime(timezone=True), nullable=True, server_default=None)
        batch.create_check_constraint(
            "ck_assignment_proposal_batches_ready_has_expiry",
            "status <> 'READY' OR expires_at IS NOT NULL",
        )
    op.create_index(
        "ix_assignment_proposal_batches_status_expires", "assignment_proposal_batches", ["status", "expires_at"]
    )

    op.add_column("assignment_proposal_items", sa.Column("decision_id", sa.Uuid(), nullable=False))
    op.alter_column("assignment_proposal_items", "selected_technician_id", new_column_name="proposed_technician_id")
    op.add_column("assignment_proposal_items", sa.Column("final_technician_id", sa.Uuid(), nullable=True))
    op.add_column("assignment_proposal_items", sa.Column("completed_model", sa.String(length=100), nullable=True))
    op.add_column("assignment_proposal_items", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
    # §7.5 bounds the stored reason; the shell table left it unbounded TEXT.
    op.alter_column(
        "assignment_proposal_items",
        "reason",
        existing_type=sa.Text(),
        type_=sa.String(length=500),
        existing_nullable=True,
    )
    op.create_unique_constraint(
        "uq_assignment_proposal_items_decision_id", "assignment_proposal_items", ["decision_id"]
    )
    op.create_unique_constraint(
        "uq_assignment_proposal_items_id_batch", "assignment_proposal_items", ["id", "batch_id"]
    )
    op.create_foreign_key(
        "fk_assignment_proposal_items_final_technician",
        "assignment_proposal_items",
        "technician_profiles",
        ["final_technician_id"],
        ["user_id"],
        ondelete="SET NULL",
    )
    with op.batch_alter_table("assignment_proposal_items") as batch:
        batch.alter_column("status", existing_type=sa.String(length=30), server_default="PENDING")
        batch.create_check_constraint(
            "ck_assignment_proposal_items_work_item_shape",
            "(work_item_type = 'TICKET' AND ticket_id IS NOT NULL AND incident_case_id IS NULL)"
            " OR (work_item_type = 'INCIDENT_CASE' AND incident_case_id IS NOT NULL AND ticket_id IS NULL)",
        )

    # §7.5: `batch_id` on the member plus a composite FK is what makes the
    # one-ticket-per-batch rule enforceable; a plain unique on the item alone
    # cannot see across items.
    op.add_column("assignment_proposal_item_members", sa.Column("batch_id", sa.Uuid(), nullable=False))
    op.create_unique_constraint(
        "uq_assignment_proposal_item_members_batch_ticket",
        "assignment_proposal_item_members",
        ["batch_id", "ticket_id"],
    )
    # The single-column FK is now covered by the composite one, and leaving both
    # would let a member reference an item from a different batch.
    op.drop_constraint(
        "assignment_proposal_item_members_item_id_fkey", "assignment_proposal_item_members", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_assignment_proposal_item_members_item_batch",
        "assignment_proposal_item_members",
        "assignment_proposal_items",
        ["item_id", "batch_id"],
        ["id", "batch_id"],
        ondelete="CASCADE",
    )


# ---------------------------------------------------------------------------
# §7.9 incident case series
# ---------------------------------------------------------------------------


def _upgrade_incident_cases() -> None:
    op.add_column("incident_cases", sa.Column("series_id", sa.Uuid(), nullable=True))
    op.add_column("incident_cases", sa.Column("sequence_no", sa.Integer(), nullable=False, server_default="1"))
    # Every existing case is a series of one, addressed by its own id.
    op.execute("UPDATE incident_cases SET series_id = id WHERE series_id IS NULL")
    with op.batch_alter_table("incident_cases") as batch:
        batch.alter_column("series_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_check_constraint("ck_incident_cases_sequence_positive", "sequence_no >= 1")
        batch.create_check_constraint("ck_incident_cases_density_positive", "density_value >= 1")
        batch.create_unique_constraint("uq_incident_cases_series_sequence", ["series_id", "sequence_no"])
    op.create_index("ix_incident_cases_series_id", "incident_cases", ["series_id"])


# ---------------------------------------------------------------------------
# §7.6, §7.7, §7.8
# ---------------------------------------------------------------------------


def _upgrade_relations_and_disputes() -> None:
    op.execute("DELETE FROM ticket_relations WHERE source_ticket_id = target_ticket_id")
    with op.batch_alter_table("ticket_relations") as batch:
        batch.create_check_constraint("ck_ticket_relations_not_self", "source_ticket_id <> target_ticket_id")

    op.execute("UPDATE duplicate_disputes SET status = 'KEEP_LINKED' WHERE status = 'KEPT_LINKED'")
    with op.batch_alter_table("duplicate_disputes") as batch:
        batch.create_check_constraint(
            "ck_duplicate_disputes_status_enum",
            "status IN ('OPEN', 'KEEP_LINKED', 'SPLIT_INDEPENDENT')",
        )

    op.execute(
        "UPDATE auto_assignment_settings SET activation_delay = CASE activation_delay "
        "WHEN '2H' THEN '2_HOURS' WHEN '5H' THEN '5_HOURS' "
        "WHEN '1D' THEN '1_DAY' WHEN '3D' THEN '3_DAYS' ELSE activation_delay END"
    )
    op.execute("DELETE FROM auto_assignment_settings WHERE id <> 1")
    with op.batch_alter_table("auto_assignment_settings") as batch:
        batch.create_check_constraint("ck_auto_assignment_settings_singleton", "id = 1")
        batch.create_check_constraint(
            "ck_auto_assignment_settings_delay_enum",
            "activation_delay IN ('IMMEDIATE', '2_HOURS', '5_HOURS', '1_DAY', '3_DAYS')",
        )


def downgrade() -> None:
    with op.batch_alter_table("auto_assignment_settings") as batch:
        batch.drop_constraint("ck_auto_assignment_settings_delay_enum", type_="check")
        batch.drop_constraint("ck_auto_assignment_settings_singleton", type_="check")
    with op.batch_alter_table("duplicate_disputes") as batch:
        batch.drop_constraint("ck_duplicate_disputes_status_enum", type_="check")
    with op.batch_alter_table("ticket_relations") as batch:
        batch.drop_constraint("ck_ticket_relations_not_self", type_="check")

    op.drop_index("ix_incident_cases_series_id", table_name="incident_cases")
    with op.batch_alter_table("incident_cases") as batch:
        batch.drop_constraint("uq_incident_cases_series_sequence", type_="unique")
        batch.drop_constraint("ck_incident_cases_density_positive", type_="check")
        batch.drop_constraint("ck_incident_cases_sequence_positive", type_="check")
    op.drop_column("incident_cases", "sequence_no")
    op.drop_column("incident_cases", "series_id")

    op.drop_constraint(
        "fk_assignment_proposal_item_members_item_batch", "assignment_proposal_item_members", type_="foreignkey"
    )
    op.create_foreign_key(
        "assignment_proposal_item_members_item_id_fkey",
        "assignment_proposal_item_members",
        "assignment_proposal_items",
        ["item_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_assignment_proposal_item_members_batch_ticket", "assignment_proposal_item_members", type_="unique"
    )
    op.drop_column("assignment_proposal_item_members", "batch_id")

    with op.batch_alter_table("assignment_proposal_items") as batch:
        batch.drop_constraint("ck_assignment_proposal_items_work_item_shape", type_="check")
    op.drop_constraint("fk_assignment_proposal_items_final_technician", "assignment_proposal_items", type_="foreignkey")
    op.drop_constraint("uq_assignment_proposal_items_id_batch", "assignment_proposal_items", type_="unique")
    op.drop_constraint("uq_assignment_proposal_items_decision_id", "assignment_proposal_items", type_="unique")
    op.alter_column(
        "assignment_proposal_items",
        "reason",
        existing_type=sa.String(length=500),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.drop_column("assignment_proposal_items", "decided_at")
    op.drop_column("assignment_proposal_items", "completed_model")
    op.drop_column("assignment_proposal_items", "final_technician_id")
    op.alter_column("assignment_proposal_items", "proposed_technician_id", new_column_name="selected_technician_id")
    op.drop_column("assignment_proposal_items", "decision_id")

    op.drop_index("ix_assignment_proposal_batches_status_expires", table_name="assignment_proposal_batches")
    with op.batch_alter_table("assignment_proposal_batches") as batch:
        batch.drop_constraint("ck_assignment_proposal_batches_ready_has_expiry", type_="check")
    op.drop_constraint(
        "uq_assignment_proposal_batches_batch_decision_id", "assignment_proposal_batches", type_="unique"
    )
    op.drop_column("assignment_proposal_batches", "batch_decision_id")
    op.drop_column("assignment_proposal_batches", "cancelled_at")
    op.alter_column("assignment_proposal_batches", "requested_by_user_id", new_column_name="created_by_user_id")

    op.drop_index("uq_ai_assignment_job_members_active_ticket", table_name="ai_assignment_job_members")
    with op.batch_alter_table("ai_assignment_jobs") as batch:
        batch.drop_constraint("ck_ai_assignment_jobs_proposal_shape", type_="check")
        batch.drop_constraint("ck_ai_assignment_jobs_direct_shape", type_="check")
        batch.drop_constraint("ck_ai_assignment_jobs_mode", type_="check")
    op.drop_constraint("fk_ai_assignment_jobs_proposal_batch", "ai_assignment_jobs", type_="foreignkey")
    op.create_foreign_key(
        "ai_assignment_jobs_proposal_batch_id_fkey",
        "ai_assignment_jobs",
        "assignment_proposal_batches",
        ["proposal_batch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint("fk_ai_assignment_jobs_cancelled_by", "ai_assignment_jobs", type_="foreignkey")
    op.drop_constraint("fk_ai_assignment_jobs_previous_assignment", "ai_assignment_jobs", type_="foreignkey")
    op.drop_index("ix_ai_assignment_jobs_mode_status", table_name="ai_assignment_jobs")
    op.drop_index("ix_ai_assignment_jobs_model_request_id", table_name="ai_assignment_jobs")
    op.drop_constraint("uq_ai_assignment_jobs_batch_decision_id", "ai_assignment_jobs", type_="unique")
    op.drop_constraint("uq_ai_assignment_jobs_decision_id", "ai_assignment_jobs", type_="unique")
    op.add_column("ai_assignment_jobs", sa.Column("failure_reason", sa.Text(), nullable=True))
    for name in (
        "started_at",
        "attempt_count",
        "claimed_at",
        "cancelled_by_user_id",
        "error_detail",
        "error_code",
        "decision_reason",
        "completed_model",
        "fallback_model",
        "primary_model",
        "input_hash",
        "raw_model_output",
        "excluded_technician_ids",
        "candidate_snapshot",
        "previous_assignment_id",
        "model_request_id",
        "batch_decision_id",
        "decision_id",
    ):
        op.drop_column("ai_assignment_jobs", name)

    with op.batch_alter_table("ticket_assignments") as batch:
        batch.drop_constraint("ck_ticket_assignments_human_source_has_actor", type_="check")
        batch.drop_constraint("ck_ticket_assignments_case_member_count", type_="check")
        batch.drop_constraint("ck_ticket_assignments_sla_factor_range", type_="check")
    op.drop_column("ticket_assignments", "completion_sla_extension_factor")
    op.drop_column("ticket_assignments", "case_member_count_snapshot")
    op.drop_column("ticket_assignments", "cycle_started_at")
    op.alter_column("ticket_assignments", "rejection_reason", new_column_name="reject_reason")

    op.drop_index("uq_ai_analysis_runs_one_success_per_session", table_name="ai_analysis_runs")
    op.drop_index("ix_ai_analysis_runs_idempotency_key", table_name="ai_analysis_runs")
    for name in ("payload_hash", "idempotency_key", "duplicate_candidates", "red_flag_relation", "duplicate"):
        op.drop_column("ai_analysis_runs", name)

    with op.batch_alter_table("tickets") as batch:
        batch.drop_constraint("ck_tickets_invalid_reason_enum", type_="check")
        batch.drop_constraint("ck_tickets_linked_duplicate_needs_master", type_="check")
        batch.drop_constraint("ck_tickets_duplicate_not_self", type_="check")
