"""`SERVICE_HOURS_RISK_V2` in the simulator: five bands, one of them manual.

Additive, and the first section is the proof: `OLD_APP` and `NEW_APP` are the
same two flows they always were, the engine still touches no database and calls
no model, and every V1 scenario produces exactly what it produced before. What
V2 adds is a wider priority scale, a different queue order, and a band that is
handed to a human instead of being scheduled.

The three things a V2 run must get right, and each is a section below:

* **P1-P4 order P4 first.** The scale inverted, so a V1 rank table applied to a
  V2 run would put P4 -- the most urgent band that still gets dispatched --
  last.
* **P5 is not scheduled at all.** No technician is chosen, the outcome is
  `REQUIRES_MANUAL_P5_REVIEW`, and its SLA status is `NOT_EVALUABLE`.
* **The SLA is measured at `work_started_at`,** on a clock that pauses overnight
  for P1-P4 and runs continuously for P5.
"""

from __future__ import annotations

import pytest

from src.domain.sla_clock import SlaPolicy
from src.models.enums import Priority
from src.simulation.engine import run_comparison, run_scenario
from src.simulation.models import (
    MANUAL_PRIORITY,
    POLICY_PRIORITIES,
    Outcome,
    Reason,
    Scenario,
    SlaStatus,
)
from src.simulation.policies import PRIORITY_RANK, PRIORITY_RANK_V2, new_app_policy, priority_rank
from src.simulation.validation import SimulationInputError, parse_priority
from tests.test_simulation.conftest import NEW_APP, outcomes_by_id, scenario, stamp, technician, ticket

V2 = SlaPolicy.SERVICE_HOURS_RISK_V2

#: `docs/risk_scoring_v2.md` §6.1.
V2_MINUTES = {
    Priority.P1: 1800,
    Priority.P2: 1200,
    Priority.P3: 600,
    Priority.P4: 180,
    Priority.P5: 5,
}


def v2_ticket(ticket_id: str, *, priority: Priority, **kwargs) -> object:
    kwargs.setdefault("sla_minutes", V2_MINUTES[priority])
    return ticket(ticket_id, priority=priority, **kwargs)


def v2_scenario(tickets, technicians, **kwargs):
    return scenario(tickets, technicians, sla_policy=V2, **kwargs)


# ---------------------------------------------------------------------------
# V1 is untouched.
# ---------------------------------------------------------------------------


def test_the_two_flows_are_still_the_only_two():
    assert [item.value for item in Scenario] == ["OLD_APP", "NEW_APP"]


def test_v1_still_refuses_a_v2_only_priority():
    """A V1 scenario carrying a P4 would run under a policy that has no deadline
    for P4 at all, and would silently produce a comparison number that means
    nothing."""
    for policy in (SlaPolicy.WALL_CLOCK_V1, SlaPolicy.SERVICE_HOURS_DRAFT_V1):
        assert POLICY_PRIORITIES[policy] == (Priority.P1, Priority.P2, Priority.P3)
        for value in ("P4", "P5"):
            with pytest.raises(SimulationInputError) as error:
                parse_priority(value, sla_policy=policy)
            assert error.value.field == "priority"


def test_v1_still_hands_p3_to_a_human():
    assert MANUAL_PRIORITY[SlaPolicy.WALL_CLOCK_V1] is Priority.P3
    assert MANUAL_PRIORITY[SlaPolicy.SERVICE_HOURS_DRAFT_V1] is Priority.P3


def test_the_v1_rank_table_is_unchanged():
    assert PRIORITY_RANK == {Priority.P3: 0, Priority.P2: 1, Priority.P1: 2}


def test_a_v1_run_still_reports_the_v1_manual_outcome():
    result = run_scenario(
        v1 := scenario(
            [ticket("T1", priority=Priority.P3, sla_minutes=5)],
            [technician()],
            sla_policy=SlaPolicy.SERVICE_HOURS_DRAFT_V1,
        ),
        new_app_policy(NEW_APP),
    )
    assert v1.sla_policy is SlaPolicy.SERVICE_HOURS_DRAFT_V1
    outcome = outcomes_by_id(result)["T1"]
    assert outcome.outcome is Outcome.REQUIRES_MANUAL_P3_REVIEW
    assert outcome.reason is Reason.P3_MANUAL_REVIEW


# ---------------------------------------------------------------------------
# V2 accepts five bands.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["P1", "P2", "P3", "P4", "P5"])
def test_v2_accepts_every_band(value):
    assert parse_priority(value, sla_policy=V2) is Priority(value)


def test_v2_still_refuses_a_bare_digit():
    with pytest.raises(SimulationInputError):
        parse_priority(3, sla_policy=V2)


