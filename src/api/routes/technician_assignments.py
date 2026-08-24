"""Technician assignment routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor, require_technician
from src.api.dependencies.database import get_db
from src.api.routes.storage import get_storage_service
from src.models.api.common import ApiResponse
from src.models.api.errors import ASSIGNMENT_NOT_FOUND, ATTACHMENT_NOT_FOUND, DomainError
from src.models.api.technician import (
    AssignmentTicketSummary,
    CompleteAssignmentRequest,
    RejectAssignmentRequest,
    TechnicianAssignmentResponse,
    TechnicianAvailabilityResponse,
    UnableToHandleRequest,
    UpdateTechnicianAvailabilityRequest,
)
from src.models.api.tickets import AttachmentDownloadUrlResponse, TicketAttachmentResponse
from src.models.enums import AttachmentType
from src.repositories.assignment_repository import AssignmentRepository
from src.repositories.attachment_repository import AttachmentRepository
from src.services.assignment_service import AssignmentService
from src.services.storage_service import StorageService
from src.services.technician_report_service import record_availability

router = APIRouter()


def _ok(request: Request, data):
    return {"data": data, "meta": {}, "error": None, "request_id": request.state.request_id}


def assignment_response(row) -> TechnicianAssignmentResponse:
    ticket = row.ticket
    return TechnicianAssignmentResponse(
        id=row.id,
        ticket=AssignmentTicketSummary(
            id=ticket.id,
            description=ticket.description,
            status=ticket.status,
            category_display_name=ticket.category.display_name if ticket.category else None,
            location_label=ticket.location.label if ticket.location else None,
            priority=ticket.priority,
            sla_due_at=ticket.sla_due_at,
            attachments=[
                TicketAttachmentResponse(
                    id=attachment.id,
                    mime_type=attachment.mime_type,
                    size_bytes=attachment.size_bytes,
                    download_url_endpoint=(
                        f"/api/v1/technician/assignments/{row.id}/attachments/{attachment.id}/download-url"
                    ),
                )
                for attachment in ticket.attachments
            ],
        ),
        status=row.status,
        assigned_at=row.assigned_at,
        accepted_at=row.accepted_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        ended_at=row.ended_at,
        unable_reason=row.unable_reason,
        reject_reason=row.rejection_reason,
        completion_note=row.completion_note,
    )


@router.get("/availability", response_model=ApiResponse[TechnicianAvailabilityResponse])
def get_availability(request: Request, actor: CurrentActor = Depends(require_technician)):
    profile = actor.technician_profile
    assert profile is not None
    return _ok(request, TechnicianAvailabilityResponse(is_available=profile.is_available))


@router.patch("/availability", response_model=ApiResponse[TechnicianAvailabilityResponse])
def update_availability(
    request: Request,
    body: UpdateTechnicianAvailabilityRequest,
    actor: CurrentActor = Depends(require_technician),
    db: Session = Depends(get_db),
):
    """Change only the caller's readiness and retain an auditable transition."""
    profile = actor.technician_profile
    assert profile is not None
    if profile.is_available != body.is_available:
        profile.is_available = body.is_available
        record_availability(
            db,
            profile.user_id,
            is_available=body.is_available,
            changed_by_user_id=actor.user.user_id,
            source="TECHNICIAN_SELF_SERVICE",
        )
        db.commit()
    return _ok(request, TechnicianAvailabilityResponse(is_available=profile.is_available))


@router.get("/assignments", response_model=ApiResponse[list[TechnicianAssignmentResponse]])
def list_assignments(request: Request, actor: CurrentActor = Depends(require_technician), db: Session = Depends(get_db)):
    rows = AssignmentRepository(db).list_for_technician(actor.user.user_id)
    return _ok(request, [assignment_response(row) for row in rows])


