"""An emergency is not a member of an incident case, at any of the three gates.

`src/domain/grouping_guard.py` carries the argument. The short version is that a
case member is not a passenger: it is a term in every other member's
`affected_scope`, so a P5 sitting in a case raises four other tickets' scores
while contributing nothing that can be worked on.

The arithmetic in `test_a_p4_is_not_pushed_over_the_line...` is the reported
failure, reproduced: a P4 at 77.50 and a P5 neighbour, one apartment apart. Count
the emergency and the P4 scores 81.25, which is P5. The system has then produced
a second emergency out of a ticket nobody classified as one, and the P4's own
manual-review gate opens on it.

Duplicate detection is deliberately left alone. A P5 still searches for
duplicates and still links to a master -- `test_emergency_review_gate.py` covers
that end -- because a duplicate is one report of one problem, and merging two
reports of a fire does not tell anybody the fire is bigger.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from src.agents.service import run_analysis
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.ticket import Ticket
from src.domain.risk_scoring import RiskCriterionScores
from src.models.enums import Priority, RiskAssessmentSource
from src.services.agent_result_service import GROUPING_GROUPED, GROUPING_NO_MATCH
from src.services.risk_assessment_service import RiskAssessmentService
from tests.test_agents.conftest import GroupingProposal, ScriptedLLM, classification

DAMP = dict(category="Thấm tường", text_category="Thấm tường", incident_facts=["tường nhà tắm thấm loang"])

#: 35 + 5 + 35 + 0 + 1.25. A P4, and 3.75 short of the P5 threshold -- which is
#: less than one step of the 20-point scope weight, so a single extra apartment
#: carries it over. The margin is what this fixture is for: a value that could
#: not cross the line no matter how many apartments joined would make the test
#: pass for the wrong reason.
ALMOST_EMERGENCY = dict(
    human_safety=4, property_spread=4, essential_function=4, affected_scope=0, deterioration_speed=1
)
P4_SCORE = Decimal("76.25")
#: What the same ticket scores if one more apartment is counted.
P4_SCORE_WITH_ONE_MORE_APARTMENT = Decimal("81.25")

#: Every criterion at the top of the scale: 100.00, an emergency on the score
#: alone rather than through a blocker floor.
EMERGENCY = dict(
    human_safety=4, property_spread=4, essential_function=4, affected_scope=4, deterioration_speed=4
)

#: 20.00, a P2 with room above it. A neighbour scored this way can join a case
#: without the extra scope step lifting it into another band, which keeps these
#: tests about the emergency rather than about §7.3's escalate-and-detach.
ORDINARY = dict(
    human_safety=1, property_spread=1, essential_function=1, affected_scope=0, deterioration_speed=1
)


def _scored_neighbour(agent_world, criteria: dict, *, unit_code: str) -> tuple:
    """A neighbouring apartment that has already reported and been scored."""
    unit_id, location_id = agent_world.make_apartment(
        like_location_id=agent_world.damp_c, unit_code=unit_code
    )
    ticket_id = agent_world.make_ticket(
        location_id=location_id,
        unit_id=unit_id,
        description="Tường nhà tắm nhà tôi bị thấm loang.",
        category_id=agent_world.wall_damp,
    )
    _score(agent_world, ticket_id, criteria)
    return unit_id, ticket_id


def _score(agent_world, ticket_id, criteria: dict) -> None:
    with agent_world.session_factory() as db:
        RiskAssessmentService(db).record(
            db.get(Ticket, ticket_id),
            criteria=RiskCriterionScores(**criteria),
            source=RiskAssessmentSource.AI_ANALYSIS,
        )
        db.commit()


def _reporting_ticket(agent_world):
    return agent_world.make_ticket(
        location_id=agent_world.damp_c,
        unit_id=agent_world.unit_c,
        description="Tường nhà tắm bị thấm, loang rộng dần.",
    )


def _cases(agent_world) -> list[IncidentCase]:
    with agent_world.session_factory() as db:
        rows = list(db.scalars(select(IncidentCase)))
        for row in rows:
            db.expunge(row)
        return rows


def _case_member_ids(agent_world) -> set:
    with agent_world.session_factory() as db:
        return set(db.scalars(select(IncidentCaseMember.ticket_id)))


# ---------------------------------------------------------------------------
# Gate 1 -- the candidate search.
# ---------------------------------------------------------------------------


def test_an_emergency_neighbour_is_never_offered_to_the_grouping_model(agent_world):
    """Not filtered out of the proposal afterwards: never shown in the first place."""
    _unit, emergency_id = _scored_neighbour(agent_world, EMERGENCY, unit_code="F0721")
    ticket_id = _reporting_ticket(agent_world)
    llm = ScriptedLLM(
        [classification(**DAMP)],
        grouping=GroupingProposal(
            grouped=True, related_ticket_ids=[str(emergency_id)], reason="Cùng trục tường."
        ),
    )

    run_analysis(ticket_id, llm=llm)

    # No candidates means the stage never reaches a model call at all.
    assert "judge_grouping" not in llm.calls
    assert agent_world.latest_run(ticket_id).grouping_status == GROUPING_NO_MATCH
    assert _cases(agent_world) == []


def test_a_groupable_neighbour_beside_an_emergency_still_groups(agent_world):
    """The filter removes the emergency, not the search."""
    _unit_a, emergency_id = _scored_neighbour(agent_world, EMERGENCY, unit_code="F0721")
    _unit_b, ordinary_id = _scored_neighbour(agent_world, ORDINARY, unit_code="F0722")
    ticket_id = _reporting_ticket(agent_world)
    llm = ScriptedLLM(
        [classification(**DAMP)],
        grouping=GroupingProposal(
            grouped=True,
            related_ticket_ids=[str(ordinary_id), str(emergency_id)],
            reason="Cùng trục tường.",
        ),
    )

    run_analysis(ticket_id, llm=llm)

    assert agent_world.latest_run(ticket_id).grouping_status == GROUPING_GROUPED
    members = _case_member_ids(agent_world)
    assert ordinary_id in members
    assert emergency_id not in members


# ---------------------------------------------------------------------------
# The reported failure.
# ---------------------------------------------------------------------------


def test_a_p4_is_not_pushed_over_the_line_by_an_emergency_it_cannot_be_grouped_with(agent_world):
    """The reported failure, with its reported numbers.

    The reporter scores 77.50. The one other apartment that reported anything is
    a P5. Counting it would make the case two apartments deep, add one step of
    the scope weight, and land the reporter on 81.25 -- an emergency, produced
    entirely by an emergency it is not allowed to be grouped with, and holding
    the reporter at a manual-review gate nobody meant to open.
    """
    _unit, emergency_id = _scored_neighbour(agent_world, EMERGENCY, unit_code="F0721")
    ticket_id = _reporting_ticket(agent_world)
    llm = ScriptedLLM(
        [classification(**DAMP, **ALMOST_EMERGENCY)],
        grouping=GroupingProposal(
            grouped=True, related_ticket_ids=[str(emergency_id)], reason="Cùng trục tường."
        ),
    )

    run_analysis(ticket_id, llm=llm)

    reporter = agent_world.ticket(ticket_id)
    assert reporter.risk_score == P4_SCORE
    assert reporter.risk_score != P4_SCORE_WITH_ONE_MORE_APARTMENT
    assert reporter.priority is Priority.P4
    assert _case_member_ids(agent_world) == set()


def test_an_emergency_is_not_counted_in_a_case_density(agent_world):
    _unit, emergency_id = _scored_neighbour(agent_world, EMERGENCY, unit_code="F0721")
    _unit_b, ordinary_id = _scored_neighbour(agent_world, ORDINARY, unit_code="F0722")
    ticket_id = _reporting_ticket(agent_world)
    llm = ScriptedLLM(
        [classification(**DAMP)],
        grouping=GroupingProposal(
            grouped=True, related_ticket_ids=[str(ordinary_id), str(emergency_id)], reason="Cùng trục tường."
        ),
    )

    run_analysis(ticket_id, llm=llm)

    (case,) = _cases(agent_world)
    # Two apartments: the reporter and the ordinary neighbour. Not three.
    assert case.density_value == 2
    assert emergency_id not in _case_member_ids(agent_world)


def test_the_emergency_keeps_the_score_it_had(agent_world):
    """It is excluded from the case, not re-scored by it."""
    _unit, emergency_id = _scored_neighbour(agent_world, EMERGENCY, unit_code="F0721")
    _unit_b, ordinary_id = _scored_neighbour(agent_world, ORDINARY, unit_code="F0722")
    ticket_id = _reporting_ticket(agent_world)
    llm = ScriptedLLM(
        [classification(**DAMP)],
        grouping=GroupingProposal(
            grouped=True, related_ticket_ids=[str(ordinary_id), str(emergency_id)], reason="Cùng trục tường."
        ),
    )

    run_analysis(ticket_id, llm=llm)

    emergency = agent_world.ticket(emergency_id)
    assert emergency.risk_score == Decimal("100.00")
    assert emergency.priority is Priority.P5


# ---------------------------------------------------------------------------
# Gate 3 -- the membership write, minutes after the search.
# ---------------------------------------------------------------------------


class _EscalatesWhileJudging(ScriptedLLM):
    """Turns a candidate into an emergency between the search and the write.

    The real version of this is a duplicate report arriving during the grouping
    stage and escalating its master. The candidate list was fetched before it
    happened and still names a ticket that is now a P5.
    """

    def __init__(self, classifications, grouping, *, world, ticket_id):
        super().__init__(classifications, grouping=grouping)
        self.world = world
        self.ticket_id = ticket_id

    def judge_grouping(self, **kwargs):
        _score(self.world, self.ticket_id, EMERGENCY)
        return super().judge_grouping(**kwargs)


def test_a_candidate_that_becomes_an_emergency_before_the_write_is_dropped(agent_world):
    _unit, escalating_id = _scored_neighbour(agent_world, ORDINARY, unit_code="F0721")
    ticket_id = _reporting_ticket(agent_world)
    llm = _EscalatesWhileJudging(
        [classification(**DAMP)],
        GroupingProposal(grouped=True, related_ticket_ids=[str(escalating_id)], reason="Cùng trục tường."),
        world=agent_world,
        ticket_id=escalating_id,
    )

    run_analysis(ticket_id, llm=llm)

    # The search offered it, the model named it, and the write refused it.
    assert "judge_grouping" in llm.calls
    assert escalating_id not in _case_member_ids(agent_world)
    assert agent_world.ticket(escalating_id).priority is Priority.P5


def test_the_reporter_is_not_left_in_a_case_of_one(agent_world):
    """A case whose only groupable member dropped out is no case at all."""
    _unit, escalating_id = _scored_neighbour(agent_world, ORDINARY, unit_code="F0721")
    ticket_id = _reporting_ticket(agent_world)
    llm = _EscalatesWhileJudging(
        [classification(**DAMP)],
        GroupingProposal(grouped=True, related_ticket_ids=[str(escalating_id)], reason="Cùng trục tường."),
        world=agent_world,
        ticket_id=escalating_id,
    )

    run_analysis(ticket_id, llm=llm)

    assert agent_world.latest_run(ticket_id).grouping_status == GROUPING_NO_MATCH
    assert _cases(agent_world) == []