# ---------------------------------------------------------------------------
# Queue order: P4 first, P1 last.
# ---------------------------------------------------------------------------


def test_the_v2_rank_table_runs_p4_to_p1():
    assert PRIORITY_RANK_V2 == {Priority.P4: 0, Priority.P3: 1, Priority.P2: 2, Priority.P1: 3}


def test_the_rank_table_is_chosen_by_policy():
    """The same band ranks differently under the two scales, which is exactly
    why this is a lookup and not a literal."""
    assert priority_rank(Priority.P3, SlaPolicy.WALL_CLOCK_V1) == 0
    assert priority_rank(Priority.P3, V2) == 1
    assert priority_rank(Priority.P4, V2) == 0


def test_an_unranked_band_sorts_last_rather_than_first():
    """A P5 in the queue would be a bug. It must not be a bug that jumps the
    queue."""
    assert priority_rank(Priority.P5, V2) > priority_rank(Priority.P1, V2)


def test_the_queue_takes_p4_before_p1_when_both_are_waiting():
    tickets = [
        v2_ticket("ROUTINE", priority=Priority.P1, created="2026-09-01T08:00", repair_minutes=60),
        v2_ticket("URGENT", priority=Priority.P4, created="2026-09-01T08:00", repair_minutes=60),
    ]
    result = run_scenario(v2_scenario(tickets, [technician()]), new_app_policy(NEW_APP))

    outcomes = outcomes_by_id(result)
    assert outcomes["URGENT"].work_started_at < outcomes["ROUTINE"].work_started_at


def test_the_full_band_order_is_p4_p3_p2_p1():
    tickets = [
        v2_ticket(priority.value, priority=priority, created="2026-09-01T08:00", repair_minutes=30)
        for priority in (Priority.P1, Priority.P2, Priority.P3, Priority.P4)
    ]
    result = run_scenario(v2_scenario(tickets, [technician()]), new_app_policy(NEW_APP))

    outcomes = outcomes_by_id(result)
    order = sorted(outcomes, key=lambda key: outcomes[key].work_started_at)
    assert order == ["P4", "P3", "P2", "P1"]


# ---------------------------------------------------------------------------
# P5 is never scheduled.
# ---------------------------------------------------------------------------


def _p5_outcome(**kwargs):
    tickets = [v2_ticket("EMERGENCY", priority=Priority.P5, **kwargs)]
    result = run_scenario(v2_scenario(tickets, [technician()]), new_app_policy(NEW_APP))
    return outcomes_by_id(result)["EMERGENCY"]


def test_a_p5_is_reported_as_requiring_manual_review():
    assert _p5_outcome().outcome is Outcome.REQUIRES_MANUAL_P5_REVIEW


def test_a_p5_carries_the_manual_review_reason():
    assert _p5_outcome().reason is Reason.P5_MANUAL_REVIEW


def test_a_p5_is_not_evaluable_against_the_sla():
    """It is waiting for a person, by design. Scoring it as on-time or late
    would put a number nobody earned in the compliance rate."""
    assert _p5_outcome().sla_status is SlaStatus.NOT_EVALUABLE


def test_no_technician_is_chosen_for_a_p5():
    outcome = _p5_outcome()
    assert outcome.assigned_technician_id is None
    assert outcome.work_started_at is None


def test_a_p5_does_not_take_a_slot_from_the_work_that_is_dispatched():
    """The emergency leaves the queue entirely rather than being scheduled and
    then skipped, so the P4 behind it starts at the top of the shift."""
    tickets = [
        v2_ticket("EMERGENCY", priority=Priority.P5, created="2026-09-01T08:00", repair_minutes=240),
        v2_ticket("URGENT", priority=Priority.P4, created="2026-09-01T08:00", repair_minutes=60),
    ]
    result = run_scenario(v2_scenario(tickets, [technician()]), new_app_policy(NEW_APP))

    outcomes = outcomes_by_id(result)
    assert outcomes["EMERGENCY"].assigned_technician_id is None
    # Four hours of emergency repair never entered the technician's day, so the
    # P4 starts at the top of the shift rather than after lunch.
    assert stamp(outcomes["URGENT"].work_started_at) == "2026-09-01 08:04"


def test_a_p5_still_costs_management_time():
    """Somebody opens the screen and deals with it. A manual outcome that cost
    nothing would make the emergency look free."""
    outcome = _p5_outcome()
    assert outcome.bql_minutes == NEW_APP.manual_review_minutes