@router.get("/assignments/{assignment_id}", response_model=ApiResponse[TechnicianAssignmentResponse])
def get_assignment(request: Request, assignment_id: UUID, actor: CurrentActor = Depends(require_technician), db: Session = Depends(get_db)):
    row = AssignmentRepository(db).get_for_technician(assignment_id, actor.user.user_id)
    if row is None:
        raise DomainError(ASSIGNMENT_NOT_FOUND, "Assignment không tồn tại.", 404)
    return _ok(request, assignment_response(row))


@router.post("/assignments/{assignment_id}/accept", response_model=ApiResponse[TechnicianAssignmentResponse])
def accept_assignment(request: Request, assignment_id: UUID, actor: CurrentActor = Depends(require_technician), db: Session = Depends(get_db)):
    row = AssignmentService(db).accept(actor.user.user_id, assignment_id)
    return _ok(request, assignment_response(row))


@router.post("/assignments/{assignment_id}/start", response_model=ApiResponse[TechnicianAssignmentResponse])
def start_assignment(request: Request, assignment_id: UUID, actor: CurrentActor = Depends(require_technician), db: Session = Depends(get_db)):
    row = AssignmentService(db).start(actor.user.user_id, assignment_id)
    return _ok(request, assignment_response(row))


@router.post("/assignments/{assignment_id}/unable-to-handle", response_model=ApiResponse[TechnicianAssignmentResponse])
def unable_to_handle(request: Request, assignment_id: UUID, body: UnableToHandleRequest, actor: CurrentActor = Depends(require_technician), db: Session = Depends(get_db)):
    row = AssignmentService(db).unable_to_handle(actor.user.user_id, assignment_id, body.reason)
    return _ok(request, assignment_response(row))


@router.post("/assignments/{assignment_id}/reject", response_model=ApiResponse[TechnicianAssignmentResponse])
def reject_assignment(request: Request, assignment_id: UUID, body: RejectAssignmentRequest, actor: CurrentActor = Depends(require_technician), db: Session = Depends(get_db)):
    row = AssignmentService(db).reject(actor.user.user_id, assignment_id, body.reason)
    return _ok(request, assignment_response(row))


@router.post("/assignments/{assignment_id}/complete", response_model=ApiResponse[TechnicianAssignmentResponse])
def complete_assignment(request: Request, assignment_id: UUID, body: CompleteAssignmentRequest, actor: CurrentActor = Depends(require_technician), db: Session = Depends(get_db)):
    row = AssignmentService(db).complete(actor.user.user_id, assignment_id, body.resolution_note, body.evidence_upload_ids)
    return _ok(request, assignment_response(row))


@router.get("/assignments/{assignment_id}/attachments/{attachment_id}/download-url", response_model=ApiResponse[AttachmentDownloadUrlResponse])
def attachment_download_url(
    request: Request,
    assignment_id: UUID,
    attachment_id: UUID,
    actor: CurrentActor = Depends(require_technician),
    db: Session = Depends(get_db),
    storage_service: StorageService = Depends(get_storage_service),
):
    assignment = AssignmentRepository(db).get_for_technician(assignment_id, actor.user.user_id)
    if assignment is None:
        raise DomainError(ASSIGNMENT_NOT_FOUND, "Assignment không tồn tại.", 404)
    attachment = AttachmentRepository(db).get_attachment(assignment.ticket_id, attachment_id)
    readable_types = {
        AttachmentType.ISSUE_ORIGINAL,
        AttachmentType.RESIDENT_SUPPLEMENT,
        AttachmentType.TECHNICIAN_COMPLETION,
    }
    if attachment is None or attachment.attachment_type not in readable_types:
        raise DomainError(ATTACHMENT_NOT_FOUND, "Attachment không tồn tại.", 404)
    return _ok(
        request,
        AttachmentDownloadUrlResponse(
            attachment_id=attachment.id,
            signed_download_url=storage_service.create_signed_download_url(attachment.object_path),
            expires_in=storage_service.settings.supabase_signed_download_ttl_seconds,
            mime_type=attachment.mime_type,
            size_bytes=attachment.size_bytes,
        ),
    )
