"""`9f0a1b2c3d4e` agrees with the model it is supposed to produce.

Same shape and same limits as `test_dispatch_migration`: whether the revision
*applies* is proved by upgrading a disposable PostgreSQL database, which needs
a database this suite does not have. What these tests catch is the failure that
survives a successful run — a column dropped from the model and left in the
database, a delete scope that quietly grew to include the building's people, or
a second head nobody noticed.

The comparison is by name against the migration source. Crude on purpose: a
cleverer check would have to execute the migration.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from src.database.base import Base

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
REVISION = "9f0a1b2c3d4e"
FILENAME = f"{REVISION}_remove_assignment_acceptance_step.py"
SOURCE = (VERSIONS / FILENAME).read_text(encoding="utf-8")


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load(VERSIONS / FILENAME)


def test_it_follows_the_dispatch_revision():
    assert MODULE.revision == REVISION
    assert MODULE.down_revision == "8e9f0a1b2c3d"


def test_it_refuses_to_pretend_it_is_reversible():
    """It deletes the whole ticket domain and drops the acceptance timestamps.

    A `downgrade` that recreated the ACCEPTED label would report success while
    everything the revision deleted stayed deleted.
    """
    with pytest.raises(RuntimeError, match="forward-only"):
        MODULE.downgrade()


def test_the_new_enum_has_no_accepted_label():
    assert "ACCEPTED" not in MODULE.NEW_ASSIGNMENT_STATUSES
    assert MODULE.NEW_ASSIGNMENT_STATUSES == (
        "ASSIGNED",
        "IN_PROGRESS",
        "COMPLETED",
        "REJECTED",
        "REASSIGNED",
        "UNABLE_TO_HANDLE",
    )
    # The Python enum and the PostgreSQL type must be the same set, or a value
    # the application can produce is one the database will refuse.
    from src.models.enums import AssignmentStatus

    assert MODULE.NEW_ASSIGNMENT_STATUSES == tuple(member.value for member in AssignmentStatus)


def test_the_enum_is_rebuilt_rather_than_edited():
    """PostgreSQL cannot drop one label, so the type is replaced under the column."""
    steps = [
        "CREATE TYPE assignment_status_enum_new",
        # The default is an expression typed against the old enum and has to go
        # before the column can be retyped.
        "ALTER COLUMN status DROP DEFAULT",
        "TYPE assignment_status_enum_new USING status::text::assignment_status_enum_new",
        "DROP TYPE assignment_status_enum",
        "ALTER TYPE assignment_status_enum_new RENAME TO assignment_status_enum",
        "SET DEFAULT 'ASSIGNED'::assignment_status_enum",
    ]
    positions = [SOURCE.index(step) for step in steps]
    assert positions == sorted(positions), "the enum swap is out of order"


def test_every_object_pinning_the_old_enum_is_dropped_and_rebuilt():
    """`ALTER COLUMN ... TYPE` does not rewrite enum literals in other objects.

    Found by running the revision rather than by reading it: PostgreSQL refuses
    the retype with "operator does not exist: assignment_status_enum_new =
    assignment_status_enum" while a check constraint or a partial index still
    holds a literal of the old type. Both dependents come off first and go back
    on afterwards -- and the one-IN_PROGRESS index in particular must come back,
    because it is the only thing that settles two concurrent `/start` calls.
    """
    dropped = [drop for drop, _ in MODULE.STATUS_DEPENDENTS]
    restored = [restore for _, restore in MODULE.STATUS_DEPENDENTS]
    assert any("uq_ticket_assignments_one_in_progress_per_technician" in item for item in dropped)
    assert any("ck_ticket_assignments_unable_reason_required" in item for item in dropped)
    assert any("CREATE UNIQUE INDEX uq_ticket_assignments_one_in_progress_per_technician" in item for item in restored)
    assert any("ADD CONSTRAINT ck_ticket_assignments_unable_reason_required" in item for item in restored)

    retype = SOURCE.index("TYPE assignment_status_enum_new USING")
    rename = SOURCE.index("RENAME TO assignment_status_enum")
    drop_loop = SOURCE.index("for drop, _restore in STATUS_DEPENDENTS")
    restore_loop = SOURCE.index("for _drop, restore in STATUS_DEPENDENTS")
    assert drop_loop < retype, "the dependents are still in place when the column is retyped"
    assert rename < restore_loop, "the dependents are rebuilt before the type is renamed back"


def test_the_data_is_deleted_before_the_enum_is_rebuilt():
    """A row still holding 'ACCEPTED' would make the cast fail."""
    assert SOURCE.index("for table in TICKET_DOMAIN_TABLES") < SOURCE.index("CREATE TYPE assignment_status_enum_new")


def test_every_acceptance_column_is_dropped_and_nothing_else():
    assert MODULE.ACCEPTANCE_COLUMNS == (
        "accepted_at",
        "acceptance_due_at",
        "acceptance_warning_at",
        "warning_sent_at",
        "cycle_started_at",
    )
    table = Base.metadata.tables["ticket_assignments"]
    for column in MODULE.ACCEPTANCE_COLUMNS:
        assert column not in table.c, f"{column} is dropped by the migration but still on the model"


def test_the_scheduling_columns_survive():
    """§4's planning facts are kept; only the acceptance clock goes."""
    table = Base.metadata.tables["ticket_assignments"]
    for column in (
        "assigned_at",
        "planned_start_at",
        "planned_finish_at",
        "planned_order",
        "risk_state",
        "slack_seconds",
        "started_at",
        "completed_at",
        "ended_at",
        "end_reason",
    ):
        assert column in table.c
        assert column not in MODULE.ACCEPTANCE_COLUMNS


