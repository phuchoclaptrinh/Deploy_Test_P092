"""Every ordinary management action, refused while the P3 gate is open.

`classification_status = MANUAL_REVIEW` is where two different things wait: a
report the analysis could not classify, and one it classified as an emergency.
Every guard written as "is it in manual review?" therefore lets a coordinator
resolve, reject or link an emergency through the generic form -- recording no
decision, no reviewer and no reason, and skipping the gate entirely.

These tests are the list of doors that used to be open. Each one asserts the
same two things: the call is refused with `409 P3_REVIEW_REQUIRED`, and the
ticket is unchanged afterwards. A guard that raises but has already mutated
something would pass the first assertion on its own.
"""

from __future__ import annotations

import pytest

from src.models.agent_schemas import P3Decision, P3ReviewStatus
from src.models.api.coordinator import (
    ClassificationOverrideRequest,
    ManualReviewResolveRequest,
)
from src.models.api.errors import P3_REVIEW_REQUIRED, DomainError
from src.models.enums import ClassificationStatus, Priority, ResolutionSource, TicketStatus
from src.services.agent_backend_service import AgentBackendService
from src.services.assignment_service import AssignmentService
from src.services.coordinator_service import CoordinatorService
from src.services.duplicate_workflow_service import DuplicateWorkflowService
from src.services.p3_review_guard import p3_review_is_pending
from tests.test_agents.conftest import ScriptedLLM, classification

#: 40 base + 10 at the entrance gate + 20 for HIGH = 70, which is P3.
P3_BY_SCORE = dict(category="An ninh / An toàn", text_category="An ninh / An toàn", severity="HIGH")


def _gated_ticket(agent_world):
    """A ticket parked at the emergency gate, in the exact state the bug
    report describes."""
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_a,
        description="Cổng chính có sự cố an ninh nghiêm trọng.",
    )
    from src.agents.service import run_analysis

    run_analysis(ticket_id, llm=ScriptedLLM([classification(**P3_BY_SCORE)]))

    ticket = agent_world.ticket(ticket_id)
    run = agent_world.latest_run(ticket_id)
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW
    assert run.p3_review_status == P3ReviewStatus.PENDING.value
    assert run.grouping_status == "WAITING_P3_MANAGEMENT_REVIEW"
    return ticket_id


def _assert_gate_refusal(excinfo, agent_world, ticket_id, before):
    """One refusal, and nothing moved."""
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == P3_REVIEW_REQUIRED
    after = agent_world.ticket(ticket_id)
    assert after.classification_status is before.classification_status
    assert after.status is before.status
    assert after.category_id == before.category_id
    assert after.priority == before.priority
    assert after.duplicate_of_ticket_id == before.duplicate_of_ticket_id
    assert after.version == before.version
    assert agent_world.latest_run(ticket_id).p3_review_status == P3ReviewStatus.PENDING.value


# ---------------------------------------------------------------------------
# The doors.
# ---------------------------------------------------------------------------


def test_generic_manual_review_resolve_is_refused(agent_world):
    ticket_id = _gated_ticket(agent_world)
    before = agent_world.ticket(ticket_id)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        CoordinatorService(db).resolve_manual_review(
            agent_world.coordinator,
            ticket_id,
            ManualReviewResolveRequest(
                category_id=agent_world.noise,
                resolution_source=ResolutionSource.TEXT,
                reason="Đổi sang tiếng ồn.",
            ),
        )

    _assert_gate_refusal(excinfo, agent_world, ticket_id, before)


def test_generic_manual_review_reject_is_refused(agent_world):
    """The most destructive of the generic actions: it would close an
    emergency as invalid."""
    ticket_id = _gated_ticket(agent_world)
    before = agent_world.ticket(ticket_id)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).manual_review_reject(
            agent_world.coordinator, ticket_id, "Không đủ thông tin."
        )

    _assert_gate_refusal(excinfo, agent_world, ticket_id, before)


def test_manual_duplicate_link_is_refused(agent_world):
    ticket_id = _gated_ticket(agent_world)
    master_id = agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_b,
        reporter=agent_world.neighbour,
        description="Sự cố an ninh tại cổng.",
        category_id=agent_world.security,
    )
    before = agent_world.ticket(ticket_id)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        DuplicateWorkflowService(db).link_duplicate(
            agent_world.coordinator, ticket_id, master_id, "Trùng phản ánh."
        )

    _assert_gate_refusal(excinfo, agent_world, ticket_id, before)
    assert agent_world.ticket(master_id).status is TicketStatus.NEW


