from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.api.routes.coordinator.clusters import (
    _cluster_ticket_response,
    _derived_cluster_responses,
    remove_ticket_from_cluster,
)
from src.database.models.ai_agent_session import AIAgentQuestion, AIAgentToolCall
from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.attachment import TicketAttachment
from src.database.models.building import Building
from src.database.models.category import CategoryCatalog
from src.database.models.floor import Floor
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.location import Location
from src.database.models.location_type import LocationType
from src.database.models.notification import Notification
from src.database.models.resident_profile import ResidentProfile
from src.database.models.technician import TechnicianProfile
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.database.models.ticket_status_history import TicketStatusHistory
from src.database.models.unit import Unit
from src.database.models.user_profile import UserProfile
from src.models.agent_schemas import (
    AgentAnalysisResultV3,
    AgentExitReason,
    AgentGroupingResult,
    AgentSeveritySource,
    AgentToolUsage,
)
from src.models.api.coordinator import ManualReviewResolveRequest
from src.models.api.errors import DomainError
from src.models.enums import (
    AnalysisRunStatus,
    AssignmentStatus,
    AttachmentType,
    Category,
    ClassificationStatus,
    Priority,
    ResolutionSource,
    Severity,
    SeveritySource,
    TicketStatus,
    UserRole,
)
from src.services.agent_backend_service import AgentBackendService
from src.services.coordinator_service import CoordinatorService
from src.services.storage_service import VerifiedStorageObject


class _StorageStub:
    def is_owned_ticket_attachment_path(self, storage_path, user_id):
        return storage_path.startswith(f"tickets/{user_id}/")

    def verify_uploaded_object(self, _storage_path, expected_mime_type=None, expected_file_size=None):
        return VerifiedStorageObject(
            mime_type=expected_mime_type or "image/jpeg",
            file_size=expected_file_size or 1,
            verified_at=datetime.now(UTC),
        )


def _seed_core(db_session):
    building = Building(code="A", name="Tower A")
    floor = Floor(building=building, floor_code="10", display_name="Floor 10", adjacency_index=10)
    unit = Unit(building=building, floor=floor, unit_code="A-1001")
    other_unit = Unit(building=building, floor=floor, unit_code="A-1002")
    location_type = LocationType(code="CORRIDOR", display_name="Corridor")
    location = Location(building=building, floor=floor, location_type=location_type, label="Corridor 10")
    water = CategoryCatalog(code=Category.WATER_LEAK, display_name="Water leak", base_score=10)
    electrical = CategoryCatalog(code=Category.ELECTRICAL_SHORT, display_name="Electrical short", base_score=70)
    inactive = CategoryCatalog(code="CUSTOM_INACTIVE", display_name="Inactive", base_score=5, is_active=False)
    resident_user = UserProfile(user_id=uuid4(), role=UserRole.RESIDENT)
    other_resident_user = UserProfile(user_id=uuid4(), role=UserRole.RESIDENT)
    coordinator = UserProfile(user_id=uuid4(), role=UserRole.COORDINATOR)
    resident = ResidentProfile(user=resident_user, unit=unit, is_primary=True)
    other_resident = ResidentProfile(user=other_resident_user, unit=other_unit, is_primary=True)
    db_session.add_all(
        [
            building,
            floor,
            unit,
            other_unit,
            location_type,
            location,
            water,
            electrical,
            inactive,
            resident_user,
            other_resident_user,
            coordinator,
            resident,
            other_resident,
        ]
    )
    db_session.commit()
    return resident, other_resident, coordinator, unit, other_unit, location, water, electrical, inactive


def _location(db_session, *, building_code="B", floor_index=20, label="Other corridor"):
    building = Building(code=building_code, name=f"Tower {building_code}")
    floor = Floor(building=building, floor_code=str(floor_index), display_name=f"Floor {floor_index}", adjacency_index=floor_index)
    location_type = LocationType(code=f"CORRIDOR_{building_code}_{floor_index}", display_name="Corridor")
    location = Location(building=building, floor=floor, location_type=location_type, label=label)
    unit = Unit(building=building, floor=floor, unit_code=f"{building_code}-{floor_index}01")
    user = UserProfile(user_id=uuid4(), role=UserRole.RESIDENT)
    resident = ResidentProfile(user=user, unit=unit, is_primary=True)
    db_session.add_all([building, floor, location_type, location, unit, user, resident])
    db_session.commit()
    return resident, location


