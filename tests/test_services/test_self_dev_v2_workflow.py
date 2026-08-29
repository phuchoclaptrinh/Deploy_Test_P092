from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.database.models.attachment import TicketAttachment
from src.database.models.audit_log import AuditLog
from src.database.models.category import CategoryCatalog
from src.database.models.floor import Floor
from src.database.models.location import Location
from src.database.models.location_type import LocationType
from src.database.models.notification import Notification
from src.database.models.resident_profile import ResidentProfile
from src.database.models.technician import TechnicianProfile, TechnicianSkill
from src.database.models.ticket import Ticket
from src.database.models.ticket_status_history import TicketStatusHistory
from src.database.models.unit import Unit
from src.database.models.user_profile import UserProfile
from src.models.api.coordinator import ClassificationOverrideRequest
from src.models.api.errors import COMPLETION_EVIDENCE_REQUIRED, DomainError
from src.models.api.tickets import TicketCreateRequest
from src.models.enums import (
    AssignmentStatus,
    AttachmentType,
    Category,
    ClassificationStatus,
    Priority,
    Severity,
    TicketStatus,
    UserRole,
)
from src.request_context import request_id_context
from src.services.assignment_service import AssignmentService
from src.services.coordinator_service import CoordinatorService
from src.services.storage_service import VerifiedStorageObject
from src.services.ticket_service import TicketService

# Assignment is allowed only during the 08:00–18:00 ICT shift.  Keep workflow
# tests independent from the wall clock on the machine that runs them.
IN_SHIFT = datetime(2026, 8, 26, 3, 0, tzinfo=UTC)


def _seed_core(db_session):
    floor = Floor(floor_code="10", display_name="Tầng 10", adjacency_index=10)
    unit = Unit(floor=floor, unit_code="A-1002")
    location_type = LocationType(code="CORRIDOR", display_name="Hành lang")
    location = Location(
        floor=floor,
        location_type=location_type,
        label="Hành lang tầng 10",
    )
    category = CategoryCatalog(code=Category.WATER, display_name="Rò rỉ nước", base_score=10)

    resident_id = uuid4()
    second_resident_id = uuid4()
    coordinator_id = uuid4()
    technician_id = uuid4()
    resident = UserProfile(user_id=resident_id, phone_e164="+84901234567", role=UserRole.RESIDENT)
    second_resident = UserProfile(
        user_id=second_resident_id,
        phone_e164="+84907654321",
        role=UserRole.RESIDENT,
    )
    coordinator = UserProfile(user_id=coordinator_id, role=UserRole.COORDINATOR)
    technician_user = UserProfile(user_id=technician_id, role=UserRole.TECHNICIAN, full_name="Tech One")
    technician = TechnicianProfile(user=technician_user, is_active=True, is_available=True)
    resident_binding = ResidentProfile(user=resident, unit=unit, is_primary=True)

    db_session.add_all(
        [
            floor,
            unit,
            location_type,
            location,
            category,
            resident,
            second_resident,
            coordinator,
            technician_user,
            technician,
            resident_binding,
        ]
    )
    db_session.flush()
    db_session.add(TechnicianSkill(technician=technician, category=category))
    db_session.commit()
    return resident, second_resident, coordinator, technician, resident_binding, location, category


