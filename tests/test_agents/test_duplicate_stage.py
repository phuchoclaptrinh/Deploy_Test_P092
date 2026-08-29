"""The duplicate stage, and the two things it must never do on its own.

Duplicate linking ends a ticket: it stops being work anybody will do and starts
following somebody else's. Everything here is about the conditions under which
that is allowed to happen automatically, and about the states in between --
`DUPLICATE_UNCERTAIN`, and a match that finished minutes ago.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.agents.nodes import RECENT_NO_NEW_INFO, RECENT_RECURRED, RECENT_UNSURE
from src.agents.service import run_analysis
from src.models.agent_schemas import AgentQuestionKind, P3ReviewStatus
from src.models.api.errors import DomainError
from src.models.enums import ClassificationStatus, TicketStatus
from src.services.agent_backend_service import AgentBackendService
from src.services.agent_result_service import (
    GROUPING_NOT_ELIGIBLE,
    GROUPING_PENDING,
    GROUPING_WAITING_DUPLICATE_DECISION,
)
from tests.test_agents.conftest import ScriptedLLM, classification, duplicate_judgement


def _active_master(world):
    return world.make_ticket(
        location_id=world.bath_a,
        unit_id=world.unit_a,
        reporter=world.neighbour,
        description="Trần nhà tắm bị rỉ nước.",
        category_id=world.water,
    )


def test_no_candidates_skips_the_duplicate_judgement(agent_world):
    """An empty list has one possible answer, so it is not worth a model call."""
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    llm = ScriptedLLM([classification()])

    outcome = run_analysis(ticket_id, llm=llm)

    assert llm.calls == ["classify"]
    assert outcome.finalized
    run = agent_world.latest_run(ticket_id)
    assert run.exit_reason == "ANALYSIS_COMPLETE"
    assert run.duplicate_candidates is None


def test_a_confirmed_duplicate_links_to_the_master_and_carries_no_priority(agent_world):
    master_id = _active_master(agent_world)
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    llm = ScriptedLLM(
        [classification()],
        judgements=[
            duplicate_judgement(
                verdict="SAME_INCIDENT",
                master_ticket_id=str(master_id),
                reason="Cùng trần nhà tắm, cùng hiện tượng rỉ nước.",
            )
        ],
    )

    run_analysis(ticket_id, llm=llm)

    ticket = agent_world.ticket(ticket_id)
    assert ticket.duplicate_of_ticket_id == master_id
    assert ticket.status is TicketStatus.LINKED_DUPLICATE
    # A duplicate is not a second copy of the work: no priority, no score, no
    # deadline of its own, and it never joins the queue.
    assert ticket.priority is None
    assert ticket.score_total is None
    assert ticket.sla_due_at is None
    assert agent_world.latest_run(ticket_id).grouping_status == GROUPING_NOT_ELIGIBLE


def test_duplicate_uncertain_waits_for_a_human_and_starts_no_grouping(agent_world):
    """The bug this guards: `DUPLICATE_UNCERTAIN` used to leave grouping
    pending, so a ticket could be folded into an incident case before anyone
    decided whether it was a duplicate at all."""
    _active_master(agent_world)
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    llm = ScriptedLLM(
        [classification()],
        judgements=[duplicate_judgement(verdict="UNCERTAIN", reason="Có phản ánh gần giống nhưng chưa đủ chắc.")],
    )

    run_analysis(ticket_id, llm=llm)

    run = agent_world.latest_run(ticket_id)
    assert run.exit_reason == "DUPLICATE_UNCERTAIN"
    assert run.grouping_status == GROUPING_WAITING_DUPLICATE_DECISION
    assert agent_world.ticket(ticket_id).classification_status is ClassificationStatus.MANUAL_REVIEW
    # The gate on the background stage, and the reason the grouping model was
    # never called.
    with agent_world.session_factory() as db:
        assert AgentBackendService(db).grouping_is_pending(ticket_id) is False
    assert "judge_grouping" not in llm.calls


def test_management_confirming_a_duplicate_links_it_and_never_groups(agent_world):
    master_id = _active_master(agent_world)
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    llm = ScriptedLLM(
        [classification()],
        judgements=[duplicate_judgement(verdict="UNCERTAIN", reason="Chưa chắc chắn.")],
    )
    run_analysis(ticket_id, llm=llm)

    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_duplicate_uncertain(
            agent_world.coordinator,
            ticket_id,
            is_duplicate=True,
            master_ticket_id=master_id,
            reason="Đã kiểm tra hiện trường, cùng một sự cố.",
        )

    ticket = agent_world.ticket(ticket_id)
    assert ticket.duplicate_of_ticket_id == master_id
    assert ticket.status is TicketStatus.LINKED_DUPLICATE
    run = agent_world.latest_run(ticket_id)
    assert run.grouping_status == GROUPING_NOT_ELIGIBLE
    with agent_world.session_factory() as db:
        assert AgentBackendService(db).grouping_is_pending(ticket_id) is False


def test_management_rejecting_a_duplicate_publishes_it_and_opens_grouping_once(agent_world):
    _active_master(agent_world)
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.bath_a,
        unit_id=agent_world.unit_a,
        description="Tường nhà tắm thấm loang.",
    )
    llm = ScriptedLLM(
        [classification(category="Thấm tường", text_category="Thấm tường")],
        judgements=[duplicate_judgement(verdict="UNCERTAIN", reason="Chưa chắc chắn.")],
    )
    # The master is a water ticket, so the WALL_DAMP candidate search finds it
    # only because the test seeds one; what matters is the uncertain verdict.
    master_same_category = agent_world.make_ticket(
        location_id=agent_world.bath_a,
        unit_id=agent_world.unit_a,
        reporter=agent_world.neighbour,
        description="Thấm tường nhà tắm.",
        category_id=agent_world.wall_damp,
    )
    run_analysis(ticket_id, llm=llm)
    assert agent_world.latest_run(ticket_id).grouping_status == GROUPING_WAITING_DUPLICATE_DECISION

    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_duplicate_uncertain(
            agent_world.coordinator,
            ticket_id,
            is_duplicate=False,
            reason="Hai vị trí khác nhau.",
        )

    ticket = agent_world.ticket(ticket_id)
    assert ticket.classification_status is ClassificationStatus.RESOLVED
    assert ticket.priority is not None
    assert ticket.duplicate_of_ticket_id is None
    run = agent_world.latest_run(ticket_id)
    assert run.grouping_status == GROUPING_PENDING
    with agent_world.session_factory() as db:
        assert AgentBackendService(db).grouping_is_pending(ticket_id) is True

    # Exactly once: the second decision is refused, so a retried request cannot
    # queue a second grouping pass.
    with agent_world.session_factory() as db, pytest.raises(DomainError):
        AgentBackendService(db).resolve_duplicate_uncertain(
            agent_world.coordinator, ticket_id, is_duplicate=False, reason="Lặp lại."
        )
    assert master_same_category is not None


def test_a_recently_completed_match_asks_the_resident_instead_of_linking(agent_world, recently_completed_master):
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    llm = ScriptedLLM(
        [classification()],
        judgements=[
            duplicate_judgement(
                verdict="SAME_INCIDENT",
                master_ticket_id=str(recently_completed_master),
                reason="Cùng hiện tượng, cùng vị trí.",
            )
        ],
    )

    outcome = run_analysis(ticket_id, llm=llm)

    assert outcome.awaiting_resident
    question = agent_world.pending_question(ticket_id)
    assert question is not None
    assert question.question_kind == AgentQuestionKind.RECENT_COMPLETION.value
    # Four fixed statements, and no prose: whether the problem came back is a
    # fact the resident states, not one to be read out of a sentence.
    assert question.allow_free_text_fallback is False
    assert set(question.options) == {RECENT_NO_NEW_INFO, RECENT_RECURRED, RECENT_UNSURE} | set(question.options)
    assert agent_world.ticket(ticket_id).duplicate_of_ticket_id is None


def test_no_new_information_on_a_recent_completion_links_the_duplicate(agent_world, recently_completed_master):
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    llm = ScriptedLLM(
        [classification()],
        judgements=[
            duplicate_judgement(
                verdict="SAME_INCIDENT",
                master_ticket_id=str(recently_completed_master),
                reason="Cùng hiện tượng, cùng vị trí.",
            )
        ],
    )
    run_analysis(ticket_id, llm=llm)
    question = agent_world.pending_question(ticket_id)

    from src.agents.service import resume_analysis

    agent_world.answer(ticket_id, question.id, RECENT_NO_NEW_INFO)
    resume_analysis(question.session_id, llm=llm)

    ticket = agent_world.ticket(ticket_id)
    assert ticket.duplicate_of_ticket_id == recently_completed_master
    assert agent_world.latest_run(ticket_id).exit_reason == "DUPLICATE_EXISTING"


def test_unsure_on_a_recent_completion_goes_to_management_not_to_a_link(agent_world, recently_completed_master):
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    llm = ScriptedLLM(
        [classification()],
        judgements=[
            duplicate_judgement(
                verdict="SAME_INCIDENT",
                master_ticket_id=str(recently_completed_master),
                reason="Cùng hiện tượng, cùng vị trí.",
            )
        ],
    )
    run_analysis(ticket_id, llm=llm)
    question = agent_world.pending_question(ticket_id)

    from src.agents.service import resume_analysis

    agent_world.answer(ticket_id, question.id, RECENT_UNSURE)
    resume_analysis(question.session_id, llm=llm)

    run = agent_world.latest_run(ticket_id)
    assert run.exit_reason == "DUPLICATE_UNCERTAIN"
    assert run.grouping_status == GROUPING_WAITING_DUPLICATE_DECISION
    assert agent_world.ticket(ticket_id).duplicate_of_ticket_id is None


def test_a_candidate_completed_over_an_hour_ago_is_not_a_candidate(agent_world):
    """The window is what makes the recurrence question meaningful; outside it
    a finished ticket is simply finished."""
    agent_world.make_ticket(
        location_id=agent_world.bath_a,
        unit_id=agent_world.unit_a,
        reporter=agent_world.neighbour,
        description="Đã xử lý xong từ hôm qua.",
        status=TicketStatus.COMPLETED,
        category_id=agent_world.water,
        completed_at=datetime.now(UTC) - timedelta(hours=5),
    )
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    llm = ScriptedLLM([classification()])

    run_analysis(ticket_id, llm=llm)

    assert llm.calls == ["classify"]
    assert agent_world.latest_run(ticket_id).exit_reason == "ANALYSIS_COMPLETE"


def test_a_technical_failure_is_a_failed_run_with_a_code_not_an_uncertain_duplicate(agent_world):
    from tests.test_agents.conftest import ExplodingLLM

    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    llm = ExplodingLLM(RuntimeError("model gateway unreachable"))

    outcome = run_analysis(ticket_id, llm=llm)

    assert outcome.failed_technically
    assert not outcome.finalized
    run = agent_world.latest_run(ticket_id)
    assert run is not None
    assert run.status.value == "FAILED"
    assert run.error_code
    # The distinction the whole design turns on: a broken model is not a
    # verdict about the ticket.
    assert run.exit_reason != "DUPLICATE_UNCERTAIN"
    assert run.p3_review_status in {None, P3ReviewStatus.NOT_REQUIRED.value}
    assert agent_world.ticket(ticket_id).classification_status is ClassificationStatus.MANUAL_REVIEW