def _ticket(db_session, resident, location, category=None, *, description="Issue", created_at=None):
    ticket = Ticket(
        reporter_user_id=resident.user_id,
        source_unit_id=resident.unit_id,
        location_id=location.id,
        description=description,
        status=TicketStatus.NEW,
        classification_status=ClassificationStatus.PROCESSING,
        category_id=category.id if category else None,
        severity=Severity.MEDIUM,
        created_at=created_at or datetime.now(UTC),
        sla_started_at=datetime.now(UTC),
    )
    db_session.add(ticket)
    db_session.commit()
    return ticket


def _result(session, ticket, category_ids, *, exit_reason=AgentExitReason.CONFIDENT_MATCH, is_confident=True):
    return AgentAnalysisResultV3(
        ticket_id=ticket.id,
        analysis_session_id=session.id,
        exit_reason=exit_reason,
        text_categories=category_ids,
        red_flag_text=False,
        image_categories=None,
        red_flag_signal=None,
        is_relevant=None,
        severity=Severity.MEDIUM,
        severity_source=AgentSeveritySource.TEXT,
        is_confident=is_confident,
        tool_usage=AgentToolUsage(
            total_tool_calls=session.total_tool_calls,
            ask_resident_rounds=session.ask_resident_rounds,
            ask_resident_elapsed_seconds=session.ask_resident_elapsed_seconds,
            search_related_tickets_called=any(
                call.tool_name == "search_related_tickets"
                for call in session.tool_calls
            ),
            propose_case_grouping_called=any(
                call.tool_name == "propose_case_grouping"
                for call in session.tool_calls
            ),
        ),
        category_catalog_version=session.category_catalog_version,
        model_version="agent-v3-test",
        analyzed_at=datetime.now(UTC),
    )


def test_agent_catalog_snapshots_active_dynamic_categories_only(db_session):
    _resident, _other, _coordinator, _unit, _other_unit, location, water, _electrical, inactive = _seed_core(db_session)
    ticket = _ticket(db_session, _resident, location)
    service = AgentBackendService(db_session, _StorageStub())

    session = service.start_session(ticket.id, model_version="agent-v3-test")
    first = service.get_category_catalog(session.id)
    second = service.get_category_catalog(session.id)

    ids = {item.category_id for item in first.categories}
    assert water.id in ids
    assert inactive.id not in ids
    assert "code" not in first.categories[0].model_dump()
    assert first.catalog_version == second.catalog_version


def test_agent_confident_match_finalizes_ticket_and_duplicate_is_rejected(db_session):
    resident, _other, _coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    ticket = _ticket(db_session, resident, location)
    service = AgentBackendService(db_session, _StorageStub())
    session = service.start_session(ticket.id, model_version="agent-v3-test")

    run = service.finalize(_result(session, ticket, [water.id]))

    assert run.contract_version == "v3"
    assert ticket.classification_status == ClassificationStatus.RESOLVED
    assert ticket.category_id == water.id
    assert ticket.priority == Priority.P1
    with pytest.raises(DomainError):
        service.finalize(_result(session, ticket, [water.id]))


def test_agent_limit_reached_routes_to_manual_review_and_red_flag_forces_p3(db_session):
    resident, _other, _coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    service = AgentBackendService(db_session, _StorageStub())
    limited_ticket = _ticket(db_session, resident, location)
    limited_session = service.start_session(limited_ticket.id, model_version="agent-v3-test")
    limited_session.total_tool_calls = 5
    db_session.commit()

    limited_run = service.finalize(
        _result(limited_session, limited_ticket, [], exit_reason=AgentExitReason.LIMIT_REACHED, is_confident=False)
    )

    assert limited_run.exit_reason == AgentExitReason.LIMIT_REACHED.value
    assert limited_ticket.classification_status == ClassificationStatus.MANUAL_REVIEW

    red_ticket = _ticket(db_session, resident, location)
    red_session = service.start_session(red_ticket.id, model_version="agent-v3-test")
    red_result = _result(red_session, red_ticket, [water.id], exit_reason=AgentExitReason.RED_FLAG)
    red_result.red_flag_text = True
    red_run = service.finalize(red_result)

    assert red_run.red_flag_text is True
    assert red_ticket.priority == Priority.P3
    assert red_ticket.score_total is None
    assert red_ticket.classification_status == ClassificationStatus.RESOLVED


