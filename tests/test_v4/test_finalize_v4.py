"""`finalize_v4()` — contract §1.7 and §3.

The theme running through these tests is that Backend does not take the Agent's
word for anything: the master must have come from this session's own search,
Density is recounted from distinct apartments, the scoring rule version is
pinned, and a session that reaches here always stops being RUNNING.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.audit_log import AuditLog
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.notification import Notification
from src.database.models.resident_profile import ResidentProfile
from src.database.models.scoring_rule_version import ScoringRuleVersion
from src.database.models.ticket_relation import TicketRelation
from src.database.models.user_profile import UserProfile
from src.models.agent_schemas import AgentSeveritySource
from src.models.agent_schemas_v4 import (
    AGENT_MODEL_VERSION_V4,
    AgentAnalysisResultV4,
    AgentExitReasonV4,
    AgentGroupingResultV4,
    AgentTicketRelation,
    AgentToolUsageV4,
)
from src.models.api.errors import ACTIVE_ASSIGNMENT_EXISTS, DomainError
from src.models.enums import (
    AssignmentStatus,
    ClassificationStatus,
    Priority,
    Severity,
    TicketStatus,
    UserRole,
)
from src.services.agent_backend_service import AgentBackendService
from src.services.agent_result_v4_service import (
    ANALYSIS_ALREADY_FINALIZED,
    CONTRACT_VALIDATION_ERROR,
    DUPLICATE_CANDIDATE_STALE,
)
from tests.test_v4.factories import attach_image, build_world, make_assignment, make_ticket


class _StorageStub:
    def create_signed_download_url(self, _path):
        return "https://example.invalid/signed"


def _service(db_session):
    return AgentBackendService(db_session, _StorageStub())


def _v4_session(service, ticket):
    return service.start_session(ticket.id, model_version=AGENT_MODEL_VERSION_V4)


def _usage(session, **overrides):
    tool_names = {call.tool_name for call in session.tool_calls}
    values = {
        "total_tool_calls": session.total_tool_calls,
        "ask_resident_rounds": session.ask_resident_rounds,
        "ask_resident_elapsed_seconds": session.ask_resident_elapsed_seconds,
        "search_related_tickets_called": "search_related_tickets" in tool_names,
        "propose_case_grouping_called": "propose_case_grouping" in tool_names,
    }
    values.update(overrides)
    return AgentToolUsageV4(**values)


def _result(session, ticket, **overrides):
    payload = {
        "ticket_id": ticket.id,
        "analysis_session_id": session.id,
        "exit_reason": AgentExitReasonV4.ANALYSIS_COMPLETE,
        "text_categories": [],
        "red_flag_text": False,
        "image_categories": None,
        "red_flag_signal": None,
        "is_relevant": None,
        "severity": Severity.MEDIUM,
        "severity_source": AgentSeveritySource.TEXT,
        "is_confident": True,
        "tool_usage": _usage(session),
        "category_catalog_version": session.category_catalog_version,
        "model_version": AGENT_MODEL_VERSION_V4,
        "analyzed_at": datetime.now(UTC),
    }
    payload.update(overrides)
    return AgentAnalysisResultV4(**payload)


# ---------------------------------------------------------------------------
# ANALYSIS_COMPLETE
# ---------------------------------------------------------------------------


def test_analysis_complete_scores_the_ticket_and_pins_a_rule_version(db_session):
    world = build_world(db_session)
    rule = ScoringRuleVersion(
        version="test-v1",
        is_active=True,
        config={
            "category_base": {member.value: 10 for member in __import__("src.models.enums", fromlist=["Category"]).Category},
            "severity": {"LOW": 0, "MEDIUM": 10, "HIGH": 20},
            "density": {"tier_2_3": 15, "tier_4_plus": 30},
            "thresholds": {"p1_upper_exclusive": 30, "p3_lower_inclusive": 60},
        },
    )
    db_session.add(rule)
    db_session.commit()

    ticket = make_ticket(world, location=world.elevator_a)
    service = _service(db_session)
    session = _v4_session(service, ticket)

    run = service.finalize_v4(_result(session, ticket, text_categories=[world.elevator.id]))

    db_session.refresh(ticket)
    db_session.refresh(session)
    assert run.contract_version == "v4"
    assert run.exit_reason == "ANALYSIS_COMPLETE"
    # §7.10: the run records which rule set produced the score.
    assert run.rule_version_id == rule.id
    assert run.score_total is not None
    assert ticket.category_id == world.elevator.id
    assert ticket.classification_status is ClassificationStatus.RESOLVED
    assert ticket.priority is not None
    assert ticket.sla_due_at is not None
    # The invariant this whole module exists for.
    assert session.status == "COMPLETED"


def test_a_later_scoring_rule_change_does_not_rewrite_a_finished_run(db_session):
    """§7.10 / §12 scenario 19."""
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a)
    service = _service(db_session)
    session = _v4_session(service, ticket)
    run = service.finalize_v4(_result(session, ticket, text_categories=[world.elevator.id]))

    pinned = run.rule_version_id
    before = run.score_total

    db_session.add(ScoringRuleVersion(version="test-v2", is_active=False, config={}))
    db_session.commit()
    db_session.refresh(run)

    assert run.rule_version_id == pinned
    assert run.score_total == before


def test_conflicting_category_sources_go_to_manual_review(db_session):
    """§3.3 / §12 scenario 20: Backend decides, and the Agent never says
    CATEGORY_MISMATCH."""
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a)
    attach_image(db_session, ticket)
    service = _service(db_session)
    session = _v4_session(service, ticket)

    run = service.finalize_v4(
        _result(
            session,
            ticket,
            text_categories=[world.elevator.id],
            image_categories=[world.water.id],
            red_flag_signal=False,
            is_relevant=True,
        )
    )

    db_session.refresh(ticket)
    assert run.exit_reason == "ANALYSIS_COMPLETE"
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW
    assert ticket.category_id is None


def test_text_only_ticket_may_not_report_image_analysis(db_session):
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a)
    service = _service(db_session)
    session = _v4_session(service, ticket)

    with pytest.raises(DomainError) as exc:
        service.finalize_v4(
            _result(
                session,
                ticket,
                text_categories=[world.elevator.id],
                image_categories=[world.elevator.id],
                red_flag_signal=False,
                is_relevant=True,
            )
        )
    assert exc.value.code == CONTRACT_VALIDATION_ERROR


# ---------------------------------------------------------------------------
# DUPLICATE_EXISTING
# ---------------------------------------------------------------------------


def _searched_master(world, service, session, ticket):
    """Run the DUPLICATE search so the master is in this session's tool log."""
    service.search_related_tickets(
        session.id,
        ticket_id=ticket.id,
        category_ids=[world.elevator.id],
        purpose="DUPLICATE",
    )


