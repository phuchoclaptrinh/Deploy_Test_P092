"""The safety gate that decides whether the E2E suite may truncate a database.

Deliberately *not* marked `postgres_e2e`: this runs in the normal suite, with
fake connections and no PostgreSQL. A gate whose own tests only run in the
environment it is protecting is a gate nobody checks.

The assertion that matters most is the last group's: when any layer of the gate
fails, no `TRUNCATE` reaches the connection. Everything else is a reason; that
one is the consequence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from tests.e2e_postgres.conftest import E2E_TABLES, PostgresEnv
from tests.e2e_postgres.safety import (
    GUARD_KEY,
    GUARD_TABLE,
    GUARD_VALUE,
    UnsafeE2ETargetError,
    require_disposable_guard,
    validate_url,
)

SAFE_URL = "postgresql+psycopg://postgres@127.0.0.1:55433/fixit_v4_e2e"
SHIM = Path(__file__).resolve().parents[2] / "scripts" / "postgres_test_shim.sql"


# ---------------------------------------------------------------------------
# Fakes. Just enough SQLAlchemy surface to answer one SELECT and record what
# was asked, so a failed guard can be shown to have executed nothing else.
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Connection:
    def __init__(self, engine, guard_value, error):
        self.engine = engine
        self._guard_value = guard_value
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, statement, parameters=None):
        self.engine.executed.append(str(statement))
        if self._error is not None:
            raise self._error
        return _Result(self._guard_value)


class FakeEngine:
    """`guard_value=None` means the row is missing; `error` means the read failed."""

    def __init__(self, *, guard_value: str | None = GUARD_VALUE, error: Exception | None = None):
        self.guard_value = guard_value
        self.error = error
        self.executed: list[str] = []

    def connect(self):
        return _Connection(self, self.guard_value, self.error)

    # `truncate()` writes inside a transaction; reads use `connect()`.
    def begin(self):
        return _Connection(self, self.guard_value, None)

    @property
    def truncates(self) -> list[str]:
        return [item for item in self.executed if "TRUNCATE" in item.upper()]


def _missing_table_error() -> ProgrammingError:
    """What PostgreSQL raises for `relation ... does not exist`."""
    return ProgrammingError(
        f"SELECT guard_value FROM {GUARD_TABLE}",
        {},
        Exception(f'relation "{GUARD_TABLE}" does not exist'),
    )


# ---------------------------------------------------------------------------
# The sentinel.
# ---------------------------------------------------------------------------


def test_the_exact_sentinel_is_accepted():
    require_disposable_guard(FakeEngine(guard_value=GUARD_VALUE))


def test_a_missing_guard_table_is_rejected():
    with pytest.raises(UnsafeE2ETargetError) as excinfo:
        require_disposable_guard(FakeEngine(error=_missing_table_error()))
    # The message has to say what to do, or it gets worked around.
    assert "scripts/postgres_test_shim.sql" in str(excinfo.value)


def test_a_missing_guard_row_is_rejected():
    with pytest.raises(UnsafeE2ETargetError) as excinfo:
        require_disposable_guard(FakeEngine(guard_value=None))
    assert GUARD_KEY in str(excinfo.value)


def test_an_incorrect_marker_is_rejected():
    """A guard table that exists with someone else's marker is not consent."""
    with pytest.raises(UnsafeE2ETargetError) as excinfo:
        require_disposable_guard(FakeEngine(guard_value="some-other-project"))
    assert "some-other-project" in str(excinfo.value)


def test_a_database_error_while_reading_is_rejected():
    """Being unable to ask is the answer: a database where the read fails is
    not a database anyone marked."""
    error = OperationalError("SELECT 1", {}, Exception("permission denied"))
    with pytest.raises(UnsafeE2ETargetError):
        require_disposable_guard(FakeEngine(error=error))


# ---------------------------------------------------------------------------
# The URL layers around it.
# ---------------------------------------------------------------------------


def _validate(url: str, *, configured: str | None = None, app_env: str | None = "test") -> str:
    return validate_url(url, configured_database_url=configured, app_env=app_env)


def test_a_disposable_url_passes_every_offline_check():
    assert _validate(SAFE_URL) == SAFE_URL


@pytest.mark.parametrize(
    "name",
    ["fixit_v4_e2e", "scratch_e2e", "someones_local_e2e"],
)
def test_a_database_name_that_says_what_it_is_for_is_accepted(name):
    _validate(f"postgresql://postgres@127.0.0.1:55433/{name}")


