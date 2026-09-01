"""The v4 persistence contract, checked against the ORM metadata (§7.1-§7.9).

These assert the *shape* the contract requires, not that the migration ran:
whether it runs is proved by upgrading a disposable PostgreSQL database, which
is what `docs/operations.md` documents. What is easy to lose silently is a
column or a constraint quietly disappearing from the model in a later refactor,
and that is what this file catches.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from src.database.base import Base

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
REVISION = "5e6f7a8b9c0d"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name}


def _index_names(table) -> set[str]:
    return {index.name for index in table.indexes}


def test_the_v4_revision_is_the_single_head_and_follows_the_shell():
    module = _load(VERSIONS / f"{REVISION}_add_v4_agent_backend_contract.py")
    assert module.revision == REVISION
    assert module.down_revision == "4d5e6f7a8b9c"


def test_tickets_carry_the_duplicate_invariants():
    table = Base.metadata.tables["tickets"]
    assert {
        "duplicate_of_ticket_id",
        "duplicate_linked_at",
        "duplicate_reason",
        "duplicate_analysis_run_id",
        "reassignment_count",
        "auto_assignment_paused",
        "auto_assignment_pause_reason",
    } <= set(table.c.keys())
    assert {
        "ck_tickets_duplicate_not_self",
        "ck_tickets_linked_duplicate_needs_master",
        "ck_tickets_invalid_reason_enum",
    } <= _constraint_names(table)


def test_analysis_runs_store_the_v4_payload_and_finalize_key():
    table = Base.metadata.tables["ai_analysis_runs"]
    assert {
        "duplicate",
        "duplicate_candidates",
        "idempotency_key",
        "payload_hash",
        "contract_version",
    } <= set(table.c.keys())
    # §1.7.9 backed by the database, not only by the row lock.
    assert "uq_ai_analysis_runs_one_success_per_session" in _index_names(table)


def test_assignments_carry_the_scheduling_fields():
    table = Base.metadata.tables["ticket_assignments"]
    assert {
        "case_member_count_snapshot",
        "completion_sla_extension_factor",
        "rejection_reason",
        "rejected_at",
        "end_reason",
        # §4's two separate concepts. `planned_start_at` is what the resident
        # sees; `planned_finish_at` is internal capacity arithmetic and never a
        # completion promise.
        "planned_start_at",
        "planned_finish_at",
        "planned_order",
        "risk_state",
        "slack_seconds",
        "started_at",
        "dispatch_event_id",
    } <= set(table.c.keys())
    # §9 deleted the proposal architecture; the column that pointed at it must
    # not come back with it.
    assert "assignment_job_id" not in table.c
    assert "acceptance_reassign_at" not in table.c
    # `9f0a1b2c3d4e` removed the acceptance step. Every column that existed only
    # to serve it is gone from the model, including `cycle_started_at`, whose
    # only purpose was to anchor those deadlines.
    for column in (
        "accepted_at",
        "acceptance_due_at",
        "acceptance_warning_at",
        "warning_sent_at",
        "cycle_started_at",
    ):
        assert column not in table.c
    assert {
        "ck_ticket_assignments_sla_factor_range",
        "ck_ticket_assignments_case_member_count",
        "ck_ticket_assignments_human_source_has_actor",
    } <= _constraint_names(table)
    assert "uq_ticket_assignments_one_active_per_ticket" in _index_names(table)


def test_a_technician_may_hold_only_one_in_progress_ticket():
    """§3, expressed in persistence rather than only in the service check.

    Two concurrent `start` calls cannot see each other's write, so the service
    guard alone would let both through.
    """
    table = Base.metadata.tables["ticket_assignments"]
    index = next(
        item for item in table.indexes if item.name == "uq_ticket_assignments_one_in_progress_per_technician"
    )
    assert index.unique is True
    assert [column.name for column in index.columns] == ["technician_id"]
    assert index.dialect_options["postgresql"]["where"] is not None


def test_dispatch_events_are_the_durable_queue():
    """§8: one durable event per ticket, claimable exactly once."""
    table = Base.metadata.tables["dispatch_events"]
    assert {
        "ticket_id",
        "status",
        "is_open",
        "priority",
        "category_id",
        "ticket_submitted_at",
        "available_at",
        "claimed_at",
        "claim_expires_at",
        "claimed_by",
        "attempt_count",
        "batch_id",
        "risk_state",
        "decision_source",
        "selected_technician_id",
        "assignment_id",
        "planned_start_at",
        "planned_finish_at",
        "slack_seconds",
        "escalation_reason",
    } <= set(table.c.keys())
    assert {
        "ck_dispatch_events_open_matches_status",
        # §2/§3: P3 must never enter the automatic workflow, and the table that
        # would carry it refuses to.
        "ck_dispatch_events_no_emergency",
        "ck_dispatch_events_claim_has_expiry",
        "ck_dispatch_events_assigned_shape",
        "ck_dispatch_events_escalated_shape",
    } <= _constraint_names(table)

    # The idempotency guarantee: one *open* event per ticket, any number of
    # closed ones.
    index = next(item for item in table.indexes if item.name == "uq_dispatch_events_open_ticket")
    assert index.unique is True
    assert [column.name for column in index.columns] == ["ticket_id"]
    assert index.dialect_options["postgresql"]["where"] is not None


def test_at_risk_decisions_record_what_was_decided_and_by_what():
    """§7: an AT_RISK decision has to be auditable after the fact."""
    table = Base.metadata.tables["at_risk_decisions"]
    assert {
        "dispatch_event_id",
        "ticket_id",
        "batch_id",
        "technician_id",
        "decision_source",
        "reason",
        "model_name",
        "latency_ms",
        # The eligible set the backend authorised. Without it, "the agent chose
        # from an allowed list" is a claim rather than a record.
        "candidate_technician_ids",
        "slack_seconds",
        "tool_snapshot",
        "error_code",
    } <= set(table.c.keys())
    assert "ck_at_risk_decisions_source" in _constraint_names(table)
    # One decision per event: a re-enqueued ticket gets a new event and a new
    # row rather than overwriting the old decision.
    assert table.c.dispatch_event_id.unique is True


def test_the_old_proposal_architecture_is_gone():
    """§9's removal list, as an assertion.

    Every one of these tables existed to serve the proposal-driven flow. A
    later merge quietly restoring one would restore an architecture with no
    code behind it, and the first thing anyone would notice is a foreign key
    failing months later.
    """
    for table in (
        "assignment_proposal_batches",
        "assignment_proposal_items",
        "assignment_proposal_item_members",
        "assignment_proposal_schedules",
        "ai_assignment_jobs",
        "ai_assignment_job_members",
    ):
        assert table not in Base.metadata.tables


def test_incident_cases_have_a_series_identity():
    table = Base.metadata.tables["incident_cases"]
    assert {"series_id", "sequence_no"} <= set(table.c.keys())
    assert "uq_incident_cases_series_sequence" in _constraint_names(table)


def test_relations_match_the_contract_and_appeals_are_gone():
    relations = Base.metadata.tables["ticket_relations"]
    assert "ck_ticket_relations_not_self" in _constraint_names(relations)
    # The resident duplicate appeal was removed from the product, so neither the
    # table nor the ticket column that only served it may come back into the
    # active model. `7a8b9c0d1e2f` drops both.
    assert "duplicate_disputes" not in Base.metadata.tables
    assert "duplicate_disputed_at" not in Base.metadata.tables["tickets"].c
    settings = Base.metadata.tables["auto_assignment_settings"]
    # §2 reduced the switch to one boolean. The activation delay and the
    # proposal-batch provenance went with the architecture that needed them;
    # what replaces them is a constraint that an enabled switch must name who
    # enabled it.
    assert {
        "ck_auto_assignment_settings_singleton",
        "ck_auto_assignment_settings_enabled_has_actor",
    } <= _constraint_names(settings)
    assert "activation_delay" not in settings.c
    assert "activated_by_batch_id" not in settings.c
    assert {"enabled_by_user_id", "enabled_at"} <= set(settings.c.keys())