class _StorageStub:
    settings = type("Settings", (), {"supabase_signed_download_ttl_seconds": 300})()

    def is_owned_completion_evidence_path(self, storage_path, technician_id):
        return storage_path.startswith(f"completion-evidence/{technician_id}/")

    def verify_uploaded_object(self, storage_path, expected_mime_type=None, expected_file_size=None):
        return VerifiedStorageObject(
            mime_type=expected_mime_type or "image/jpeg",
            file_size=expected_file_size or 1,
            verified_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    def create_signed_download_url(self, storage_path):
        return f"https://signed.local/{storage_path}"


def _approved_ticket(db_session, resident, coordinator, binding, location, category):
    ticket = TicketService(db_session).create_ticket(
        resident.user_id,
        binding,
        TicketCreateRequest(location_id=location.id, description="Nước chảy ở hành lang"),
    )
    ticket.classification_status = ClassificationStatus.RESOLVED
    ticket.category_id = category.id
    ticket.category = category
    ticket.severity = Severity.MEDIUM
    ticket.priority = Priority.P1
    db_session.commit()
    return CoordinatorService(db_session).approve(coordinator.user_id, ticket.id)


def test_resident_create_then_coordinator_assigns_and_technician_completes(db_session):
    resident, second_resident, coordinator, technician, binding, location, category = _seed_core(db_session)

    ticket = TicketService(db_session).create_ticket(
        resident.user_id,
        binding,
        TicketCreateRequest(location_id=location.id, description="Nước chảy ở hành lang"),
    )
    assert ticket.status == TicketStatus.NEW
    assert ticket.classification_status == ClassificationStatus.PROCESSING
    assert ticket.source_unit_id == binding.unit_id
    assert ticket.sla_started_at is not None

    # Simulate the backend-owned result after the AI boundary.
    locked = db_session.scalar(select(Ticket).where(Ticket.id == ticket.id))
    locked.classification_status = ClassificationStatus.RESOLVED
    locked.category_id = category.id
    locked.category = category
    locked.severity = Severity.MEDIUM
    locked.priority = Priority.P1
    db_session.commit()

    request_id = str(uuid4())
    token = request_id_context.set(request_id)
    try:
        service = CoordinatorService(db_session)
        approved = service.approve(coordinator.user_id, ticket.id)
        assert approved.status == TicketStatus.APPROVED
        assignment_service = AssignmentService(db_session, _StorageStub())
        assignment = assignment_service.assign(coordinator.user_id, ticket.id, technician.user_id, now=IN_SHIFT)
        assert assignment.status == AssignmentStatus.ASSIGNED
        # Straight to work: there is no acknowledgement call in between, and
        # this is the only assignment the technician holds, so it is the head
        # of their queue.
        started = assignment_service.start(technician.user_id, assignment.id)
        assert started.status == AssignmentStatus.IN_PROGRESS
        upload = assignment_service.uploads.create_upload_session(
            technician.user_id,
            f"completion-evidence/{technician.user_id}/2026/08/done.jpg",
            "done.jpg",
            "image/jpeg",
            128,
        )
        completed = assignment_service.complete(technician.user_id, assignment.id, "Đã xử lý xong", [upload.id])
        assert completed.status == AssignmentStatus.COMPLETED
        assert completed.ticket.status == TicketStatus.COMPLETED
    finally:
        request_id_context.reset(token)

    # The single resident account bound to the unit receives workflow notifications.
    recipient_ids = set(db_session.scalars(select(Notification.recipient_user_id)))
    assert resident.user_id in recipient_ids

    audits = list(db_session.scalars(select(AuditLog).order_by(AuditLog.id)))
    assert [row.action for row in audits] == [
        "APPROVE_TICKET",
        "ASSIGN_TECHNICIAN",
        "START_ASSIGNMENT",
        "COMPLETE_ASSIGNMENT",
    ]
    assert all(row.request_id is not None for row in audits)
    assert all(isinstance(row.id, int) and row.id > 0 for row in audits)


def test_coordinator_can_override_pending_agent_result_with_category_and_priority(db_session):
    """Once analysis has finished, however it finished, Building Management may
    still classify the report by hand.

    The ticket is moved out of the private AI phase first: while
    `classification_status` is PENDING/PROCESSING the report has not been handed
    over yet, and the coordinator cannot see it at all — which the companion
    test below asserts.
    """
    resident, _second, coordinator, _technician, binding, location, category = _seed_core(db_session)
    ticket = TicketService(db_session).create_ticket(
        resident.user_id,
        binding,
        TicketCreateRequest(location_id=location.id, description="Agent không phân loại được"),
    )
    ticket.classification_status = ClassificationStatus.FAILED
    db_session.commit()

    updated = CoordinatorService(db_session).override_classification(
        coordinator.user_id,
        ticket.id,
        ClassificationOverrideRequest(
            category_id=category.id,
            priority=Priority.P2,
            reason="BQL phân loại thủ công để tiếp tục xử lý",
        ),
    )

    assert updated.classification_status == ClassificationStatus.RESOLVED
    assert updated.category_id == category.id
    assert updated.priority == Priority.P2
    assert updated.sla_due_at is not None


def test_coordinator_cannot_touch_a_ticket_still_in_the_private_ai_phase(db_session):
    """A freshly created report is PROCESSING, so it does not exist for BQL yet."""
    resident, _second, coordinator, _technician, binding, location, category = _seed_core(db_session)
    ticket = TicketService(db_session).create_ticket(
        resident.user_id,
        binding,
        TicketCreateRequest(location_id=location.id, description="Agent chưa trả kết quả"),
    )
    assert ticket.classification_status == ClassificationStatus.PROCESSING
    service = CoordinatorService(db_session)

    # Not-found rather than forbidden: guessing the ID must not confirm it exists.
    with pytest.raises(DomainError) as read_error:
        service.get_ticket(ticket.id)
    assert read_error.value.status_code == 404

    with pytest.raises(DomainError) as write_error:
        service.override_classification(
            coordinator.user_id,
            ticket.id,
            ClassificationOverrideRequest(
                category_id=category.id,
                priority=Priority.P2,
                reason="BQL không được chạm vào phản ánh đang phân tích",
            ),
        )
    assert write_error.value.status_code == 404

    items, total = service.list_tickets(1, 20)
    assert total == 0
    assert items == []


def test_coordinator_cannot_override_classification_after_approval(db_session):
    resident, _second, coordinator, _technician, binding, location, category = _seed_core(db_session)
    ticket = TicketService(db_session).create_ticket(
        resident.user_id,
        binding,
        TicketCreateRequest(location_id=location.id, description="Rò rỉ nước tại hành lang"),
    )
    ticket.classification_status = ClassificationStatus.RESOLVED
    ticket.category_id = category.id
    ticket.priority = Priority.P2
    ticket.severity = Severity.MEDIUM
    db_session.commit()

    service = CoordinatorService(db_session)
    service.approve(coordinator.user_id, ticket.id)

    try:
        service.override_classification(
            coordinator.user_id,
            ticket.id,
            ClassificationOverrideRequest(priority=Priority.P1, reason="Không được sửa sau khi duyệt"),
        )
    except DomainError as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("Approved tickets must not allow classification overrides")


def test_unit_can_have_only_one_resident_account(db_session):
    resident, second_resident, _coordinator, _technician, binding, _location, _category = _seed_core(db_session)
    db_session.add(ResidentProfile(user_id=second_resident.user_id, unit_id=binding.unit_id, is_primary=True))
    try:
        db_session.commit()
    except Exception:
        db_session.rollback()
    else:
        raise AssertionError("unit uniqueness must reject a second Resident binding")


def test_manual_review_source_must_match_latest_ai_prediction(db_session):
    from src.database.models.ai_analysis import AIAnalysisRun
    from src.models.api.coordinator import ManualReviewResolveRequest
    from src.models.api.errors import CATEGORY_REQUIRED, DomainError
    from src.models.enums import AnalysisRunStatus, ResolutionSource, SeveritySource

    resident, _second, coordinator, _technician, binding, location, water = _seed_core(db_session)
    electrical = CategoryCatalog(code=Category.POWER_OUTAGE, display_name="Chập điện", base_score=50)
    db_session.add(electrical)
    db_session.flush()

    ticket = TicketService(db_session).create_ticket(
        resident.user_id,
        binding,
        TicketCreateRequest(location_id=location.id, description="Có nước và dây điện bất thường"),
    )
    ticket.classification_status = ClassificationStatus.MANUAL_REVIEW
    ticket.severity = Severity.HIGH
    db_session.add(
        AIAnalysisRun(
            ticket_id=ticket.id,
            run_number=1,
            text_model_version="text-v1",
            vision_model_version="vision-v1",
            text_categories=[Category.WATER.value],
            image_categories=[Category.POWER_OUTAGE.value],
            red_flag_text=False,
            red_flag_signal=False,
            severity=Severity.HIGH,
            severity_source=SeveritySource.VISION,
            category_match=False,
            status=AnalysisRunStatus.SUCCEEDED,
        )
    )
    db_session.commit()

    service = CoordinatorService(db_session)
    try:
        service.resolve_manual_review(
            coordinator.user_id,
            ticket.id,
            ManualReviewResolveRequest(
                category_id=water.id,
                resolution_source=ResolutionSource.IMAGE,
                reason="Chọn theo ảnh",
            ),
        )
    except DomainError as exc:
        assert exc.code == CATEGORY_REQUIRED
    else:
        raise AssertionError("IMAGE resolution must use a Category predicted by the image")


def test_completion_requires_technician_owned_evidence(db_session):
    resident, _second, coordinator, technician, binding, location, category = _seed_core(db_session)
    ticket = TicketService(db_session).create_ticket(
        resident.user_id,
        binding,
        TicketCreateRequest(location_id=location.id, description="Đèn hỏng"),
    )
    ticket.classification_status = ClassificationStatus.RESOLVED
    ticket.category_id = category.id
    ticket.category = category
    ticket.severity = Severity.MEDIUM
    ticket.priority = Priority.P1
    db_session.commit()
    CoordinatorService(db_session).approve(coordinator.user_id, ticket.id)
    service = AssignmentService(db_session, _StorageStub())
    assignment = service.assign(coordinator.user_id, ticket.id, technician.user_id, now=IN_SHIFT)
    service.start(technician.user_id, assignment.id)

    resident_upload = service.uploads.create_upload_session(
        resident.user_id,
        f"tickets/{resident.user_id}/2026/08/original.jpg",
        "original.jpg",
        "image/jpeg",
        128,
    )
    db_session.commit()
    try:
        service.complete(technician.user_id, assignment.id, "Xong", [resident_upload.id])
    except DomainError as exc:
        assert exc.code == COMPLETION_EVIDENCE_REQUIRED
    else:
        raise AssertionError("resident upload must not complete technician assignment")


def test_unable_to_handle_marks_ticket_unresolvable_and_records_reason(db_session):
    resident, _second, coordinator, technician, binding, location, category = _seed_core(db_session)
    ticket = _approved_ticket(db_session, resident, coordinator, binding, location, category)
    service = AssignmentService(db_session, _StorageStub())
    assignment = service.assign(coordinator.user_id, ticket.id, technician.user_id, now=IN_SHIFT)
    service.start(technician.user_id, assignment.id)

    result = service.unable_to_handle(technician.user_id, assignment.id, "Cần nhà thầu chuyên dụng")

    assert result.status == AssignmentStatus.UNABLE_TO_HANDLE
    assert result.is_active is False
    assert result.unable_reason == "Cần nhà thầu chuyên dụng"
    assert result.ended_at is not None
    assert result.ticket.status == TicketStatus.UNRESOLVABLE
    history = db_session.scalar(
        select(TicketStatusHistory)
        .where(TicketStatusHistory.ticket_id == ticket.id)
        .order_by(TicketStatusHistory.created_at.desc())
    )
    assert history.to_status == TicketStatus.UNRESOLVABLE
    assert history.reason == "Cần nhà thầu chuyên dụng"
    assert (
        db_session.scalar(select(Notification).where(Notification.notification_type == "TICKET_UNRESOLVABLE"))
        is not None
    )
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "UNABLE_TO_HANDLE"))
    assert audit is not None
    assert audit.reason == "Cần nhà thầu chuyên dụng"


