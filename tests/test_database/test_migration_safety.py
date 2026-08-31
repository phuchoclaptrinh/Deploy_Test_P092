"""The gates in front of `alembic upgrade`.

Written after the near miss they exist to prevent. `.env` on this project sets
`APP_ENV=development`, which is true of the checkout, and `DATABASE_URL` to a
hosted Supabase project, which is where the only copy of the data lives. The
env/flag gates both passed, so `alembic upgrade head` from a developer shell
would have run `a1b2c3d4e5f7` -- which deletes the ticket graph and has no
downgrade -- against the shared database without asking anything.

The tests below are mostly about the *shape* of the refusal rather than the
fact of it. A gate that stops the wrong migration but does not say which two
values disagreed gets satisfied by whichever variable is easiest to change.
"""

from __future__ import annotations

import pytest

from src.config import Settings
from src.database.migration_safety import (
    READ_ONLY_ALEMBIC_COMMANDS,
    alembic_command_requires_live_migration_approval,
    is_local_target,
    target_fingerprint,
    validate_live_migration_safety,
)

#: `user:password@` rather than anything shorter in the two `postgresql://`
#: literals below: `scripts/scan_secrets.py` matches that scheme followed by
#: credentials, and `postgresql://user:password@` is the placeholder it already
#: allowlists. The `postgresql+psycopg://` forms do not match the pattern at
#: all, so they keep a distinguishable password -- one of the tests is that the
#: fingerprint drops it.
LOCAL = "postgresql+psycopg://postgres:secret@localhost:5432/fixit"
REMOTE = "postgresql+psycopg://postgres:secret@db.abcdefgh.supabase.co:5432/postgres"
REMOTE_FINGERPRINT = "db.abcdefgh.supabase.co:5432/postgres"


def settings(**overrides) -> Settings:
    """Every field the gate reads is pinned, so `.env` cannot decide a test."""
    base = {
        "app_env": "development",
        "allow_live_migration": True,
        "database_url": LOCAL,
        "migration_target": "",
    }
    return Settings(**{**base, **overrides})


# --- the two original gates -------------------------------------------------


def test_production_cannot_migrate_online_at_all():
    with pytest.raises(RuntimeError, match="development or test"):
        validate_live_migration_safety(settings(app_env="production"))


def test_the_flag_is_required():
    with pytest.raises(RuntimeError, match="ALLOW_LIVE_MIGRATION"):
        validate_live_migration_safety(settings(allow_live_migration=False))


@pytest.mark.parametrize("command", sorted(READ_ONLY_ALEMBIC_COMMANDS))
def test_known_read_only_alembic_commands_need_no_live_migration_approval(command):
    assert not alembic_command_requires_live_migration_approval(command)


@pytest.mark.parametrize("command", ["upgrade", "downgrade", "stamp", "ensure_version", None, "new_command"])
def test_mutating_or_unknown_alembic_commands_fail_closed(command):
    assert alembic_command_requires_live_migration_approval(command)


# --- naming the target ------------------------------------------------------


def test_a_local_target_needs_no_ceremony():
    """The ordinary development loop. Ceremony here would be kept permanently
    satisfied, which is how the flag ended up in `.env` twice."""
    validate_live_migration_safety(settings())  # does not raise


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "host.docker.internal"])
def test_the_local_hosts(host):
    assert is_local_target(f"postgresql+psycopg://u:p@{host}:5432/fixit")


def test_a_remote_target_is_refused_until_it_is_named():
    with pytest.raises(RuntimeError) as error:
        validate_live_migration_safety(settings(database_url=REMOTE))
    message = str(error.value)
    assert REMOTE_FINGERPRINT in message, "the refusal must say which database it saw"
    assert "MIGRATION_TARGET" in message, "and which variable answers it"


def test_the_refusal_says_why_app_env_did_not_cover_this():
    """`APP_ENV=development` is not wrong, and somebody reading the refusal
    will reasonably think it should already have been enough."""
    with pytest.raises(RuntimeError) as error:
        validate_live_migration_safety(settings(database_url=REMOTE))
    assert "APP_ENV" in str(error.value)


def test_a_named_remote_target_is_allowed():
    validate_live_migration_safety(
        settings(database_url=REMOTE, migration_target=REMOTE_FINGERPRINT)
    )  # does not raise


def test_surrounding_whitespace_is_not_a_mismatch():
    """Shells and copy-paste add it; it is not a different database."""
    validate_live_migration_safety(
        settings(database_url=REMOTE, migration_target=f"  {REMOTE_FINGERPRINT}\n")
    )


def test_naming_one_database_while_pointing_at_another_is_refused():
    """The case the gate is really for: the operator means the staging clone,
    and `.env` still points at production."""
    with pytest.raises(RuntimeError) as error:
        validate_live_migration_safety(
            settings(database_url=REMOTE, migration_target="staging.internal:5432/fixit")
        )
    message = str(error.value)
    assert "staging.internal:5432/fixit" in message, "must show what was asked for"
    assert REMOTE_FINGERPRINT in message, "and what was actually connected"


def test_a_missing_url_is_left_to_the_caller():
    """`alembic/env.py` reports this in its own words one line later. Two
    different messages for one missing variable helps nobody."""
    validate_live_migration_safety(settings(database_url=""))  # does not raise


# --- the fingerprint --------------------------------------------------------


def test_the_fingerprint_carries_no_credentials():
    """It goes into error messages, shell history and migration logs."""
    fingerprint = target_fingerprint(REMOTE)
    assert "secret" not in fingerprint
    assert "postgres:" not in fingerprint
    assert fingerprint == REMOTE_FINGERPRINT


def test_the_fingerprint_ignores_the_driver():
    """`postgresql://` and `postgresql+psycopg://` are the same server, and
    `alembic/env.py` rewrites one into the other on its way past."""
    plain = "postgresql://user:password@db.abcdefgh.supabase.co:5432/postgres"
    assert target_fingerprint(plain) == target_fingerprint(REMOTE)


def test_the_pooler_is_a_different_fingerprint_from_the_direct_host():
    """A session pooler may be a valid fallback, but it remains a different
    endpoint and the operator must name the one actually being migrated."""
    pooler = "postgresql://user:password@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"
    assert target_fingerprint(pooler) != target_fingerprint(REMOTE)