def test_duplicate_existing_links_atomically_and_creates_no_assignment(db_session):
    """§3.1 / §12 scenario 2."""
    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
        priority=Priority.P2,
    )
    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service = _service(db_session)
    session = _v4_session(service, ticket)
    _searched_master(world, service, session, ticket)
    db_session.refresh(session)

    run = service.finalize_v4(
        _result(
            session,
            ticket,
            exit_reason=AgentExitReasonV4.DUPLICATE_EXISTING,
            text_categories=[world.elevator.id],
            is_confident=True,
            confidence_notes="Cùng thang máy A và cùng hiện tượng.",
            duplicate=AgentTicketRelation(master_ticket_id=master.id, reason="Cùng thang máy A, cùng hiện tượng."),
            tool_usage=_usage(session),
        )
    )

    db_session.refresh(ticket)
    assert ticket.status is TicketStatus.LINKED_DUPLICATE
    assert ticket.classification_status is ClassificationStatus.RESOLVED
    assert ticket.duplicate_of_ticket_id == master.id
    assert ticket.duplicate_linked_at is not None
    assert ticket.duplicate_reason
    assert ticket.duplicate_analysis_run_id == run.id
    # §3.1: no Priority, no score, no SLA, no assignment.
    assert ticket.priority is None
    assert ticket.score_total is None
    assert ticket.sla_due_at is None
    assert not ticket.assignments
    assert run.duplicate["master_ticket_id"] == str(master.id)

    audit = db_session.scalars(select(AuditLog).where(AuditLog.action == "TICKET_LINKED_AS_DUPLICATE")).all()
    assert len(audit) == 1
    assert audit[0].actor_role == "SYSTEM"

    notes = db_session.scalars(
        select(Notification).where(Notification.notification_type == "TICKET_LINKED_AS_DUPLICATE")
    ).all()
    assert notes
    payload = notes[0].payload
    assert payload["master_reference_code"].startswith("PA-")
    # §3.1: reduced master data only — never the master's reporter or unit.
    assert str(master.reporter_user_id) not in repr(payload)
    assert str(master.source_unit_id) not in repr(payload)


