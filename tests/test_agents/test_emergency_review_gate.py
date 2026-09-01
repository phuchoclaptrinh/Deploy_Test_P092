"""The mandatory human gate in front of the emergency priority.

P5 means "respond within five minutes, by hand". Nothing automatic decides that
on its own, and nothing automatic keeps running behind it: a P5 classification
waits for a coordinator to confirm the emergency or downgrade it.

Two things are worth stating once, because every test here depends on them:

* **The scale inverted.** `Priority` now runs P1..P5 with **P5** the most urgent
  and carrying the five-minute SLA. Every "P3" in this file's history meant what
  P5 means now. `Severity` is gone entirely.
* **There is one route into the gate, not two.** v1 had a separate red-flag
  terminal that set P3 with no score. v2 has none: a fire is the blocker code
  `FIRE_OR_SMOKE`, which floors the priority at P5 through the same calculator
  every other ticket goes through and lands on the same exit.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.agents.service import resume_after_emergency_downgrade, run_analysis
from src.database.models.notification import Notification
from src.database.models.ticket_risk_assessment import TicketRiskAssessment
from src.models.api.errors import DomainError
from src.models.enums import ClassificationStatus, EmergencyDecision, EmergencyReviewStatus, Priority, TicketStatus
from src.services.agent_backend_service import AgentBackendService
from src.services.agent_result_service import (
    GROUPING_GROUPED,
    GROUPING_NOT_ELIGIBLE,
    GROUPING_WAITING_DUPLICATE_DECISION,
    GROUPING_WAITING_EMERGENCY_REVIEW,
)
from tests.test_agents.conftest import (
    GroupingProposal,
    ScriptedLLM,
    classification,
    duplicate_judgement,
)

#: 35 + 5 + 35 + 0 + 5 = 80.00, exactly the bottom of the P5 band. This is the
#: scored route into the gate, with no blocker involved.
P5_BY_SCORE = dict(
    category="An ninh / An toàn",
    text_category="An ninh / An toàn",
    human_safety=4,
    property_spread=4,
    essential_function=4,
    affected_scope=0,
    deterioration_speed=4,
)

#: The other route: a low score that a named emergency floors at P5. The
#: default criteria score 20.00 -- a P2 -- so any P5 here is the blocker's doing
#: and nothing else.
P5_BY_BLOCKER = dict(
    blockers=[{"code": "FIRE_OR_SMOKE", "evidence": ["Ảnh cho thấy khói bốc ra từ hộp kỹ thuật hành lang."]}],
    ai_reason="Có khói và mùi khét ở khu vực chung, nguy hiểm tức thời.",
)


def _emergency_ticket(agent_world, *, overrides=None, llm=None, judgements=None):
    """A ticket that classifies as P5, is warned about, and stops at the gate.

    A duplicate judgement is scripted by default because an emergency now
    *does* run the duplicate stage -- see `docs/risk_scoring_v2.md` §7.1. The
    default verdict is DIFFERENT_INCIDENT, which is the case where the ticket
    stands on its own at the gate.
    """
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_a,
        description="Cổng chính có sự cố an ninh nghiêm trọng.",
    )
    llm = llm or ScriptedLLM(
        [classification(**(overrides or P5_BY_SCORE))],
        judgements=judgements
        if judgements is not None
        else [duplicate_judgement(verdict="DIFFERENT_INCIDENT", reason="Không phải cùng sự cố.")],
    )
    run_analysis(ticket_id, llm=llm)
    return ticket_id, llm


# ---------------------------------------------------------------------------
# Entering the gate.
# ---------------------------------------------------------------------------


def test_a_p3_classification_waits_for_management(agent_world):
    ticket_id, _llm = _emergency_ticket(agent_world)

    run = agent_world.latest_run(ticket_id)
    assert run.exit_reason == "EMERGENCY_REVIEW_REQUIRED"
    assert run.emergency_review_status == EmergencyReviewStatus.PENDING.value
    assert run.ai_priority_before_review is Priority.P5
    assert run.effective_priority is Priority.P5
    # Its own waiting state, not the duplicate one: different gate, different
    # decision, and a ticket is never in both.
    assert run.grouping_status == GROUPING_WAITING_EMERGENCY_REVIEW


def test_a_blocker_reaches_the_gate_through_the_same_exit_as_a_high_score(agent_world):
    """One emergency path. The blocker does not get a terminal of its own."""
    ticket_id, _llm = _emergency_ticket(agent_world, overrides=P5_BY_BLOCKER)

    ticket = agent_world.ticket(ticket_id)
    run = agent_world.latest_run(ticket_id)
    assert run.exit_reason == "EMERGENCY_REVIEW_REQUIRED"
    assert run.emergency_review_status == EmergencyReviewStatus.PENDING.value
    # The urgency is applied immediately; only publication waits.
    assert ticket.priority is Priority.P5
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW
    # And the score is on record, so a coordinator can see that the blocker --
    # not the rubric -- is what put it here.
    assert float(ticket.risk_score) == 20.00
    with agent_world.session_factory() as db:
        assessment = db.get(TicketRiskAssessment, ticket.current_risk_assessment_id)
        assert assessment.score_priority is Priority.P2
        assert assessment.blocker_floor is Priority.P5
        assert assessment.blocker_codes == ["FIRE_OR_SMOKE"]


def test_an_emergency_warns_first_and_then_runs_the_duplicate_stage(agent_world):
    """The reverse of v1, and the reverse on purpose.

    v1 stopped a P3 dead before duplicate retrieval to save the minutes it
    costs. v2 spends those minutes *after* the alarm instead of before it,
    which costs the coordinator nothing and answers the question that actually
    matters: is this the fourth person reporting the fire we already know
    about, or a second fire?
    """
    agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_a,
        reporter=agent_world.neighbour,
        description="Sự cố an ninh tại cổng.",
        category_id=agent_world.security,
    )
    ticket_id, llm = _emergency_ticket(agent_world)

    assert llm.calls == ["classify", "judge_duplicate"]
    run = agent_world.latest_run(ticket_id)
    assert run.duplicate_verdict == "DIFFERENT_INCIDENT"
    # Different incident, so the ticket stands on its own at the gate.
    assert run.exit_reason == "EMERGENCY_REVIEW_REQUIRED"


def test_the_warning_is_raised_before_the_duplicate_lookup_runs(agent_world):
    """The alarm is a side effect of classification, not of the round finishing.

    Asserted through the notification, because that is the thing a coordinator
    actually sees: it exists even on the path where the round goes on to spend
    a model call on the duplicate judgement.
    """
    ticket_id, _llm = _emergency_ticket(agent_world)

    with agent_world.session_factory() as db:
        warnings = db.scalars(
            select(Notification).where(
                Notification.ticket_id == ticket_id,
                Notification.notification_type == "TICKET_EMERGENCY_WARNING",
            )
        ).all()
    assert len(warnings) >= 1


def test_an_emergency_that_is_a_confident_duplicate_links_and_pulls_its_master_up(agent_world):
    """`docs/risk_scoring_v2.md` §7.1, the confident row.

    The master is the ticket somebody will actually work. If a second resident
    reports the same incident and that report reads as an emergency, the
    incident is an emergency -- the master having been filed first does not make
    it less urgent.
    """
    master_id = agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_a,
        reporter=agent_world.neighbour,
        description="Sự cố an ninh tại cổng.",
        category_id=agent_world.security,
    )
    ticket_id, _llm = _emergency_ticket(
        agent_world,
        judgements=[
            duplicate_judgement(
                verdict="SAME_INCIDENT", master_ticket_id=str(master_id), reason="Cùng sự cố tại cổng."
            )
        ],
    )

    ticket = agent_world.ticket(ticket_id)
    master = agent_world.ticket(master_id)
    assert ticket.duplicate_of_ticket_id == master_id
    assert master.priority is Priority.P5

    run = agent_world.latest_run(ticket_id)
    # No review item on the duplicate: the warning already fired, and a second
    # emergency to triage for the same incident is noise.
    assert run.exit_reason == "DUPLICATE_EXISTING"
    assert run.emergency_review_status == EmergencyReviewStatus.NOT_REQUIRED.value


def test_p3_never_schedules_grouping(agent_world):
    ticket_id, llm = _emergency_ticket(agent_world)

    with agent_world.session_factory() as db:
        assert AgentBackendService(db).grouping_is_pending(ticket_id) is False
    assert "judge_grouping" not in llm.calls


def test_p3_neither_publishes_nor_closes_the_ticket(agent_world):
    ticket_id, _llm = _emergency_ticket(agent_world)

    ticket = agent_world.ticket(ticket_id)
    # RESOLVED is what hands a report to the operational queue. Manual review
    # is where it waits instead.
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW
    assert ticket.status is TicketStatus.NEW
    assert ticket.completed_at is None


def test_a_emergency_ticket_cannot_also_wait_for_a_duplicate_decision(agent_world):
    ticket_id, _llm = _emergency_ticket(agent_world)

    run = agent_world.latest_run(ticket_id)
    assert run.grouping_status != GROUPING_WAITING_DUPLICATE_DECISION
    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).resolve_duplicate_uncertain(
            agent_world.coordinator, ticket_id, is_duplicate=False, reason="Không trùng."
        )
    assert excinfo.value.status_code == 409


# ---------------------------------------------------------------------------
# Backend authorization behind the gate.
# ---------------------------------------------------------------------------


def test_no_new_analysis_session_can_start_while_the_gate_is_open(agent_world):
    """The outermost protection. Re-running the analysis is how the retry
    action would otherwise answer the very question the gate is asking."""
    ticket_id, _llm = _emergency_ticket(agent_world)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).start_session(ticket_id, model_version="test")

    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "EMERGENCY_REVIEW_REQUIRED"


def test_a_direct_duplicate_search_is_refused_while_the_gate_is_open(agent_world):
    """The rules are enforced in the services, not only by the routing, so a
    client replaying the round's own session id hits the same wall."""
    ticket_id, _llm = _emergency_ticket(agent_world)
    session_id = agent_world.latest_session_id(ticket_id)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).search_related_tickets(
            session_id,
            ticket_id=ticket_id,
            category_id=agent_world.security,
            purpose="GROUPING",
        )

    assert excinfo.value.status_code == 409


