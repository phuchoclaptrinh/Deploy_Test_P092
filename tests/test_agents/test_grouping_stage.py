"""Background grouping: one physical problem spreading through the building.

Grouping is not duplicate detection, and the difference decides how a ticket is
treated. Duplicates collapse into one piece of work; grouped tickets stay
separate pieces of work that happen to share an incident case. So nothing here
changes a member's status, assignment or SLA -- it only records the case.

It also never runs in the resident's request. The stage starts only after the
foreground round has been finalized and the resident notified, and only from
the `PENDING` grouping state.
"""

from __future__ import annotations

from src.agents.service import run_analysis, run_case_grouping
from src.models.enums import TicketStatus
from src.services.agent_backend_service import AgentBackendService
from src.services.agent_result_service import (
    GROUPING_GROUPED,
    GROUPING_NO_MATCH,
    GROUPING_NOT_ELIGIBLE,
)
from tests.test_agents.conftest import GroupingProposal, ScriptedLLM, classification

DAMP = dict(category="Thấm tường", text_category="Thấm tường", incident_facts=["tường nhà tắm thấm loang"])


def test_a_spreading_category_groups_adjacent_floors_in_the_background(agent_world):
    neighbour_id = agent_world.make_ticket(
        location_id=agent_world.damp_d,
        unit_id=agent_world.unit_d,
        reporter=agent_world.neighbour,
        description="Tường nhà tắm nhà tôi bị thấm loang.",
        category_id=agent_world.wall_damp,
    )
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.damp_c,
        unit_id=agent_world.unit_c,
        description="Tường nhà tắm bị thấm, loang rộng dần.",
    )
    llm = ScriptedLLM(
        [classification(**DAMP)],
        grouping=GroupingProposal(
            grouped=True,
            related_ticket_ids=[str(neighbour_id)],
            reason="Cùng trục tường, hai tầng liền kề, thấm trong cùng khoảng thời gian.",
        ),
    )

    run_analysis(ticket_id, llm=llm)

    run = agent_world.latest_run(ticket_id)
    assert run.grouping_status == GROUPING_GROUPED
    assert "judge_grouping" in llm.calls
    # The order matters: the resident's result is written by the foreground
    # round, and grouping only looks for a case afterwards.
    assert llm.calls.index("classify") < llm.calls.index("judge_grouping")
    # Members stay independent tickets.
    neighbour = agent_world.ticket(neighbour_id)
    assert neighbour.status is TicketStatus.NEW
    assert neighbour.duplicate_of_ticket_id is None
    # Density counts apartments, not tickets.
    assert int(run.grouping["density"]) == 2


def test_a_non_spreading_category_never_reaches_the_grouping_model(agent_world):
    """Only four categories can spread. The rest never cost a model call."""
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_a,
        description="Tiếng ồn lớn ở khu vực thang máy vào ban đêm.",
    )
    llm = ScriptedLLM([classification(category="Tiếng ồn", text_category="Tiếng ồn")])

    run_analysis(ticket_id, llm=llm)

    assert llm.calls == ["classify"]
    assert agent_world.latest_run(ticket_id).grouping_status == GROUPING_NOT_ELIGIBLE
    # And a direct call is a no-op rather than an error.
    run_case_grouping(ticket_id, llm=llm)
    assert llm.calls == ["classify"]


def test_grouping_records_no_match_when_no_neighbour_reported_anything(agent_world):
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.damp_c,
        unit_id=agent_world.unit_c,
        description="Tường nhà tắm bị thấm.",
    )
    llm = ScriptedLLM([classification(**DAMP)])

    run_analysis(ticket_id, llm=llm)

    # No candidates, so no grouping model call either.
    assert llm.calls == ["classify"]
    assert agent_world.latest_run(ticket_id).grouping_status == GROUPING_NO_MATCH


def test_grouping_only_proposes_tickets_the_backend_supplied(agent_world):
    """A proposal naming a ticket outside the candidate list is dropped, not
    trusted: the model would be grouping something nobody filtered."""
    from uuid import uuid4

    neighbour_id = agent_world.make_ticket(
        location_id=agent_world.damp_d,
        unit_id=agent_world.unit_d,
        reporter=agent_world.neighbour,
        description="Tường nhà tắm nhà tôi bị thấm.",
        category_id=agent_world.wall_damp,
    )
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.damp_c,
        unit_id=agent_world.unit_c,
        description="Tường nhà tắm bị thấm.",
    )
    llm = ScriptedLLM(
        [classification(**DAMP)],
        grouping=GroupingProposal(
            grouped=True,
            related_ticket_ids=[str(uuid4())],
            reason="Một mã ticket không nằm trong danh sách được cung cấp.",
        ),
    )

    run_analysis(ticket_id, llm=llm)

    assert agent_world.latest_run(ticket_id).grouping_status == GROUPING_NO_MATCH
    assert agent_world.ticket(neighbour_id).duplicate_of_ticket_id is None


def test_grouping_runs_only_from_the_pending_state(agent_world):
    """`grouping_is_pending` is the single gate, and it closes once the stage
    has run, so a repeated background task cannot group twice."""
    neighbour_id = agent_world.make_ticket(
        location_id=agent_world.damp_d,
        unit_id=agent_world.unit_d,
        reporter=agent_world.neighbour,
        description="Tường nhà tắm nhà tôi bị thấm.",
        category_id=agent_world.wall_damp,
    )
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.damp_c,
        unit_id=agent_world.unit_c,
        description="Tường nhà tắm bị thấm.",
    )
    llm = ScriptedLLM(
        [classification(**DAMP)],
        grouping=GroupingProposal(grouped=True, related_ticket_ids=[str(neighbour_id)], reason="Cùng trục tường."),
    )
    run_analysis(ticket_id, llm=llm)
    calls_after_first = list(llm.calls)

    with agent_world.session_factory() as db:
        assert AgentBackendService(db).grouping_is_pending(ticket_id) is False
    run_case_grouping(ticket_id, llm=llm)

    assert llm.calls == calls_after_first
    assert agent_world.latest_run(ticket_id).grouping_status == GROUPING_GROUPED