def test_a_linked_duplicate_is_published_and_carries_no_appeal_surface(db_session):
    """Detection survives the removal of the resident appeal.

    Once the Agent links the report, classification is finished, so the report
    leaves the private AI phase: the rest of the apartment sees it, Building
    Management sees it, and both payloads offer a plain informational link with
    no "my incident is different" action and no dispute field anywhere.
    """
    from src.api.routes.coordinator_tickets import coordinator_ticket_response
    from src.api.routes.tickets import resident_ticket_response
    from src.repositories.ticket_repository import TicketRepository

    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
        priority=Priority.P2,
        description="Mô tả riêng của căn hộ khác.",
    )
    reporter = world.resident(0)
    housemate_user = UserProfile(user_id=uuid4(), role=UserRole.RESIDENT, full_name="Người nhà")
    housemate = ResidentProfile(user=housemate_user, unit=reporter.unit, is_primary=False)
    db_session.add_all([housemate_user, housemate])
    db_session.commit()

    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service = _service(db_session)
    session = _v4_session(service, ticket)
    _searched_master(world, service, session, ticket)
    db_session.refresh(session)

    service.finalize_v4(
        _result(
            session,
            ticket,
            exit_reason=AgentExitReasonV4.DUPLICATE_EXISTING,
            text_categories=[world.elevator.id],
            is_confident=True,
            confidence_notes="Cùng thang máy A và cùng hiện tượng.",
            duplicate=AgentTicketRelation(master_ticket_id=master.id, reason="Cùng thang máy A, cùng hiện tượng."),
            tool_usage=_usage(session),
        )
    )
    db_session.refresh(ticket)
    assert ticket.status is TicketStatus.LINKED_DUPLICATE
    assert ticket.classification_status is ClassificationStatus.RESOLVED

    tickets = TicketRepository(db_session)
    # The housemate did not send it, but classification has finished.
    items, total = tickets.list_resident_tickets(reporter.unit_id, housemate.user_id, 1, 20)
    assert total == 1
    assert [row.id for row in items] == [ticket.id]

    visible = tickets.get_resident_visible_ticket(reporter.unit_id, housemate.user_id, ticket.id)
    assert visible is not None

    resident_payload = resident_ticket_response(visible, housemate.user_id).model_dump()
    assert resident_payload["duplicate_of_ticket_id"] == master.id
    assert resident_payload["duplicate_master_display_code"].startswith("PA-")
    assert resident_payload["available_actions"] == []
    assert "duplicate_dispute_status" not in resident_payload
    blob = repr(resident_payload)
    assert "DISPUTE_DUPLICATE" not in blob
    # Reduced master data only: no identity, no description from the other unit.
    assert str(master.reporter_user_id) not in blob
    assert str(master.source_unit_id) not in blob
    assert "Mô tả riêng của căn hộ khác." not in blob

    coordinator_payload = coordinator_ticket_response(
        tickets.get_coordinator_visible_ticket(ticket.id)
    ).model_dump()
    assert coordinator_payload["duplicate_of_ticket_id"] == master.id
    assert "duplicate_dispute_status" not in coordinator_payload
    assert "DISPUTE_DUPLICATE" not in repr(coordinator_payload)

    # The duplicate-result notification still reaches the reporting apartment.
    notes = db_session.scalars(
        select(Notification).where(Notification.notification_type == "TICKET_LINKED_AS_DUPLICATE")
    ).all()
    assert {note.recipient_user_id for note in notes} == {reporter.user_id, housemate.user_id}