def test_a_direct_grouping_proposal_is_refused_while_the_gate_is_open(agent_world):
    ticket_id, _llm = _emergency_ticket(agent_world)
    session_id = agent_world.latest_session_id(ticket_id)
    other = agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_b,
        reporter=agent_world.neighbour,
        description="Sự cố an ninh khác.",
        category_id=agent_world.security,
    )

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).propose_case_grouping(
            session_id, ticket_id=ticket_id, related_ticket_ids=[other], reason="Cùng khu vực."
        )

    assert excinfo.value.status_code == 409


# ---------------------------------------------------------------------------
# Confirming.
# ---------------------------------------------------------------------------


def test_confirming_p3_publishes_the_emergency_and_records_who_decided(agent_world):
    ticket_id, _llm = _emergency_ticket(agent_world)

    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_emergency_review(
            agent_world.coordinator,
            ticket_id,
            decision=EmergencyDecision.CONFIRM_P5,
            reason="Đã xác minh, đúng là khẩn cấp.",
        )

    ticket = agent_world.ticket(ticket_id)
    run = agent_world.latest_run(ticket_id)
    assert ticket.classification_status is ClassificationStatus.RESOLVED
    assert ticket.priority is Priority.P5
    assert ticket.sla_due_at is not None
    assert run.emergency_review_status == EmergencyReviewStatus.CONFIRMED.value
    assert run.emergency_decision == EmergencyDecision.CONFIRM_P5.value
    assert run.emergency_reviewed_by == agent_world.coordinator
    assert run.emergency_reviewed_at is not None
    assert run.emergency_decision_reason == "Đã xác minh, đúng là khẩn cấp."


