"""A case holds five apartments. The sixth opens the next case in the series.

`docs/risk_scoring_v2.md` §4 caps an `IncidentCase` at five members and §7.2
re-scores every member when the confirmed count moves. Put together they have a
consequence that is easy to write and easy to get wrong: one grouping write can
change *two* cases, and both of them have members whose scope just changed.

The bug this file exists to keep out: the write loop walked its members, rolled
the working case over to a successor when the fifth one filled it, and then
recomputed density for the case it happened to be holding at the end. That is
the successor. The case that had just been filled to five kept the
`density_value=1` it was created with, and its five members each scored their
`affected_scope` as though their apartment were the only one -- five priorities
too low, in the one situation where the building has the most evidence that they
are too low.

The other half of §4 is checked here too: scope is never summed across a
`series_id`. Six apartments in one series are a case of five and a case of one,
not a case of six.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from src.agents.service import run_analysis
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.ticket import Ticket
from src.database.models.ticket_risk_assessment import TicketRiskAssessment
from src.domain.risk_scoring import RiskCriterionScores
from src.models.enums import Priority, RiskAssessmentSource
from src.services.agent_result_service import GROUPING_GROUPED
from src.services.risk_assessment_service import RiskAssessmentService
from tests.test_agents.conftest import GroupingProposal, ScriptedLLM, classification

DAMP = dict(category="Thấm tường", text_category="Thấm tường", incident_facts=["tường nhà tắm thấm loang"])

#: `classification()` scores 20.00 before scope. Each scope step adds 20/4.
BASE_SCORE = Decimal("20.00")
SCOPE_STEP = Decimal("5.00")


def _neighbours(agent_world, count: int) -> list[tuple]:
    """`count` reporting apartments on the damp floor, newest last.

    All on one floor so the candidate search returns every one of them: it fills
    its five slots with same-floor tickets first, and a test that let some of
    them land on the adjacent floor would be depending on that ordering.
    """
    made = []
    for index in range(count):
        unit_id, location_id = agent_world.make_apartment(
            like_location_id=agent_world.damp_c, unit_code=f"F07{20 + index}"
        )
        ticket_id = agent_world.make_ticket(
            location_id=location_id,
            unit_id=unit_id,
            description="Tường nhà tắm nhà tôi bị thấm loang.",
            category_id=agent_world.wall_damp,
        )
        # Scored the way the model would have scored them, so the re-score has
        # criteria to work from. `_rescore_case_members` leaves an unassessed
        # member alone rather than inventing five numbers for it, and a
        # neighbour with no assessment would quietly opt out of the assertion
        # this file is making.
        _score_like_the_model(agent_world, ticket_id)
        made.append((unit_id, ticket_id))
    return made


def _score_like_the_model(agent_world, ticket_id) -> None:
    with agent_world.session_factory() as db:
        RiskAssessmentService(db).record(
            db.get(Ticket, ticket_id),
            criteria=RiskCriterionScores(
                human_safety=1, property_spread=1, essential_function=1,
                affected_scope=0, deterioration_speed=1,
            ),
            source=RiskAssessmentSource.AI_ANALYSIS,
        )
        db.commit()


def _cases_in_series(agent_world) -> list[IncidentCase]:
    with agent_world.session_factory() as db:
        rows = list(db.scalars(select(IncidentCase).order_by(IncidentCase.sequence_no)))
        for row in rows:
            db.expunge(row)
        return rows


def _members(agent_world, case_id) -> set:
    with agent_world.session_factory() as db:
        return set(
            db.scalars(select(IncidentCaseMember.ticket_id).where(IncidentCaseMember.case_id == case_id))
        )


def _current_assessment(agent_world, ticket_id) -> TicketRiskAssessment:
    with agent_world.session_factory() as db:
        row = db.scalars(
            select(TicketRiskAssessment)
            .where(TicketRiskAssessment.ticket_id == ticket_id)
            .order_by(TicketRiskAssessment.revision_no.desc())
            .limit(1)
        ).one()
        db.expunge(row)
        return row


def _group_six(agent_world):
    """Five neighbours plus the reporter: one case of five, one of one."""
    neighbours = _neighbours(agent_world, 5)
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.damp_c,
        unit_id=agent_world.unit_c,
        description="Tường nhà tắm bị thấm, loang rộng dần.",
    )
    llm = ScriptedLLM(
        [classification(**DAMP)],
        grouping=GroupingProposal(
            grouped=True,
            related_ticket_ids=[str(item[1]) for item in neighbours],
            reason="Cùng trục tường, cùng tầng, thấm trong cùng khoảng thời gian.",
        ),
    )
    run_analysis(ticket_id, llm=llm)
    assert agent_world.latest_run(ticket_id).grouping_status == GROUPING_GROUPED
    return ticket_id, [item[1] for item in neighbours]


def test_six_apartments_become_a_case_of_five_and_a_case_of_one(agent_world):
    _group_six(agent_world)

    first, second = _cases_in_series(agent_world)
    assert len(_members(agent_world, first.id)) == 5
    assert len(_members(agent_world, second.id)) == 1
    assert [first.sequence_no, second.sequence_no] == [1, 2]
    assert first.series_id == second.series_id


def test_the_filled_case_records_the_five_apartments_it_actually_holds(agent_world):
    """The regression. `density_value` used to stay at 1 on this case."""
    _group_six(agent_world)

    first, second = _cases_in_series(agent_world)
    assert first.density_value == 5
    assert second.density_value == 1


def test_scope_is_never_summed_across_the_series(agent_world):
    """Two cases in one series are two counts, not one count of six."""
    _group_six(agent_world)

    first, second = _cases_in_series(agent_world)
    assert first.density_value + second.density_value == 6
    assert max(first.density_value, second.density_value) == 5


def test_every_member_of_the_filled_case_is_re_scored_to_the_confirmed_count(agent_world):
    """Five members, five re-scores -- not one for whoever triggered it."""
    _group_six(agent_world)

    first, _second = _cases_in_series(agent_world)
    for ticket_id in _members(agent_world, first.id):
        current = _current_assessment(agent_world, ticket_id)
        assert current.confirmed_affected_unit_count == 5
        # clamp(5 - 1, 0, 4).
        assert current.backend_scope_score == 4
        assert current.effective_scope_score == 4
        assert current.risk_score == BASE_SCORE + 4 * SCOPE_STEP
        assert current.final_priority is Priority.P3


def test_the_overflow_member_is_scored_as_the_one_apartment_it_is(agent_world):
    """The successor case has one member, so its scope is the floor of the scale."""
    _group_six(agent_world)

    _first, second = _cases_in_series(agent_world)
    (ticket_id,) = _members(agent_world, second.id)
    current = _current_assessment(agent_world, ticket_id)
    assert current.confirmed_affected_unit_count == 1
    assert current.backend_scope_score == 0
    assert current.risk_score == BASE_SCORE


def test_the_analysis_run_reports_a_density_per_case(agent_world):
    """One `density` field described one case while the database held two."""
    ticket_id, _neighbour_ids = _group_six(agent_world)

    grouping = agent_world.latest_run(ticket_id).grouping
    first, second = _cases_in_series(agent_world)
    assert grouping["case_densities"] == {str(first.id): 5, str(second.id): 1}
    # The single `density` still describes the reporter's own case, which is the
    # one `case_id` names.
    assert grouping["case_id"] == str(first.id)
    assert grouping["density"] == 5