def test_a_v2_p3_is_dispatched_rather_than_handed_to_a_human():
    """The band that used to be the emergency is now an ordinary ten-hour
    priority, and treating it as manual would take real work out of the queue."""
    tickets = [v2_ticket("T1", priority=Priority.P3, repair_minutes=60)]
    result = run_scenario(v2_scenario(tickets, [technician()]), new_app_policy(NEW_APP))

    outcome = outcomes_by_id(result)["T1"]
    assert outcome.outcome is Outcome.ASSIGNED
    assert outcome.assigned_technician_id == "KTV_01"


# ---------------------------------------------------------------------------
# The SLA clock.
# ---------------------------------------------------------------------------


def test_a_p4_clock_pauses_overnight():
    """Three service hours from 17:00 finish at 10:00 the next morning, not at
    20:00 with nobody in the building."""
    tickets = [v2_ticket("T1", priority=Priority.P4, created="2026-09-01T17:00", repair_minutes=30)]
    scenario_input = v2_scenario(tickets, [technician()])

    assert stamp(scenario_input.tickets[0].sla_due_at(V2)) == "2026-09-02 10:00"


def test_a_p5_clock_does_not_pause_overnight():
    tickets = [v2_ticket("T1", priority=Priority.P5, created="2026-09-01T23:00")]
    scenario_input = v2_scenario(tickets, [technician()])

    assert stamp(scenario_input.tickets[0].sla_due_at(V2)) == "2026-09-01 23:05"


def test_the_sla_is_judged_on_when_work_started_not_on_when_it_finished():
    """A repair begun before the deadline that runs long is `ON_TIME`. The
    promise is that somebody comes."""
    tickets = [
        v2_ticket("T1", priority=Priority.P4, created="2026-09-01T08:00", repair_minutes=600),
    ]
    result = run_scenario(v2_scenario(tickets, [technician()]), new_app_policy(NEW_APP))

    outcome = outcomes_by_id(result)["T1"]
    assert outcome.sla_status is SlaStatus.ON_TIME
    assert outcome.completed_at > outcome.sla_due_at


def test_a_start_after_the_deadline_is_late_however_fast_the_repair_is():
    tickets = [
        v2_ticket("BLOCKER", priority=Priority.P4, created="2026-09-01T08:00", repair_minutes=480),
        v2_ticket("T1", priority=Priority.P4, created="2026-09-01T08:00", repair_minutes=10),
    ]
    result = run_scenario(v2_scenario(tickets, [technician()]), new_app_policy(NEW_APP))

    outcome = outcomes_by_id(result)["T1"]
    assert outcome.sla_status is SlaStatus.LATE_STARTED


# ---------------------------------------------------------------------------
# The comparison still works, and still touches nothing.
# ---------------------------------------------------------------------------


def test_a_v2_comparison_runs_both_flows():
    tickets = [
        v2_ticket("T1", priority=Priority.P4, repair_minutes=60),
        v2_ticket("T2", priority=Priority.P5),
    ]
    result = run_comparison(v2_scenario(tickets, [technician()]))

    assert result.sla_policy is V2
    assert {result.old_app.scenario, result.new_app.scenario} == {Scenario.OLD_APP, Scenario.NEW_APP}


def test_both_flows_hand_a_p5_to_a_human():
    """The manual flow refuses it for the same reason the scheduled one does:
    an emergency is not dispatch work under either."""
    tickets = [v2_ticket("T1", priority=Priority.P5)]
    result = run_comparison(v2_scenario(tickets, [technician()]))

    for run in (result.old_app, result.new_app):
        assert outcomes_by_id(run)["T1"].outcome is Outcome.REQUIRES_MANUAL_P5_REVIEW


def test_score_total_is_still_the_tie_break():
    """`docs/risk_scoring_v2.md` §12: a V2 export may put `risk_score` in this
    field, and the ordering contract is unchanged by that."""
    tickets = [
        v2_ticket("LOW", priority=Priority.P4, created="2026-09-01T08:00", repair_minutes=60, score_total=61.0),
        v2_ticket("HIGH", priority=Priority.P4, created="2026-09-01T08:00", repair_minutes=60, score_total=78.5),
    ]
    result = run_scenario(v2_scenario(tickets, [technician()]), new_app_policy(NEW_APP))

    outcomes = outcomes_by_id(result)
    assert outcomes["HIGH"].work_started_at < outcomes["LOW"].work_started_at


def test_the_v2_path_still_reaches_no_database_and_no_model():
    """The guarantee `test_no_database_writes` makes for V1, restated for the
    path V2 adds: it is the same engine, and the new branch introduces no
    import of its own."""
    import src.simulation.engine as engine
    import src.simulation.models as models
    import src.simulation.policies as policies

    for module in (engine, models, policies):
        source = open(module.__file__, encoding="utf-8").read()
        assert "SessionLocal" not in source
        assert "sqlalchemy" not in source
        assert "AgentLLMClient" not in source