def test_confirming_the_emergency_does_not_resume_the_automation(agent_world):
    """Grouping is the part that stays off. Duplicate already ran, before the
    gate; confirming does not start anything new."""
    agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_a,
        reporter=agent_world.neighbour,
        description="Sự cố an ninh tại cổng.",
        category_id=agent_world.security,
    )
    ticket_id, llm = _emergency_ticket(agent_world)

    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_emergency_review(
            agent_world.coordinator, ticket_id, decision=EmergencyDecision.CONFIRM_P5
        )

    run = agent_world.latest_run(ticket_id)
    assert run.grouping_status == GROUPING_NOT_ELIGIBLE
    with agent_world.session_factory() as db:
        assert AgentBackendService(db).grouping_is_pending(ticket_id) is False
    # No grouping call, and no second classification.
    assert "judge_grouping" not in llm.calls
    assert llm.calls.count("classify") == 1


def test_a_confirmed_gate_cannot_be_decided_twice(agent_world):
    ticket_id, _llm = _emergency_ticket(agent_world)
    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_emergency_review(
            agent_world.coordinator, ticket_id, decision=EmergencyDecision.CONFIRM_P5
        )

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).resolve_emergency_review(
            agent_world.coordinator, ticket_id, decision=EmergencyDecision.CONFIRM_P5
        )
    assert excinfo.value.status_code == 409


