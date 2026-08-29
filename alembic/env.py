from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import src.database.models  # noqa: F401
from alembic import context
from src.config import get_settings
from src.database.base import Base
from src.database.migration_safety import validate_live_migration_safety as _validate_live_migration_safety

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    """Return the configured SQLAlchemy URL without exposing credentials."""
    database_url = get_settings().require_database_url()
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def validate_live_migration_safety() -> None:
    _validate_live_migration_safety()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    validate_live_migration_safety()
    config.set_main_option("sqlalchemy.url", get_database_url())

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            # One transaction per revision, not one for the whole run.
            #
            # PostgreSQL refuses to *use* an enum label that was added by an
            # as-yet-uncommitted transaction ("unsafe use of new value ... of
            # enum type"). Several revisions here add a label and a later
            # revision references it in a policy or a check constraint, so a
            # single run-wide transaction cannot migrate a fresh database at
            # all. Committing per revision also means a failure leaves the
            # earlier revisions applied instead of rolling the whole chain back.
            transaction_per_migration=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