def test_duplicate_master_must_come_from_this_sessions_search(db_session):
    """§1.5 item 3: a master the Agent invented is refused."""
    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
    )
    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service = _service(db_session)
    session = _v4_session(service, ticket)
    # No search at all.

    with pytest.raises(DomainError) as exc:
        service.finalize_v4(
            _result(
                session,
                ticket,
                exit_reason=AgentExitReasonV4.DUPLICATE_EXISTING,
                text_categories=[world.elevator.id],
                duplicate=AgentTicketRelation(master_ticket_id=master.id, reason="Không có bằng chứng."),
            )
        )
    assert exc.value.code == CONTRACT_VALIDATION_ERROR
    db_session.rollback()
    db_session.refresh(ticket)
    assert ticket.status is TicketStatus.NEW


def test_duplicate_is_refused_when_the_master_closes_first(db_session):
    """§3.1: `409 DUPLICATE_CANDIDATE_STALE`, and nothing written."""
    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
    )
    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service = _service(db_session)
    session = _v4_session(service, ticket)
    _searched_master(world, service, session, ticket)
    db_session.refresh(session)

    master.status = TicketStatus.COMPLETED
    db_session.commit()

    with pytest.raises(DomainError) as exc:
        service.finalize_v4(
            _result(
                session,
                ticket,
                exit_reason=AgentExitReasonV4.DUPLICATE_EXISTING,
                text_categories=[world.elevator.id],
                duplicate=AgentTicketRelation(master_ticket_id=master.id, reason="Master vừa đóng."),
                tool_usage=_usage(session),
            )
        )
    assert exc.value.code == DUPLICATE_CANDIDATE_STALE
    assert exc.value.status_code == 409
    db_session.rollback()
    db_session.refresh(ticket)
    assert ticket.duplicate_of_ticket_id is None
    assert ticket.status is TicketStatus.NEW


def test_duplicate_is_refused_when_the_ticket_already_has_an_assignment(db_session):
    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
    )
    ticket = make_ticket(
        world, location=world.elevator_a, category=world.elevator, status=TicketStatus.APPROVED
    )
    make_assignment(world, ticket, world.technician(0), status=AssignmentStatus.ACCEPTED)

    service = _service(db_session)
    session = _v4_session(service, ticket)
    _searched_master(world, service, session, ticket)
    db_session.refresh(session)

    with pytest.raises(DomainError) as exc:
        service.finalize_v4(
            _result(
                session,
                ticket,
                exit_reason=AgentExitReasonV4.DUPLICATE_EXISTING,
                text_categories=[world.elevator.id],
                duplicate=AgentTicketRelation(master_ticket_id=master.id, reason="Đã có người xử lý."),
                tool_usage=_usage(session),
            )
        )
    assert exc.value.code == ACTIVE_ASSIGNMENT_EXISTS