# ---------------------------------------------------------------------------
# Downgrading.
# ---------------------------------------------------------------------------


def test_downgrading_without_a_reason_is_rejected(agent_world):
    ticket_id, _llm = _emergency_ticket(agent_world)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).resolve_emergency_review(
            agent_world.coordinator,
            ticket_id,
            decision=EmergencyDecision.DOWNGRADE_PRIORITY,
            priority=Priority.P2,
            reason="   ",
        )

    assert excinfo.value.status_code == 400
    assert agent_world.latest_run(ticket_id).emergency_review_status == EmergencyReviewStatus.PENDING.value


def test_downgrading_back_to_p3_is_rejected(agent_world):
    """Confirming is the action for keeping P3; a downgrade that lands on P3
    would launder the confirmation through the wrong door."""
    ticket_id, _llm = _emergency_ticket(agent_world)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).resolve_emergency_review(
            agent_world.coordinator,
            ticket_id,
            decision=EmergencyDecision.DOWNGRADE_PRIORITY,
            priority=Priority.P5,
            reason="Vẫn khẩn cấp.",
        )

    assert excinfo.value.status_code == 400
    assert agent_world.latest_run(ticket_id).emergency_review_status == EmergencyReviewStatus.PENDING.value


def test_downgrading_records_both_priorities_and_the_reason(agent_world):
    ticket_id, _llm = _emergency_ticket(agent_world)

    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_emergency_review(
            agent_world.coordinator,
            ticket_id,
            decision=EmergencyDecision.DOWNGRADE_PRIORITY,
            priority=Priority.P2,
            reason="Đã kiểm tra, không có nguy hiểm tức thời.",
        )

    run = agent_world.latest_run(ticket_id)
    assert run.emergency_review_status == EmergencyReviewStatus.DOWNGRADED.value
    assert run.ai_priority_before_review is Priority.P5
    assert run.effective_priority is Priority.P2
    assert run.emergency_decision_reason == "Đã kiểm tra, không có nguy hiểm tức thời."
    assert agent_world.ticket(ticket_id).priority is Priority.P2


def test_downgrading_resumes_the_duplicate_stage_and_publishes_the_ticket(agent_world):
    ticket_id, _llm = _emergency_ticket(agent_world)

    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_emergency_review(
            agent_world.coordinator,
            ticket_id,
            decision=EmergencyDecision.DOWNGRADE_PRIORITY,
            priority=Priority.P2,
            reason="Không có nguy hiểm tức thời.",
        )
    resumed = ScriptedLLM([])  # no classification: the reviewed one is reused
    resume_after_emergency_downgrade(ticket_id, llm=resumed)

    # No second classification. Re-running it would score P3 again and undo the
    # decision a human just took.
    assert "classify" not in resumed.calls
    runs = agent_world.runs(ticket_id)
    assert [run.exit_reason for run in runs] == ["EMERGENCY_REVIEW_REQUIRED", "ANALYSIS_COMPLETE"]
    ticket = agent_world.ticket(ticket_id)
    assert ticket.classification_status is ClassificationStatus.RESOLVED
    # The coordinator's priority survives the re-score.
    assert ticket.priority is Priority.P2
    assert runs[-1].effective_priority is Priority.P2


