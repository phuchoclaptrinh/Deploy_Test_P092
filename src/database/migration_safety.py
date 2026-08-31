"""Safety checks for live database migrations.

Three gates now, answering different questions. `APP_ENV` and
`ALLOW_LIVE_MIGRATION` ask whether this checkout may migrate anything at all.
`MIGRATION_TARGET` asks whether it may migrate *this particular* database.

The third exists because the first two were not protecting what they appeared
to protect. A working `.env` on this project carries `APP_ENV=development` --
correctly, it is a development checkout -- while `DATABASE_URL` points at a
hosted Supabase project holding the only copy of the data. Both gates passed.
`alembic upgrade head` from that shell would have run `a1b2c3d4e5f7`, which
deletes the operational ticket graph and has no downgrade, against the shared
database, and nothing in the chain would have asked a question first.

So a non-local target has to be named on the command line, and the name has to
match what `DATABASE_URL` actually resolves to. Naming it is the whole point.
An operator who types the fingerprint of the staging clone and is refused
because `.env` still points at production has been told the one thing they
needed to know, on the near side of the delete.

Local targets are exempt. `alembic upgrade head` against localhost is the
ordinary development loop; it runs many times a day, and adding ceremony to it
would only teach people to keep the ceremony permanently satisfied -- which is
exactly how `ALLOW_LIVE_MIGRATION=true` came to be sitting in `.env` twice.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from src.config import Settings, get_settings

#: Hosts where getting it wrong costs one developer an afternoon rather than
#: costing a building its ticket history.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})

#: Alembic commands that may inspect the migration chain or database but never
#: change the database schema. Everything not named here is treated as mutating
#: so a new or programmatic Alembic command cannot accidentally bypass the gate.
READ_ONLY_ALEMBIC_COMMANDS = frozenset({"check", "current", "heads", "history", "revision", "show"})


def alembic_command_requires_live_migration_approval(command_name: str | None) -> bool:
    """Return False only for Alembic commands known to leave the DB untouched."""
    return command_name not in READ_ONLY_ALEMBIC_COMMANDS


def target_fingerprint(database_url: str) -> str:
    """Return `host[:port]/database` for a SQLAlchemy URL.

    Enough to tell two servers apart, and it carries no credentials, so it is
    safe to put in an error message, a shell history and a migration log.
    """
    parsed = urlsplit(database_url)
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port else ""
    return f"{host}{port}/{parsed.path.lstrip('/')}"


def is_local_target(database_url: str) -> bool:
    """Whether this URL names a database only this machine can reach.

    A URL with no host at all is local by construction -- `sqlite:///./app.db`
    is a file, and there is no server to name. Without this the check treated
    an empty hostname as "not in LOCAL_HOSTS" and demanded a fingerprint for
    the most local target there is.
    """
    host = urlsplit(database_url).hostname
    if not host:
        return True
    return host.lower() in LOCAL_HOSTS


def validate_live_migration_safety(settings: Settings | None = None) -> None:
    """Allow online migrations only for explicitly gated dev/test targets."""
    selected = settings or get_settings()

    if selected.app_env not in {"development", "test"}:
        raise RuntimeError("Online migration is allowed only in development or test.")

    if not selected.allow_live_migration:
        raise RuntimeError("Set ALLOW_LIVE_MIGRATION=true to run online migrations.")

    _validate_the_target_was_named(selected)


def _validate_the_target_was_named(settings: Settings) -> None:
    url = settings.database_url
    if not url:
        # Nothing resolved yet. `alembic/env.py` calls `require_database_url()`
        # immediately afterwards and says so in its own words; duplicating that
        # here would just mean two different messages for one missing variable.
        return
    if is_local_target(url):
        return

    fingerprint = target_fingerprint(url)
    declared = settings.migration_target.strip()

    if not declared:
        raise RuntimeError(
            f"DATABASE_URL points at {fingerprint}, which is not a local database.\n"
            f"Name the target before migrating it:\n"
            f"\n"
            f'    MIGRATION_TARGET="{fingerprint}" ALLOW_LIVE_MIGRATION=true \\\n'
            f"        python -m alembic upgrade head\n"
            f"\n"
            f"APP_ENV says development, but that describes this checkout, not the\n"
            f"server at the far end of DATABASE_URL. Typing the target is how the\n"
            f"two get checked against each other."
        )

    if declared != fingerprint:
        raise RuntimeError(
            f"MIGRATION_TARGET names {declared}, but DATABASE_URL resolves to {fingerprint}.\n"
            f"One of the two is not the database you meant, and this chain contains a\n"
            f"revision that cannot be undone. Nothing is migrated until they agree."
        )


__all__ = [
    "LOCAL_HOSTS",
    "READ_ONLY_ALEMBIC_COMMANDS",
    "alembic_command_requires_live_migration_approval",
    "is_local_target",
    "target_fingerprint",
    "validate_live_migration_safety",
]
