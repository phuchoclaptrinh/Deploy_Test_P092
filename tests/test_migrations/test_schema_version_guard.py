"""The startup check that refuses a database behind the code.

Written after a real incident: the branch carried `a1b2c3d4e5f7`, the database
it was pointed at did not, and the app booted cleanly and then returned 500 on
`GET /coordinator/tickets` with `column tickets.current_risk_assessment_id does
not exist`. The message names one column, so the obvious reading is that one
column is missing rather than that an entire revision has not been applied.

The two silent cases are as much the subject as the raising one. A guard that
fires on a `create_all` database breaks the test suite, and one that fires on an
unreachable database sends people to look at migrations when the real problem is
the network.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from src.database.schema_version import (
    SchemaOutOfDateError,
    assert_schema_is_current,
    current_revision,
)


@pytest.fixture
def blank_engine(tmp_path):
    return create_engine(f"sqlite:///{tmp_path / 'blank.db'}")


def _stamp(engine, revision: str) -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": revision})


def test_a_database_with_no_version_table_is_left_alone(blank_engine):
    """`Base.metadata.create_all` builds no `alembic_version`, and a schema
    built straight from the models is in step with them by construction."""
    assert current_revision(blank_engine) is None
    assert_schema_is_current(blank_engine)  # does not raise


def test_a_database_at_head_passes(blank_engine):
    from src.database.schema_version import _head_revision

    head = _head_revision()
    assert head is not None, "the migration chain should be readable from a checkout"
    _stamp(blank_engine, head)
    assert current_revision(blank_engine) == head
    assert_schema_is_current(blank_engine)  # does not raise


def test_a_database_behind_the_chain_is_refused(blank_engine):
    # The revision the incident happened on: the last one before the v2 cutover.
    _stamp(blank_engine, "5b6c7d8e9f0a")
    with pytest.raises(SchemaOutOfDateError) as error:
        assert_schema_is_current(blank_engine)
    message = str(error.value)
    assert "5b6c7d8e9f0a" in message, "the message must say where the database actually is"
    assert "alembic upgrade head" in message, "and what to run"
    assert "MIGRATION_TARGET" in message, "a remote target must be named explicitly"
    assert "ALLOW_LIVE_MIGRATION" in message, "the destructive command still needs its opt-in"


def test_the_refusal_warns_about_the_destructive_revision(blank_engine):
    """`a1b2c3d4e5f7` deletes the ticket graph and has no downgrade. Somebody
    reading this message is about to run it."""
    _stamp(blank_engine, "5b6c7d8e9f0a")
    with pytest.raises(SchemaOutOfDateError) as error:
        assert_schema_is_current(blank_engine)
    assert "a1b2c3d4e5f7" in str(error.value)
    assert "operations.md" in str(error.value)
    assert "§12" in str(error.value)
    assert "prove the backup can be restored" in str(error.value)


def test_an_unreachable_database_is_not_reported_as_a_schema_problem():
    """It is a connection problem, and it will say so on its own."""
    engine = create_engine("postgresql+psycopg://nobody@127.0.0.1:1/nothing", connect_args={"connect_timeout": 1})
    assert current_revision(engine) is None
    assert_schema_is_current(engine)  # does not raise
