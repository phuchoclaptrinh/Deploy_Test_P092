"""The gate that stands between this suite and a database it must not truncate.

The suite is destructive by design — it truncates every table the v4 flows
write, so that it is repeatable. That makes "which database is this?" the most
important question in the package, and it is not a question a URL can answer.
A migrated Railway, Neon or RDS database has an ordinary PostgreSQL URL on an
ordinary host with an ordinary name; so does a colleague's staging copy.

So the authoritative check is not on the URL at all. It is a sentinel row that
only exists in a database somebody deliberately ran `scripts/postgres_test_shim.sql`
against. Applying that file is the act of consent; the fixture only reads it,
and never creates or repairs it. A fixture that could plant its own permission
slip would not be a gate.

Everything else here is defence in depth around that: a dedicated environment
variable, a PostgreSQL scheme, no managed-provider host, not the `.env`
database, not production, and a database name that says out loud what it is
for. Each layer is individually bypassable by a determined mistake; the
sentinel is the one that has to hold.

These helpers live outside `conftest.py` so the safety gate itself can be
tested — in the normal suite, with fake connections and no PostgreSQL.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

ENV_VAR = "V4_E2E_DATABASE_URL"
REPO_ROOT = Path(__file__).resolve().parents[2]
SHIM_PATH = "scripts/postgres_test_shim.sql"

# Hosts that are never a disposable test database, whatever the URL claims.
MANAGED_HOST_MARKERS = (
    "supabase.co",
    "supabase.com",
    "supabase.in",
    "pooler.supabase",
    "neon.tech",
    "rds.amazonaws.com",
    "railway.app",
    "render.com",
    "azure.com",
)

# The name this suite expects, and the suffix it will settle for. Not a
# security boundary — a name is only a label — but a database called
# `fixit_v4_e2e` is one somebody created for this, and `staging` is not.
EXPECTED_DATABASE_NAME = "fixit_v4_e2e"
ACCEPTED_NAME_SUFFIX = "_e2e"

# The sentinel. A fixed, project-specific marker, deliberately not a secret:
# its whole job is to be absent from every database nobody chose.
GUARD_TABLE = "public.v4_e2e_disposable_guard"
GUARD_KEY = "suite"
GUARD_VALUE = "fixit-v4-e2e-disposable-database"

_SHIM_HINT = (
    f"Apply `{SHIM_PATH}` to a database you are willing to have truncated, "
    "and only to such a database. See docs/operations.md §8."
)


class UnsafeE2ETargetError(RuntimeError):
    """The target database is not provably disposable, so nothing may run.

    Raised rather than skipped: a silent skip is how an unverified claim ends
    up in a report.
    """


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def validate_url(url: str, *, configured_database_url: str | None, app_env: str | None) -> str:
    """Every check that can be made without connecting.

    Ordered cheapest-first, and each one reports the specific reason: a gate
    that says only "unsafe" gets worked around rather than understood.
    """
    url = url.strip()
    parts = urlsplit(url)

    if not parts.scheme.startswith("postgresql"):
        raise UnsafeE2ETargetError(f"scheme {parts.scheme!r} is not PostgreSQL.")

    host = (parts.hostname or "").lower()
    if any(marker in host for marker in MANAGED_HOST_MARKERS):
        raise UnsafeE2ETargetError(
            f"host {host!r} belongs to a managed database provider, not a disposable database."
        )

    if configured_database_url and configured_database_url.strip() == url:
        raise UnsafeE2ETargetError("it is the same database as DATABASE_URL in .env.")

    if app_env == "production":
        raise UnsafeE2ETargetError("APP_ENV is production.")

    name = database_name(url)
    if name != EXPECTED_DATABASE_NAME and not name.endswith(ACCEPTED_NAME_SUFFIX):
        raise UnsafeE2ETargetError(
            f"database name {name!r} does not look disposable; expected "
            f"{EXPECTED_DATABASE_NAME!r} or a name ending in {ACCEPTED_NAME_SUFFIX!r}."
        )

    return url


def require_disposable_guard(engine) -> None:
    """The authoritative check. Read-only, and it never repairs anything.

    Any failure at all is fatal — a missing table, a missing row, a wrong
    marker, or a database error while asking. Especially a database error: the
    interesting case is a database that is not the one anyone meant, and being
    unable to read the sentinel there is exactly the answer being looked for.
    """
    statement = text(
        f"SELECT guard_value FROM {GUARD_TABLE} WHERE guard_key = :key"  # noqa: S608 - fixed identifier
    )
    try:
        with engine.connect() as connection:
            found = connection.execute(statement, {"key": GUARD_KEY}).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise UnsafeE2ETargetError(
            f"could not read the disposable-database sentinel {GUARD_TABLE}: "
            f"{type(exc).__name__}. {_SHIM_HINT}"
        ) from exc

    if found is None:
        raise UnsafeE2ETargetError(
            f"the disposable-database sentinel row {GUARD_KEY!r} is missing from "
            f"{GUARD_TABLE}. {_SHIM_HINT}"
        )
    if found != GUARD_VALUE:
        raise UnsafeE2ETargetError(
            f"the disposable-database sentinel in {GUARD_TABLE} reads {found!r}, "
            f"not the marker this suite plants. {_SHIM_HINT}"
        )