def test_duplicate_is_refused_on_a_different_asset(db_session):
    """§12 scenario 3: same Category, same building, elevator B."""
    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
    )
    ticket = make_ticket(world, location=world.elevator_b, category=world.elevator)
    service = _service(db_session)
    session = _v4_session(service, ticket)
    # The DUPLICATE search for elevator B does not return the elevator A master
    # at all, so it can never be finalized against it.
    _searched_master(world, service, session, ticket)
    db_session.refresh(session)

    with pytest.raises(DomainError):
        service.finalize_v4(
            _result(
                session,
                ticket,
                exit_reason=AgentExitReasonV4.DUPLICATE_EXISTING,
                text_categories=[world.elevator.id],
                duplicate=AgentTicketRelation(master_ticket_id=master.id, reason="Nhầm thang máy."),
                tool_usage=_usage(session),
            )
        )


# ---------------------------------------------------------------------------
# DUPLICATE_UNCERTAIN and LIMIT_REACHED
# ---------------------------------------------------------------------------


def test_duplicate_uncertain_goes_to_manual_review_with_candidate_evidence(db_session):
    """§1.7.4 / §12 scenario 11."""
    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
    )
    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service = _service(db_session)
    session = _v4_session(service, ticket)
    _searched_master(world, service, session, ticket)
    db_session.refresh(session)

    run = service.finalize_v4(
        _result(
            session,
            ticket,
            exit_reason=AgentExitReasonV4.DUPLICATE_UNCERTAIN,
            text_categories=[world.elevator.id],
            is_confident=False,
            confidence_notes="Cùng thang máy nhưng hiện tượng mô tả khác nhau.",
            tool_usage=_usage(session),
        )
    )

    db_session.refresh(ticket)
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW
    assert ticket.duplicate_of_ticket_id is None
    assert run.duplicate_candidates
    assert str(master.id) in {row["ticket_id"] for row in run.duplicate_candidates}


def test_duplicate_uncertain_requires_a_duplicate_search(db_session):
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service = _service(db_session)
    session = _v4_session(service, ticket)

    with pytest.raises(DomainError):
        # The payload validator forbids claiming the search happened when it did
        # not, and Backend re-checks the log; either way this cannot finalize.
        service.finalize_v4(
            _result(
                session,
                ticket,
                exit_reason=AgentExitReasonV4.DUPLICATE_UNCERTAIN,
                text_categories=[world.elevator.id],
                is_confident=False,
                confidence_notes="Chưa chắc chắn.",
                tool_usage=_usage(session, search_related_tickets_called=True),
            )
        )


def test_limit_reached_requires_an_actually_exhausted_budget(db_session):
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service = _service(db_session)
    session = _v4_session(service, ticket)

    with pytest.raises(DomainError):
        service.finalize_v4(
            _result(
                session,
                ticket,
                exit_reason=AgentExitReasonV4.LIMIT_REACHED,
                text_categories=[world.elevator.id],
                is_confident=False,
                tool_usage=_usage(session, total_tool_calls=5),
            )
        )


def test_limit_reached_moves_to_manual_review(db_session):
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service = _service(db_session)
    session = _v4_session(service, ticket)
    session.total_tool_calls = 5
    db_session.commit()
    db_session.refresh(session)

    service.finalize_v4(
        _result(
            session,
            ticket,
            exit_reason=AgentExitReasonV4.LIMIT_REACHED,
            text_categories=[world.elevator.id],
            is_confident=False,
            tool_usage=_usage(session),
        )
    )

    db_session.refresh(ticket)
    db_session.refresh(session)
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW
    assert session.status == "COMPLETED"


# ---------------------------------------------------------------------------
# RED_FLAG
# ---------------------------------------------------------------------------


