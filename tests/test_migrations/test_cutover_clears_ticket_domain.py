"""The teardown step of `a1b2c3d4e5f7`, executed rather than read.

This file exists because of a specific failure. The cutover was run against a
real database and aborted on its second statement:

    psycopg.errors.CheckViolation: new row for relation "tickets" violates
    check constraint "ck_tickets_linked_duplicate_needs_master"
    [SQL: UPDATE tickets SET duplicate_of_ticket_id = NULL]

§7.1 says a LINKED_DUPLICATE ticket must point at a master. The teardown clears
that pointer to break the self-reference before deleting, and one ticket in the
database had been linked as a duplicate two days earlier. Every table in the
migration was correct; the *order of operations against data* was not.

Nothing caught it because every other test in `tests/test_migrations/` reads
the migration's source text. Source text cannot fail a check constraint. Nor
could the gap be closed by running the chain end to end on SQLite -- an earlier
revision renders JSONB with no dialect guard, so the v1 chain does not build
there at all, which is precisely why this code path had only ever run once, on
production, at the worst possible moment.

So: a table with the real constraint, a real LINKED_DUPLICATE row, and the
migration's own function called against it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "alembic" / "versions" / "a1b2c3d4e5f7_hard_cutover_to_risk_scoring_v2.py"


@pytest.fixture(scope="module")
def cutover():
    spec = importlib.util.spec_from_file_location("_cutover_exec", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def connection(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'cutover.db'}")
    with engine.begin() as conn:
        yield conn
    engine.dispose()


def _build_tickets(conn: sa.Connection, cutover) -> None:
    """The subset of `tickets` this step touches, with §7.1 in force.

    Deliberately not the full v1 table: what is under test is the interaction
    between one constraint and one UPDATE, and a faithful reproduction of
    thirty unrelated columns would only make the failure harder to read.
    """
    conn.execute(
        sa.text(
            "CREATE TABLE tickets ("
            "  id TEXT PRIMARY KEY,"
            "  status TEXT NOT NULL,"
            "  duplicate_of_ticket_id TEXT REFERENCES tickets(id) ON DELETE SET NULL,"
            "  duplicate_analysis_run_id TEXT,"
            f"  CONSTRAINT {cutover.LINKED_DUPLICATE_CHECK} CHECK ({cutover.LINKED_DUPLICATE_CONDITION})"
            ")"
        )
    )


def _link_a_duplicate(conn: sa.Connection) -> None:
    """A master and a ticket linked to it, which is what production had."""
    conn.execute(sa.text("INSERT INTO tickets (id, status) VALUES ('master', 'RESOLVED')"))
    conn.execute(
        sa.text(
            "INSERT INTO tickets (id, status, duplicate_of_ticket_id) "
            "VALUES ('dupe', 'LINKED_DUPLICATE', 'master')"
        )
    )


def _run(conn: sa.Connection, cutover) -> None:
    """Call the migration's own function, with `op` bound to this connection."""
    context = MigrationContext.configure(conn)
    with Operations.context(context):
        inspector = sa.inspect(conn)
        cutover.clear_ticket_domain(inspector, set(inspector.get_table_names()))


# --- the reported failure ---------------------------------------------------


def test_the_constraint_really_does_reject_the_naked_update(connection, cutover):
    """First, that the reproduction is real.

    A regression test for a fix is worth nothing unless the unfixed statement
    actually fails in it. This is the exact SQL from the traceback.
    """
    _build_tickets(connection, cutover)
    _link_a_duplicate(connection)
    with pytest.raises(sa.exc.IntegrityError):
        connection.execute(sa.text("UPDATE tickets SET duplicate_of_ticket_id = NULL"))


def test_the_teardown_survives_a_linked_duplicate(connection, cutover):
    """The fix. This is the case that aborted the production run."""
    _build_tickets(connection, cutover)
    _link_a_duplicate(connection)

    _run(connection, cutover)

    remaining = connection.execute(sa.text("SELECT count(*) FROM tickets")).scalar()
    assert remaining == 0, "the cutover empties the ticket table"


def test_the_invariant_is_restored_not_removed(connection, cutover):
    """Suspended for the teardown, in force again afterwards.

    The cheap fix -- drop the constraint and move on -- would pass the test
    above and leave v2 writing linked duplicates that point at nothing.
    """
    _build_tickets(connection, cutover)
    _link_a_duplicate(connection)

    _run(connection, cutover)

    with pytest.raises(sa.exc.IntegrityError):
        connection.execute(
            sa.text("INSERT INTO tickets (id, status) VALUES ('orphan', 'LINKED_DUPLICATE')")
        )


def test_a_table_with_no_linked_duplicates_is_emptied_too(connection, cutover):
    """The ordinary case still has to work."""
    _build_tickets(connection, cutover)
    connection.execute(sa.text("INSERT INTO tickets (id, status) VALUES ('plain', 'NEW')"))

    _run(connection, cutover)

    assert connection.execute(sa.text("SELECT count(*) FROM tickets")).scalar() == 0


def test_a_database_without_the_constraint_is_not_broken_by_the_fix(connection, cutover):
    """A database that never ran `5e6f7a8b9c0d` does not carry §7.1.

    Dropping a constraint that is not there would turn a missing precondition
    into a failed migration -- the same class of bug as the one being fixed.
    """
    connection.execute(
        sa.text(
            "CREATE TABLE tickets ("
            "  id TEXT PRIMARY KEY, status TEXT NOT NULL,"
            "  duplicate_of_ticket_id TEXT, duplicate_analysis_run_id TEXT)"
        )
    )
    connection.execute(sa.text("INSERT INTO tickets (id, status) VALUES ('x', 'LINKED_DUPLICATE')"))

    _run(connection, cutover)

    assert connection.execute(sa.text("SELECT count(*) FROM tickets")).scalar() == 0


def test_it_does_not_require_the_tables_to_exist(connection, cutover):
    """`existing` guards every statement; an absent table is skipped, not an
    error. A partially-built database must not turn into a stack trace."""
    _run(connection, cutover)  # nothing created at all