def test_a_downgraded_ticket_that_is_a_duplicate_is_linked_not_grouped(agent_world):
    master_id = agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_a,
        reporter=agent_world.neighbour,
        description="Sự cố an ninh tại cổng.",
        category_id=agent_world.security,
    )
    ticket_id, _llm = _emergency_ticket(agent_world)
    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_emergency_review(
            agent_world.coordinator,
            ticket_id,
            decision=EmergencyDecision.DOWNGRADE_PRIORITY,
            priority=Priority.P1,
            reason="Không nguy hiểm.",
        )

    resumed = ScriptedLLM(
        [],
        judgements=[
            duplicate_judgement(
                verdict="SAME_INCIDENT", master_ticket_id=str(master_id), reason="Cùng sự cố tại cổng."
            )
        ],
    )
    resume_after_emergency_downgrade(ticket_id, llm=resumed)

    assert agent_world.ticket(ticket_id).duplicate_of_ticket_id == master_id
    assert agent_world.latest_run(ticket_id).grouping_status == GROUPING_NOT_ELIGIBLE


def test_a_downgraded_ticket_reaches_grouping_only_once_it_is_independent(agent_world):
    """The full chain the plan describes: P3 -> downgrade -> duplicate stage ->
    independent -> grouping allowed."""
    neighbour_id = agent_world.make_ticket(
        location_id=agent_world.damp_d,
        unit_id=agent_world.unit_d,
        reporter=agent_world.neighbour,
        description="Tường nhà tắm nhà tôi cũng bị thấm.",
        category_id=agent_world.wall_damp,
    )
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.damp_c,
        unit_id=agent_world.unit_c,
        description="Tường nhà tắm thấm loang, lan rộng.",
    )
    # WALL_DAMP is one of the four spreading categories, and the default scores
    # nowhere near 80 -- so the gate is opened by a blocker rather than by the
    # rubric, which is also what makes this a test about grouping and not about
    # the score.
    run_analysis(
        ticket_id,
        llm=ScriptedLLM([classification(category="Thấm tường", text_category="Thấm tường", **P5_BY_BLOCKER)]),
    )
    assert agent_world.latest_run(ticket_id).emergency_review_status == EmergencyReviewStatus.PENDING.value

    with agent_world.session_factory() as db:
        backend = AgentBackendService(db)
        assert backend.grouping_is_pending(ticket_id) is False
        backend.resolve_emergency_review(
            agent_world.coordinator,
            ticket_id,
            decision=EmergencyDecision.DOWNGRADE_PRIORITY,
            priority=Priority.P2,
            reason="Thấm tường, không nguy hiểm tức thời.",
        )
    # Still not groupable: the duplicate stage has not run yet.
    with agent_world.session_factory() as db:
        assert AgentBackendService(db).grouping_is_pending(ticket_id) is False

    resumed = ScriptedLLM(
        [],
        grouping=GroupingProposal(
            grouped=True,
            related_ticket_ids=[str(neighbour_id)],
            reason="Cùng trục tường, hai tầng liền kề, thấm trong cùng khoảng thời gian.",
        ),
    )
    resume_after_emergency_downgrade(ticket_id, llm=resumed)

    run = agent_world.latest_run(ticket_id)
    assert run.exit_reason == "ANALYSIS_COMPLETE"
    # Grouping was reached and ran: the ticket came out of the duplicate stage
    # independent, which is the only thing that opens the background stage.
    assert "judge_grouping" in resumed.calls
    assert run.grouping_status == GROUPING_GROUPED
    # Grouped tickets stay independent tickets that share one incident case.
    assert agent_world.ticket(neighbour_id).duplicate_of_ticket_id is None
    assert int(run.grouping["density"]) == 2


def test_only_a_pending_gate_can_be_reviewed(agent_world):
    """An ordinary P1/P2 ticket has no gate to decide."""
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    run_analysis(ticket_id, llm=ScriptedLLM([classification()]))
    assert agent_world.latest_run(ticket_id).emergency_review_status == EmergencyReviewStatus.NOT_REQUIRED.value

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).resolve_emergency_review(
            agent_world.coordinator, ticket_id, decision=EmergencyDecision.CONFIRM_P5
        )
    assert excinfo.value.status_code == 409
