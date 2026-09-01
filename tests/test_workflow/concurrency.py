"""A database harness that can actually hold two transactions at once.

The rest of the suite runs on `:memory:` SQLite behind a `StaticPool`, which is
exactly right for everything except a race: `StaticPool` hands every session the
*same physical connection*, so two "concurrent" sessions share one transaction.
The loser's `rollback()` then discards the winner's uncommitted work, and the
winner returns an object whose status was never committed. That is not a weaker
proof of the concurrency contract -- it is a proof of nothing, and it fails
intermittently in a way that reads like a product bug.

So the race tests get their own harness, with one rule: **one physical
connection per session.**

Two backends, in order of preference:

* **PostgreSQL**, when `V4_E2E_DATABASE_URL` is set -- the same convention
  `pytest.ini` documents for the other end-to-end tests. This is the real
  proof: `SELECT ... FOR UPDATE` genuinely blocks, and the partial unique index
  is enforced the way production enforces it.
* **File-backed SQLite** otherwise, with `NullPool` so each session opens its
  own connection to the file, WAL so a reader does not block the writer, and a
  busy timeout so the loser waits for the winner's commit instead of failing
  instantly on a lock it would have got a millisecond later.

`with_for_update` is a no-op on SQLite, so the SQLite run proves less than the
PostgreSQL run. It still proves the thing these tests exist for -- that exactly
one of two competing calls wins and the loser leaves nothing behind -- because
the queue-head rule and the partial unique index are both enforced there too.
Each test says which guarantee it is leaning on.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from src.database.base import Base
from src.database.models.ticket_assignment import TicketAssignment
from src.models.api.errors import DomainError
from src.models.enums import AssignmentStatus

POSTGRES_URL_ENV = "V4_E2E_DATABASE_URL"


def postgres_url() -> str | None:
    """The disposable PostgreSQL URL, spelled for the driver this project has.

    `psycopg2` is not installed; the deployment runs `psycopg` v3. SQLAlchemy
    defaults a bare `postgresql://` to psycopg2, so the same rewrite
    `alembic/env.py` applies is applied here -- otherwise setting the variable
    fails with a confusing ImportError instead of running the tests.
    """
    url = os.environ.get(POSTGRES_URL_ENV, "").strip()
    if not url:
        return None
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


@dataclass(frozen=True)
class Harness:
    """A session factory whose sessions do not share a connection."""

    factory: Callable[[], Session]
    backend: str

    @property
    def is_postgres(self) -> bool:
        return self.backend == "postgresql"


def _sqlite_engine(path: str):
    engine = create_engine(
        f"sqlite+pysqlite:///{path}",
        # One connection per session. This is the whole point of the module.
        poolclass=NullPool,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        # WAL: a reader never blocks the writer, so the loser reaches the write
        # and is refused there rather than being turned away at the door.
        cursor.execute("PRAGMA journal_mode=WAL")
        # Wait for the winner's commit instead of failing on a lock that is
        # about to be released; without this the loser fails for the wrong
        # reason and the test proves nothing about the constraint.
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest.fixture
def concurrent_db(tmp_path) -> Harness:
    """A schema two threads can genuinely contend on, torn down afterwards."""
    url = postgres_url()
    if url:
        # Each run gets its own schema on the disposable database, so two runs
        # -- or a run beside another suite -- never share tables.
        schema = f"race_{uuid.uuid4().hex[:12]}"
        admin = create_engine(url, poolclass=NullPool)
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, poolclass=NullPool)

        # `SET search_path` as a statement, not as an `options` startup
        # parameter: a connection pooler in front of PostgreSQL -- Supabase's,
        # for one -- drops startup parameters, and the tests would then all
        # share `public` and collide on the seeded catalog instead of failing
        # visibly.
        @event.listens_for(engine, "connect")
        def _use_schema(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute(f'SET search_path TO "{schema}"')
            cursor.close()

        Base.metadata.create_all(engine)
        try:
            yield Harness(sessionmaker(bind=engine, expire_on_commit=False), "postgresql")
        finally:
            engine.dispose()
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            admin.dispose()
        return

    engine = _sqlite_engine((tmp_path / "race.sqlite").as_posix())
    Base.metadata.create_all(engine)
    try:
        yield Harness(sessionmaker(bind=engine, expire_on_commit=False), "sqlite")
    finally:
        engine.dispose()


@dataclass
class Outcome:
    """What one racing call returned or raised, plus which thread it was."""

    label: str
    value: Any = None
    error: BaseException | None = None

    @property
    def won(self) -> bool:
        return self.error is None and getattr(self.value, "status", None) is AssignmentStatus.IN_PROGRESS

    def describe(self) -> str:
        """One line a failing assertion can be read from without a debugger.

        A `DomainError` is identified by its code; anything else carries its
        message, because the interesting failures here are the ones nobody
        anticipated -- a deadlock, a lock timeout -- and SQLAlchemy's own
        `.code` is a docs shorthand like `e3q8` that says nothing about what
        actually happened.
        """
        if self.error is None:
            return f"{self.label}: ok status={getattr(self.value, 'status', self.value)}"
        code = getattr(self.error, "code", None)
        if isinstance(self.error, DomainError):
            return f"{self.label}: DomainError({code})"
        detail = " ".join(str(getattr(self.error, "orig", self.error)).split())[:160]
        return f"{self.label}: {type(self.error).__name__}({detail})"


def race(harness: Harness, calls: list[tuple[str, Callable[[Session], Any]]]) -> list[Outcome]:
    """Run every call on its own session, released from a barrier together.

    The barrier is what makes this a race rather than two sequential calls that
    happen to be on threads: every caller has its session open and is one line
    away from the contended write before any of them proceeds.
    """
    outcomes: list[Outcome] = []
    lock = threading.Lock()
    barrier = threading.Barrier(len(calls))

    def run(label: str, work: Callable[[Session], Any]) -> None:
        session = harness.factory()
        outcome = Outcome(label)
        try:
            barrier.wait(timeout=30)
            outcome.value = work(session)
        except BaseException as exc:  # noqa: BLE001 - the exception *is* the result
            outcome.error = exc
        finally:
            session.close()
            with lock:
                outcomes.append(outcome)

    threads = [threading.Thread(target=run, args=item, name=item[0]) for item in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "a racing call never returned"
    assert len(outcomes) == len(calls)
    return outcomes


def live_assignments(harness: Harness, technician_id) -> list[TicketAssignment]:
    """Every assignment the database currently considers live for one technician."""
    session = harness.factory()
    try:
        return list(
            session.scalars(
                select(TicketAssignment).where(
                    TicketAssignment.technician_id == technician_id,
                    TicketAssignment.status == AssignmentStatus.IN_PROGRESS,
                    TicketAssignment.is_active.is_(True),
                )
            )
        )
    finally:
        session.close()


__all__ = ["Harness", "Outcome", "concurrent_db", "live_assignments", "postgres_url", "race"]