def test_assignment_requires_matching_technician_skill(db_session):
    resident, _second, coordinator, technician, binding, location, category = _seed_core(db_session)
    ticket = _approved_ticket(db_session, resident, coordinator, binding, location, category)

    assignment = AssignmentService(db_session).assign(coordinator.user_id, ticket.id, technician.user_id, now=IN_SHIFT)

    assert assignment.technician_id == technician.user_id


def test_ticket_lists_filter_by_category_id_including_dynamic_category(db_session):
    resident, _second, coordinator, _technician, binding, location, water = _seed_core(db_session)
    dynamic = CategoryCatalog(code="CUSTOM_DYNAMIC", display_name="Custom dynamic", base_score=12)
    db_session.add(dynamic)
    db_session.commit()

    water_ticket = TicketService(db_session).create_ticket(
        resident.user_id,
        binding,
        TicketCreateRequest(location_id=location.id, description="Water"),
    )
    dynamic_ticket = TicketService(db_session).create_ticket(
        resident.user_id,
        binding,
        TicketCreateRequest(location_id=location.id, description="Dynamic"),
    )
    water_ticket.category_id = water.id
    water_ticket.classification_status = ClassificationStatus.RESOLVED
    dynamic_ticket.category_id = dynamic.id
    dynamic_ticket.classification_status = ClassificationStatus.RESOLVED
    db_session.commit()

    resident_items, resident_total = TicketService(db_session).list_my_tickets(
        binding,
        resident.user_id,
        page=1,
        page_size=20,
        category_id=dynamic.id,
    )
    coordinator_items, coordinator_total = CoordinatorService(db_session).list_tickets(
        1,
        20,
        category_id=dynamic.id,
    )

    assert resident_total == 1
    assert [item.id for item in resident_items] == [dynamic_ticket.id]
    assert coordinator_total == 1
    assert [item.id for item in coordinator_items] == [dynamic_ticket.id]


