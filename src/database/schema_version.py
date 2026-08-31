"""Refuse to serve a database whose schema is behind the code.

The failure this exists to prevent is a confusing one. The ORM knows about
`tickets.current_risk_assessment_id` because `a1b2c3d4e5f7` adds it; a database
that has not run that migration does not have the column. Nothing notices at
boot -- the app starts, reports itself healthy, and then every request that
touches `tickets` returns a 500 with a psycopg `UndefinedColumn` buried under
forty lines of SQLAlchemy traceback. The message names one column, so the
obvious reading is that one column is missing, when in fact the whole revision
is.

Checked once at startup, against the same `alembic_version` row `alembic
current` reads. A schema that is behind is not a degraded mode worth serving:
every write path in the application is already broken, and failing at boot puts
the error where somebody is looking, in a sentence that says what to run.

**Deliberately silent in two cases.**

A database with no `alembic_version` table has not been migrated at all -- it
was built by `Base.metadata.create_all`, which is what the test suite does, and
which is by construction in step with the models. There is no revision to
compare.

A database that cannot be reached is not a schema problem, and reporting it as
one would send somebody looking in the wrong place. The connection error will
surface on its own, in its own words, on the first request.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_DIR = REPO_ROOT / "alembic"

VERSION_TABLE = "alembic_version"


class SchemaOutOfDateError(RuntimeError):
    """The database is behind the migration chain the code was written for."""


def _head_revision() -> str | None:
    """The single head of the local migration chain, or None if unreadable.

    Unreadable rather than fatal: a deployment that ships the application
    without the `alembic/` directory is a legitimate packaging choice, and it
    should not be turned into a boot failure by a check that exists to make
    boot failures clearer.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        if not ALEMBIC_DIR.exists():
            return None
        config = Config()
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        heads = ScriptDirectory.from_config(config).get_heads()
    except Exception:  # noqa: BLE001 -- see the docstring; never fatal here.
        logger.debug("Could not read the migration chain; skipping the schema check.", exc_info=True)
        return None
    if len(heads) != 1:
        # Two heads is its own bug, and `tests/test_migrations/test_single_head.py`
        # is where it is reported. Not worth blocking a boot over.
        logger.warning("Migration chain has %d heads; skipping the schema version check.", len(heads))
        return None
    return heads[0]


def current_revision(engine: Engine) -> str | None:
    """What the database says it is at, or None when it has no version table."""
    try:
        if not inspect(engine).has_table(VERSION_TABLE):
            return None
        with engine.connect() as connection:
            return connection.scalar(text(f"SELECT version_num FROM {VERSION_TABLE}"))
    except SQLAlchemyError:
        logger.debug("Could not read %s; skipping the schema check.", VERSION_TABLE, exc_info=True)
        return None


def assert_schema_is_current(engine: Engine) -> None:
    """Raise when the database is behind the code. See the module docstring."""
    head = _head_revision()
    if head is None:
        return
    current = current_revision(engine)
    if current is None or current == head:
        return

    raise SchemaOutOfDateError(
        f"Database schema is at revision {current}; this build expects {head}.\n"
        f"Every request that touches a migrated table will fail until the chain is applied.\n"
        f"\n"
        f"Do not disable this guard. Back up the database and prove the backup can be restored first.\n"
        f"For a non-local database, the migration process must set MIGRATION_TARGET to the exact\n"
        f"host:port/database fingerprint and set ALLOW_LIVE_MIGRATION=true before running:\n"
        f"\n"
        f"    python -m alembic upgrade head\n"
        f"\n"
        f"Read docs/operations.md §12 first if {head} is being reached through "
        f"a1b2c3d4e5f7: that revision deletes the operational ticket graph and "
        f"has no downgrade."
    )


__all__ = ["ALEMBIC_DIR", "SchemaOutOfDateError", "assert_schema_is_current", "current_revision"]