def test_related_ticket_search_masks_raw_resident_description_and_grouping_is_constrained(db_session):
    resident, other, _coordinator, _unit, _other_unit, location, water, electrical, _inactive = _seed_core(db_session)
    current = _ticket(db_session, resident, location, water, description="Current")
    related = _ticket(
        db_session,
        other,
        location,
        water,
        description="secret phone 0900000000 and private detail",
        created_at=current.created_at - timedelta(hours=1),
    )
    service = AgentBackendService(db_session, _StorageStub())
    session = service.start_session(current.id, model_version="agent-v3-test")

    response = service.search_related_tickets(session.id, ticket_id=current.id, category_ids=[water.id])
    summaries = [item["summary"] for item in response["related_tickets"]]

    assert related.id in {item.id for item in db_session.scalars(select(Ticket))}
    assert all("secret phone" not in summary for summary in summaries)
    assert all("0900000000" not in summary for summary in summaries)

    accepted = service.propose_case_grouping(
        session.id,
        ticket_id=current.id,
        related_ticket_ids=[related.id],
        reason="Same category and nearby unit",
    )
    assert accepted["accepted"] is True
    assert db_session.scalar(
        select(IncidentCaseMember).where(
            IncidentCaseMember.ticket_id == current.id
        )
    ) is None

    electrical_ticket = _ticket(db_session, resident, location, electrical, description="Electrical")
    electrical_session = service.start_session(electrical_ticket.id, model_version="agent-v3-test")
    rejected = service.propose_case_grouping(
        electrical_session.id,
        ticket_id=electrical_ticket.id,
        related_ticket_ids=[related.id],
        reason="Not allowed",
    )
    assert rejected["accepted"] is False
    assert rejected["rejected_reason"] == "RELATED_TICKET_NOT_FROM_SESSION_SEARCH"


def test_resident_question_answer_and_timeout_invalidates_ticket(db_session):
    resident, other, _coordinator, _unit, _other_unit, location, _water, _electrical, _inactive = _seed_core(db_session)
    service = AgentBackendService(db_session, _StorageStub())
    ticket = _ticket(db_session, resident, location)
    session = service.start_session(ticket.id, model_version="agent-v3-test")
    question = service.create_question(
        session.id,
        ticket_id=ticket.id,
        question_type="MULTIPLE_CHOICE",
        question_text="Where is the issue?",
        options=["Bathroom", "Kitchen"],
    )

    assert service.pending_resident_question(resident, ticket.id, resident.user_id).id == question.id
    with pytest.raises(DomainError):
        service.pending_resident_question(other, ticket.id, other.user_id)

    upload = service.uploads.create_upload_session(
        resident.user_id,
        f"tickets/{resident.user_id}/supplement.jpg",
        "supplement.jpg",
        "image/jpeg",
        10,
    )
    db_session.commit()
    answered = service.answer_question(
        resident,
        ticket.id,
        question.id,
        resident.user_id,
        answer_type="NEW_PHOTO",
        upload_id=upload.id,
    )

    assert answered.status == "ANSWERED"
    assert db_session.scalar(select(TicketAttachment).where(TicketAttachment.attachment_type == AttachmentType.RESIDENT_SUPPLEMENT)) is not None

    timeout_ticket = _ticket(db_session, resident, location)
    timeout_session = service.start_session(timeout_ticket.id, model_version="agent-v3-test")
    timeout_question = service.create_question(
        timeout_session.id,
        ticket_id=timeout_ticket.id,
        question_type="FREE_TEXT",
        question_text="Need more detail",
    )
    timeout_session.waiting_deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    timeout_question.expires_at = timeout_session.waiting_deadline_at
    db_session.commit()

    assert service.handle_timeouts(datetime.now(UTC)) == 1
    assert timeout_ticket.status == TicketStatus.INVALID
    assert db_session.scalar(select(AIAgentQuestion).where(AIAgentQuestion.id == timeout_question.id)).status == "EXPIRED"
    assert db_session.scalar(select(TicketStatusHistory).where(TicketStatusHistory.ticket_id == timeout_ticket.id)) is not None
    assert db_session.scalar(select(Notification).where(Notification.ticket_id == timeout_ticket.id)) is not None