def test_coordinator_ticket_list_orders_newest_first(db_session):
    resident, _second, _coordinator, _technician, binding, location, category = _seed_core(db_session)
    older = TicketService(db_session).create_ticket(
        resident.user_id,
        binding,
        TicketCreateRequest(location_id=location.id, description="Ticket cũ ưu tiên cao"),
    )
    newer = TicketService(db_session).create_ticket(
        resident.user_id,
        binding,
        TicketCreateRequest(location_id=location.id, description="Ticket mới ưu tiên thấp"),
    )
    now = datetime.now(UTC)
    older.created_at = now - timedelta(hours=1)
    older.category_id = category.id
    older.classification_status = ClassificationStatus.RESOLVED
    older.priority = Priority.P3
    newer.created_at = now
    newer.category_id = category.id
    newer.classification_status = ClassificationStatus.RESOLVED
    newer.priority = Priority.P1
    db_session.commit()

    items, total = CoordinatorService(db_session).list_tickets(1, 20)

    assert total == 2
    assert [item.id for item in items] == [newer.id, older.id]


def test_assignment_rejects_wrong_skill(db_session):
    resident, _second, coordinator, technician, binding, location, category = _seed_core(db_session)
    wrong = CategoryCatalog(code=Category.POWER_OUTAGE, display_name="Chập điện", base_score=50)
    db_session.add(wrong)
    for skill in list(technician.skills):
        db_session.delete(skill)
    db_session.flush()
    db_session.add(TechnicianSkill(technician=technician, category=wrong))
    db_session.commit()
    ticket = _approved_ticket(db_session, resident, coordinator, binding, location, category)

    try:
        AssignmentService(db_session).assign(coordinator.user_id, ticket.id, technician.user_id, now=IN_SHIFT)
    except DomainError as exc:
        assert exc.code == "TECHNICIAN_NOT_ELIGIBLE"
    else:
        raise AssertionError("wrong skill must be rejected")


