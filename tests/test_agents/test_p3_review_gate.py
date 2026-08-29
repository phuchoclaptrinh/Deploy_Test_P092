"""The mandatory human gate in front of the emergency priority.

P3 means "respond within five minutes". Nothing automatic decides that on its
own, and nothing automatic keeps running behind it: a P3 classification stops
the pipeline before duplicate retrieval and waits for a coordinator to confirm
the emergency or downgrade it.

Two things are worth stating once, because every test here depends on them:

* **P3 is a `Priority`, not a `Severity`.** In this system `Severity` is
  LOW/MEDIUM/HIGH and `Priority` is P1/P2/P3, where P3 is the most urgent and
  carries the five-minute SLA.
* **A red flag is a P3 by definition,** so it goes through the same gate as a
  ticket that merely scored one.
"""

from __future__ import annotations

import pytest

from src.agents.service import resume_after_p3_downgrade, run_analysis
from src.models.agent_schemas import P3Decision, P3ReviewStatus
from src.models.api.errors import DomainError
from src.models.enums import ClassificationStatus, Priority, TicketStatus
from src.services.agent_backend_service import AgentBackendService
from src.services.agent_result_service import (
    GROUPING_GROUPED,
    GROUPING_NOT_ELIGIBLE,
    GROUPING_WAITING_DUPLICATE_DECISION,
    GROUPING_WAITING_P3_REVIEW,
)
from tests.test_agents.conftest import (
    GroupingProposal,
    ScriptedLLM,
    classification,
    duplicate_judgement,
)

#: 40 base + 10 at the entrance gate + 20 for HIGH = 70, which is P3. This is
#: the scored route into the gate, with no danger signal involved.
P3_BY_SCORE = dict(category="An ninh / An toàn", text_category="An ninh / An toàn", severity="HIGH")

RED_FLAG = dict(
    severity="HIGH",
    red_flag=True,
    ai_reason="Có khói và mùi khét ở khu vực chung, nguy hiểm tức thời.",
)


def _p3_ticket(agent_world, *, overrides=None, llm=None):
    """A ticket that classifies as P3 and stops at the gate."""
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_a,
        description="Cổng chính có sự cố an ninh nghiêm trọng.",
    )
    llm = llm or ScriptedLLM([classification(**(overrides or P3_BY_SCORE))])
    run_analysis(ticket_id, llm=llm)
    return ticket_id, llm


# ---------------------------------------------------------------------------
# Entering the gate.
# ---------------------------------------------------------------------------


def test_a_p3_classification_waits_for_management(agent_world):
    ticket_id, _llm = _p3_ticket(agent_world)

    run = agent_world.latest_run(ticket_id)
    assert run.exit_reason == "P3_REVIEW_REQUIRED"
    assert run.p3_review_status == P3ReviewStatus.PENDING.value
    assert run.ai_priority_before_review is Priority.P3
    assert run.effective_priority is Priority.P3
    # Its own waiting state, not the duplicate one: different gate, different
    # decision, and a ticket is never in both.
    assert run.grouping_status == GROUPING_WAITING_P3_REVIEW


def test_a_red_flag_goes_through_the_same_gate(agent_world):
    ticket_id, _llm = _p3_ticket(agent_world, overrides=RED_FLAG)

    ticket = agent_world.ticket(ticket_id)
    run = agent_world.latest_run(ticket_id)
    assert run.exit_reason == "RED_FLAG"
    assert run.p3_review_status == P3ReviewStatus.PENDING.value
    # The urgency is applied immediately; only publication waits.
    assert ticket.priority is Priority.P3
    assert ticket.red_flag_detected is True
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW


def test_p3_never_reaches_the_duplicate_stage(agent_world):
    """The gate is in front of duplicate retrieval, so a live match at the same
    location must not even be looked up."""
    agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_a,
        reporter=agent_world.neighbour,
        description="Sự cố an ninh tại cổng.",
        category_id=agent_world.security,
    )
    ticket_id, llm = _p3_ticket(agent_world)

    assert llm.calls == ["classify"]
    run = agent_world.latest_run(ticket_id)
    assert run.duplicate_candidates is None
    assert run.duplicate_verdict is None