def test_ask_resident_counts_as_tool_and_answered_question_cannot_timeout_later(db_session):
    resident, _other, _coordinator, _unit, _other_unit, location, _water, _electrical, _inactive = _seed_core(db_session)
    service = AgentBackendService(db_session, _StorageStub())
    ticket = _ticket(db_session, resident, location)
    session = service.start_session(ticket.id, model_version="agent-v3-test")

    question = service.create_question(
        session.id,
        ticket_id=ticket.id,
        question_type="MULTIPLE_CHOICE",
        question_text="Pick one",
        options=["A", "B"],
    )
    db_session.refresh(session)

    assert session.total_tool_calls == 1
    assert session.ask_resident_rounds == 1
    assert db_session.scalar(select(AIAgentToolCall).where(AIAgentToolCall.tool_name == "ask_resident")) is not None

    service.answer_question(resident, ticket.id, question.id, resident.user_id, answer_type="OPTION", answer_text="A")
    db_session.refresh(session)
    assert session.waiting_deadline_at is None

    session.waiting_deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert service.handle_timeouts(datetime.now(UTC)) == 0
    assert ticket.status == TicketStatus.NEW


def test_grouping_works_before_ticket_category_is_finalized_and_rejects_bad_geography(db_session):
    resident, other, _coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    cross_building_resident, cross_building_location = _location(db_session, building_code="B", floor_index=10)
    far_floor = Floor(building=location.building, floor_code="99", display_name="Floor 99", adjacency_index=99)
    far_unit = Unit(building=location.building, floor=far_floor, unit_code="A-9901")
    far_location = Location(building=location.building, floor=far_floor, location_type=location.location_type, label="Far corridor")
    far_user = UserProfile(user_id=uuid4(), role=UserRole.RESIDENT)
    far_floor_resident = ResidentProfile(user=far_user, unit=far_unit, is_primary=True)
    db_session.add_all([far_floor, far_unit, far_location, far_user, far_floor_resident])
    db_session.commit()
    current = _ticket(db_session, resident, location, None, description="Unfinalized")
    related = _ticket(db_session, other, location, water, created_at=current.created_at - timedelta(hours=1))
    cross_building = _ticket(db_session, cross_building_resident, cross_building_location, water, created_at=current.created_at)
    far_floor_ticket = _ticket(db_session, far_floor_resident, far_location, water, created_at=current.created_at)
    service = AgentBackendService(db_session, _StorageStub())
    session = service.start_session(current.id, model_version="agent-v3-test")

    response = service.search_related_tickets(session.id, ticket_id=current.id, category_ids=[water.id])
    returned_ids = {item["ticket_id"] for item in response["related_tickets"]}

    assert str(related.id) in returned_ids
    assert str(cross_building.id) not in returned_ids
    assert str(far_floor_ticket.id) not in returned_ids

    accepted = service.propose_case_grouping(session.id, ticket_id=current.id, related_ticket_ids=[related.id], reason="Nearby water issue")
    assert current.category_id is None
    assert accepted["accepted"] is True
    assert accepted["density"] == 2


def test_cluster_route_derives_case_from_related_tickets_without_agent_materialized_case(db_session):
    resident, other, _coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    first = _ticket(db_session, resident, location, water, description="Water leak 1")
    second = _ticket(db_session, other, location, water, description="Water leak 2", created_at=first.created_at - timedelta(hours=2))

    clusters = _derived_cluster_responses(db_session, excluded_ticket_ids=set(), limit=10)

    assert len(clusters) == 1
    assert clusters[0].category_id == water.id
    assert clusters[0].density == 2
    assert {ticket.id for ticket in clusters[0].tickets} == {first.id, second.id}


def test_confident_finalize_auto_materializes_related_cluster_without_agent_grouping_tool(db_session):
    resident, other, _coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    first = _ticket(db_session, resident, location, water, description="Water leak 1")
    second = _ticket(db_session, other, location, None, description="Water leak 2", created_at=first.created_at + timedelta(minutes=2))
    service = AgentBackendService(db_session, _StorageStub())
    session = service.start_session(second.id, model_version="agent-v3-test")

    service.finalize(_result(session, second, [water.id]))

    members = list(
        db_session.scalars(
            select(IncidentCaseMember).where(
                IncidentCaseMember.ticket_id.in_([first.id, second.id])
            )
        )
    )
    assert {member.ticket_id for member in members} == {first.id, second.id}
    assert len({member.case_id for member in members}) == 1