def test_assignment_rejects_zero_skills(db_session):
    resident, _second, coordinator, technician, binding, location, category = _seed_core(db_session)
    for skill in list(technician.skills):
        db_session.delete(skill)
    db_session.commit()
    ticket = _approved_ticket(db_session, resident, coordinator, binding, location, category)

    try:
        AssignmentService(db_session).assign(coordinator.user_id, ticket.id, technician.user_id, now=IN_SHIFT)
    except DomainError as exc:
        assert exc.code == "TECHNICIAN_NOT_ELIGIBLE"
    else:
        raise AssertionError("zero skills must be rejected")


def test_assignment_rejects_inactive_or_unavailable_technician(db_session):
    resident, _second, coordinator, technician, binding, location, category = _seed_core(db_session)
    ticket = _approved_ticket(db_session, resident, coordinator, binding, location, category)
    technician.is_active = False
    db_session.commit()

    try:
        AssignmentService(db_session).assign(coordinator.user_id, ticket.id, technician.user_id, now=IN_SHIFT)
    except DomainError as exc:
        assert exc.code == "TECHNICIAN_NOT_ELIGIBLE"
    else:
        raise AssertionError("inactive technician must be rejected")

    technician.is_active = True
    technician.is_available = False
    db_session.commit()
    try:
        AssignmentService(db_session).assign(coordinator.user_id, ticket.id, technician.user_id, now=IN_SHIFT)
    except DomainError as exc:
        assert exc.code == "TECHNICIAN_NOT_ELIGIBLE"
    else:
        raise AssertionError("unavailable technician must be rejected")