def test_red_flag_forces_p3_and_links_evidence_without_becoming_a_duplicate(db_session):
    """§3.3 / §12 scenario 4."""
    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
        priority=Priority.P2,
    )
    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service = _service(db_session)
    session = _v4_session(service, ticket)
    _searched_master(world, service, session, ticket)
    db_session.refresh(session)

    run = service.finalize_v4(
        _result(
            session,
            ticket,
            exit_reason=AgentExitReasonV4.RED_FLAG,
            text_categories=[world.elevator.id],
            red_flag_text=True,
            severity=Severity.HIGH,
            red_flag_relation=AgentTicketRelation(
                master_ticket_id=master.id, reason="Cùng thang máy, nay có người mắc kẹt bên trong."
            ),
            tool_usage=_usage(session),
        )
    )

    db_session.refresh(ticket)
    db_session.refresh(master)
    assert ticket.priority is Priority.P3
    assert ticket.red_flag_detected is True
    assert ticket.classification_status is ClassificationStatus.RESOLVED
    # The new ticket keeps its own pipeline; it is not a duplicate.
    assert ticket.status is not TicketStatus.LINKED_DUPLICATE
    assert ticket.duplicate_of_ticket_id is None

    relation = db_session.scalar(select(TicketRelation).where(TicketRelation.source_ticket_id == ticket.id))
    assert relation is not None
    assert relation.relation_type == "RED_FLAG_EVIDENCE"
    assert relation.target_ticket_id == master.id
    assert relation.analysis_run_id == run.id
    # §3.3 item 3: the master is escalated for review, never downgraded.
    assert master.priority is Priority.P3
    assert master.classification_status is ClassificationStatus.MANUAL_REVIEW


def test_red_flag_survives_a_master_that_closed_before_the_link(db_session):
    """§3.3 item 4: the report is never lost because the link failed."""
    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
    )
    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service = _service(db_session)
    session = _v4_session(service, ticket)
    _searched_master(world, service, session, ticket)
    db_session.refresh(session)
    master.status = TicketStatus.COMPLETED
    db_session.commit()

    service.finalize_v4(
        _result(
            session,
            ticket,
            exit_reason=AgentExitReasonV4.RED_FLAG,
            text_categories=[world.elevator.id],
            red_flag_text=True,
            severity=Severity.HIGH,
            red_flag_relation=AgentTicketRelation(master_ticket_id=master.id, reason="Master vừa đóng."),
            tool_usage=_usage(session),
        )
    )

    db_session.refresh(ticket)
    assert ticket.priority is Priority.P3
    assert db_session.scalar(select(TicketRelation).where(TicketRelation.source_ticket_id == ticket.id)) is None


# ---------------------------------------------------------------------------
# INSUFFICIENT_INPUT
# ---------------------------------------------------------------------------


def test_insufficient_input_invalidates_and_counts_toward_the_ai_rejection_limit(db_session):
    """§3.3 / §8.2."""
    from src.database.models.resident_ticket_rate_limit import ResidentTicketRateLimit

    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a)
    service = _service(db_session)
    session = _v4_session(service, ticket)

    service.finalize_v4(
        _result(
            session,
            ticket,
            exit_reason=AgentExitReasonV4.INSUFFICIENT_INPUT,
            text_categories=None,
            severity=None,
            severity_source=None,
            is_confident=False,
        )
    )

    db_session.refresh(ticket)
    assert ticket.status is TicketStatus.INVALID
    assert ticket.invalid_reason == "CONTENT_INSUFFICIENT"
    assert ticket.classification_status is ClassificationStatus.RESOLVED

    limit = db_session.scalar(
        select(ResidentTicketRateLimit).where(ResidentTicketRateLimit.reporter_user_id == ticket.reporter_user_id)
    )
    assert limit is not None and limit.ai_rejection_count == 1


# ---------------------------------------------------------------------------
# Idempotency (§1.7.9)
# ---------------------------------------------------------------------------


def test_replaying_the_same_payload_returns_the_stored_run(db_session):
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a)
    service = _service(db_session)
    session = _v4_session(service, ticket)
    result = _result(session, ticket, text_categories=[world.elevator.id])

    first = service.finalize_v4(result, idempotency_key="key-1")
    second = service.finalize_v4(result, idempotency_key="key-1")

    assert first.id == second.id
    runs = db_session.scalars(select(AIAnalysisRun).where(AIAnalysisRun.ticket_id == ticket.id)).all()
    assert len(runs) == 1


