"""The automatic path, end to end (§2, §6, §7, §8).

Resident submits -> classified -> eligible -> not duplicate -> not P3 -> skip
grouping -> scheduler -> SAFE assigned, AT_RISK to the agent -> Building
Management told.

These run against SQLite through the same service the worker calls, so what is
being tested is the real transaction shape rather than a rehearsal of it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.database.models.dispatch import AtRiskDecision, DispatchEvent
from src.database.models.notification import Notification
from src.database.models.ticket_assignment import TicketAssignment
from src.dispatch.agent.schemas import AtRiskDecisionError
from src.dispatch.agent.service import AtRiskOutcome
from src.dispatch.service import DispatchService
from src.models.enums import (
    AssignmentSource,
    ClassificationStatus,
    DispatchDecisionSource,
    DispatchEscalationReason,
    DispatchEventStatus,
    DispatchRiskState,
    Priority,
)
from tests.test_dispatch.conftest import NOW, dispatchable_ticket, local, queue
from tests.test_workflow.factories import make_assignment, make_ticket


class StubAgent:
    """Stands in for the model. `picks` is keyed by dispatch-event id."""

    def __init__(self, picks=None, error: Exception | None = None) -> None:
        self.picks = picks or {}
        self.error = error
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.picks


def run(world, *, agent=None, now=NOW):
    return DispatchService(world.db, agent=agent or StubAgent(), worker_id="test").run_micro_batch(now)


# ------------------------------------------------------------------- enqueue


def test_an_eligible_ticket_produces_one_durable_event(world, automatic_on):
    ticket = dispatchable_ticket(world)
    event = queue(world, ticket)

    assert event is not None
    assert event.status == DispatchEventStatus.PENDING.value
    assert event.is_open is True
    assert event.priority == Priority.P2.value


def test_enqueue_is_idempotent(world, automatic_on):
    """§8: two workers racing produce one event, not two dispatch attempts."""
    ticket = dispatchable_ticket(world)
    first = queue(world, ticket)
    second = queue(world, ticket)

    assert first.id == second.id
    assert world.db.query(DispatchEvent).count() == 1


def test_a_p3_emergency_never_enters_the_automatic_workflow(world, automatic_on):
    """§2 and §3 both say so, and the table refuses to carry it either way."""
    assert queue(world, dispatchable_ticket(world, priority=Priority.P3)) is None
    assert world.db.query(DispatchEvent).count() == 0


def test_a_duplicate_or_unclassified_ticket_is_building_managements(world, automatic_on):
    master = dispatchable_ticket(world)
    duplicate = dispatchable_ticket(world)
    duplicate.duplicate_of_ticket_id = master.id
    unclassified = dispatchable_ticket(world)
    unclassified.classification_status = ClassificationStatus.MANUAL_REVIEW
    world.db.commit()

    assert queue(world, duplicate) is None
    assert queue(world, unclassified) is None


def test_nothing_is_enqueued_while_the_switch_is_off(world):
    """§2: turning it off stops future automatic assignment."""
    assert queue(world, dispatchable_ticket(world)) is None


# ------------------------------------------------------------ the SAFE path


def test_a_safe_ticket_is_assigned_without_calling_the_agent(world, automatic_on):
    """§7: the agent is for AT_RISK only. A SAFE pass must not touch it."""
    ticket = dispatchable_ticket(world)
    queue(world, ticket)
    agent = StubAgent()

    report = run(world, agent=agent)

    assert report.assigned_safe == 1
    assert report.at_risk == 0
    assert agent.requests == []

    assignment = world.db.query(TicketAssignment).filter_by(ticket_id=ticket.id).one()
    assert assignment.assignment_source == AssignmentSource.AUTO_SCHEDULER.value
    assert assignment.risk_state == DispatchRiskState.SAFE.value
    # No human decided it, so none is named.
    assert assignment.assigned_by_user_id is None
    # §4: the planned window, all present. There is no acceptance deadline
    # beside them any more -- the column is gone, not merely left null.
    assert not hasattr(assignment, "acceptance_due_at")
    assert assignment.planned_start_at is not None
    assert assignment.planned_finish_at is not None
    assert assignment.planned_order == 0


def test_the_event_records_the_outcome_it_produced(world, automatic_on):
    ticket = dispatchable_ticket(world)
    event = queue(world, ticket)

    run(world)
    world.db.refresh(event)

    assert event.status == DispatchEventStatus.ASSIGNED.value
    assert event.is_open is False
    assert event.decision_source == DispatchDecisionSource.SCHEDULER.value
    assert event.assignment_id is not None
    assert event.batch_id is not None
    assert event.decided_at is not None


def test_the_resident_and_the_technician_are_both_told(world, automatic_on):
    ticket = dispatchable_ticket(world)
    queue(world, ticket)

    run(world)

    kinds = {row.notification_type for row in world.db.query(Notification).all()}
    assert {"ASSIGNMENT_CREATED", "TICKET_ASSIGNED"} <= kinds


def test_a_batch_spreads_work_instead_of_stacking_one_technician(world, automatic_on):
    """Placements accumulate inside the pass (§8).

    Without that, twenty tickets in one micro-batch would all be booked into the
    same free slot and the schedule would be fiction.
    """
    for _ in range(3):
        queue(world, dispatchable_ticket(world))

    report = run(world)

    assert report.assigned_safe == 3
    holders = {row.technician_id for row in world.db.query(TicketAssignment).all()}
    assert len(holders) == 3


# ----------------------------------------------------------- the AT_RISK path


def _saturate(world, technician, *, hours: int, deadline: str):
    """Give a technician a commitment that a new ticket would break."""
    ticket = make_ticket(world, category=world.water, priority=Priority.P2)
    assignment = make_assignment(world, ticket, technician)
    assignment.planned_finish_at = local(deadline)
    assignment.planned_start_at = NOW
    world.db.commit()
    return assignment


def test_an_at_risk_ticket_goes_to_the_agent_and_takes_its_pick(world, automatic_on):
    for index in range(3):
        _saturate(world, world.technician(index), hours=5, deadline="2026-08-26T09:00")
    ticket = dispatchable_ticket(world)
    event = queue(world, ticket)

    chosen = world.technician(1)
    agent = StubAgent(
        picks={
            str(event.id): AtRiskOutcome(
                technician_id=chosen.user_id, reason="Lịch nhẹ nhất.", model_name="stub", latency_ms=12
            )
        }
    )
    report = run(world, agent=agent)

    assert report.at_risk == 1
    assert report.assigned_by_agent == 1
    assert len(agent.requests) == 1

    assignment = world.db.query(TicketAssignment).filter_by(ticket_id=ticket.id).one()
    assert assignment.technician_id == chosen.user_id
    assert assignment.assignment_source == AssignmentSource.AUTO_AGENT.value
    assert assignment.risk_state == DispatchRiskState.AT_RISK.value


def test_an_at_risk_decision_is_recorded_with_its_candidate_set(world, automatic_on):
    """§7: the result has to be auditable after the fact."""
    for index in range(3):
        _saturate(world, world.technician(index), hours=5, deadline="2026-08-26T09:00")
    ticket = dispatchable_ticket(world)
    event = queue(world, ticket)
    chosen = world.technician(0)
    agent = StubAgent(
        picks={str(event.id): AtRiskOutcome(chosen.user_id, "Vì đây là lựa chọn ít trễ nhất.", "stub", 30)}
    )

    run(world, agent=agent)

    decision = world.db.query(AtRiskDecision).one()
    assert decision.decision_source == DispatchDecisionSource.AGENT.value
    assert decision.technician_id == chosen.user_id
    assert decision.model_name == "stub"
    assert len(decision.candidate_technician_ids) == 3
    assert decision.slack_seconds is not None


def test_building_management_is_told_about_every_at_risk_decision(world, automatic_on):
    for index in range(3):
        _saturate(world, world.technician(index), hours=5, deadline="2026-08-26T09:00")
    event = queue(world, dispatchable_ticket(world))
    agent = StubAgent(picks={str(event.id): AtRiskOutcome(world.technician(0).user_id, "ok", "stub", 5)})

    run(world, agent=agent)

    types = [row.notification_type for row in world.db.query(Notification).all()]
    assert "DISPATCH_AT_RISK_DECISION" in types


def test_the_agent_is_called_once_for_the_whole_at_risk_subset(world, automatic_on):
    """§7/§8: one call per micro-batch, never one per ticket."""
    for index in range(3):
        _saturate(world, world.technician(index), hours=5, deadline="2026-08-26T09:00")
    for _ in range(4):
        queue(world, dispatchable_ticket(world))

    agent = StubAgent()
    report = run(world, agent=agent)

    assert report.at_risk == 4
    assert len(agent.requests) == 1
    assert len(agent.requests[0].tickets) == 4


@pytest.mark.parametrize(
    "failure",
    [AtRiskDecisionError("AGENT_TIMEOUT", "no answer"), RuntimeError("provider exploded")],
)
def test_an_agent_failure_falls_back_to_the_least_negative_slack(world, automatic_on, failure):
    """The resolved decision: assign anyway, from the scheduler's ranking.

    Recorded as `AUTO_FALLBACK` / `SCHEDULER_FALLBACK` rather than as an agent
    decision, because "nobody reasoned about this one" is exactly the fact an
    auditor is looking for.
    """
    for index in range(3):
        _saturate(world, world.technician(index), hours=5, deadline="2026-08-26T09:00")
    ticket = dispatchable_ticket(world)
    queue(world, ticket)

    report = run(world, agent=StubAgent(error=failure))

    assert report.assigned_by_fallback == 1
    assert report.assigned_by_agent == 0
    assignment = world.db.query(TicketAssignment).filter_by(ticket_id=ticket.id).one()
    assert assignment.assignment_source == AssignmentSource.AUTO_FALLBACK.value

    decision = world.db.query(AtRiskDecision).one()
    assert decision.decision_source == DispatchDecisionSource.SCHEDULER_FALLBACK.value
    assert decision.model_name is None
    assert decision.error_code is not None


def test_a_ticket_the_agent_skipped_falls_back_on_its_own(world, automatic_on):
    """Partial answers are handled per ticket, not by failing the batch."""
    for index in range(3):
        _saturate(world, world.technician(index), hours=5, deadline="2026-08-26T09:00")
    answered = queue(world, dispatchable_ticket(world))
    queue(world, dispatchable_ticket(world))

    agent = StubAgent(
        picks={str(answered.id): AtRiskOutcome(world.technician(0).user_id, "ok", "stub", 7)}
    )
    report = run(world, agent=agent)

    assert report.assigned_by_agent == 1
    assert report.assigned_by_fallback == 1


# --------------------------------------------------------------- escalation


def test_no_skilled_technician_escalates_instead_of_guessing(world, automatic_on):
    """§3: never let the agent invent a technician. Escalate."""
    from src.database.models.technician import TechnicianSkill

    world.db.query(TechnicianSkill).delete()
    world.db.commit()
    ticket = dispatchable_ticket(world)
    event = queue(world, ticket)

    agent = StubAgent()
    report = run(world, agent=agent)

    assert report.escalated == 1
    assert agent.requests == []
    world.db.refresh(event)
    assert event.status == DispatchEventStatus.ESCALATED.value
    assert event.escalation_reason == DispatchEscalationReason.NO_ELIGIBLE_TECHNICIAN.value
    world.db.refresh(ticket)
    assert ticket.auto_assignment_paused is True


def test_turning_the_switch_off_escalates_what_was_already_queued(world, automatic_on):
    """§2: it stops future assignment; queued work surfaces to a human."""
    ticket = dispatchable_ticket(world)
    event = queue(world, ticket)
    automatic_on.enabled = False
    automatic_on.enabled_by_user_id = None
    automatic_on.enabled_at = None
    world.db.commit()

    report = run(world)

    assert report.escalated == 1
    world.db.refresh(event)
    assert event.escalation_reason == DispatchEscalationReason.AUTO_ASSIGNMENT_DISABLED.value
    assert world.db.query(TicketAssignment).count() == 0


def test_a_ticket_a_coordinator_already_took_is_superseded_not_escalated(world, automatic_on):
    """A manual win is a success for the queue, not something to alert about."""
    ticket = dispatchable_ticket(world)
    event = queue(world, ticket)
    make_assignment(world, ticket, world.technician(0))

    report = run(world)

    world.db.refresh(event)
    assert event.status == DispatchEventStatus.SUPERSEDED.value
    assert report.escalated == 0
    assert "DISPATCH_ESCALATED" not in {row.notification_type for row in world.db.query(Notification).all()}


# --------------------------------------------------------- shift and claiming


def test_outside_the_shift_a_pass_defers_rather_than_escalating(world, automatic_on):
    """§3 closes the window; the tickets are still automatically assignable.

    Escalating them every night would hand Building Management a queue every
    morning that the system was about to handle by itself at 08:00.
    """
    ticket = dispatchable_ticket(world)
    event = queue(world, ticket, now=local("2026-08-26T21:00"))

    report = run(world, now=local("2026-08-26T21:00"))

    assert report.out_of_shift is True
    assert report.claimed == 0
    world.db.refresh(event)
    assert event.status == DispatchEventStatus.PENDING.value
    # And it now says when it will be looked at.
    assert event.available_at.replace(tzinfo=None) >= local("2026-08-27T08:00").replace(tzinfo=None)


def test_a_micro_batch_never_takes_more_than_the_configured_ceiling(world, automatic_on):
    """§8 caps a micro-batch at 20 tickets."""
    for _ in range(23):
        queue(world, dispatchable_ticket(world))

    report = run(world)

    assert report.claimed == 20
    assert world.db.query(DispatchEvent).filter_by(status=DispatchEventStatus.PENDING.value).count() == 3


def test_an_abandoned_claim_returns_to_the_queue(world, automatic_on):
    """§8: a worker that died mid-batch must not strand its tickets."""
    ticket = dispatchable_ticket(world)
    event = queue(world, ticket)
    event.status = DispatchEventStatus.CLAIMED.value
    event.claimed_at = NOW - timedelta(hours=1)
    event.claim_expires_at = NOW - timedelta(minutes=30)
    event.claimed_by = "worker-that-died"
    event.attempt_count = 1
    world.db.commit()

    report = run(world)

    assert report.reclaimed == 1
    # Reclaimed and then processed in the same pass.
    assert report.assigned_safe == 1


def test_a_ticket_that_keeps_killing_workers_stops_being_retried(world, automatic_on):
    """Three failed claims is evidence, not bad luck."""
    ticket = dispatchable_ticket(world)
    event = queue(world, ticket)
    event.status = DispatchEventStatus.CLAIMED.value
    event.claim_expires_at = NOW - timedelta(minutes=1)
    event.attempt_count = 3
    world.db.commit()

    run(world)

    world.db.refresh(event)
    assert event.status == DispatchEventStatus.ESCALATED.value
    assert event.error_code == "DISPATCH_ATTEMPTS_EXHAUSTED"


def test_an_empty_queue_is_a_cheap_no_op(world, automatic_on):
    report = run(world)
    assert report.claimed == 0
    assert report.assigned_safe == 0
    assert report.errors == []
