"""The v4 persistence contract, checked against the ORM metadata (§7.1-§7.9).

These assert the *shape* the contract requires, not that the migration ran:
whether it runs is proved by upgrading a disposable PostgreSQL database, which
is what `docs/v4_operations.md` documents. What is easy to lose silently is a
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
        "red_flag_relation",
        "duplicate_candidates",
        "idempotency_key",
        "payload_hash",
        "contract_version",
        "rule_version_id",
    } <= set(table.c.keys())
    # §1.7.9 backed by the database, not only by the row lock.
    assert "uq_ai_analysis_runs_one_success_per_session" in _index_names(table)


def test_assignments_carry_the_cycle_clock_and_case_sla():
    table = Base.metadata.tables["ticket_assignments"]
    assert {
        "cycle_started_at",
        "case_member_count_snapshot",
        "completion_sla_extension_factor",
        "rejection_reason",
        "rejected_at",
        "end_reason",
        "assignment_job_id",
        "acceptance_warning_at",
        "acceptance_reassign_at",
        "warning_sent_at",
    } <= set(table.c.keys())
    assert table.c.cycle_started_at.nullable is False
    assert {
        "ck_ticket_assignments_sla_factor_range",
        "ck_ticket_assignments_case_member_count",
        "ck_ticket_assignments_human_source_has_actor",
    } <= _constraint_names(table)
    # The pre-v4 guarantee must survive.
    assert "uq_ticket_assignments_one_active_per_ticket" in _index_names(table)


def test_assignment_jobs_have_the_per_mode_shape_constraints():
    table = Base.metadata.tables["ai_assignment_jobs"]
    assert {
        "decision_id",
        "batch_decision_id",
        "model_request_id",
        "previous_assignment_id",
        "candidate_snapshot",
        "excluded_technician_ids",
        "raw_model_output",
        "primary_model",
        "fallback_model",
        "completed_model",
        "decision_reason",
        "error_code",
        "error_detail",
        "cancelled_by_user_id",
        "execute_after",
        "primary_deadline_at",
        "fallback_deadline_at",
        "claimed_at",
        "started_at",
    } <= set(table.c.keys())
    assert {
        "ck_ai_assignment_jobs_mode",
        "ck_ai_assignment_jobs_direct_shape",
        "ck_ai_assignment_jobs_proposal_shape",
    } <= _constraint_names(table)


def test_only_one_active_job_member_per_ticket():
    """§5.1 expressed in persistence rather than in prose."""
    table = Base.metadata.tables["ai_assignment_job_members"]
    index = next(item for item in table.indexes if item.name == "uq_ai_assignment_job_members_active_ticket")
    assert index.unique is True
    assert [column.name for column in index.columns] == ["ticket_id"]
    assert index.dialect_options["postgresql"]["where"] is not None


def test_proposal_items_and_members_have_the_composite_guarantee():
    items = Base.metadata.tables["assignment_proposal_items"]
    members = Base.metadata.tables["assignment_proposal_item_members"]
    assert {"decision_id", "proposed_technician_id", "final_technician_id", "completed_model", "decided_at"} <= set(
        items.c.keys()
    )
    assert "uq_assignment_proposal_items_id_batch" in _constraint_names(items)
    # §7.5: one ticket per batch, enforceable only because the member carries
    # batch_id and a composite FK back to (item_id, batch_id).
    assert "batch_id" in members.c
    assert "uq_assignment_proposal_item_members_batch_ticket" in _constraint_names(members)
    composite = [
        constraint
        for constraint in members.foreign_key_constraints
        if constraint.name == "fk_assignment_proposal_item_members_item_batch"
    ]
    assert composite and len(composite[0].columns) == 2


def test_proposal_batches_can_be_undecided():
    table = Base.metadata.tables["assignment_proposal_batches"]
    assert "requested_by_user_id" in table.c
    assert "cancelled_at" in table.c
    # §4.6 item 6: null means the coordinator has not been asked yet.
    assert table.c.continue_auto_assignment.nullable is True
    assert table.c.activation_delay.nullable is True
    assert "ck_assignment_proposal_batches_ready_has_expiry" in _constraint_names(table)


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
    assert {"ck_auto_assignment_settings_singleton", "ck_auto_assignment_settings_delay_enum"} <= _constraint_names(
        settings
    )