def test_resident_response_distinguishes_approved_from_assigned(db_session):
    from src.api.routes.tickets import resident_ticket_response

    resident, _second, coordinator, technician, binding, location, category = _seed_core(db_session)
    ticket = _approved_ticket(db_session, resident, coordinator, binding, location, category)

    unassigned = resident_ticket_response(TicketService(db_session).get_ticket(binding, ticket.id, resident.user_id))
    assert unassigned.display_status != "Đã gán kỹ thuật viên"
    assert unassigned.technician is None

    AssignmentService(db_session).assign(coordinator.user_id, ticket.id, technician.user_id, now=IN_SHIFT)
    assigned = resident_ticket_response(TicketService(db_session).get_ticket(binding, ticket.id, resident.user_id))
    payload = assigned.model_dump()

    assert assigned.display_status == "Đã gán kỹ thuật viên"
    assert assigned.technician is not None
    assert assigned.technician.id == technician.user_id
    assert assigned.technician.full_name == "Tech One"
    assert "phone" not in str(payload).lower()
    assert "email" not in str(payload).lower()


def test_technician_downloads_own_ticket_allowed_attachment_types(db_session):
    from types import SimpleNamespace

    from src.api.routes.technician_assignments import attachment_download_url

    resident, _second, coordinator, technician, binding, location, category = _seed_core(db_session)
    ticket = _approved_ticket(db_session, resident, coordinator, binding, location, category)
    assignment = AssignmentService(db_session).assign(coordinator.user_id, ticket.id, technician.user_id, now=IN_SHIFT)
    attachments = [
        TicketAttachment(
            ticket_id=ticket.id,
            attachment_type=attachment_type,
            storage_bucket="ticket-attachments",
            object_path=f"tickets/{ticket.id}/{attachment_type.value}.jpg",
            mime_type="image/jpeg",
            size_bytes=10,
            uploaded_by=resident.user_id,
        )
        for attachment_type in (
            AttachmentType.ISSUE_ORIGINAL,
            AttachmentType.RESIDENT_SUPPLEMENT,
            AttachmentType.TECHNICIAN_COMPLETION,
        )
    ]
    db_session.add_all(attachments)
    db_session.commit()
    request = SimpleNamespace(state=SimpleNamespace(request_id="req-1"))
    actor = SimpleNamespace(user=SimpleNamespace(user_id=technician.user_id))

    for attachment in attachments:
        response = attachment_download_url(request, assignment.id, attachment.id, actor, db_session, _StorageStub())
        assert response["data"].attachment_id == attachment.id
        assert response["data"].signed_download_url.startswith("https://signed.local/")


