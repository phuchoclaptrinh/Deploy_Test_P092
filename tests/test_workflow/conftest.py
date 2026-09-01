"""A real database for the workflow end-to-end tests.

The runtime opens its own short-lived database sessions through `SessionLocal`,
exactly as it does in production — the graph, the worker stages and `finalize()`
each get their own. The shared in-memory `db_session` fixture cannot serve that:
one connection handed to several sessions produces interleaved transactions that
prove nothing about the real thing.

So this fixture points `SessionLocal` at a file-backed SQLite database in the
test's own temp directory. Each session opens its own connection, commits are
real, and the isolation is close enough to PostgreSQL for what these tests
assert. Row-level locking is exercised against PostgreSQL by the migration
check, not here.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import src.database.models  # noqa: F401  - registers every mapper
from src.database.base import Base

# Registered here rather than imported per test module: a fixture imported into
# a test file is shadowed by the parameter of the same name, which is a real
# redefinition and which ruff is right to flag. Its own module explains why the
# race tests cannot use the shared in-memory session.
from tests.test_workflow.concurrency import concurrent_db  # noqa: F401, E402


@dataclass
class V4Env:
    session_factory: sessionmaker

    def session(self) -> Session:
        return self.session_factory()


@pytest.fixture
def v4_env(tmp_path, monkeypatch) -> Iterator[V4Env]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'v4.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    # `from src.database.session import SessionLocal` binds the factory into the
    # importing module, so patching only the source module would leave every
    # already-imported runtime module talking to the developer database. Rebind
    # it everywhere it currently points at the real one, which also covers
    # modules added later without another edit here.
    import src.database.session as db_session_module

    original = db_session_module.SessionLocal
    monkeypatch.setattr(db_session_module, "SessionLocal", factory)
    for module in list(sys.modules.values()):
        if module is None or getattr(module, "__name__", "").split(".")[0] != "src":
            continue
        if getattr(module, "SessionLocal", None) is original:
            monkeypatch.setattr(module, "SessionLocal", factory)

    try:
        yield V4Env(session_factory=factory)
    finally:
        engine.dispose()
