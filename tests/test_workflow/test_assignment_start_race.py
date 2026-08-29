"""Two concurrent `/start` calls, and the guarantee that exactly one wins.

§3 says a technician holds at most one IN_PROGRESS assignment. Since the
acceptance step was removed, `/start` is the *only* way into that state, which
makes this the one place the rule can be broken -- and it cannot be defended by
the service check alone. `_assert_no_other_in_progress` runs its query inside a
transaction that cannot see the other transaction's uncommitted row, so two
simultaneous calls both pass it. Three things stand behind that check, and each
test below names the one it is exercising:

1. **The queue-head rule.** Both callers re-simulate the technician's queue
   from the same committed data, so both compute the same head. Only one
   assignment can be `planned_order == 0`, so the loser is refused before it
   writes anything.
2. **The row lock.** `SELECT ... FOR UPDATE` on the assignment serialises two
   calls naming the *same* assignment; the second reads the row after the
   first committed and finds it already IN_PROGRESS.
3. **The partial unique index**
   `uq_ticket_assignments_one_in_progress_per_technician`. The last line of
   defence, and the only one that survives a caller finding a way past the
   first two. `AssignmentService.start` translates its violation into the same
   readable error the service check would have produced.

These run on the `concurrent_db` harness rather than the suite's shared
in-memory session -- see `tests/test_workflow/concurrency.py` for why that
distinction is load-bearing rather than fussy.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from src.database.models.ticket_assignment import TicketAssignment
from src.dispatch.planning import reindex_technicians
from src.models.api.errors import DomainError
from src.models.enums import AssignmentStatus, ClassificationStatus, Priority, TicketStatus
from src.services.assignment_service import AssignmentService, _is_one_in_progress_violation

# `concurrent_db` is a fixture; `tests/test_workflow/conftest.py` registers it.
from tests.test_workflow.concurrency import live_assignments, race
from tests.test_workflow.factories import build_world, make_assignment, make_ticket

#: What a refused start is allowed to look like. All three are the same fact to
#: the technician -- "not this one, not now" -- and which one they get depends
#: on how far into the transaction the loser got before it was stopped.
EXPECTED_LOSS_CODES = {"ASSIGNMENT_NOT_AT_QUEUE_HEAD", "TECHNICIAN_NOT_ELIGIBLE"}


def _world_with_queue(harness, count: int = 2):
    """One technician holding `count` scheduled, startable assignments."""
    session = harness.factory()
    try:
        world = build_world(session, technician_count=1, resident_count=max(count, 2))
        technician = world.technician(0)
        now = datetime.now(UTC)
        rows = []
        for index in range(count):
            ticket = make_ticket(
                world,
                resident=world.resident(index),
                category=world.water,
                status=TicketStatus.APPROVED,
                classification_status=ClassificationStatus.RESOLVED,
                priority=Priority.P2,
                created_at=now - timedelta(hours=count - index),
            )
            rows.append(make_assignment(world, ticket, technician, assigned_at=now))
        # The scheduler decides the order, exactly as it does in production.
        reindex_technicians(session, {technician.user_id}, now)
        session.commit()
        ordered = sorted(rows, key=lambda row: row.planned_order)
        return technician.user_id, [row.id for row in ordered], [row.ticket_id for row in ordered]
    finally:
        session.close()


def _assignment(harness, assignment_id) -> TicketAssignment:
    session = harness.factory()
    try:
        return session.get(TicketAssignment, assignment_id)
    finally:
        session.close()


def _ticket_status(harness, ticket_id):
    from src.database.models.ticket import Ticket

    session = harness.factory()
    try:
        return session.scalar(select(Ticket.status).where(Ticket.id == ticket_id))
    finally:
        session.close()


def _start(technician_id, assignment_id):
    return lambda session: AssignmentService(session).start(technician_id, assignment_id)


def _assert_clean_loss(outcome) -> None:
    """A refused start must be refused *recognisably*, not by an internal error."""
    error = outcome.error
    assert error is not None
    if isinstance(error, DomainError):
        assert error.code in EXPECTED_LOSS_CODES, f"unexpected refusal: {outcome.describe()}"
        assert error.status_code == 409
    else:
        # An IntegrityError reaching the caller means `start` failed to
        # translate the index violation -- allowed only if it is some *other*
        # constraint, which would itself be a bug worth seeing.
        raise AssertionError(f"a losing start raised an untranslated error: {outcome.describe()}")


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_two_concurrent_starts_on_competing_work_produce_exactly_one_winner(concurrent_db):
    """Two sessions, two valid-looking jobs, one technician: exactly one wins.

    Not "at most one". Both calls are otherwise valid, so a run in which nobody
    starts working is as much a failure as one in which two people do -- it
    would mean a technician tapped "Bắt đầu xử lý", got an error, and no job
    began.
    """
    technician_id, assignment_ids, ticket_ids = _world_with_queue(concurrent_db, count=2)

    outcomes = race(
        concurrent_db,
        [
            ("head", _start(technician_id, assignment_ids[0])),
            ("next", _start(technician_id, assignment_ids[1])),
        ],
    )
    described = [outcome.describe() for outcome in outcomes]

    winners = [outcome for outcome in outcomes if outcome.won]
    losers = [outcome for outcome in outcomes if not outcome.won]
    assert len(winners) == 1, f"expected exactly one winner, got {described}"
    assert len(losers) == 1, f"expected exactly one loser, got {described}"
    _assert_clean_loss(losers[0])

    # The database agrees with the callers.
    live = live_assignments(concurrent_db, technician_id)
    assert len(live) == 1, f"{len(live)} live jobs after the race; outcomes were {described}"
    assert live[0].id == winners[0].value.id


def test_the_loser_leaves_nothing_half_written(concurrent_db):
    """A refused start rolls back its assignment *and* its ticket.

    `start` mutates four things before it can fail: the assignment, the ticket,
    the ticket's status history and the resident notification. A partial commit
    would tell a resident work had begun on a job nobody started.
    """
    technician_id, assignment_ids, ticket_ids = _world_with_queue(concurrent_db, count=2)

    outcomes = race(
        concurrent_db,
        [
            ("head", _start(technician_id, assignment_ids[0])),
            ("next", _start(technician_id, assignment_ids[1])),
        ],
    )
    winner = next(outcome for outcome in outcomes if outcome.won)
    won_id = winner.value.id
    lost_id = next(item for item in assignment_ids if item != won_id)
    lost_ticket = ticket_ids[assignment_ids.index(lost_id)]

    lost = _assignment(concurrent_db, lost_id)
    assert lost.status is AssignmentStatus.ASSIGNED
    assert lost.started_at is None
    assert lost.is_active is True
    assert _ticket_status(concurrent_db, lost_ticket) is TicketStatus.APPROVED

    session = concurrent_db.factory()
    try:
        from src.database.models.notification import Notification
        from src.database.models.ticket_status_history import TicketStatusHistory

        started_rows = list(
            session.scalars(
                select(TicketStatusHistory).where(
                    TicketStatusHistory.ticket_id == lost_ticket,
                    TicketStatusHistory.to_status == TicketStatus.IN_PROGRESS,
                )
            )
        )
        assert started_rows == [], "the losing transaction left a timeline event behind"
        notices = list(
            session.scalars(
                select(Notification).where(
                    Notification.ticket_id == lost_ticket,
                    Notification.notification_type == "TICKET_STARTED",
                )
            )
        )
        assert notices == [], "the losing transaction told the resident work had begun"
    finally:
        session.close()


def test_two_concurrent_starts_on_the_same_assignment_produce_exactly_one_winner(concurrent_db):
    """The double-submit race: one job, two taps, two sessions.

    This is the case the queue-head rule cannot separate -- both callers name
    the same assignment, so both are the head. What separates them is the row
    lock: the second `SELECT ... FOR UPDATE` returns only after the first
    commits, and by then the row is IN_PROGRESS, which is not a state `start`
    accepts as a source.
    """
    if not concurrent_db.is_postgres:
        pytest.skip(
            "needs a real row lock: SQLAlchemy compiles FOR UPDATE away on SQLite, "
            f"so set {'V4_E2E_DATABASE_URL'} to run this one"
        )
    technician_id, assignment_ids, ticket_ids = _world_with_queue(concurrent_db, count=1)
    head = assignment_ids[0]

    outcomes = race(
        concurrent_db,
        [("tap-1", _start(technician_id, head)), ("tap-2", _start(technician_id, head))],
    )
    described = [outcome.describe() for outcome in outcomes]

    assert len([outcome for outcome in outcomes if outcome.won]) == 1, f"outcomes were {described}"
    loser = next(outcome for outcome in outcomes if not outcome.won)
    assert isinstance(loser.error, DomainError), f"outcomes were {described}"
    # The row is no longer ASSIGNED by the time the loser reads it.
    assert loser.error.code == "INVALID_STATUS_TRANSITION", f"outcomes were {described}"

    assert len(live_assignments(concurrent_db, technician_id)) == 1
    assert _ticket_status(concurrent_db, ticket_ids[0]) is TicketStatus.IN_PROGRESS


def test_racing_starts_never_deadlock(concurrent_db):
    """The loser is refused, not killed by the database.

    Found by running the race on PostgreSQL rather than by reading the code.
    `start` renumbers *every* assignment the technician holds, so before the
    queue was locked in id order each caller held its own row and then reached
    for the other's -- PostgreSQL broke the cycle by aborting one transaction,
    and the technician got a 500 where a 409 was owed. Nobody won that race.

    Asserted as "no caller failed with a database-level error", which is the
    shape the deadlock took and the shape a lock timeout would take too.
    """
    technician_id, assignment_ids, _tickets = _world_with_queue(concurrent_db, count=3)

    outcomes = race(
        concurrent_db,
        [(f"caller-{index}", _start(technician_id, item)) for index, item in enumerate(assignment_ids)],
    )
    described = [outcome.describe() for outcome in outcomes]

    for outcome in outcomes:
        if outcome.error is not None:
            assert isinstance(outcome.error, DomainError), f"a caller hit the database: {described}"
    assert len([outcome for outcome in outcomes if outcome.won]) == 1, described
    assert len(live_assignments(concurrent_db, technician_id)) == 1


def test_the_queue_is_locked_in_a_single_deterministic_order(concurrent_db):
    """The mechanism behind the test above, asserted on the SQL it emits.

    A future edit that dropped the ordered lock would still pass every
    single-threaded test, and the deadlock would come back only under load. So
    this watches the statements `start` actually issues: the whole queue is
    taken, ordered by id, before the first row of it is written.
    """
    statements: list[str] = []
    technician_id, assignment_ids, _tickets = _world_with_queue(concurrent_db, count=3)
    session = concurrent_db.factory()

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def _record(_conn, _cursor, statement, *_args):
        statements.append(" ".join(statement.split()))

    try:
        AssignmentService(session).start(technician_id, assignment_ids[0])
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", _record)
        session.close()

    first_write = next(
        (index for index, item in enumerate(statements) if item.upper().startswith("UPDATE TICKET_ASSIGNMENTS")),
        None,
    )
    assert first_write is not None, "start wrote nothing"

    ordered_lock = [
        index
        for index, item in enumerate(statements[:first_write])
        if "FROM ticket_assignments" in item and "ORDER BY ticket_assignments.id" in item
    ]
    assert ordered_lock, f"no ordered lock before the first write; saw {statements[:first_write]}"
    if concurrent_db.is_postgres:
        # SQLAlchemy compiles FOR UPDATE away on SQLite, so the clause itself
        # can only be asserted where it means something.
        assert any("FOR UPDATE" in statements[index] for index in ordered_lock), statements[: first_write + 1]


# ---------------------------------------------------------------------------
# The last line of defence, on its own
# ---------------------------------------------------------------------------


def test_the_database_refuses_a_second_live_job_for_one_technician(concurrent_db):
    """The index, tested directly rather than through a race that may not reach it.

    The two tests above are satisfied by the queue-head rule and the row lock,
    which is the correct outcome but means neither of them proves the index is
    still there. A migration that dropped it -- `9f0a1b2c3d4e` rebuilds it
    around the enum swap -- would leave both of them passing.
    """
    technician_id, assignment_ids, _tickets = _world_with_queue(concurrent_db, count=2)
    session = concurrent_db.factory()
    try:
        first = session.get(TicketAssignment, assignment_ids[0])
        first.status = AssignmentStatus.IN_PROGRESS
        first.started_at = datetime.now(UTC)
        session.commit()

        second = session.get(TicketAssignment, assignment_ids[1])
        second.status = AssignmentStatus.IN_PROGRESS
        second.started_at = datetime.now(UTC)
        with pytest.raises(IntegrityError) as raised:
            session.commit()
        # PostgreSQL names the index; SQLite names the column it is on. The
        # service translates both, and this asserts against the same helper so
        # the two cannot drift apart.
        assert _is_one_in_progress_violation(raised.value), str(raised.value.orig)
        session.rollback()
    finally:
        session.close()

    assert len(live_assignments(concurrent_db, technician_id)) == 1


def test_the_index_violation_is_reported_as_a_readable_error(concurrent_db):
    """A technician reads one explanation, not a driver message.

    Reached by driving `start` at an assignment whose technician already holds a
    live job written *outside* this session -- which is what the losing side of
    a genuine race sees. The service check catches this one first; the point of
    the test is that when it does not, the translation exists and the
    transaction is still rolled back.
    """
    technician_id, assignment_ids, _tickets = _world_with_queue(concurrent_db, count=2)
    other = concurrent_db.factory()
    session = concurrent_db.factory()
    try:
        held = other.get(TicketAssignment, assignment_ids[0])
        held.status = AssignmentStatus.IN_PROGRESS
        held.started_at = datetime.now(UTC)
        other.commit()

        with pytest.raises(DomainError) as raised:
            AssignmentService(session).start(technician_id, assignment_ids[1])
        assert raised.value.code == "TECHNICIAN_NOT_ELIGIBLE"
        assert raised.value.status_code == 409
        assert "công việc khác" in raised.value.message
        # And the translation recognises this constraint without swallowing its
        # sibling, which fires on an entirely different mistake.
        assert _is_one_in_progress_violation(
            IntegrityError("stmt", {}, Exception("uq_ticket_assignments_one_in_progress_per_technician"))
        )
        assert _is_one_in_progress_violation(
            IntegrityError("stmt", {}, Exception("UNIQUE constraint failed: ticket_assignments.technician_id"))
        )
        assert not _is_one_in_progress_violation(
            IntegrityError("stmt", {}, Exception("uq_ticket_assignments_one_active_per_ticket"))
        )
        assert not _is_one_in_progress_violation(
            IntegrityError("stmt", {}, Exception("UNIQUE constraint failed: ticket_assignments.ticket_id"))
        )
    finally:
        session.close()
        other.close()

    assert len(live_assignments(concurrent_db, technician_id)) == 1


def test_the_harness_really_gives_each_session_its_own_connection(concurrent_db):
    """Guards the guard.

    The bug this whole module exists to fix was a harness that shared one
    connection between "concurrent" sessions, so the loser's rollback discarded
    the winner's work. If that ever comes back, every test above starts passing
    for the wrong reason -- so the property is asserted directly.
    """
    left = concurrent_db.factory()
    right = concurrent_db.factory()
    try:
        assert left.connection().connection is not right.connection().connection
    finally:
        left.close()
        right.close()