def test_approve_is_refused(agent_world):
    ticket_id = _gated_ticket(agent_world)
    before = agent_world.ticket(ticket_id)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        CoordinatorService(db).approve(agent_world.coordinator, ticket_id)

    _assert_gate_refusal(excinfo, agent_world, ticket_id, before)


def test_classification_override_is_refused(agent_world):
    """Overriding the priority here would answer the gate's question through a
    door that records no reviewer and no reason."""
    ticket_id = _gated_ticket(agent_world)
    before = agent_world.ticket(ticket_id)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        CoordinatorService(db).override_classification(
            agent_world.coordinator,
            ticket_id,
            ClassificationOverrideRequest(priority=Priority.P1, reason="Hạ mức."),
        )

    _assert_gate_refusal(excinfo, agent_world, ticket_id, before)


def test_request_information_is_refused(agent_world):
    """Retired, but still routable by an old client."""
    ticket_id = _gated_ticket(agent_world)
    before = agent_world.ticket(ticket_id)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        CoordinatorService(db).request_information(
            agent_world.coordinator, ticket_id, "Bạn gửi thêm ảnh nhé."
        )

    _assert_gate_refusal(excinfo, agent_world, ticket_id, before)


def test_assignment_is_refused(agent_world):
    """Unreachable through the normal lifecycle, because a gated ticket is
    never APPROVED. Asserted anyway: that is currently true because two other
    rules happen to agree, and this makes it true on its own."""
    ticket_id = _gated_ticket(agent_world)
    before = agent_world.ticket(ticket_id)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AssignmentService(db).assign(agent_world.coordinator, ticket_id, agent_world.coordinator)

    _assert_gate_refusal(excinfo, agent_world, ticket_id, before)


def test_the_duplicate_uncertain_decision_is_refused(agent_world):
    """The two gates never overlap: a P3 ticket never reached the duplicate
    stage, so there is no uncertain verdict to settle."""
    ticket_id = _gated_ticket(agent_world)
    before = agent_world.ticket(ticket_id)

    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        AgentBackendService(db).resolve_duplicate_uncertain(
            agent_world.coordinator, ticket_id, is_duplicate=False, reason="Không trùng."
        )

    assert excinfo.value.status_code == 409
    after = agent_world.ticket(ticket_id)
    assert after.classification_status is before.classification_status
    assert agent_world.latest_run(ticket_id).p3_review_status == P3ReviewStatus.PENDING.value


# ---------------------------------------------------------------------------
# The one door that is open.
# ---------------------------------------------------------------------------


def test_the_p3_review_itself_is_allowed(agent_world):
    ticket_id = _gated_ticket(agent_world)

    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_p3_review(
            agent_world.coordinator, ticket_id, decision=P3Decision.CONFIRM_P3
        )

    with agent_world.session_factory() as db:
        assert p3_review_is_pending(db, ticket_id) is False
    assert agent_world.latest_run(ticket_id).p3_review_status == P3ReviewStatus.CONFIRMED.value


def test_the_generic_actions_reopen_once_the_gate_is_cleared(agent_world):
    """The guard is a gate, not a permanent lock. A downgrade clears it and the
    ordinary coordinator actions work again."""
    ticket_id = _gated_ticket(agent_world)

    with agent_world.session_factory() as db:
        AgentBackendService(db).resolve_p3_review(
            agent_world.coordinator,
            ticket_id,
            decision=P3Decision.DOWNGRADE_SEVERITY,
            priority=Priority.P2,
            reason="Đã kiểm tra, không nguy hiểm tức thời.",
        )

    with agent_world.session_factory() as db:
        assert p3_review_is_pending(db, ticket_id) is False
    # `approve` still refuses, but on its own terms now -- the ticket has not
    # been published yet -- rather than on the gate's.
    with agent_world.session_factory() as db, pytest.raises(DomainError) as excinfo:
        CoordinatorService(db).approve(agent_world.coordinator, ticket_id)
    assert excinfo.value.code != P3_REVIEW_REQUIRED


