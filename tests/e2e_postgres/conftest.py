"""Gate and wiring for the PostgreSQL end-to-end suite.

These tests are the only ones that touch a real PostgreSQL database, and they
truncate it. The rules about which database that may be live in `safety.py`,
where they can be tested without PostgreSQL; this module is the wiring that
enforces them and points `SessionLocal` at the result.

The order matters. Nothing connects until the URL has passed every offline
check, nothing destructive runs until the sentinel has been read back from the
database itself, and `PostgresEnv.truncate()` re-reads that sentinel every time
rather than trusting a flag set earlier in the session.

An unset `V4_E2E_DATABASE_URL` skips the suite. Anything else — a wrong URL, a
missing sentinel, an unmigrated schema — fails loudly, because a silent skip is
how an unverified claim gets made.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import src.database.models  # noqa: F401  - registers every mapper
from tests.e2e_postgres.safety import (
    ENV_VAR,
    REPO_ROOT,
    UnsafeE2ETargetError,
    require_disposable_guard,
    validate_url,
)

# Everything the v4 flows write. Truncated before seeding so the suite is
# repeatable; the schema itself is left to Alembic, and the sentinel table is
# deliberately absent from this list.
E2E_TABLES = (
    "at_risk_decisions",
    "dispatch_events",
    "auto_assignment_settings",
    "ai_agent_tool_calls",
    "ai_agent_questions",
    "ai_analysis_runs",
    "ai_analysis_sessions",
    "ticket_relations",
    "incident_case_members",
    "incident_cases",
    "ticket_assignments",
    "ticket_status_history",
    "ticket_attachments",
    "notifications",
    "audit_logs",
    "tickets",
    "technician_skills",
    "technician_profiles",
    "resident_profiles",
    "units",
    "locations",
    "location_types",
    "floors",
    "buildings",
    "categories",
    "user_profiles",
    "resident_ticket_rate_limits",
    "auth.users",
)


def _configured_database_url() -> str | None:
    """What `.env` points at — the database this suite must never be given.

    Read from the file rather than the environment because `tests/conftest.py`
    has already replaced `DATABASE_URL` with the in-memory SQLite URL by the
    time any of this runs.
    """
    env_file = Path(REPO_ROOT) / ".env"
    if not env_file.exists():
        return None
    from dotenv import dotenv_values

    return dotenv_values(env_file).get("DATABASE_URL")


def _fail(exc: UnsafeE2ETargetError) -> None:
    pytest.fail(f"{ENV_VAR} is unsafe: {exc}", pytrace=False)


def _validated_url() -> str:
    url = os.environ.get(ENV_VAR, "").strip()
    if not url:
        pytest.skip(
            f"{ENV_VAR} is not set. See docs/operations.md §8 for the disposable "
            "PostgreSQL procedure."
        )
    try:
        return validate_url(
            url,
            configured_database_url=_configured_database_url(),
            app_env=os.environ.get("APP_ENV"),
        )
    except UnsafeE2ETargetError as exc:
        _fail(exc)
        raise  # unreachable; keeps the return type honest


def _require_migrated(engine) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config(str(Path(REPO_ROOT) / "alembic.ini"))).get_current_head()
    with engine.connect() as connection:
        stamped = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
    if stamped != head:
        pytest.fail(
            f"Database is at revision {stamped!r}, expected head {head!r}. "
            f"Run: DATABASE_URL=... python -m alembic upgrade head",
            pytrace=False,
        )


@dataclass
class PostgresEnv:
    engine: object
    session_factory: sessionmaker

    def session(self) -> Session:
        return self.session_factory()

    def truncate(self) -> None:
        """The only destructive statement in the package.

        The sentinel is re-read here rather than trusted from fixture setup.
        It costs one round trip, and it means no future caller can reach a
        `TRUNCATE` through this object without the check having just passed.
        """
        require_disposable_guard(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {', '.join(E2E_TABLES)} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def pg_env() -> Iterator[PostgresEnv]:
    """A real PostgreSQL database, with `SessionLocal` pointed at it.

    The v4 runtime opens its own short-lived sessions through `SessionLocal` —
    the graph, `finalize()` and each worker stage all get
    their own, which is the whole point of running this against PostgreSQL:
    the row locks, the partial unique indexes and the check constraints are
    the real ones here.
    """
    url = _validated_url()
    engine = create_engine(url, pool_pre_ping=True)
    try:
        require_disposable_guard(engine)
    except UnsafeE2ETargetError as exc:
        engine.dispose()
        _fail(exc)
    _require_migrated(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    # Rebind every module that imported `SessionLocal` by value, not just the
    # module that defines it — see tests/test_workflow/conftest.py for the same
    # problem. Restored on teardown so nothing leaks into a later suite.
    import src.database.session as db_session_module

    original = db_session_module.SessionLocal
    patched: list[object] = []
    for module in [db_session_module, *sys.modules.values()]:
        if module is None or getattr(module, "__name__", "").split(".")[0] != "src":
            continue
        if getattr(module, "SessionLocal", None) is original:
            module.SessionLocal = factory
            patched.append(module)

    env = PostgresEnv(engine=engine, session_factory=factory)
    env.truncate()
    try:
        yield env
    finally:
        for module in patched:
            module.SessionLocal = original
        engine.dispose()