def test_auto_grouping_does_not_add_new_ticket_to_assigned_case(db_session):
    resident, other, coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    tech_user = UserProfile(user_id=uuid4(), role=UserRole.TECHNICIAN, full_name="Tech One")
    tech = TechnicianProfile(user=tech_user)
    assigned = _ticket(db_session, resident, location, water, description="Already assigned water leak")
    assigned.status = TicketStatus.APPROVED
    db_session.add(
        TicketAssignment(
            ticket=assigned,
            technician=tech,
            assigned_by_user_id=coordinator.user_id,
            status=AssignmentStatus.ASSIGNED,
            is_active=True,
        )
    )
    incoming = _ticket(db_session, other, location, None, description="New water leak", created_at=assigned.created_at + timedelta(minutes=2))
    db_session.add_all([tech_user, tech])
    db_session.commit()
    service = AgentBackendService(db_session, _StorageStub())
    session = service.start_session(incoming.id, model_version="agent-v3-test")

    service.finalize(_result(session, incoming, [water.id]))

    members = list(
        db_session.scalars(
            select(IncidentCaseMember).where(
                IncidentCaseMember.ticket_id.in_([assigned.id, incoming.id])
            )
        )
    )
    assert members == []


def test_cluster_ticket_response_includes_active_assignment(db_session):
    resident, other, coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    tech_user = UserProfile(user_id=uuid4(), role=UserRole.TECHNICIAN, full_name="Tech One")
    tech = TechnicianProfile(user=tech_user)
    first = _ticket(db_session, resident, location, water, description="Water leak 1")
    second = _ticket(db_session, other, location, water, description="Water leak 2", created_at=first.created_at - timedelta(hours=2))
    first.status = TicketStatus.APPROVED
    second.status = TicketStatus.APPROVED
    assignment = TicketAssignment(
        ticket=first,
        technician=tech,
        assigned_by_user_id=coordinator.user_id,
        status=AssignmentStatus.ASSIGNED,
        is_active=True,
    )
    db_session.add_all([tech_user, tech, assignment])
    db_session.commit()

    assigned_ticket = _cluster_ticket_response(first)
    unassigned_ticket = _cluster_ticket_response(second)

    assert assigned_ticket.active_assignment_id == assignment.id
    assert assigned_ticket.active_assignment_status == AssignmentStatus.ASSIGNED
    assert assigned_ticket.active_technician_id == tech.user_id
    assert assigned_ticket.active_technician_name == "Tech One"
    assert unassigned_ticket.active_assignment_id is None


def test_snapshot_base_score_and_backend_grouping_density_are_used(db_session):
    resident, other, _coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    current = _ticket(db_session, resident, location, None)
    related = _ticket(db_session, other, location, water, created_at=current.created_at - timedelta(hours=1))
    service = AgentBackendService(db_session, _StorageStub())
    session = service.start_session(current.id, model_version="agent-v3-test")
    service.search_related_tickets(session.id, ticket_id=current.id, category_ids=[water.id])
    service.propose_case_grouping(session.id, ticket_id=current.id, related_ticket_ids=[related.id], reason="Nearby water issue")
    water.base_score = 80
    db_session.commit()

    bad = _result(session, current, [water.id])
    bad.grouping = AgentGroupingResult(grouped=True, density=99, related_ticket_ids=[related.id], reason="Fake density")
    with pytest.raises(DomainError):
        service.finalize(bad)

    db_session.refresh(session)
    good = _result(session, current, [water.id])
    good.grouping = AgentGroupingResult(grouped=True, density=2, related_ticket_ids=[related.id], reason="Backend accepted")
    service.finalize(good)

    assert current.score_total == 35
    assert current.priority == Priority.P2
    assert db_session.scalar(
        select(IncidentCaseMember).where(
            IncidentCaseMember.ticket_id == current.id
        )
    ) is not None


def test_red_flag_canonicalizes_exit_reason_even_when_agent_submits_other_reason(db_session):
    resident, _other, _coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    ticket = _ticket(db_session, resident, location)
    service = AgentBackendService(db_session, _StorageStub())
    session = service.start_session(ticket.id, model_version="agent-v3-test")
    result = _result(session, ticket, [water.id], exit_reason=AgentExitReason.CONFIDENT_MATCH)
    result.red_flag_text = True

    run = service.finalize(result)

    assert run.exit_reason == AgentExitReason.RED_FLAG.value
    assert ticket.priority == Priority.P3
    assert ticket.score_total is None