def test_p3_never_schedules_grouping(agent_world):
    ticket_id, llm = _p3_ticket(agent_world)

    with agent_world.session_factory() as db:
        assert AgentBackendService(db).grouping_is_pending(ticket_id) is False
    assert "judge_grouping" not in llm.calls


def test_p3_neither_publishes_nor_closes_the_ticket(agent_world):
    ticket_id, _llm = _p3_ticket(agent_world)

    ticket = agent_world.ticket(ticket_id)
    # RESOLVED is what hands a report to the operational queue. Manual review
    # is where it waits instead.
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW
    assert ticket.status is TicketStatus.NEW
    assert ticket.completed_at is None


def test_a_p3_ticket_cannot_also_wait_for_a_duplicate_decision(agent_world):
    ticket_id, _llm = _p3_ticket(agent_world)

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
    ticket_id, _llm = _p3_ticket(agent_world)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).start_session(ticket_id, model_version="test")

    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "P3_REVIEW_REQUIRED"


def test_a_direct_duplicate_search_is_refused_while_the_gate_is_open(agent_world):
    """The rules are enforced in the services, not only by the routing, so a
    client replaying the round's own session id hits the same wall."""
    ticket_id, _llm = _p3_ticket(agent_world)
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
    ticket_id, _llm = _p3_ticket(agent_world)
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
    ticket_id, _llm = _p3_ticket(agent_world)

    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_p3_review(
            agent_world.coordinator,
            ticket_id,
            decision=P3Decision.CONFIRM_P3,
            reason="Đã xác minh, đúng là khẩn cấp.",
        )

    ticket = agent_world.ticket(ticket_id)
    run = agent_world.latest_run(ticket_id)
    assert ticket.classification_status is ClassificationStatus.RESOLVED
    assert ticket.priority is Priority.P3
    assert ticket.sla_due_at is not None
    assert run.p3_review_status == P3ReviewStatus.CONFIRMED.value
    assert run.p3_decision == P3Decision.CONFIRM_P3.value
    assert run.p3_reviewed_by == agent_world.coordinator
    assert run.p3_reviewed_at is not None
    assert run.p3_decision_reason == "Đã xác minh, đúng là khẩn cấp."


def test_confirming_p3_does_not_resume_the_automation(agent_world):
    """Deliberate: correlating an emergency with other reports is not worth the
    minutes it costs."""
    agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_a,
        reporter=agent_world.neighbour,
        description="Sự cố an ninh tại cổng.",
        category_id=agent_world.security,
    )
    ticket_id, llm = _p3_ticket(agent_world)

    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_p3_review(
            agent_world.coordinator, ticket_id, decision=P3Decision.CONFIRM_P3
        )

    run = agent_world.latest_run(ticket_id)
    assert run.grouping_status == GROUPING_NOT_ELIGIBLE
    assert run.duplicate_verdict is None
    with agent_world.session_factory() as db:
        assert AgentBackendService(db).grouping_is_pending(ticket_id) is False
    assert llm.calls == ["classify"]


def test_a_confirmed_gate_cannot_be_decided_twice(agent_world):
    ticket_id, _llm = _p3_ticket(agent_world)
    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_p3_review(
            agent_world.coordinator, ticket_id, decision=P3Decision.CONFIRM_P3
        )

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).resolve_p3_review(
            agent_world.coordinator, ticket_id, decision=P3Decision.CONFIRM_P3
        )
    assert excinfo.value.status_code == 409


# ---------------------------------------------------------------------------
# Downgrading.
# ---------------------------------------------------------------------------


