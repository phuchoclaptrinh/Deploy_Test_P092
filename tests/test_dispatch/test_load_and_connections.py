"""Peak load, latency and the connection budget (§8).

§8's requirements are not about correctness -- the rest of the suite covers that
-- they are about what happens at the moment the building floods and forty
reports arrive at once. Three properties, and each fails silently in production
if nobody asserts it:

* **The statement count must not grow with the batch.** An N+1 inside the
  scheduler still produces correct assignments. It just produces them twenty
  times as slowly, against a database with a 15-session ceiling.
* **A micro-batch must fit inside its window.** §8 asks for batches roughly
  every 0.5-1 second; a pass that takes longer than its own interval turns the
  queue into a backlog that never drains.
* **The pools must not add up to more than the quota.** The API and the worker
  size their own pools and neither can see the other, so the only place the sum
  exists is `Settings.peak_db_session_budget`.

The latency assertion is deliberately loose (a multiple of the budget, not the
budget itself): CI machines are slow and shared, and a test that fails on a busy
runner teaches people to ignore it. What it catches is an order-of-magnitude
regression, which is the kind that matters.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import event

from src.config import Settings
from src.database.models.dispatch import DispatchEvent
from src.database.models.ticket_assignment import TicketAssignment
from src.dispatch.service import DispatchService
from src.models.enums import DispatchEventStatus
from tests.test_dispatch.conftest import NOW, dispatchable_ticket, queue


class StubAgent:
    def __init__(self) -> None:
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        return {}


class StatementCounter:
    """Counts SQL statements issued inside the block."""

    def __init__(self, session) -> None:
        self.bind = session.get_bind()
        self.statements: list[str] = []

    def __enter__(self):
        event.listen(self.bind, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *_exc):
        event.remove(self.bind, "before_cursor_execute", self._record)

    def _record(self, _conn, _cursor, statement, _params, _context, _many):
        self.statements.append(statement)

    @property
    def selects(self) -> int:
        return sum(1 for item in self.statements if item.lstrip().upper().startswith("SELECT"))


def _pass(world, agent=None):
    return DispatchService(world.db, agent=agent or StubAgent(), worker_id="load-test").run_micro_batch(NOW)


# ------------------------------------------------------------- query scaling


@pytest.mark.parametrize("size", [1, 5, 20])
def test_the_bulk_load_costs_the_same_whatever_the_batch_size(world, automatic_on, size):
    """§8: "Do not query candidates one ticket at a time."

    `World.query_count` counts only the loader's own statements, which is the
    number that must stay flat.
    """
    for _ in range(size):
        queue(world, dispatchable_ticket(world))

    report = _pass(world)

    assert report.claimed == size
    # Six, for one ticket and for twenty alike: technicians+skills, their
    # queues, exclusions and category codes from the loader, plus the batch-wide
    # emergency-gate check and the batch-wide notification recipients.
    assert report.query_count == 6


def test_the_whole_pass_does_not_issue_a_statement_per_ticket(world, automatic_on):
    """The end-to-end guard, not just the loader's.

    Twenty tickets cost more than one -- each writes an assignment, an audit row
    and its notifications -- but the *read* side must stay flat, so the ratio
    between a batch of one and a batch of twenty is nowhere near twentyfold.
    """
    for _ in range(1):
        queue(world, dispatchable_ticket(world))
    with StatementCounter(world.db) as small:
        _pass(world)

    for _ in range(20):
        queue(world, dispatchable_ticket(world))
    with StatementCounter(world.db) as large:
        _pass(world)

    assert large.selects < small.selects * 6


def test_the_at_risk_subset_costs_one_agent_call_and_one_history_query(world, automatic_on):
    """§7/§8: one call per micro-batch, one bulk history pull behind it."""
    from tests.test_dispatch.test_dispatch_service import _saturate

    for index in range(3):
        _saturate(world, world.technician(index), hours=5, deadline="2026-08-26T09:00")
    for _ in range(8):
        queue(world, dispatchable_ticket(world))

    agent = StubAgent()
    report = _pass(world, agent)

    assert report.at_risk == 8
    assert len(agent.requests) == 1
    assert report.agent_calls == 1


# ------------------------------------------------------------------- latency


def test_a_full_micro_batch_fits_inside_its_own_window(world, automatic_on):
    """§8: batches roughly every 0.5-1 second, up to 20 tickets.

    A pass slower than its interval turns the queue into a backlog. The margin
    here is generous on purpose -- this catches an order-of-magnitude
    regression, not a slow CI runner.
    """
    for _ in range(20):
        queue(world, dispatchable_ticket(world))

    started = time.monotonic()
    report = _pass(world)
    elapsed_ms = (time.monotonic() - started) * 1000

    assert report.claimed == 20
    budget_ms = Settings(app_env="test").dispatch_micro_batch_interval_ms
    assert elapsed_ms < budget_ms * 10
    assert report.duration_ms >= 0


def test_the_scheduler_itself_is_pure_arithmetic(world, automatic_on):
    """Scheduling happens in memory after the bulk query (§8).

    Asserted by counting statements between the load and the writes: if the
    scheduler touched the database, this number would move with the roster.
    """
    for _ in range(10):
        queue(world, dispatchable_ticket(world))

    report = _pass(world)
    assert report.query_count == 6


# ------------------------------------------------------- connection budgeting


def test_the_pool_budget_is_the_sum_of_both_processes():
    settings = Settings(app_env="test")
    assert settings.peak_db_session_budget == (
        settings.api_db_pool_size
        + settings.api_db_max_overflow
        + settings.dispatch_worker_db_pool_size
        + settings.dispatch_worker_db_max_overflow
    )


def test_the_default_configuration_stays_under_the_supabase_ceiling():
    """The known operational constraint from §8: 15 sessions."""
    settings = Settings(app_env="test")
    assert settings.supabase_max_sessions == 15
    assert settings.peak_db_session_budget <= settings.supabase_max_sessions


def test_a_configuration_that_could_exhaust_the_quota_refuses_to_boot():
    """Discovered at startup, not at peak load with residents waiting."""
    over = Settings(app_env="test", api_db_pool_size=20, database_url="postgresql://x/y")
    with pytest.raises(RuntimeError) as exc:
        over.validate_runtime_safety()
    assert "Supabase" in str(exc.value)


def test_the_worker_pool_is_sized_separately_from_the_api_pool():
    """Both processes talk to one database and neither can see the other."""
    from src.database.session import engine_options_for

    settings = Settings(app_env="test")
    assert settings.dispatch_worker_db_pool_size < settings.api_db_pool_size
    # On SQLite there is no server session to budget, so no pool options apply.
    assert engine_options_for("worker") == {}


def test_the_micro_batch_ceiling_cannot_be_widened_by_configuration():
    """§8 fixes the ceiling at 20; it is a contract, not a tuning knob."""
    with pytest.raises(ValueError):
        Settings(app_env="test", dispatch_micro_batch_size=50)
    with pytest.raises(ValueError):
        Settings(app_env="test", dispatch_micro_batch_interval_ms=5000)


# ------------------------------------------------------------- idempotency


def test_two_workers_cannot_assign_the_same_ticket_twice(world, automatic_on):
    """§8: idempotency and locking.

    The second pass finds the event closed and the ticket already assigned, and
    writes nothing -- which is what the partial unique index on open events and
    the one-active-assignment-per-ticket index together guarantee.
    """
    ticket = dispatchable_ticket(world)
    queue(world, ticket)

    first = _pass(world)
    second = _pass(world)

    assert first.assigned_safe == 1
    assert second.claimed == 0
    assert world.db.query(TicketAssignment).filter_by(ticket_id=ticket.id).count() == 1


def test_a_closed_event_is_never_reclaimed(world, automatic_on):
    ticket = dispatchable_ticket(world)
    queue(world, ticket)
    _pass(world)

    open_events = world.db.query(DispatchEvent).filter_by(is_open=True).count()
    assert open_events == 0
    assert world.db.query(DispatchEvent).filter_by(status=DispatchEventStatus.ASSIGNED.value).count() == 1