def test_v3_p0_can_be_resolved_and_v2_manual_review_still_uses_category_code(db_session):
    resident, _other, coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    service = AgentBackendService(db_session, _StorageStub())
    v3_ticket = _ticket(db_session, resident, location)
    v3_session = service.start_session(v3_ticket.id, model_version="agent-v3-test")
    v3_session.total_tool_calls = 5
    db_session.commit()
    service.finalize(_result(v3_session, v3_ticket, [water.id], exit_reason=AgentExitReason.LIMIT_REACHED, is_confident=False))

    resolved = CoordinatorService(db_session).resolve_manual_review(
        coordinator.user_id,
        v3_ticket.id,
        ManualReviewResolveRequest(category_id=water.id, resolution_source=ResolutionSource.TEXT, reason="Use v3 text category"),
    )
    assert resolved.classification_status == ClassificationStatus.RESOLVED

    v2_ticket = _ticket(db_session, resident, location, water)
    v2_ticket.classification_status = ClassificationStatus.MANUAL_REVIEW
    v2_ticket.severity = Severity.MEDIUM
    db_session.add(
        AIAnalysisRun(
            ticket_id=v2_ticket.id,
            run_number=1,
            text_categories=[Category.WATER_LEAK.value],
            image_categories=None,
            red_flag_text=False,
            red_flag_signal=False,
            severity=Severity.MEDIUM,
            severity_source=SeveritySource.TEXT_FALLBACK,
            category_match=False,
            status=AnalysisRunStatus.SUCCEEDED,
        )
    )
    db_session.commit()

    resolved_v2 = CoordinatorService(db_session).resolve_manual_review(
        coordinator.user_id,
        v2_ticket.id,
        ManualReviewResolveRequest(category_id=water.id, resolution_source=ResolutionSource.TEXT, reason="Use v2 text category"),
    )
    assert resolved_v2.classification_status == ClassificationStatus.RESOLVED


def test_invalid_question_answer_and_photo_upload_combinations_are_rejected(db_session):
    resident, other, _coordinator, _unit, _other_unit, location, _water, _electrical, _inactive = _seed_core(db_session)
    service = AgentBackendService(db_session, _StorageStub())
    ticket = _ticket(db_session, resident, location)
    session = service.start_session(ticket.id, model_version="agent-v3-test")

    with pytest.raises(DomainError):
        service.create_question(session.id, ticket_id=ticket.id, question_type="CHOICE", question_text="Bad")

    question = service.create_question(
        session.id,
        ticket_id=ticket.id,
        question_type="MULTIPLE_CHOICE",
        question_text="Pick one",
        options=["A", "B"],
    )
    with pytest.raises(DomainError):
        service.answer_question(resident, ticket.id, question.id, resident.user_id, answer_type="OPTION", answer_text="C")
    with pytest.raises(DomainError):
        service.answer_question(resident, ticket.id, question.id, resident.user_id, answer_type="FREE_TEXT", answer_text="other")

    wrong_owner_upload = service.uploads.create_upload_session(
        other.user_id,
        f"tickets/{other.user_id}/wrong.jpg",
        "wrong.jpg",
        "image/jpeg",
        10,
    )
    db_session.commit()
    with pytest.raises(DomainError):
        service.answer_question(resident, ticket.id, question.id, resident.user_id, answer_type="NEW_PHOTO", upload_id=wrong_owner_upload.id)

    reused_upload = service.uploads.create_upload_session(
        resident.user_id,
        f"tickets/{resident.user_id}/reused.jpg",
        "reused.jpg",
        "image/jpeg",
        10,
    )
    reused_upload.status = "consumed"
    reused_upload.consumed_at = datetime.now(UTC)
    db_session.commit()
    with pytest.raises(DomainError):
        service.answer_question(resident, ticket.id, question.id, resident.user_id, answer_type="NEW_PHOTO", upload_id=reused_upload.id)

    expired_upload = service.uploads.create_upload_session(
        resident.user_id,
        f"tickets/{resident.user_id}/expired.jpg",
        "expired.jpg",
        "image/jpeg",
        10,
    )
    expired_upload.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(DomainError):
        service.answer_question(resident, ticket.id, question.id, resident.user_id, answer_type="NEW_PHOTO", upload_id=expired_upload.id)


def test_in_progress_resident_response_keeps_technician_visible(db_session):
    from src.api.routes.tickets import resident_ticket_response

    resident, _other, coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    tech_user = UserProfile(user_id=uuid4(), role=UserRole.TECHNICIAN, full_name="Tech One")
    tech = TechnicianProfile(user=tech_user)
    ticket = _ticket(db_session, resident, location, water)
    ticket.status = TicketStatus.IN_PROGRESS
    ticket.classification_status = ClassificationStatus.RESOLVED
    assignment = TicketAssignment(
        ticket=ticket,
        technician=tech,
        assigned_by_user_id=coordinator.user_id,
        status=AssignmentStatus.IN_PROGRESS,
        is_active=True,
    )
    db_session.add_all([tech_user, tech, assignment])
    db_session.commit()

    response = resident_ticket_response(ticket)

    assert response.technician is not None
    assert response.technician.full_name == "Tech One"


