"""`get_candidate_dispatch_history` -- the at-risk agent's one tool (§7).

Two things these tests protect, and the second is the important one:

* the aggregates §7 asks for are actually computed, per window, per category;
* **nothing else comes out.** The tool is the only channel between the database
  and the model, so a resident description or a phone number leaking into its
  payload would leak straight into a prompt. `test_the_payload_carries_no_
  resident_data` walks the serialized result and asserts the absence directly
  rather than trusting the column list.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.dispatch.agent.tool import get_candidate_dispatch_history, percentile
from src.dispatch.shift import VN_TZ
from src.models.enums import AssignmentStatus, Priority, TicketStatus
from tests.test_workflow.factories import build_world, make_assignment, make_ticket

NOW = datetime.fromisoformat("2026-08-26T10:00").replace(tzinfo=VN_TZ).astimezone(UTC)


def local(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=VN_TZ).astimezone(UTC)


def _completed(world, technician, *, category, started: str, completed: str, assigned: str | None = None):
    ticket = make_ticket(
        world,
        category=category,
        status=TicketStatus.COMPLETED,
        priority=Priority.P1,
    )
    assignment = make_assignment(
        world,
        ticket,
        technician,
        status=AssignmentStatus.COMPLETED,
        is_active=False,
        assigned_at=local(assigned or started),
    )
    assignment.started_at = local(started)
    assignment.completed_at = local(completed)
    world.db.commit()
    return assignment


def test_percentile_is_nearest_rank():
    """Small samples: three completed jobs, not a distribution to interpolate."""
    assert percentile([], 0.5) is None
    assert percentile([10], 0.8) == 10
    assert percentile([10, 20, 30, 40], 0.5) == 20
    assert percentile([10, 20, 30, 40], 0.8) == 40


def test_every_candidate_gets_every_window_even_with_no_history(db_session):
    """A brand-new technician and a failed lookup must not look the same."""
    world = build_world(db_session, technician_count=2)
    ids = [world.technician(0).user_id, world.technician(1).user_id]

    history = get_candidate_dispatch_history(db_session, ids, world.water.id, NOW)

    assert set(history) == set(ids)
    for windows in history.values():
        assert [window.window_days for window in windows] == [30, 60, 90]
        assert all(window.assigned_count == 0 for window in windows)


def test_handling_time_is_measured_in_working_seconds(db_session):
    """A job spanning a night is not a fourteen-hour job.

    16:00 to 11:00 next morning is two working hours plus three, not nineteen --
    and the P80 estimates it will be compared against are in working time too.
    """
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    _completed(world, technician, category=world.water, started="2026-08-25T16:00", completed="2026-08-26T11:00")

    history = get_candidate_dispatch_history(db_session, [technician.user_id], world.water.id, NOW)
    stats = next(item for item in history[technician.user_id][0].by_category if item.category_code == "WATER")

    assert stats.completed_count == 1
    assert stats.p50_working_seconds == 5 * 3600


def test_windows_narrow_as_they_shorten(db_session):
    """A 45-day-old job belongs to the 60- and 90-day windows, not the 30."""
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    old = NOW - timedelta(days=45)
    _completed(
        world,
        technician,
        category=world.water,
        started=old.astimezone(VN_TZ).strftime("%Y-%m-%dT%H:%M"),
        completed=(old + timedelta(hours=2)).astimezone(VN_TZ).strftime("%Y-%m-%dT%H:%M"),
    )

    windows = {w.window_days: w for w in get_candidate_dispatch_history(
        db_session, [technician.user_id], world.water.id, NOW
    )[technician.user_id]}

    assert windows[30].completed_count == 0
    assert windows[60].completed_count == 1
    assert windows[90].completed_count == 1


def test_start_performance_counts_against_the_planned_start(db_session):
    """Starting is the first positive action, so it is what the record measures.

    There is no acceptance step left to score, and no start *deadline* either --
    `planned_start_at` is the schedule the technician was given, and beating it
    or missing it is the honest thing to say about them.
    """
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)

    on_time = make_assignment(
        world, make_ticket(world, category=world.water, priority=Priority.P1), technician,
        assigned_at=local("2026-08-26T08:00"),
    )
    on_time.planned_start_at = local("2026-08-26T09:00")
    on_time.started_at = local("2026-08-26T08:10")

    late = make_assignment(
        world, make_ticket(world, category=world.water, priority=Priority.P1), technician,
        assigned_at=local("2026-08-25T08:00"),
    )
    late.planned_start_at = local("2026-08-25T09:00")
    late.started_at = local("2026-08-25T11:00")
    world.db.commit()

    window = get_candidate_dispatch_history(db_session, [technician.user_id], world.water.id, NOW)[
        technician.user_id
    ][0]

    assert window.started_count == 2
    assert window.started_on_time_count == 1
    # Ten minutes and three hours; the median of two is the lower rank.
    assert window.median_assignment_to_start_seconds == 600
    # The acceptance metrics are gone from the shape, not merely zeroed.
    assert not hasattr(window, "accepted_count")
    assert not hasattr(window, "median_acceptance_seconds")


def test_an_assignment_with_no_planned_start_is_not_counted_late(db_session):
    """No schedule was promised, so none was missed."""
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    row = make_assignment(
        world, make_ticket(world, category=world.water, priority=Priority.P1), technician,
        assigned_at=local("2026-08-26T08:00"),
    )
    row.planned_start_at = None
    row.started_at = local("2026-08-26T09:30")
    world.db.commit()

    window = get_candidate_dispatch_history(db_session, [technician.user_id], world.water.id, NOW)[
        technician.user_id
    ][0]
    assert window.started_on_time_count == 1


def test_the_wait_before_starting_is_measured_in_working_seconds(db_session):
    """A queue that spans the overnight gap is the normal case now.

    Assigned at 17:30, started at 08:10 the next morning: forty working minutes
    of waiting, not the fifteen hours a wall clock would report. Scoring the
    night as idleness would make every technician holding a queue look slow.
    """
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    row = make_assignment(
        world, make_ticket(world, category=world.water, priority=Priority.P1), technician,
        assigned_at=local("2026-08-25T17:30"),
    )
    row.started_at = local("2026-08-26T08:10")
    world.db.commit()

    window = get_candidate_dispatch_history(db_session, [technician.user_id], world.water.id, NOW)[
        technician.user_id
    ][0]
    # 17:30-18:00 plus 08:00-08:10.
    assert window.median_assignment_to_start_seconds == 40 * 60


def test_rejections_reassignments_and_unable_to_handle_are_counted_apart(db_session):
    """§7 asks for all three, and they mean different things about a technician."""
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    for end_reason, status in (
        ("TECHNICIAN_REJECTED", AssignmentStatus.REJECTED),
        ("COORDINATOR_REASSIGNED", AssignmentStatus.REASSIGNED),
        ("UNABLE_TO_HANDLE", AssignmentStatus.UNABLE_TO_HANDLE),
    ):
        make_assignment(
            world,
            make_ticket(world, category=world.water, priority=Priority.P1),
            technician,
            status=status,
            is_active=False,
            end_reason=end_reason,
            assigned_at=local("2026-08-26T08:00"),
        )

    window = get_candidate_dispatch_history(db_session, [technician.user_id], world.water.id, NOW)[
        technician.user_id
    ][0]

    assert window.rejected_count == 1
    assert window.reassigned_away_count == 1
    assert window.unable_to_handle_count == 1


def test_the_requested_category_always_appears_even_at_zero(db_session):
    """"Never worked this category" and "the tool did not look" are different."""
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    _completed(world, technician, category=world.water, started="2026-08-26T08:00", completed="2026-08-26T10:00")

    history = get_candidate_dispatch_history(db_session, [technician.user_id], world.elevator.id, NOW)
    codes = {item.category_code for item in history[technician.user_id][0].by_category}

    assert "ELEVATOR" in codes
    assert "WATER" in codes


def test_the_payload_carries_no_resident_data(db_session):
    """§7's privacy boundary, checked on the serialized output.

    Asserted against the JSON rather than against the column list, because the
    column list is what a future change would edit -- and this is the test that
    should fail when it does.
    """
    world = build_world(db_session, technician_count=1)
    technician = world.technician(0)
    resident = world.resident(0)
    resident.user.full_name = "Nguyễn Văn Bí Mật"
    secret = "Nhà tôi ở A-1000, gọi 0909123456"
    ticket = make_ticket(world, category=world.water, priority=Priority.P1, description=secret)
    world.db.commit()
    _completed(world, technician, category=world.water, started="2026-08-26T08:00", completed="2026-08-26T10:00")
    make_assignment(world, ticket, technician, assigned_at=local("2026-08-26T08:00"))

    history = get_candidate_dispatch_history(db_session, [technician.user_id], world.water.id, NOW)
    blob = "".join(window.model_dump_json() for window in history[technician.user_id])

    assert secret not in blob
    assert "0909123456" not in blob
    assert "Bí Mật" not in blob
    assert "A-1000" not in blob
    # The technician's own name is absent too: the agent is given opaque ids and
    # the backend maps them back for the manager UI.
    assert technician.user.full_name not in blob


def test_one_statement_serves_every_window_and_every_candidate(db_session):
    """§8: the query count must not grow with candidates or windows."""
    world = build_world(db_session, technician_count=3)
    ids = [world.technician(index).user_id for index in range(3)]
    for index in range(3):
        _completed(
            world, world.technician(index), category=world.water,
            started="2026-08-26T08:00", completed="2026-08-26T10:00",
        )

    statements: list[str] = []
    from sqlalchemy import event

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", record)
    try:
        get_candidate_dispatch_history(db_session, ids, world.water.id, NOW)
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", record)

    # One pull of the assignment facts, plus one lookup of the requested
    # category code. Three technicians times three windows is nine questions
    # answered by two statements.
    assert len(statements) == 2
