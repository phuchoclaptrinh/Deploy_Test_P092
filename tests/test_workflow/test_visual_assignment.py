"""Visual Assignment: the pool, the board, and one confirming action (§1).

Four rules these tests exist to hold:

* the pool is **everything Building Management may place**, which is broader
  than the automatic path -- a P3 emergency belongs here precisely because §2
  forbids it from the automatic one;
* a grouped cluster is **one draggable unit** and cannot be split, enforced by
  never offering its members separately rather than by validating afterwards;
* the board shows what is **wrong or risky** about every pairing *before*
  anyone drags anything;
* confirm is **all or nothing** -- and every §3 hard constraint rejects it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.technician import TechnicianSkill
from src.database.models.ticket_assignment import TicketAssignment
from src.dispatch.shift import VN_TZ
from src.models.api.errors import VISUAL_PLACEMENT_INVALID, VISUAL_UNIT_NOT_PLACEABLE, DomainError
from src.models.enums import (
    AnalysisRunStatus,
    AssignmentSource,
    ClassificationStatus,
    PlacementWarningCode,
    Priority,
    TicketStatus,
)
from src.services.visual_assignment_service import VisualAssignmentService
from tests.test_workflow.factories import build_world, make_assignment, make_ticket


def local(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=VN_TZ).astimezone(UTC)


IN_SHIFT = local("2026-08-26T09:00")
AFTER_HOURS = local("2026-08-26T21:00")
NO_ANALYSIS_RUN = object()


@pytest.fixture
def world(db_session):
    return build_world(db_session, resident_count=6, technician_count=3)


def add_analysis_run(world, ticket, *, grouping_status="NO_MATCH", run_number=1):
    run = AIAnalysisRun(
        ticket_id=ticket.id,
        run_number=run_number,
        status=AnalysisRunStatus.SUCCEEDED,
        grouping_status=grouping_status,
    )
    world.db.add(run)
    world.db.commit()
    return run


def placeable(world, *, category=None, priority=Priority.P2, grouping_status="NO_MATCH", **kwargs):
    ticket = make_ticket(
        world,
        category=category or world.water,
        status=TicketStatus.APPROVED,
        classification_status=ClassificationStatus.RESOLVED,
        priority=priority,
        **kwargs,
    )
    if grouping_status is not NO_ANALYSIS_RUN:
        add_analysis_run(world, ticket, grouping_status=grouping_status)
    return ticket


def board(world, *, now=IN_SHIFT):
    return VisualAssignmentService(world.db).board(now=now)


def preview_for(unit, technician):
    return next(item for item in unit.previews if item.technician_id == technician.user_id)


# ------------------------------------------------------------------- the pool


def test_the_pool_holds_unassigned_eligible_work(world):
    ticket = placeable(world)
    result = board(world)

    assert [unit.unit_id for unit in result.units] == [f"ticket:{ticket.id}"]
    assert result.units[0].member_count == 1
    # §5's internal estimate travels with the card so the board can size a day.
    assert result.units[0].p80_seconds == 4 * 3600


def test_a_p3_emergency_is_on_the_board_even_though_it_is_barred_from_automation(world):
    """§2 sends everything the automatic path refuses to Building Management.

    This board is where those land, so the one priority automation must never
    touch is exactly the one a manager must be able to place by hand.
    """
    ticket = placeable(world, priority=Priority.P3, grouping_status="NOT_ELIGIBLE")
    assert [unit.unit_id for unit in board(world).units] == [f"ticket:{ticket.id}"]


def test_an_already_assigned_ticket_leaves_the_pool(world):
    ticket = placeable(world)
    make_assignment(world, ticket, world.technician(0))

    assert board(world).units == []


def test_a_duplicate_or_unapproved_ticket_is_not_a_placement_decision(world):
    master = placeable(world)
    duplicate = placeable(world)
    duplicate.duplicate_of_ticket_id = master.id
    make_ticket(world, category=world.water, status=TicketStatus.NEW)
    world.db.commit()

    assert [unit.unit_id for unit in board(world).units] == [f"ticket:{master.id}"]


def test_a_ticket_waits_for_grouping_before_it_enters_the_board(world):
    """Grouping is a gate in front of the visual assignment pool."""
    placeable(world, grouping_status="PENDING")
    placeable(world, grouping_status="WAITING_DUPLICATE_DECISION")
    placeable(world, grouping_status="WAITING_P3_MANAGEMENT_REVIEW")
    placeable(world, grouping_status="BLOCKED")
    placeable(world, grouping_status=NO_ANALYSIS_RUN)

    assert board(world).units == []


def test_a_grouped_ticket_requires_an_open_case_before_it_enters_the_board(world):
    placeable(world, grouping_status="GROUPED")

    assert board(world).units == []


# ---------------------------------------------------------------- grouping


def _case_with(world, *tickets):
    now = datetime.now(UTC)
    case = IncidentCase(
        category_id=world.water.id,
        status="OPEN",
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
        density_value=len(tickets),
        sequence_no=1,
    )
    world.db.add(case)
    world.db.commit()
    for ticket in tickets:
        world.db.add(
            IncidentCaseMember(case_id=case.id, ticket_id=ticket.id, source_unit_id=ticket.source_unit_id)
        )
    world.db.commit()
    return case


def test_a_group_is_one_draggable_unit_and_its_members_are_never_offered_alone(world):
    """§1: grouped tickets must remain grouped as one work unit."""
    first = placeable(world, resident=world.resident(0), grouping_status="GROUPED")
    second = placeable(world, resident=world.resident(1), grouping_status="GROUPED")
    case = _case_with(world, first, second)

    units = board(world).units

    assert [unit.unit_id for unit in units] == [f"case:{case.id}"]
    assert units[0].unit_type == "GROUP"
    assert set(units[0].ticket_ids) == {first.id, second.id}
    # Two water tickets on one visit cost both P80s, not one.
    assert units[0].p80_seconds == 8 * 3600


def test_a_group_takes_its_most_urgent_members_priority(world):
    """A group is done when its hardest promise is kept, not its easiest."""
    low = placeable(world, resident=world.resident(0), priority=Priority.P1, grouping_status="GROUPED")
    high = placeable(world, resident=world.resident(1), priority=Priority.P3, grouping_status="GROUPED")
    _case_with(world, low, high)

    assert board(world).units[0].priority is Priority.P3


def test_confirming_a_group_assigns_every_member_to_one_technician(world):
    first = placeable(world, resident=world.resident(0), grouping_status="GROUPED")
    second = placeable(world, resident=world.resident(1), grouping_status="GROUPED")
    case = _case_with(world, first, second)
    technician = world.technician(0)

    result = VisualAssignmentService(world.db).confirm(
        world.coordinator.user_id, [(f"case:{case.id}", technician.user_id)], now=IN_SHIFT
    )

    assert result.assigned_unit_count == 1
    assert result.assigned_ticket_count == 2
    holders = {row.technician_id for row in world.db.query(TicketAssignment).all()}
    assert holders == {technician.user_id}


# ------------------------------------------------------------------ warnings


def test_the_board_shows_technician_workload_and_the_planned_day(world):
    placeable(world)
    busy = world.technician(0)
    held = make_ticket(world, category=world.water, priority=Priority.P2)
    assignment = make_assignment(world, held, busy)
    assignment.planned_finish_at = local("2026-08-26T15:00")
    world.db.commit()

    column = next(item for item in board(world).technicians if item.technician_id == busy.user_id)

    assert column.active_assignment_count == 1
    assert column.day_ends_at is not None
    assert column.planned_slots[0]["order"] == 0


def test_a_missing_skill_blocks_the_drop_rather_than_warning_about_it(world):
    """§3 is hard-enforced, so the board refuses instead of letting confirm fail."""
    unskilled = world.technician(0)
    world.db.query(TechnicianSkill).filter_by(technician_id=unskilled.user_id).delete()
    world.db.commit()
    placeable(world)

    unit = board(world).units[0]
    preview = preview_for(unit, unskilled)

    assert preview.blocked is True
    assert PlacementWarningCode.MISSING_SKILL in preview.warnings
    assert unskilled.user_id not in unit.eligible_technician_ids


def test_an_unavailable_technician_is_blocked(world):
    away = world.technician(1)
    away.is_available = False
    world.db.commit()
    placeable(world)

    preview = preview_for(board(world).units[0], away)
    assert preview.blocked is True
    assert PlacementWarningCode.TECHNICIAN_UNAVAILABLE in preview.warnings


def test_outside_the_shift_every_placement_is_blocked(world):
    placeable(world)
    result = board(world, now=AFTER_HOURS)

    assert result.within_working_shift is False
    assert all(preview.blocked for preview in result.units[0].previews)
    assert result.units[0].eligible_technician_ids == []


def test_schedule_risk_and_overload_are_shown_but_allowed(world):
    """Neither is on §3's list, so neither blocks a manager's judgement."""
    crowded = world.technician(0)
    for _ in range(2):
        held = make_ticket(world, category=world.water, priority=Priority.P2)
        assignment = make_assignment(world, held, crowded)
        assignment.planned_finish_at = local("2026-08-26T11:00")
        world.db.commit()
    placeable(world)

    preview = preview_for(board(world).units[0], crowded)

    assert preview.blocked is False
    assert {PlacementWarningCode.SCHEDULE_RISK, PlacementWarningCode.OVERLOADED} & set(preview.warnings)


def test_every_preview_carries_the_planned_window_it_would_produce(world):
    placeable(world)
    preview = preview_for(board(world).units[0], world.technician(0))

    assert preview.planned_start_at is not None
    assert preview.planned_finish_at is not None
    assert preview.planned_finish_at > preview.planned_start_at


# -------------------------------------------------------------- bulk confirm


def test_confirm_writes_every_placement_in_one_action(world):
    first, second = placeable(world), placeable(world)
    service = VisualAssignmentService(world.db)

    result = service.confirm(
        world.coordinator.user_id,
        [
            (f"ticket:{first.id}", world.technician(0).user_id),
            (f"ticket:{second.id}", world.technician(1).user_id),
        ],
        now=IN_SHIFT,
    )

    assert result.assigned_ticket_count == 2
    rows = world.db.query(TicketAssignment).all()
    assert {row.assignment_source for row in rows} == {AssignmentSource.COORDINATOR_VISUAL.value}
    # A human decided each of these, and the row names them.
    assert all(row.assigned_by_user_id == world.coordinator.user_id for row in rows)
    # §4's planned times are written here too, not only on the automatic path.
    assert all(row.planned_start_at is not None and row.planned_order is not None for row in rows)
    assert all(row.planned_order is not None for row in rows)


def test_one_invalid_placement_rejects_the_whole_board(world):
    """§1 asks for one transaction, so a partial write is not an outcome."""
    good, bad = placeable(world), placeable(world)
    unskilled = world.technician(2)
    world.db.query(TechnicianSkill).filter_by(technician_id=unskilled.user_id).delete()
    world.db.commit()

    with pytest.raises(DomainError) as exc:
        VisualAssignmentService(world.db).confirm(
            world.coordinator.user_id,
            [
                (f"ticket:{good.id}", world.technician(0).user_id),
                (f"ticket:{bad.id}", unskilled.user_id),
            ],
            now=IN_SHIFT,
        )

    assert exc.value.code == VISUAL_PLACEMENT_INVALID
    assert exc.value.status_code == 409
    # Nothing at all was written, including the placement that was fine.
    assert world.db.query(TicketAssignment).count() == 0


def test_the_rejection_names_which_placements_failed_and_why(world):
    """So the board can mark them rather than making anyone hunt."""
    ticket = placeable(world)
    unskilled = world.technician(0)
    world.db.query(TechnicianSkill).filter_by(technician_id=unskilled.user_id).delete()
    world.db.commit()

    with pytest.raises(DomainError) as exc:
        VisualAssignmentService(world.db).confirm(
            world.coordinator.user_id, [(f"ticket:{ticket.id}", unskilled.user_id)], now=IN_SHIFT
        )

    failures = exc.value.details["failures"]
    assert failures[0]["unit_id"] == f"ticket:{ticket.id}"
    assert PlacementWarningCode.MISSING_SKILL.value in failures[0]["codes"]


def test_confirming_outside_the_working_shift_is_refused(world):
    ticket = placeable(world)

    with pytest.raises(DomainError) as exc:
        VisualAssignmentService(world.db).confirm(
            world.coordinator.user_id, [(f"ticket:{ticket.id}", world.technician(0).user_id)], now=AFTER_HOURS
        )
    assert exc.value.code == VISUAL_PLACEMENT_INVALID


def test_a_ticket_someone_else_already_took_is_refused(world):
    ticket = placeable(world)
    make_assignment(world, ticket, world.technician(2))

    with pytest.raises(DomainError) as exc:
        VisualAssignmentService(world.db).confirm(
            world.coordinator.user_id, [(f"ticket:{ticket.id}", world.technician(0).user_id)], now=IN_SHIFT
        )
    assert "ACTIVE_ASSIGNMENT_EXISTS" in exc.value.details["failures"][0]["codes"]


def test_a_stale_board_cannot_confirm_a_ticket_still_waiting_for_grouping(world):
    ticket = placeable(world, grouping_status="PENDING")

    with pytest.raises(DomainError) as exc:
        VisualAssignmentService(world.db).confirm(
            world.coordinator.user_id, [(f"ticket:{ticket.id}", world.technician(0).user_id)], now=IN_SHIFT
        )

    assert "GROUPING_NOT_READY" in exc.value.details["failures"][0]["codes"]


def test_the_same_unit_twice_in_one_confirm_is_refused(world):
    ticket = placeable(world)

    with pytest.raises(DomainError) as exc:
        VisualAssignmentService(world.db).confirm(
            world.coordinator.user_id,
            [
                (f"ticket:{ticket.id}", world.technician(0).user_id),
                (f"ticket:{ticket.id}", world.technician(1).user_id),
            ],
            now=IN_SHIFT,
        )
    assert exc.value.code == VISUAL_PLACEMENT_INVALID


def test_a_unit_that_no_longer_exists_is_refused(world):
    from uuid import uuid4

    with pytest.raises(DomainError) as exc:
        VisualAssignmentService(world.db).confirm(
            world.coordinator.user_id, [(f"ticket:{uuid4()}", world.technician(0).user_id)], now=IN_SHIFT
        )
    assert exc.value.code == VISUAL_UNIT_NOT_PLACEABLE


def test_confirming_nothing_is_a_no_op_rather_than_an_error(world):
    result = VisualAssignmentService(world.db).confirm(world.coordinator.user_id, [], now=IN_SHIFT)
    assert result.assigned_ticket_count == 0


def test_a_confirmed_placement_is_not_booked_twice(world):
    """The assignment row is written before it is scheduled.

    So the queue it is scheduled against must exclude it. Loading it back as
    existing work would book its duration twice and hand the resident a start
    time one whole job too late -- a bug that produces perfectly plausible
    timestamps and is invisible without arithmetic.
    """
    ticket = placeable(world)
    technician = world.technician(0)

    VisualAssignmentService(world.db).confirm(
        world.coordinator.user_id, [(f"ticket:{ticket.id}", technician.user_id)], now=IN_SHIFT
    )

    row = world.db.query(TicketAssignment).filter_by(ticket_id=ticket.id).one()
    # A free technician at 09:00 starts a WATER job (4h P80) immediately, and
    # the commitment is the finish plus one 30-minute buffer.
    assert row.planned_start_at.replace(tzinfo=UTC) == IN_SHIFT
    assert row.planned_finish_at.replace(tzinfo=UTC) == local("2026-08-26T13:30")
    assert row.planned_order == 0


def test_a_second_placement_queues_behind_the_first(world):
    """Two units on one technician run in sequence, not in parallel."""
    first, second = placeable(world), placeable(world)
    technician = world.technician(0)

    VisualAssignmentService(world.db).confirm(
        world.coordinator.user_id,
        [(f"ticket:{first.id}", technician.user_id), (f"ticket:{second.id}", technician.user_id)],
        now=IN_SHIFT,
    )

    rows = {row.ticket_id: row for row in world.db.query(TicketAssignment).all()}
    assert rows[first.id].planned_start_at.replace(tzinfo=UTC) == IN_SHIFT
    # 09:00 + 4h = 13:00, so the second starts there rather than also at 09:00.
    assert rows[second.id].planned_start_at.replace(tzinfo=UTC) == local("2026-08-26T13:00")