def test_catalog_snapshot_cannot_be_overwritten_after_session_start(db_session):
    resident, _other, _coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    ticket = _ticket(db_session, resident, location)
    service = AgentBackendService(db_session, _StorageStub())

    session = service.start_session(ticket.id, model_version="agent-v3-test")
    first = service.get_category_catalog(session.id)
    first_water = next(item for item in first.categories if item.category_id == water.id)

    water.base_score = 80
    db_session.commit()

    second = service.get_category_catalog(session.id)
    second_water = next(item for item in second.categories if item.category_id == water.id)

    assert first_water.base_score == 10
    assert second_water.base_score == 10
    assert second.catalog_version == first.catalog_version


def test_late_answer_immediately_times_out_session_and_invalidates_ticket(db_session):
    resident, _other, _coordinator, _unit, _other_unit, location, _water, _electrical, _inactive = _seed_core(db_session)
    service = AgentBackendService(db_session, _StorageStub())
    ticket = _ticket(db_session, resident, location)
    session = service.start_session(ticket.id, model_version="agent-v3-test")
    question = service.create_question(
        session.id,
        ticket_id=ticket.id,
        question_type="FREE_TEXT",
        question_text="Need more detail",
    )

    question.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.waiting_deadline_at = question.expires_at
    db_session.commit()

    with pytest.raises(DomainError):
        service.answer_question(
            resident,
            ticket.id,
            question.id,
            resident.user_id,
            answer_type="FREE_TEXT",
            answer_text="late",
        )

    db_session.refresh(question)
    db_session.refresh(session)
    db_session.refresh(ticket)

    assert question.status == "EXPIRED"
    assert session.status == "TIMED_OUT"
    assert session.waiting_deadline_at is None
    assert ticket.status == TicketStatus.INVALID
    assert ticket.classification_status == ClassificationStatus.FAILED


def test_red_flag_ignores_invalid_grouping_and_sets_p3_sla(db_session):
    resident, _other, _coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    service = AgentBackendService(db_session, _StorageStub())
    ticket = _ticket(db_session, resident, location)
    session = service.start_session(ticket.id, model_version="agent-v3-test")

    result = _result(
        session,
        ticket,
        [water.id],
        exit_reason=AgentExitReason.CONFIDENT_MATCH,
    )
    result.red_flag_text = True
    result.grouping = AgentGroupingResult(
        grouped=True,
        density=99,
        related_ticket_ids=[uuid4()],
        reason="stale grouping must be ignored on red flag",
    )

    run = service.finalize(result)

    assert run.exit_reason == AgentExitReason.RED_FLAG.value
    assert run.grouping is None
    assert ticket.priority == Priority.P3
    assert ticket.score_total is None
    assert ticket.sla_due_at is not None


def test_grouping_category_must_match_final_resolved_category_and_no_early_incident_created(db_session):
    resident, other, _coordinator, _unit, _other_unit, location, water, electrical, _inactive = _seed_core(db_session)
    current = _ticket(db_session, resident, location, None)
    related = _ticket(
        db_session,
        other,
        location,
        water,
        created_at=current.created_at - timedelta(hours=1),
    )
    service = AgentBackendService(db_session, _StorageStub())
    session = service.start_session(current.id, model_version="agent-v3-test")

    service.search_related_tickets(
        session.id,
        ticket_id=current.id,
        category_ids=[water.id],
    )
    accepted = service.propose_case_grouping(
        session.id,
        ticket_id=current.id,
        related_ticket_ids=[related.id],
        reason="Nearby water issue",
    )
    assert accepted["accepted"] is True
    assert db_session.scalar(
        select(IncidentCaseMember).where(
            IncidentCaseMember.ticket_id == current.id
        )
    ) is None

    bad = _result(session, current, [electrical.id])
    bad.grouping = AgentGroupingResult(
        grouped=True,
        density=2,
        related_ticket_ids=[related.id],
        reason="Agent changed final category",
    )

    with pytest.raises(DomainError):
        service.finalize(bad)

    assert db_session.scalar(
        select(IncidentCaseMember).where(
            IncidentCaseMember.ticket_id == current.id
        )
    ) is None