def test_an_unclassifiable_manual_review_ticket_is_untouched_by_the_guard(agent_world):
    """The other occupant of MANUAL_REVIEW. Narrowing the generic form to
    non-P3 tickets must not have narrowed it to nothing."""
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.bath_a,
        unit_id=agent_world.unit_a,
        description="asdfgh",
    )
    from src.agents.service import run_analysis

    run_analysis(
        ticket_id,
        llm=ScriptedLLM(
            [
                classification(
                    category=None,
                    text_category=None,
                    severity=None,
                    understandable=False,
                    incident_facts=[],
                    ai_reason="Mô tả không đọc được.",
                )
            ]
        ),
    )
    # Force the generic manual-review state the coordinator form is for.
    with agent_world.session_factory() as db:
        from src.database.models.ticket import Ticket

        ticket = db.get(Ticket, ticket_id)
        ticket.classification_status = ClassificationStatus.MANUAL_REVIEW
        ticket.status = TicketStatus.NEW
        ticket.severity = None
        db.commit()

    with agent_world.session_factory() as db:
        assert p3_review_is_pending(db, ticket_id) is False
        resolved = CoordinatorService(db).resolve_manual_review(
            agent_world.coordinator,
            ticket_id,
            ManualReviewResolveRequest(
                category_id=agent_world.noise,
                # OTHER, because the run recorded no text category to match
                # against -- the coordinator is deciding this one themselves.
                resolution_source=ResolutionSource.OTHER,
                reason="Là phản ánh tiếng ồn.",
                severity="MEDIUM",
            ),
        )

    assert resolved.classification_status is ClassificationStatus.RESOLVED
    assert resolved.category_id == agent_world.noise


# ---------------------------------------------------------------------------
# What the UI is told it may offer.
# ---------------------------------------------------------------------------


def test_available_actions_offer_only_the_p3_review(agent_world):
    """A hint, not the authorization -- but a hint that offered the generic
    form would send a coordinator into an action that can only 409."""
    from src.api.routes.coordinator_tickets import _available_actions

    ticket_id = _gated_ticket(agent_world)
    with agent_world.session_factory() as db:
        from src.database.models.ticket import Ticket

        actions = _available_actions(db.get(Ticket, ticket_id))

    assert actions == ["REVIEW_P3"]
    assert not {
        "RESOLVE_MANUAL_REVIEW",
        "REJECT_MANUAL_REVIEW",
        "APPROVE",
        "OVERRIDE_CLASSIFICATION",
        "ASSIGN",
    } & set(actions)


def test_a_duplicate_uncertain_ticket_keeps_its_own_actions(agent_world):
    """The two waiting states are not merged. An uncertain duplicate still
    gets the generic manual-review actions alongside its duplicate panel."""
    from src.agents.service import run_analysis
    from src.api.routes.coordinator_tickets import _available_actions
    from tests.test_agents.conftest import duplicate_judgement

    agent_world.make_ticket(
        location_id=agent_world.bath_a,
        unit_id=agent_world.unit_a,
        reporter=agent_world.neighbour,
        description="Trần nhà tắm bị rỉ nước.",
        category_id=agent_world.water,
    )
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    run_analysis(
        ticket_id,
        llm=ScriptedLLM(
            [classification()],
            judgements=[duplicate_judgement(verdict="UNCERTAIN", reason="Chưa đủ chắc chắn.")],
        ),
    )

    with agent_world.session_factory() as db:
        from src.database.models.ticket import Ticket

        ticket = db.get(Ticket, ticket_id)
        assert _available_actions(ticket) == ["RESOLVE_MANUAL_REVIEW", "REJECT_MANUAL_REVIEW"]
        assert p3_review_is_pending(db, ticket_id) is False


def test_the_resident_cannot_cancel_an_emergency_under_review(agent_world):
    """Not a management action, but it would still end a ticket a coordinator
    is mid-decision on. The resident UI hides the button; this is the rule."""
    from src.services.ticket_service import TicketService

    ticket_id = _gated_ticket(agent_world)
    before = agent_world.ticket(ticket_id)

    with agent_world.session_factory() as db:
        from src.database.models.resident_profile import ResidentProfile

        profile = db.query(ResidentProfile).filter(ResidentProfile.unit_id == agent_world.unit_a).first()
        with pytest.raises(DomainError) as excinfo:
            TicketService(db).cancel_ticket(agent_world.resident, profile, ticket_id)

    _assert_gate_refusal(excinfo, agent_world, ticket_id, before)