def test_a_different_payload_after_finalization_is_a_conflict(db_session):
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a)
    service = _service(db_session)
    session = _v4_session(service, ticket)

    service.finalize_v4(_result(session, ticket, text_categories=[world.elevator.id]), idempotency_key="key-1")

    with pytest.raises(DomainError) as exc:
        service.finalize_v4(
            _result(session, ticket, text_categories=[world.water.id], severity=Severity.HIGH),
            idempotency_key="key-2",
        )
    assert exc.value.code == ANALYSIS_ALREADY_FINALIZED
    assert exc.value.status_code == 409


def test_finalize_refuses_a_v3_session(db_session):
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a)
    service = _service(db_session)
    session = service.start_session(ticket.id, model_version="fixit-agent-v3-langgraph-1")

    with pytest.raises(DomainError):
        service.finalize_v4(_result(session, ticket, text_categories=[world.elevator.id]))


def test_declared_tool_usage_must_match_the_session_counters(db_session):
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a)
    service = _service(db_session)
    session = _v4_session(service, ticket)

    with pytest.raises(DomainError):
        service.finalize_v4(
            _result(session, ticket, text_categories=[world.elevator.id], tool_usage=_usage(session, total_tool_calls=3))
        )


def test_a_stale_analyzed_at_is_rejected(db_session):
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a)
    service = _service(db_session)
    session = _v4_session(service, ticket)

    with pytest.raises(DomainError) as exc:
        service.finalize_v4(
            _result(
                session,
                ticket,
                text_categories=[world.elevator.id],
                analyzed_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
    assert exc.value.code == CONTRACT_VALIDATION_ERROR


def test_a_category_outside_the_pinned_catalog_is_rejected(db_session):
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a)
    service = _service(db_session)
    session = _v4_session(service, ticket)

    with pytest.raises(DomainError):
        service.finalize_v4(_result(session, ticket, text_categories=[uuid4()]))


# ---------------------------------------------------------------------------
# Grouping and Density (§1.4, §7.9)
# ---------------------------------------------------------------------------


def test_backend_recomputes_density_from_distinct_apartments(db_session):
    """§1.4 / §12 scenario 13: the Agent sends no Density and Backend counts
    apartments, not tickets."""
    world = build_world(db_session)
    now = datetime.now(UTC)
    neighbour_a = make_ticket(
        world,
        resident=world.resident(1),
        location=world.corridor_11,
        category=world.water,
        status=TicketStatus.APPROVED,
        created_at=now - timedelta(hours=2),
    )
    # Same apartment as neighbour_a: a second ticket, but not a second unit.
    neighbour_b = make_ticket(
        world,
        resident=world.resident(1),
        location=world.corridor_11,
        category=world.water,
        status=TicketStatus.APPROVED,
        created_at=now - timedelta(hours=1),
    )
    ticket = make_ticket(world, resident=world.resident(0), location=world.corridor_10, category=world.water)

    service = _service(db_session)
    session = _v4_session(service, ticket)
    service.search_related_tickets(
        session.id, ticket_id=ticket.id, category_ids=[world.water.id], purpose="GROUPING"
    )
    service.propose_case_grouping(
        session.id,
        ticket_id=ticket.id,
        related_ticket_ids=[neighbour_a.id, neighbour_b.id],
        reason="Rò nước lan giữa hai tầng liền kề.",
    )
    db_session.refresh(session)

    run = service.finalize_v4(
        _result(
            session,
            ticket,
            text_categories=[world.water.id],
            grouping=AgentGroupingResultV4(
                grouped=True,
                related_ticket_ids=[neighbour_a.id, neighbour_b.id],
                reason="Cùng trục nước, hai tầng liền kề.",
            ),
            tool_usage=_usage(session),
        )
    )

    case = db_session.scalar(select(IncidentCase))
    assert case is not None
    # Three tickets, two apartments.
    members = db_session.scalars(select(IncidentCaseMember).where(IncidentCaseMember.case_id == case.id)).all()
    assert len(members) == 3
    assert case.density_value == 2
    assert case.series_id is not None
    assert case.sequence_no == 1
    assert run.grouping["density"] == 2
    # §1.4: density is never a field the Agent supplied.
    assert "density" not in AgentGroupingResultV4.model_fields


def test_grouping_requires_an_accepted_backend_proposal(db_session):
    world = build_world(db_session)
    neighbour = make_ticket(
        world,
        resident=world.resident(1),
        location=world.corridor_11,
        category=world.water,
        status=TicketStatus.APPROVED,
    )
    ticket = make_ticket(world, location=world.corridor_10, category=world.water)
    service = _service(db_session)
    session = _v4_session(service, ticket)
    service.search_related_tickets(
        session.id, ticket_id=ticket.id, category_ids=[world.water.id], purpose="GROUPING"
    )
    db_session.refresh(session)

    with pytest.raises(DomainError):
        service.finalize_v4(
            _result(
                session,
                ticket,
                text_categories=[world.water.id],
                grouping=AgentGroupingResultV4(
                    grouped=True, related_ticket_ids=[neighbour.id], reason="Chưa qua propose_case_grouping."
                ),
                tool_usage=_usage(session, propose_case_grouping_called=True),
            )
        )


def test_a_sixth_member_opens_the_next_case_in_the_series(db_session):
    """§7.9 / §12 scenario 26: five in the first case, never six."""
    world = build_world(db_session, resident_count=8)
    now = datetime.now(UTC)
    neighbours = [
        make_ticket(
            world,
            resident=world.resident(index),
            location=world.corridor_11,
            category=world.water,
            status=TicketStatus.APPROVED,
            created_at=now - timedelta(hours=index + 1),
        )
        for index in range(1, 7)
    ]
    ticket = make_ticket(world, resident=world.resident(0), location=world.corridor_10, category=world.water)

    service = _service(db_session)
    session = _v4_session(service, ticket)
    service.search_related_tickets(
        session.id, ticket_id=ticket.id, category_ids=[world.water.id], purpose="GROUPING"
    )
    service.propose_case_grouping(
        session.id,
        ticket_id=ticket.id,
        related_ticket_ids=[item.id for item in neighbours],
        reason="Rò nước lan rộng.",
    )
    db_session.refresh(session)

    service.finalize_v4(
        _result(
            session,
            ticket,
            text_categories=[world.water.id],
            grouping=AgentGroupingResultV4(
                grouped=True,
                related_ticket_ids=[item.id for item in neighbours],
                reason="Cùng trục nước.",
            ),
            tool_usage=_usage(session),
        )
    )

    cases = db_session.scalars(select(IncidentCase).order_by(IncidentCase.sequence_no)).all()
    counts = [
        len(db_session.scalars(select(IncidentCaseMember).where(IncidentCaseMember.case_id == case.id)).all())
        for case in cases
    ]
    assert max(counts) <= 5
    assert sum(counts) == 7
    assert len({case.series_id for case in cases}) == 1
    assert [case.sequence_no for case in cases] == list(range(1, len(cases) + 1))


def test_scoring_uses_the_backend_density(db_session):
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.corridor_10, category=world.water)
    service = _service(db_session)
    session = _v4_session(service, ticket)

    run = service.finalize_v4(_result(session, ticket, text_categories=[world.water.id]))

    db_session.refresh(ticket)
    # A lone water-leak report: density 1, so no density bonus in the components.
    assert run.score_components is not None
    assert Decimal(str(run.score_total)) == ticket.score_total