def test_downgrading_without_a_reason_is_rejected(agent_world):
    ticket_id, _llm = _p3_ticket(agent_world)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).resolve_p3_review(
            agent_world.coordinator,
            ticket_id,
            decision=P3Decision.DOWNGRADE_SEVERITY,
            priority=Priority.P2,
            reason="   ",
        )

    assert excinfo.value.status_code == 400
    assert agent_world.latest_run(ticket_id).p3_review_status == P3ReviewStatus.PENDING.value


def test_downgrading_back_to_p3_is_rejected(agent_world):
    """Confirming is the action for keeping P3; a downgrade that lands on P3
    would launder the confirmation through the wrong door."""
    ticket_id, _llm = _p3_ticket(agent_world)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).resolve_p3_review(
            agent_world.coordinator,
            ticket_id,
            decision=P3Decision.DOWNGRADE_SEVERITY,
            priority=Priority.P3,
            reason="Vẫn khẩn cấp.",
        )

    assert excinfo.value.status_code == 400
    assert agent_world.latest_run(ticket_id).p3_review_status == P3ReviewStatus.PENDING.value


def test_downgrading_records_both_priorities_and_the_reason(agent_world):
    ticket_id, _llm = _p3_ticket(agent_world)

    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_p3_review(
            agent_world.coordinator,
            ticket_id,
            decision=P3Decision.DOWNGRADE_SEVERITY,
            priority=Priority.P2,
            reason="Đã kiểm tra, không có nguy hiểm tức thời.",
        )

    run = agent_world.latest_run(ticket_id)
    assert run.p3_review_status == P3ReviewStatus.DOWNGRADED.value
    assert run.ai_priority_before_review is Priority.P3
    assert run.effective_priority is Priority.P2
    assert run.p3_decision_reason == "Đã kiểm tra, không có nguy hiểm tức thời."
    assert agent_world.ticket(ticket_id).priority is Priority.P2


def test_downgrading_resumes_the_duplicate_stage_and_publishes_the_ticket(agent_world):
    ticket_id, _llm = _p3_ticket(agent_world)

    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_p3_review(
            agent_world.coordinator,
            ticket_id,
            decision=P3Decision.DOWNGRADE_SEVERITY,
            priority=Priority.P2,
            reason="Không có nguy hiểm tức thời.",
        )
    resumed = ScriptedLLM([])  # no classification: the reviewed one is reused
    resume_after_p3_downgrade(ticket_id, llm=resumed)

    # No second classification. Re-running it would score P3 again and undo the
    # decision a human just took.
    assert "classify" not in resumed.calls
    runs = agent_world.runs(ticket_id)
    assert [run.exit_reason for run in runs] == ["P3_REVIEW_REQUIRED", "ANALYSIS_COMPLETE"]
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
    ticket_id, _llm = _p3_ticket(agent_world)
    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_p3_review(
            agent_world.coordinator,
            ticket_id,
            decision=P3Decision.DOWNGRADE_SEVERITY,
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
    resume_after_p3_downgrade(ticket_id, llm=resumed)

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
    # WALL_DAMP is one of the four spreading categories, and HIGH severity on a
    # 20-point base does not reach P3 -- so the gate is opened by a red flag.
    run_analysis(
        ticket_id,
        llm=ScriptedLLM([classification(category="Thấm tường", text_category="Thấm tường", **RED_FLAG)]),
    )
    assert agent_world.latest_run(ticket_id).p3_review_status == P3ReviewStatus.PENDING.value

    with agent_world.session_factory() as db:
        backend = AgentBackendService(db)
        assert backend.grouping_is_pending(ticket_id) is False
        backend.resolve_p3_review(
            agent_world.coordinator,
            ticket_id,
            decision=P3Decision.DOWNGRADE_SEVERITY,
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
    resume_after_p3_downgrade(ticket_id, llm=resumed)

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
    assert agent_world.latest_run(ticket_id).p3_review_status == P3ReviewStatus.NOT_REQUIRED.value

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).resolve_p3_review(
            agent_world.coordinator, ticket_id, decision=P3Decision.CONFIRM_P3
        )
    assert excinfo.value.status_code == 409
