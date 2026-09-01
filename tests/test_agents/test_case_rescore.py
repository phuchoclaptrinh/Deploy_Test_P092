"""Grouping changes what the backend can count, so it changes every score.

`docs/risk_scoring_v2.md` §4 makes the scope criterion the one place where the
backend overrules the Agent: an Agent looking at one report can only estimate how
far a problem reaches, and a closed case can count it. §7.2 follows from that —
when a case gains a member, *every* member's confirmed count moved, so every
member is re-scored, not only the ticket that triggered the grouping.

§7.3 is the awkward consequence: a re-score can push a member to P5, and a P5 is
manual-only, so it has to leave the case it just joined. The order matters and is
the subject of the last part of this file.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select

from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.ticket import Ticket
from src.database.models.ticket_risk_assessment import TicketRiskAssessment
from src.domain.risk_scoring import backend_scope_score
from src.models.enums import Priority, RiskAssessmentSource
from src.services.agent_result_service import AgentResultService


@pytest.fixture
def cases(agent_world):
    """A helper bound to one agent world, so the tests read as scenarios."""
    return _CaseWorld(agent_world)


class _CaseWorld:
    def __init__(self, world):
        self.world = world

    def ticket(self, *, unit_id: UUID, location_id: UUID, criteria: dict[str, int], blockers=()) -> UUID:
        """A scored ticket, without going through the model."""
        from src.domain.risk_scoring import RiskCriterionScores
        from src.services.risk_assessment_service import RiskAssessmentService

        ticket_id = self.world.make_ticket(
            location_id=location_id, unit_id=unit_id, category_id=self.world.wall_damp
        )
        with self.world.session_factory() as db:
            ticket = db.get(Ticket, ticket_id)
            RiskAssessmentService(db).record(
                ticket,
                criteria=RiskCriterionScores(**criteria),
                source=RiskAssessmentSource.AI_ANALYSIS,
                blocker_codes=list(blockers),
                evidence={"blockers": ["Bằng chứng."]} if blockers else {},
            )
            db.commit()
        return ticket_id

    def group(self, case_ticket_ids: list[UUID]) -> IncidentCase:
        """Put the given tickets in one case, then run the v2 re-score."""
        with self.world.session_factory() as db:
            case = IncidentCase(
                category_id=self.world.wall_damp,
                window_start=_now(db),
                window_end=_now(db),
                density_value=1,
            )
            db.add(case)
            db.flush()
            case.series_id = case.id
            for ticket_id in case_ticket_ids:
                ticket = db.get(Ticket, ticket_id)
                db.add(
                    IncidentCaseMember(
                        case_id=case.id, ticket_id=ticket.id, source_unit_id=ticket.source_unit_id
                    )
                )
            db.flush()

            service = AgentResultService(db)
            service._recompute_density(case)
            escalated = service._rescore_case_members(case, service._case_member_tickets(case))
            if escalated:
                service._detach_members(escalated, reason="Rescore sau gộp cụm đưa phản ánh lên P5")
            db.commit()
            db.refresh(case)
            db.expunge(case)
            return case

    def read(self, ticket_id: UUID) -> Ticket:
        with self.world.session_factory() as db:
            ticket = db.get(Ticket, ticket_id)
            db.expunge(ticket)
            return ticket

    def revisions(self, ticket_id: UUID) -> list[TicketRiskAssessment]:
        with self.world.session_factory() as db:
            rows = list(
                db.scalars(
                    select(TicketRiskAssessment)
                    .where(TicketRiskAssessment.ticket_id == ticket_id)
                    .order_by(TicketRiskAssessment.revision_no)
                )
            )
            for row in rows:
                db.expunge(row)
            return rows

    def members(self, case_id: UUID) -> set[UUID]:
        with self.world.session_factory() as db:
            return set(
                db.scalars(select(IncidentCaseMember.ticket_id).where(IncidentCaseMember.case_id == case_id))
            )

    def case(self, case_id: UUID) -> IncidentCase:
        with self.world.session_factory() as db:
            case = db.get(IncidentCase, case_id)
            db.expunge(case)
            return case


def _now(db):
    from datetime import UTC, datetime

    return datetime.now(UTC)


#: 0 + 5 + 35 + scope + 0. At scope 0 that is 40 (P3); at scope 4 it is 60 (P4).
SPREADING = {
    "human_safety": 0,
    "property_spread": 4,
    "essential_function": 4,
    "affected_scope": 0,
    "deterioration_speed": 0,
}
#: 35 + 0 + 35 + scope + 0. At scope 0 that is 70 (P4); at scope 2 it is 80 (P5).
NEARLY_EMERGENCY = {
    "human_safety": 4,
    "property_spread": 0,
    "essential_function": 4,
    "affected_scope": 0,
    "deterioration_speed": 0,
}


# ---------------------------------------------------------------------------
# §7.2 -- grouping re-scores every member.
# ---------------------------------------------------------------------------


def test_a_case_of_two_raises_the_confirmed_scope_of_both_members(cases, agent_world):
    a = cases.ticket(unit_id=agent_world.unit_c, location_id=agent_world.damp_c, criteria=SPREADING)
    b = cases.ticket(unit_id=agent_world.unit_d, location_id=agent_world.damp_d, criteria=SPREADING)

    case = cases.group([a, b])

    assert case.density_value == 2
    for ticket_id in (a, b):
        current = cases.revisions(ticket_id)[-1]
        assert current.source is RiskAssessmentSource.GROUPING_RESCORE
        assert current.confirmed_affected_unit_count == 2
        assert current.backend_scope_score == 1
        assert current.effective_scope_score == 1
        # 40.00 + one step of the 20-point scope weight.
        assert current.risk_score == Decimal("45.00")


def test_the_agents_own_estimate_survives_next_to_the_confirmed_count(cases, agent_world):
    """An estimate that was overruled is the most interesting thing on the row."""
    optimistic = dict(SPREADING, affected_scope=4)
    a = cases.ticket(unit_id=agent_world.unit_c, location_id=agent_world.damp_c, criteria=optimistic)
    b = cases.ticket(unit_id=agent_world.unit_d, location_id=agent_world.damp_d, criteria=SPREADING)

    cases.group([a, b])

    current = cases.revisions(a)[-1]
    assert current.ai_scope_score == 4
    assert current.backend_scope_score == 1
    assert current.effective_scope_score == 1


def test_re_scoring_appends_a_revision_rather_than_editing_the_one_before_it(cases, agent_world):
    a = cases.ticket(unit_id=agent_world.unit_c, location_id=agent_world.damp_c, criteria=SPREADING)
    b = cases.ticket(unit_id=agent_world.unit_d, location_id=agent_world.damp_d, criteria=SPREADING)

    cases.group([a, b])

    history = cases.revisions(a)
    assert [row.revision_no for row in history] == [1, 2]
    assert history[0].source is RiskAssessmentSource.AI_ANALYSIS
    assert history[0].backend_scope_score is None
    assert history[1].supersedes_id == history[0].id


def test_the_ticket_cache_follows_the_newest_revision(cases, agent_world):
    a = cases.ticket(unit_id=agent_world.unit_c, location_id=agent_world.damp_c, criteria=SPREADING)
    b = cases.ticket(unit_id=agent_world.unit_d, location_id=agent_world.damp_d, criteria=SPREADING)

    cases.group([a, b])

    ticket = cases.read(a)
    current = cases.revisions(a)[-1]
    assert ticket.current_risk_assessment_id == current.id
    assert ticket.risk_score == current.risk_score
    assert ticket.priority is current.final_priority


@pytest.mark.parametrize(("units", "expected_scope"), [(1, 0), (2, 1), (3, 2)])
def test_the_confirmed_count_maps_onto_the_documented_scope_scale(cases, agent_world, units, expected_scope):
    assert backend_scope_score(units) == expected_scope


# ---------------------------------------------------------------------------
# §7.3 -- a member the re-score pushes to P5.
# ---------------------------------------------------------------------------


def test_a_member_pushed_to_p5_by_the_case_is_detached_from_it(cases, agent_world):
    a = cases.ticket(unit_id=agent_world.unit_c, location_id=agent_world.damp_c, criteria=NEARLY_EMERGENCY)
    b = cases.ticket(unit_id=agent_world.unit_d, location_id=agent_world.damp_d, criteria=SPREADING)

    case = cases.group([a, b])

    # 75.00 + one scope step = 78.75, still P4... so nothing escalates yet.
    assert cases.read(a).priority is Priority.P4
    assert cases.members(case.id) == {a, b}


def test_the_escalating_member_keeps_p5_after_it_leaves_the_case(cases, agent_world):
    """The loop this exists to break: escalate -> detach -> lose the scope ->
    fall back to P4 -> re-group -> escalate again.

    A P5 that has been recorded is an event that happened, not a function of the
    membership the ticket currently has.
    """
    a = cases.ticket(unit_id=agent_world.unit_c, location_id=agent_world.damp_c, criteria=NEARLY_EMERGENCY)
    b = cases.ticket(unit_id=agent_world.unit_d, location_id=agent_world.damp_d, criteria=NEARLY_EMERGENCY)
    c = cases.ticket(unit_id=agent_world.unit_a, location_id=agent_world.bath_a, criteria=SPREADING)

    case = cases.group([a, b, c])

    # Three apartments -> scope 2 -> 75.00 + 7.50 = 82.50, which is P5.
    assert cases.read(a).priority is Priority.P5
    assert cases.read(b).priority is Priority.P5
    assert a not in cases.members(case.id)
    assert b not in cases.members(case.id)


def test_the_revision_that_escalated_snapshots_the_case_it_was_still_in(cases, agent_world):
    """Written before the detach, because afterwards there is no case to ask."""
    a = cases.ticket(unit_id=agent_world.unit_c, location_id=agent_world.damp_c, criteria=NEARLY_EMERGENCY)
    b = cases.ticket(unit_id=agent_world.unit_d, location_id=agent_world.damp_d, criteria=NEARLY_EMERGENCY)
    c = cases.ticket(unit_id=agent_world.unit_a, location_id=agent_world.bath_a, criteria=SPREADING)

    case = cases.group([a, b, c])

    escalating = next(row for row in cases.revisions(a) if row.final_priority is Priority.P5)
    assert escalating.case_id_snapshot == case.id
    assert escalating.case_density_snapshot == 3
    assert escalating.confirmed_affected_unit_count == 3


def test_the_case_keeps_working_when_one_member_becomes_an_emergency(cases, agent_world):
    """One member escalating is not a reason to stop work on the rest."""
    a = cases.ticket(unit_id=agent_world.unit_c, location_id=agent_world.damp_c, criteria=NEARLY_EMERGENCY)
    b = cases.ticket(unit_id=agent_world.unit_d, location_id=agent_world.damp_d, criteria=SPREADING)
    c = cases.ticket(unit_id=agent_world.unit_a, location_id=agent_world.bath_a, criteria=SPREADING)

    case = cases.group([a, b, c])

    remaining = cases.members(case.id)
    assert a not in remaining
    assert remaining == {b, c}
    assert cases.case(case.id).status == "OPEN"
    # And the survivors are re-scored on the count that is actually left.
    for ticket_id in remaining:
        assert cases.revisions(ticket_id)[-1].confirmed_affected_unit_count == 2
        assert cases.read(ticket_id).priority is not Priority.P5


def test_a_case_that_loses_its_last_member_is_closed_with_a_reason(cases, agent_world):
    a = cases.ticket(unit_id=agent_world.unit_c, location_id=agent_world.damp_c, criteria=NEARLY_EMERGENCY)
    b = cases.ticket(unit_id=agent_world.unit_d, location_id=agent_world.damp_d, criteria=NEARLY_EMERGENCY)
    c = cases.ticket(unit_id=agent_world.unit_a, location_id=agent_world.bath_a, criteria=NEARLY_EMERGENCY)

    case = cases.group([a, b, c])

    assert cases.members(case.id) == set()
    closed = cases.case(case.id)
    assert closed.status == "CLOSED"
    assert closed.closed_at is not None
    assert "P5" in (closed.closed_reason or "")


def test_a_closed_case_is_kept_rather_than_deleted(cases, agent_world):
    """It is the record of why three tickets were scored the way they were."""
    a = cases.ticket(unit_id=agent_world.unit_c, location_id=agent_world.damp_c, criteria=NEARLY_EMERGENCY)
    b = cases.ticket(unit_id=agent_world.unit_d, location_id=agent_world.damp_d, criteria=NEARLY_EMERGENCY)
    c = cases.ticket(unit_id=agent_world.unit_a, location_id=agent_world.bath_a, criteria=NEARLY_EMERGENCY)

    case = cases.group([a, b, c])

    assert cases.case(case.id) is not None
    for ticket_id in (a, b, c):
        escalating = next(row for row in cases.revisions(ticket_id) if row.case_id_snapshot is not None)
        assert escalating.case_id_snapshot == case.id


# ---------------------------------------------------------------------------
# §4.2 -- one apartment is one unit, however many reports it sends.
# ---------------------------------------------------------------------------


def test_two_reports_from_one_apartment_are_one_confirmed_unit(cases, agent_world):
    a = cases.ticket(unit_id=agent_world.unit_c, location_id=agent_world.damp_c, criteria=SPREADING)
    b = cases.ticket(unit_id=agent_world.unit_c, location_id=agent_world.damp_c, criteria=SPREADING)

    case = cases.group([a, b])

    assert case.density_value == 1
    assert cases.revisions(a)[-1].confirmed_affected_unit_count == 1
    assert cases.revisions(a)[-1].backend_scope_score == 0