def test_technician_queue_sorts_by_priority_and_includes_attachment_metadata(db_session):
    from src.api.routes.technician_assignments import assignment_response
    from src.repositories.assignment_repository import AssignmentRepository

    resident, _second, coordinator, technician, binding, location, category = _seed_core(db_session)
    tickets = []
    for label, priority in (("p1", Priority.P1), ("p3", Priority.P3), ("p2", Priority.P2)):
        ticket = _approved_ticket(db_session, resident, coordinator, binding, location, category)
        ticket.description = label
        ticket.priority = priority
        db_session.commit()
        assignment = AssignmentService(db_session).assign(
            coordinator.user_id, ticket.id, technician.user_id, now=IN_SHIFT
        )
        if priority == Priority.P3:
            db_session.add(
                TicketAttachment(
                    ticket_id=ticket.id,
                    attachment_type=AttachmentType.ISSUE_ORIGINAL,
                    storage_bucket="ticket-attachments",
                    object_path=f"tickets/{ticket.id}/original.jpg",
                    mime_type="image/jpeg",
                    size_bytes=10,
                    uploaded_by=resident.user_id,
                )
            )
            db_session.commit()
        tickets.append((ticket, assignment))

    rows = AssignmentRepository(db_session).list_for_technician(technician.user_id)
    responses = [assignment_response(row).model_dump() for row in rows]

    assert [item["ticket"]["priority"] for item in responses] == ["P3", "P2", "P1"]
    p3 = responses[0]
    assert p3["ticket"]["sla_due_at"] == tickets[1][0].sla_due_at
    assert p3["ticket"]["attachments"][0]["download_url_endpoint"].startswith("/api/v1/technician/assignments/")
    assert "score_total" not in str(p3)
    assert "model_version" not in str(p3)
    assert "object_path" not in str(p3)


def test_technician_download_masks_other_assignment_and_wrong_ticket_attachment(db_session):
    from types import SimpleNamespace

    from src.api.routes.technician_assignments import attachment_download_url

    resident, _second, coordinator, technician, binding, location, category = _seed_core(db_session)
    other_user = UserProfile(user_id=uuid4(), role=UserRole.TECHNICIAN, full_name="Tech Two")
    other_technician = TechnicianProfile(user=other_user, is_active=True, is_available=True)
    db_session.add_all([other_user, other_technician])
    db_session.flush()
    db_session.add(TechnicianSkill(technician=other_technician, category=category))
    db_session.commit()
    ticket = _approved_ticket(db_session, resident, coordinator, binding, location, category)
    assignment = AssignmentService(db_session).assign(coordinator.user_id, ticket.id, technician.user_id, now=IN_SHIFT)
    attachment = TicketAttachment(
        ticket_id=ticket.id,
        attachment_type=AttachmentType.ISSUE_ORIGINAL,
        storage_bucket="ticket-attachments",
        object_path=f"tickets/{ticket.id}/original.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        uploaded_by=resident.user_id,
    )
    other_ticket = TicketService(db_session).create_ticket(
        resident.user_id,
        binding,
        TicketCreateRequest(location_id=location.id, description="Khác"),
    )
    wrong_ticket_attachment = TicketAttachment(
        ticket_id=other_ticket.id,
        attachment_type=AttachmentType.ISSUE_ORIGINAL,
        storage_bucket="ticket-attachments",
        object_path=f"tickets/{other_ticket.id}/original.jpg",
        mime_type="image/jpeg",
        size_bytes=10,
        uploaded_by=resident.user_id,
    )
    db_session.add_all([attachment, wrong_ticket_attachment])
    db_session.commit()
    request = SimpleNamespace(state=SimpleNamespace(request_id="req-1"))

    for actor_user_id, attachment_id, code in (
        (other_technician.user_id, attachment.id, "ASSIGNMENT_NOT_FOUND"),
        (technician.user_id, wrong_ticket_attachment.id, "ATTACHMENT_NOT_FOUND"),
    ):
        actor = SimpleNamespace(user=SimpleNamespace(user_id=actor_user_id))
        try:
            attachment_download_url(request, assignment.id, attachment_id, actor, db_session, _StorageStub())
        except DomainError as exc:
            assert exc.status_code == 404
            assert exc.code == code
        else:
            raise AssertionError("unauthorized attachment read must be masked")
