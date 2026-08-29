"""The scheduler: ordering, slack, and the SAFE/AT_RISK verdict (§6).

Pure arithmetic, no database. Every test states the schedule it is describing
in wall-clock Vietnam time, because a slack figure in seconds is unreadable
otherwise and a test nobody can read is a test nobody will fix.

The four rules §6 asks for, and where each is pinned:

1. lowest remaining slack first  -> `test_ordering_*`
2. tie on slack -> higher score  -> `test_a_score_tie_break_decides_a_slack_tie`
3. tie on score -> earlier submission -> `test_submission_time_breaks_a_score_tie`
4. still equal -> any order      -> `test_a_total_tie_is_still_deterministic`
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from src.dispatch.scheduler import (
    DEFAULT_SAFETY_BUFFER,
    WorkUnit,
    decide,
    order_units,
    place,
    simulate,
    sort_slack_seconds,
)
from src.dispatch.shift import VN_TZ, to_local
from src.models.enums import DispatchRiskState

BUFFER = DEFAULT_SAFETY_BUFFER


def local(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=VN_TZ).astimezone(UTC)


def stamp(moment: datetime) -> str:
    return to_local(moment).strftime("%Y-%m-%d %H:%M")


def unit(
    name: int,
    hours: float,
    *,
    deadline: str | None = None,
    score: int = 10,
    submitted: str = "2026-08-26T07:00",
    in_progress: bool = False,
    started: str | None = None,
) -> WorkUnit:
    return WorkUnit(
        key=UUID(int=name),
        ticket_ids=(UUID(int=1000 + name),),
        duration=timedelta(hours=hours),
        score=Decimal(score),
        submitted_at=local(submitted),
        deadline=local(deadline) if deadline else None,
        in_progress=in_progress,
        started_at=local(started) if started else None,
    )


NOW = local("2026-08-26T08:00")
TECH_A = UUID(int=901)
TECH_B = UUID(int=902)


# ---------------------------------------------------------------- simulation


def test_a_queue_is_simulated_end_to_end_through_the_working_window():
    slots = simulate([unit(1, 4, deadline="2026-08-26T13:00"), unit(2, 5, deadline="2026-08-26T18:00")], NOW, BUFFER)

    assert [stamp(slot.planned_start_at) for slot in slots] == ["2026-08-26 08:00", "2026-08-26 12:00"]
    assert [stamp(slot.planned_finish_at) for slot in slots] == ["2026-08-26 12:00", "2026-08-26 17:00"]
    # Slack is the gap between where the simulation lands a unit and what it
    # promised: one hour of headroom each here.
    assert [slot.slack_seconds for slot in slots] == [3600, 3600]


def test_an_in_progress_unit_is_pinned_first_and_keeps_its_real_start():
    """A live job is not re-ordered, and its start is history rather than a plan.

    Started at 16:00 yesterday, so two working hours (16:00-18:00) are already
    spent and two of its four remain.
    """
    live = unit(1, 4, deadline="2026-08-26T18:00", score=1, in_progress=True, started="2026-08-25T16:00")
    queued = unit(2, 2, deadline="2026-08-26T10:00", score=99)

    slots = simulate([queued, live], NOW, BUFFER)

    assert slots[0].unit.key == live.key
    assert stamp(slots[0].planned_start_at) == "2026-08-25 16:00"
    assert stamp(slots[0].planned_finish_at) == "2026-08-26 10:00"


def test_an_overrunning_in_progress_unit_never_gives_back_capacity():
    """A job past its estimate counts as zero remaining, not as negative time."""
    live = unit(1, 2, deadline="2026-08-26T12:00", in_progress=True, started="2026-08-25T08:00")
    slots = simulate([live, unit(2, 3, deadline="2026-08-26T18:00")], NOW, BUFFER)

    assert stamp(slots[1].planned_start_at) == "2026-08-26 08:00"
    assert stamp(slots[1].planned_finish_at) == "2026-08-26 11:00"


# ------------------------------------------------------------------ ordering


def test_ordering_puts_the_least_slack_first():
    tight = unit(1, 4, deadline="2026-08-26T12:00")
    roomy = unit(2, 4, deadline="2026-08-27T17:00")

    assert [item.key for item in order_units([roomy, tight], NOW, BUFFER)] == [tight.key, roomy.key]


def test_an_uncommitted_unit_sits_between_late_work_and_comfortable_work():
    """A brand-new unit has slack zero by construction (§6 item 1).

    So it goes behind anything already running late and ahead of anything with
    genuine headroom -- without inventing a priority constant to say so.
    """
    late = unit(1, 6, deadline="2026-08-26T10:00")
    fresh = unit(2, 3)
    comfortable = unit(3, 2, deadline="2026-08-28T17:00")

    assert sort_slack_seconds(fresh, NOW, BUFFER) == 0
    assert sort_slack_seconds(late, NOW, BUFFER) < 0
    assert sort_slack_seconds(comfortable, NOW, BUFFER) > 0
    ordered = order_units([comfortable, fresh, late], NOW, BUFFER)
    assert [item.key for item in ordered] == [late.key, fresh.key, comfortable.key]


def test_a_score_tie_break_decides_a_slack_tie():
    low = unit(1, 3, score=10)
    high = unit(2, 3, score=90)

    assert [item.key for item in order_units([low, high], NOW, BUFFER)] == [high.key, low.key]


def test_submission_time_breaks_a_score_tie():
    later = unit(1, 3, score=50, submitted="2026-08-26T07:30")
    earlier = unit(2, 3, score=50, submitted="2026-08-25T09:00")

    assert [item.key for item in order_units([later, earlier], NOW, BUFFER)] == [earlier.key, later.key]


def test_a_total_tie_is_still_deterministic():
    """§6 item 4 permits any order; the scheduler picks a stable one anyway.

    An acceptable-but-unstable order would make every schedule test flaky for
    no gain.
    """
    first = unit(1, 3, score=50, submitted="2026-08-26T07:00")
    second = unit(2, 3, score=50, submitted="2026-08-26T07:00")

    once = [item.key for item in order_units([first, second], NOW, BUFFER)]
    twice = [item.key for item in order_units([second, first], NOW, BUFFER)]
    assert once == twice


# ------------------------------------------------------------ SAFE / AT_RISK


def test_a_free_technician_makes_any_placement_safe():
    decision = decide(unit(9, 4, score=50), {TECH_A: []}, NOW, BUFFER)

    assert decision.risk_state is DispatchRiskState.SAFE
    assert stamp(decision.best.candidate.planned_start_at) == "2026-08-26 08:00"
    # The commitment written is the simulated finish plus one safety buffer,
    # which is what gives a fresh placement its headroom.
    assert stamp(decision.best.committed_deadline) == "2026-08-26 12:30"


def test_placement_is_safe_when_the_new_work_goes_behind_a_kept_promise():
    """A just-placed unit and a brand-new one tie at slack zero, so score decides.

    The existing job's deadline is exactly what a placement at 08:00 would have
    committed (4h + a 30-minute buffer), which is the normal state of freshly
    scheduled work -- and the case that must not report AT_RISK, or the agent
    would be called for every ticket that meets a non-empty queue.
    """
    existing = unit(1, 4, deadline="2026-08-26T12:30", score=90)
    decision = decide(unit(9, 3, score=10), {TECH_A: [existing]}, NOW, BUFFER)

    assert decision.risk_state is DispatchRiskState.SAFE
    assert decision.best.worst_committed_slack >= 0
    assert stamp(decision.best.candidate.planned_start_at) == "2026-08-26 12:00"


def test_placement_is_at_risk_when_it_pushes_a_commitment_late():
    """The new unit outranks the queued one on score and eats its deadline."""
    existing = unit(1, 5, deadline="2026-08-26T13:30", score=10)
    decision = decide(unit(9, 4, score=90), {TECH_A: [existing]}, NOW, BUFFER)

    assert decision.risk_state is DispatchRiskState.AT_RISK
    assert decision.best.worst_committed_slack < 0


def test_one_safe_technician_makes_the_whole_unit_safe():
    """§6: AT_RISK requires *every* valid assignment to create negative slack."""
    loaded = [unit(1, 5, deadline="2026-08-26T13:30", score=10)]
    decision = decide(unit(9, 4, score=90), {TECH_A: loaded, TECH_B: []}, NOW, BUFFER)

    assert decision.risk_state is DispatchRiskState.SAFE
    # And the safe technician is ranked ahead of the one who would break.
    assert decision.best.technician_id == TECH_B
    assert decision.placements[-1].technician_id == TECH_A


def test_an_uncommitted_neighbour_can_never_be_the_reason_for_at_risk():
    """A unit with no `planned_finish_at` made no promise there is to break.

    Assignments written before scheduling existed land in this state, and they
    must not make every later placement look risky.
    """
    legacy = unit(1, 8, score=1)
    assert legacy.deadline is None

    decision = decide(unit(9, 4, score=90), {TECH_A: [legacy]}, NOW, BUFFER)
    assert decision.risk_state is DispatchRiskState.SAFE
    assert decision.best.worst_committed_slack is None


def test_no_eligible_technician_is_infeasible_rather_than_at_risk():
    """§3: with nobody eligible there is nothing to choose between.

    The caller escalates to Building Management; it must not hand an empty
    candidate set to an agent and ask it to pick.
    """
    decision = decide(unit(9, 4), {}, NOW, BUFFER)

    assert decision.is_feasible is False
    assert decision.risk_state is None
    assert decision.placements == ()


def test_an_empty_queue_outranks_a_busy_one_that_merely_has_slack_left():
    """No commitment at all is unlimited headroom, not zero headroom.

    Both technicians can start the unit at 08:00 here, so the tie falls to
    remaining headroom -- and a technician holding nothing must win it.
    """
    busy = [unit(1, 3, deadline="2026-08-26T18:00", score=1)]
    decision = decide(unit(9, 2, score=50), {TECH_A: busy, TECH_B: []}, NOW, BUFFER)

    assert decision.best.technician_id == TECH_B
    assert decision.best.worst_committed_slack is None
    assert stamp(decision.best.candidate.planned_start_at) == "2026-08-26 08:00"


def test_a_report_that_has_waited_outranks_a_fresh_one():
    """§6 item 1 with submission time behind it.

    A unit that arrived yesterday afternoon has burned its provisional headroom
    and sorts ahead of one that arrived this morning, without either of them
    having a committed deadline yet.
    """
    waited = unit(1, 3, score=10, submitted="2026-08-25T14:00")
    fresh = unit(2, 3, score=10, submitted="2026-08-26T08:00")

    assert sort_slack_seconds(waited, NOW, BUFFER) < sort_slack_seconds(fresh, NOW, BUFFER)
    assert [item.key for item in order_units([fresh, waited], NOW, BUFFER)] == [waited.key, fresh.key]


def test_the_at_risk_ranking_head_is_the_least_negative_option():
    """This ordering is also the fallback when the agent cannot answer (§7)."""
    mild = [unit(1, 5, deadline="2026-08-26T16:00", score=10)]
    severe = [unit(2, 5, deadline="2026-08-26T11:00", score=10)]

    decision = decide(unit(9, 4, score=90), {TECH_A: severe, TECH_B: mild}, NOW, BUFFER)

    assert decision.risk_state is DispatchRiskState.AT_RISK
    assert decision.best.technician_id == TECH_B
    assert decision.best.worst_committed_slack > decision.placements[-1].worst_committed_slack


def test_placing_outside_the_shift_still_schedules_from_the_next_opening():
    """The scheduler itself has no shift gate -- §3's is in eligibility.

    What it must not do is invent an overnight start time.
    """
    decision = decide(unit(9, 3), {TECH_A: []}, local("2026-08-26T22:00"), BUFFER)

    assert stamp(decision.best.candidate.planned_start_at) == "2026-08-27 08:00"


def test_a_naive_timestamp_is_normalised_rather_than_raising():
    """SQLite hands back naive datetimes; the struct absorbs that at the edge."""
    naive = WorkUnit(
        key=UUID(int=1),
        ticket_ids=(UUID(int=2),),
        duration=timedelta(hours=2),
        score=Decimal(1),
        submitted_at=datetime(2026, 8, 26, 1, 0),
        deadline=datetime(2026, 8, 26, 9, 0),
    )
    assert naive.submitted_at.tzinfo is UTC
    assert naive.deadline.tzinfo is UTC


def test_placement_accumulates_so_two_units_cannot_share_one_slot():
    """The batch path books each placement before considering the next."""
    queue: list[WorkUnit] = []
    first = decide(unit(1, 4, score=50), {TECH_A: queue}, NOW, BUFFER)
    queue.append(unit(1, 4, score=50, deadline=stamp(first.best.committed_deadline).replace(" ", "T")))
    second = place(TECH_A, queue, unit(2, 3, score=50), NOW, BUFFER)

    assert stamp(first.best.candidate.planned_start_at) == "2026-08-26 08:00"
    assert stamp(second.candidate.planned_start_at) == "2026-08-26 12:00"