def test_the_one_in_progress_index_survives_the_enum_swap():
    """It matters more than before: starting is now the only way into that state.

    The revision does touch it -- it has to, see the dependency test above --
    but it comes back with the same name and the same predicate, so the model
    and the database still agree about it.
    """
    table = Base.metadata.tables["ticket_assignments"]
    names = {index.name for index in table.indexes}
    assert "uq_ticket_assignments_one_in_progress_per_technician" in names
    restored = [restore for _, restore in MODULE.STATUS_DEPENDENTS]
    rebuild = next(item for item in restored if "one_in_progress_per_technician" in item)
    assert "UNIQUE INDEX" in rebuild
    assert "(technician_id)" in rebuild
    assert "status = 'IN_PROGRESS'" in rebuild
    assert "is_active" in rebuild


def test_no_start_deadline_is_invented():
    """§3 of the change: the start SLA is an open business decision.

    A column added now would need a default, and that default would silently
    become the policy nobody approved.
    """
    assert "start_due_at" not in SOURCE.split('"""', 2)[2]
    assert "start_warning_at" not in SOURCE.split('"""', 2)[2]


def test_the_delete_scope_is_the_ticket_domain_and_only_that():
    """Every table listed reaches `tickets`, or exists only to describe them.

    Derived from the model metadata rather than restated, so a new ticket-domain
    table added later fails this test instead of being quietly skipped by the
    reset.
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
    # Everything with a path to `tickets` must be in the reset, or the delete
    # fails on a foreign key -- or worse, succeeds and leaves orphans.
    #
    # `ticket_risk_assessments` is exempt because it did not exist when this
    # revision ran: `a1b2c3d4e5f7` creates it, and that revision's own test
    # asserts its reset covers it. A revision cannot be asked to delete from a
    # table a later one introduces.
    introduced_later = {"ticket_risk_assessments"}
    assert reaches_tickets - introduced_later <= listed, (
        f"missing from the reset: {sorted(reaches_tickets - introduced_later - listed)}"
    )

    # The three that carry no `ticket_id` are named here deliberately, so adding
    # a fourth is a decision rather than an accident.
    assert listed - reaches_tickets == {
        "incident_cases",
        "resident_ticket_rate_limits",
        "ticket_attachment_upload_sessions",
    }


def test_the_building_and_its_people_are_not_deleted():
    """Accounts, catalogs and configuration outlive a ticket-data reset."""
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
    # The audit trail is trimmed, not truncated: only rows about tickets and
    # assignments, which is where ACCEPT_ASSIGNMENT lives.
    assert "DELETE FROM audit_logs WHERE entity_type IN ('TICKET', 'TICKET_ASSIGNMENT')" in SOURCE


#: Foreign keys the migration nulls out before the delete loop runs, so they
#: impose no ordering on it. Both are on `tickets`: the self-reference, and the
#: forward half of the tickets <-> ai_analysis_runs cycle.
PRE_NULLED = {("tickets", "duplicate_of_ticket_id"), ("tickets", "duplicate_analysis_run_id")}


def test_children_are_deleted_before_their_parents():
    """Ordering, or the foreign keys refuse the delete."""
    order = MODULE.TICKET_DOMAIN_TABLES
    position = {name: index for index, name in enumerate(order)}
    tables = Base.metadata.tables
    for name in order:
        if name not in tables:
            continue
        for fk in tables[name].foreign_keys:
            parent = fk.column.table.name
            if parent == name or parent not in position:
                continue
            if (name, fk.parent.name) in PRE_NULLED:
                continue
            assert position[name] < position[parent], f"{name} is deleted after its parent {parent}"


def test_every_pre_nulled_column_is_actually_nulled():
    """The exemption above is only sound if the migration really clears them."""
    for table, column in PRE_NULLED:
        assert f"UPDATE {table} SET {column} = NULL" in SOURCE


def test_the_cycle_between_tickets_and_analysis_runs_is_broken_first():
    """`tickets.duplicate_analysis_run_id` points forward; the runs point back."""
    assert SOURCE.index("UPDATE tickets SET duplicate_analysis_run_id = NULL") < SOURCE.index(
        "for table in TICKET_DOMAIN_TABLES"
    )
