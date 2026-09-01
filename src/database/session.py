"""Synchronous SQLAlchemy engine and session factory.

§8's connection budget is enforced here, and the shape of this module follows
from it. The API and the dispatch worker are two processes talking to the same
Supabase instance, whose session pooler has a hard 15-session quota. Neither
process can see the other's usage, so each sizes its own pool from configuration
and `Settings.validate_runtime_safety` refuses to boot when the two budgets add
up to more than the quota.

The module-level `engine` / `SessionLocal` belong to the **API** role, because
that is what almost every importer wants and a default that silently gave a
background process the API's pool would be the expensive mistake. The worker
calls `make_session_factory("worker")` explicitly instead.
"""

from collections.abc import Generator
from typing import Literal

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings

settings = get_settings()

DbRole = Literal["api", "worker"]


def get_database_url() -> str:
    """Return a SQLAlchemy URL compatible with the installed psycopg driver."""
    database_url = settings.require_database_url()
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def engine_options_for(role: DbRole) -> dict[str, object]:
    """Pool settings for one process role.

    Only PostgreSQL gets pool options: SQLite (tests, local runs) has no server
    session to budget, and passing `pool_size` to its default pool class raises.

    `pool_timeout` stays deliberately short. When the quota really is exhausted,
    failing a request in five seconds surfaces the problem; blocking on the pool
    turns it into a cascade of slow requests that looks like an application
    performance issue instead of a connection one.
    """
    database_url = get_database_url()
    if not database_url.startswith("postgresql"):
        return {}
    if role == "worker":
        pool_size = settings.dispatch_worker_db_pool_size
        max_overflow = settings.dispatch_worker_db_max_overflow
    else:
        pool_size = settings.api_db_pool_size
        max_overflow = settings.api_db_max_overflow
    return {
        "pool_size": pool_size,
        "max_overflow": max_overflow,
        "pool_timeout": 5,
        "pool_recycle": 300,
        "pool_pre_ping": True,
    }


def make_engine(role: DbRole = "api") -> Engine:
    return create_engine(get_database_url(), **engine_options_for(role))


def make_session_factory(role: DbRole = "api") -> sessionmaker[Session]:
    """A session factory sized for one process role.

    The dispatch worker calls this rather than importing `SessionLocal`, so its
    pool is the worker budget and not the API's.
    """
    return sessionmaker(autocommit=False, autoflush=False, bind=make_engine(role))


database_url = get_database_url()
engine = make_engine("api")
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session]:
    """Yield a database session and close it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