def test_agent_tool_usage_must_match_backend_canonical_usage(db_session):
    resident, _other, _coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    service = AgentBackendService(db_session, _StorageStub())
    ticket = _ticket(db_session, resident, location)
    session = service.start_session(ticket.id, model_version="agent-v3-test")

    service.search_related_tickets(
        session.id,
        ticket_id=ticket.id,
        category_ids=[water.id],
    )

    result = _result(session, ticket, [water.id])
    result.tool_usage.search_related_tickets_called = False

    with pytest.raises(DomainError):
        service.finalize(result)


def test_invalid_ticket_sets_failed_classification_and_resident_display(db_session):
    from src.api.routes.tickets import resident_ticket_response

    resident, _other, _coordinator, _unit, _other_unit, location, _water, _electrical, _inactive = _seed_core(db_session)
    service = AgentBackendService(db_session, _StorageStub())
    ticket = _ticket(db_session, resident, location)
    session = service.start_session(ticket.id, model_version="agent-v3-test")

    result = AgentAnalysisResultV3(
        ticket_id=ticket.id,
        analysis_session_id=session.id,
        exit_reason=AgentExitReason.INSUFFICIENT_INPUT,
        text_categories=None,
        red_flag_text=False,
        image_categories=None,
        red_flag_signal=None,
        is_relevant=None,
        severity=None,
        severity_source=None,
        is_confident=False,
        grouping=None,
        tool_usage=AgentToolUsage(
            total_tool_calls=0,
            ask_resident_rounds=0,
            ask_resident_elapsed_seconds=0,
            search_related_tickets_called=False,
            propose_case_grouping_called=False,
        ),
        category_catalog_version=session.category_catalog_version,
        model_version="agent-v3-test",
        analyzed_at=datetime.now(UTC),
    )

    service.finalize(result)

    assert ticket.status == TicketStatus.INVALID
    assert ticket.classification_status == ClassificationStatus.FAILED
    assert resident_ticket_response(ticket).display_status == "Không hợp lệ"


def _materialized_case(db_session, tickets):
    case = IncidentCase(
        category_id=tickets[0].category_id,
        building_id=tickets[0].location.building_id,
        window_start=min(ticket.created_at for ticket in tickets) - timedelta(days=3),
        window_end=max(ticket.created_at for ticket in tickets),
        density_value=len({ticket.source_unit_id for ticket in tickets}),
    )
    db_session.add(case)
    db_session.flush()
    case.series_id = case.id
    for ticket in tickets:
        db_session.add(IncidentCaseMember(case_id=case.id, ticket_id=ticket.id, source_unit_id=ticket.source_unit_id))
    db_session.commit()
    return case


class _RouteRequest:
    def __init__(self):
        self.state = SimpleNamespace(request_id="test-request")


def test_removing_a_ticket_from_a_case_keeps_the_ticket_and_recomputes_density(db_session):
    resident, other, _coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    first = _ticket(db_session, resident, location, water, description="Water leak 1")
    second = _ticket(db_session, other, location, water, description="Water leak 2", created_at=first.created_at - timedelta(hours=2))
    case = _materialized_case(db_session, [first, second])

    response = remove_ticket_from_cluster(_RouteRequest(), case.id, second.id, None, db_session)

    cluster = response["data"]
    assert [ticket.id for ticket in cluster.tickets] == [first.id]
    assert cluster.density == 1
    assert db_session.get(Ticket, second.id) is not None
    assert db_session.scalar(
        select(IncidentCaseMember).where(IncidentCaseMember.case_id == case.id, IncidentCaseMember.ticket_id == second.id)
    ) is None


def test_removing_the_last_ticket_from_a_case_is_blocked_until_an_empty_case_rule_exists(db_session):
    resident, other, _coordinator, _unit, _other_unit, location, water, _electrical, _inactive = _seed_core(db_session)
    first = _ticket(db_session, resident, location, water, description="Water leak 1")
    second = _ticket(db_session, other, location, water, description="Water leak 2", created_at=first.created_at - timedelta(hours=2))
    case = _materialized_case(db_session, [first, second])
    remove_ticket_from_cluster(_RouteRequest(), case.id, second.id, None, db_session)

    with pytest.raises(DomainError) as excinfo:
        remove_ticket_from_cluster(_RouteRequest(), case.id, first.id, None, db_session)

    assert excinfo.value.status_code == 409
    assert db_session.scalar(
        select(IncidentCaseMember).where(IncidentCaseMember.case_id == case.id, IncidentCaseMember.ticket_id == first.id)
    ) is not None