@pytest.mark.parametrize(
    "name",
    ["postgres", "fixit", "staging", "fixit_prod", "railway", "e2e_fixit"],
)
def test_an_unsafe_database_name_is_rejected(name):
    with pytest.raises(UnsafeE2ETargetError) as excinfo:
        _validate(f"postgresql://postgres@127.0.0.1:55433/{name}")
    assert "does not look disposable" in str(excinfo.value)


@pytest.mark.parametrize(
    "host",
    [
        "db.abcdefgh.supabase.co",
        "aws-0-ap-southeast-1.pooler.supabase.com",
        "ep-cool-name-123456.eu-central-1.aws.neon.tech",
        "mydb.abcdefgh.eu-west-1.rds.amazonaws.com",
        "containers-us-west-1.railway.app",
    ],
)
def test_a_managed_provider_host_is_rejected(host):
    """These all have ordinary PostgreSQL URLs and could all be somebody's
    real database."""
    with pytest.raises(UnsafeE2ETargetError) as excinfo:
        # No password component: this test is about the host, and a
        # credential-shaped literal in a tracked file is a scan_secrets finding.
        _validate(f"postgresql://user@{host}:5432/fixit_v4_e2e")
    assert "managed database provider" in str(excinfo.value)


def test_a_non_postgresql_scheme_is_rejected():
    with pytest.raises(UnsafeE2ETargetError):
        _validate("sqlite:///throwaway_e2e.db")


def test_the_configured_database_is_rejected():
    """Even when it is local and even when it is named like a test database."""
    with pytest.raises(UnsafeE2ETargetError) as excinfo:
        _validate(SAFE_URL, configured=SAFE_URL)
    assert "DATABASE_URL in .env" in str(excinfo.value)


def test_production_is_rejected():
    with pytest.raises(UnsafeE2ETargetError) as excinfo:
        _validate(SAFE_URL, app_env="production")
    assert "production" in str(excinfo.value)


# ---------------------------------------------------------------------------
# The consequence: a failed guard executes nothing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "engine_kwargs",
    [
        {"error": _missing_table_error()},
        {"guard_value": None},
        {"guard_value": "some-other-project"},
    ],
    ids=["no-guard-table", "no-guard-row", "wrong-marker"],
)
def test_no_truncate_is_executed_when_the_guard_fails(engine_kwargs):
    engine = FakeEngine(**engine_kwargs)
    env = PostgresEnv(engine=engine, session_factory=None)

    with pytest.raises(UnsafeE2ETargetError):
        env.truncate()

    assert engine.truncates == []


def test_the_guard_is_re_read_on_every_truncate():
    """Not cached from fixture setup: a `TRUNCATE` reachable through this
    object always has a just-passed check in front of it."""
    engine = FakeEngine()
    env = PostgresEnv(engine=engine, session_factory=None)

    env.truncate()
    env.truncate()

    assert len(engine.truncates) == 2
    assert len([item for item in engine.executed if "guard_value" in item]) == 2


def test_the_sentinel_table_is_never_truncated():
    """It has to survive the suite, or the next run would find no consent."""
    assert not any("guard" in table for table in E2E_TABLES)


# ---------------------------------------------------------------------------
# The committed shim.
# ---------------------------------------------------------------------------


def test_the_shim_plants_exactly_the_marker_the_gate_expects():
    """The two halves are in different files and have to agree."""
    sql = SHIM.read_text(encoding="utf-8")
    assert "public.v4_e2e_disposable_guard" in sql
    assert f"'{GUARD_KEY}'" in sql
    assert f"'{GUARD_VALUE}'" in sql


def test_the_shim_is_idempotent():
    """It is applied to a database that may already have been prepared, so
    every statement in it has to be re-runnable."""
    sql = SHIM.read_text(encoding="utf-8")
    statements = [
        line.strip()
        for line in sql.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]

    for line in statements:
        upper = line.upper()
        if upper.startswith("CREATE TABLE"):
            assert "IF NOT EXISTS" in upper, line
        if upper.startswith("CREATE SCHEMA"):
            assert "IF NOT EXISTS" in upper, line
        if upper.startswith("CREATE FUNCTION"):
            pytest.fail(f"not re-runnable, needs CREATE OR REPLACE: {line}")
        # Roles are guarded by the DO block's IF NOT EXISTS checks.
        if upper.startswith("CREATE ROLE"):
            assert "IF NOT EXISTS" in sql.upper(), line

    assert "ON CONFLICT (guard_key) DO UPDATE" in sql
    assert "CREATE TEMP" not in sql.upper()
    assert "CREATE TEMPORARY" not in sql.upper()
