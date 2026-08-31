"""`a1b2c3d4e5f7` agrees with the model it is supposed to produce.

Same shape and same limits as `test_acceptance_removal_migration`: whether the
revision *applies* is proved by upgrading a disposable PostgreSQL database,
which needs a database this suite does not have. What these tests catch is the
failure that survives a successful run — a column dropped from the model and
left in the database, a v1 scoring field quietly surviving the cutover, or a
second head nobody noticed.

The comparison is by name against the migration source. Crude on purpose: a
cleverer check would have to execute the migration.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from src.database.base import Base
from src.domain.risk_scoring import RUBRIC_VERSION
from src.models.enums import Priority

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
REVISION = "a1b2c3d4e5f7"
FILENAME = f"{REVISION}_hard_cutover_to_risk_scoring_v2.py"
SOURCE = (VERSIONS / FILENAME).read_text(encoding="utf-8")


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load(VERSIONS / FILENAME)


# The "exactly one head" invariant lives in `test_single_head`, which owns it for
# the whole versions directory rather than for whichever revision is last.


def test_it_follows_the_acceptance_removal_revision():
    assert MODULE.revision == REVISION
    assert MODULE.down_revision == "9f0a1b2c3d4e"


def test_it_refuses_to_pretend_it_is_reversible():
    """It deletes the ticket domain and inverts the priority scale.

    A `downgrade` that recreated `severity_v2_enum` and `categories.base_score`
    would report success while every ticket, assignment, analysis run and
    dispatch event stayed deleted, and while every assessment written since had
    nowhere to go.
    """
    with pytest.raises(RuntimeError, match="forward-only"):
        MODULE.downgrade()


# ---------------------------------------------------------------------------
# The delete scope.
# ---------------------------------------------------------------------------


def test_the_delete_scope_is_the_ticket_domain_and_only_that():
    """Every table that reaches `tickets` is in the reset.

    Derived from the model metadata rather than restated, so a ticket-domain
    table added later fails this test instead of being quietly skipped.
    """
    tables = Base.metadata.tables
    reaches_tickets = {"tickets"}
    changed = True
    while changed:
        changed = False
        for name, table in tables.items():
            if name in reaches_tickets:
                continue
            if {fk.column.table.name for fk in table.foreign_keys} & reaches_tickets:
                reaches_tickets.add(name)
                changed = True

    listed = set(MODULE.TICKET_DOMAIN_TABLES)
    assert reaches_tickets <= listed, f"missing from the reset: {sorted(reaches_tickets - listed)}"


def test_the_building_and_its_people_are_not_deleted():
    """Accounts, catalogs and configuration outlive a ticket-data reset.

    This is what makes the cutover a *scoping* decision rather than a database
    wipe: nothing about who lives where, who fixes what, or what the categories
    are depends on the scoring model that changed.
    """
    preserved = {
        "user_profiles",
        "resident_profiles",
        "technician_profiles",
        "technician_skills",
        "technician_availability_events",
        "categories",
        "locations",
        "location_types",
        "floors",
        "units",
        "auto_assignment_settings",
        "audit_logs",
    }
    assert preserved.isdisjoint(MODULE.TICKET_DOMAIN_TABLES)
    assert "DELETE FROM audit_logs WHERE entity_type IN ('TICKET', 'TICKET_ASSIGNMENT')" in SOURCE


def test_children_are_deleted_before_their_parents():
    """Ordering, or the foreign keys refuse the delete."""
    order = MODULE.TICKET_DOMAIN_TABLES
    position = {name: index for index, name in enumerate(order)}
    # Foreign keys the migration nulls out before the delete loop runs, so they
    # impose no ordering on it. Two are on `tickets`; the other two are the back
    # halves of the tickets <-> runs and runs <-> assessments cycles.
    pre_nulled = {
        ("tickets", "duplicate_of_ticket_id"),
        ("tickets", "duplicate_analysis_run_id"),
        ("tickets", "current_risk_assessment_id"),
        ("ai_analysis_runs", "risk_assessment_id"),
    }

    for name in order:
        table = Base.metadata.tables.get(name)
        if table is None:
            continue
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            if parent == name or parent not in position:
                continue
            if (name, fk.parent.name) in pre_nulled:
                continue
            assert position[name] < position[parent], f"{name} must be deleted before {parent}"


# ---------------------------------------------------------------------------
# What the cutover removes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("tickets", "severity"),
        ("tickets", "severity_source"),
        ("tickets", "red_flag_detected"),
        ("tickets", "score_total"),
        ("categories", "base_score"),
        ("categories", "priority_ceiling"),
        ("ai_analysis_runs", "severity"),
        ("ai_analysis_runs", "severity_source"),
        ("ai_analysis_runs", "red_flag"),
        ("ai_analysis_runs", "rule_version_id"),
        ("ai_analysis_runs", "score_total"),
        ("ai_analysis_runs", "priority_raw"),
        ("ai_analysis_runs", "priority_final"),
        ("ai_analysis_runs", "ceiling_applied"),
    ],
)
def test_every_v1_scoring_column_is_dropped_by_the_migration_and_gone_from_the_model(table, column):
    assert column in MODULE.DROPPED_COLUMNS[table]
    assert column not in Base.metadata.tables[table].c


def test_the_scoring_rule_table_is_dropped_and_unmapped():
    assert 'op.drop_table("scoring_rule_versions")' in SOURCE
    assert "scoring_rule_versions" not in Base.metadata.tables


def test_the_severity_types_are_dropped_only_once_nothing_references_them():
    """The columns come off first, then the PostgreSQL types.

    Reversed, the DROP TYPE fails on a dependency and the whole revision rolls
    back — which is safe, but leaves an operator debugging an ordering bug
    instead of running a migration.
    """
    columns_at = SOURCE.index("for table, columns in DROPPED_COLUMNS.items()")
    types_at = SOURCE.index('DROP TYPE IF EXISTS severity_v2_enum')
    assert columns_at < types_at


def test_the_constraint_that_required_a_base_score_is_dropped():
    assert "ck_categories_active_base_score_required" in SOURCE
    assert "DROP CONSTRAINT IF EXISTS" in SOURCE


# ---------------------------------------------------------------------------
# What the cutover adds.
# ---------------------------------------------------------------------------


def test_the_priority_enum_is_rebuilt_with_five_labels():
    assert MODULE.NEW_PRIORITIES == ("P1", "P2", "P3", "P4", "P5")
    assert [member.value for member in Priority] == list(MODULE.NEW_PRIORITIES)


def test_every_priority_column_is_swapped_onto_the_rebuilt_type():
    """A column left on the old type pins it and makes the DROP TYPE fail."""
    listed = {(table, column) for table, column in MODULE.PRIORITY_COLUMNS}
    for name, table in Base.metadata.tables.items():
        for column in table.c:
            enum_name = getattr(column.type, "name", None)
            if enum_name == "priority_level_enum" and (name, column.name) not in listed:
                # New columns introduced by this same revision are created on
                # the rebuilt type directly, so they need no swap.
                assert name == "ticket_risk_assessments", f"{name}.{column.name} is not in PRIORITY_COLUMNS"


def test_the_assessment_table_matches_the_model():
    table = Base.metadata.tables["ticket_risk_assessments"]
    expected = {
        "id",
        "ticket_id",
        "revision_no",
        "source",
        "ai_analysis_run_id",
        "supersedes_id",
        "human_safety_score",
        "property_spread_score",
        "essential_function_score",
        "ai_scope_score",
        "backend_scope_score",
        "effective_scope_score",
        "deterioration_speed_score",
        "confirmed_affected_unit_count",
        "blocker_codes",
        "evidence",
        "unknown_facts",
        "risk_score",
        "score_priority",
        "blocker_floor",
        "final_priority",
        "rubric_version",
        "case_id_snapshot",
        "case_density_snapshot",
        "override_reason",
        "reviewed_by",
        "created_at",
    }
    assert set(table.c.keys()) == expected
    for column in expected:
        assert f'"{column}"' in SOURCE, f"{column} is on the model but not in the migration"


def test_the_assessment_table_constrains_the_rubric_scale():
    constraints = {constraint.name for constraint in Base.metadata.tables["ticket_risk_assessments"].constraints}
    assert "uq_ticket_risk_assessments_ticket_revision" in constraints
    for column in MODULE.CRITERION_COLUMNS:
        assert f"ck_ticket_risk_assessments_{column}_range" in constraints
    assert "ck_ticket_risk_assessments_score_range" in constraints
    assert "ck_ticket_risk_assessments_unit_count_range" in constraints


def test_the_ticket_keeps_only_a_cache_of_the_current_assessment():
    columns = set(Base.metadata.tables["tickets"].c.keys())
    assert {"current_risk_assessment_id", "risk_score", "priority", "sla_started_at", "sla_due_at"} <= columns
    # Nothing else about the score lives on the ticket: the criteria, the
    # blockers and the evidence are on the revision, and duplicating any of
    # them here would create a second answer to the same question.
    assert not {"human_safety_score", "blocker_codes", "evidence", "rubric_version"} & columns


def test_the_emergency_gate_columns_are_renamed_rather_than_recreated():
    """A rename says "same gate, new name"; a drop-and-add says "new gate"."""
    assert MODULE.RENAMED_REVIEW_COLUMNS[0] == ("p3_review_status", "emergency_review_status")
    runs = set(Base.metadata.tables["ai_analysis_runs"].c.keys())
    for old_name, new_name in MODULE.RENAMED_REVIEW_COLUMNS:
        assert old_name not in runs
        assert new_name in runs


def test_the_rubric_version_the_code_stamps_is_the_one_the_column_holds():
    assert Base.metadata.tables["ticket_risk_assessments"].c.rubric_version.type.length >= len(RUBRIC_VERSION)


def test_the_enum_types_it_creates_itself_are_not_emitted_a_second_time(monkeypatch):
    """The one failure this suite cannot see by running, so it is asserted instead.

    The revision creates `priority_level_enum` and `risk_assessment_source_enum`
    itself, then hands the same names to `op.create_table`. The column types
    therefore have to reach the PostgreSQL dialect carrying `create_type=False`,
    or `create_table` emits a second `CREATE TYPE` and the revision dies with
    `DuplicateObject` -- on a real server, and only there. SQLite renders an
    enum as VARCHAR + CHECK and never creates a type at all, so every other test
    in this file passes either way.

    `sa.Enum(..., create_type=False)` does not do it: `create_type` is a
    PostgreSQL-dialect argument, the generic type accepts it and loses it on the
    way down, and the impl comes back with `create_type=True`.
    """
    monkeypatch.setattr(MODULE, "_is_postgres", lambda: True)
    column_types = {
        "priority_level_enum": MODULE._priority_enum(),
        "risk_assessment_source_enum": MODULE._existing_enum(
            *MODULE.RISK_ASSESSMENT_SOURCES, name="risk_assessment_source_enum"
        ),
    }
    for name, column_type in column_types.items():
        impl = column_type.dialect_impl(postgresql.dialect())
        assert impl.create_type is False, f"{name} would be created twice on PostgreSQL"
