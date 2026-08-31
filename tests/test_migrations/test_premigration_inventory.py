"""The inventory taken before the cutover deletes the ticket graph.

The report is the only record of what was there. Its failure mode is not a
wrong number -- it is a table nobody listed, counted in neither column, so the
"everything else is unchanged" check silently skips it and nobody finds out
until somebody goes looking for data that is no longer there.

So the assertion that matters is completeness: every table the ORM knows about
is either one the cutover empties or one it must leave alone. Add a model and
this fails until the new table is placed in one of the two lists.

The report itself is PostgreSQL-only (`to_regclass`, `version()`), which is
also the point -- it exists to be run against a real server, and the test suite
runs on SQLite. What can be checked here is the bookkeeping, and it is the part
a person cannot check by eye.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def inventory():
    return _load(REPO / "scripts" / "premigration_inventory.py", "_inventory")


@pytest.fixture(scope="module")
def cutover():
    path = REPO / "alembic" / "versions" / "a1b2c3d4e5f7_hard_cutover_to_risk_scoring_v2.py"
    return _load(path, "_cutover")


@pytest.fixture(scope="module")
def model_tables() -> set[str]:
    import src.database.models  # noqa: F401  -- registers every mapper
    from src.database.base import Base

    return set(Base.metadata.tables)


def test_the_doomed_list_is_the_migrations_own(inventory, cutover):
    """Read from the revision, not restated beside it. A second copy of an
    eighteen-table deletion list is a second thing to forget to update."""
    assert inventory._tables_the_cutover_empties() == tuple(cutover.TICKET_DOMAIN_TABLES)


def test_nothing_is_in_both_columns(inventory):
    doomed = set(inventory._tables_the_cutover_empties())
    assert not doomed & set(inventory.SURVIVING_TABLES)


def test_every_table_is_accounted_for(inventory, model_tables):
    """The one that actually protects anything.

    A table in neither list is invisible to the report: it is not counted
    before, so an unexpected change afterwards cannot be noticed.
    """
    doomed = set(inventory._tables_the_cutover_empties())
    listed = doomed | set(inventory.SURVIVING_TABLES) | {"audit_logs"}
    missing = sorted(model_tables - listed)
    assert not missing, (
        f"{missing} appear in the ORM but in neither column of the inventory. "
        "Put each one in TICKET_DOMAIN_TABLES (if the cutover empties it) or in "
        "SURVIVING_TABLES (if it must be untouched)."
    )


def test_the_inventory_names_no_table_the_orm_does_not_have(inventory, model_tables):
    """The other direction: a stale name counts nothing and reads as a table
    that was already empty."""
    doomed = set(inventory._tables_the_cutover_empties())
    unknown = sorted((doomed | set(inventory.SURVIVING_TABLES)) - model_tables)
    assert not unknown, f"{unknown} are listed in the inventory but are not ORM tables"


def test_it_starts_from_the_revision_before_the_cutover(inventory, cutover):
    assert inventory.EXPECTED_REVISION == cutover.down_revision


def test_the_audit_trail_is_only_trimmed_of_ticket_rows(cutover):
    """Account, category and auto-assignment history is not ticket data."""
    source = (
        REPO / "alembic" / "versions" / "a1b2c3d4e5f7_hard_cutover_to_risk_scoring_v2.py"
    ).read_text(encoding="utf-8")
    assert "DELETE FROM audit_logs WHERE entity_type IN ('TICKET', 'TICKET_ASSIGNMENT')" in source
    assert "DELETE FROM audit_logs;" not in source


def test_the_script_will_not_read_a_url_from_the_environment(inventory):
    """Every failure this whole procedure guards against starts with acting on
    a database nobody named out loud."""
    source = (REPO / "scripts" / "premigration_inventory.py").read_text(encoding="utf-8")
    assert "get_settings" not in source
    assert "os.environ" not in source
    assert "getenv" not in source
