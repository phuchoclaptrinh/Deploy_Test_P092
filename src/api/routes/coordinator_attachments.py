"""Coordinator attachment download route."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor, require_coordinator
from src.api.dependencies.database import get_db
from src.api.routes.storage import get_storage_service
from src.models.api.common import ApiResponse
from src.models.api.errors import ATTACHMENT_NOT_FOUND, DomainError
from src.models.api.tickets import AttachmentDownloadUrlResponse
from src.repositories.attachment_repository import AttachmentRepository
from src.services.coordinator_service import CoordinatorService
from src.services.storage_service import StorageService

router = APIRouter()


@router.get(
    "/tickets/{ticket_id}/attachments/{attachment_id}/download-url",
    response_model=ApiResponse[AttachmentDownloadUrlResponse],
)
def attachment_download_url(
    request: Request,
    ticket_id: UUID,
    attachment_id: UUID,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
    storage_service: StorageService = Depends(get_storage_service),
):
    service = CoordinatorService(db)
    # Authorize the parent ticket first: a report still in the private AI phase
    # is not visible to Building Management, so no URL is signed for its photos.
    ticket = service.get_ticket(ticket_id)
    attachment = AttachmentRepository(db).get_attachment(ticket.id, attachment_id)
    if attachment is None:
        raise DomainError(ATTACHMENT_NOT_FOUND, "Attachment không tồn tại.", 404)
    data = AttachmentDownloadUrlResponse(
        attachment_id=attachment.id,
        signed_download_url=storage_service.create_signed_download_url(attachment.object_path),
        expires_in=storage_service.settings.supabase_signed_download_ttl_seconds,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
    )
    return {"data": data, "meta": {}, "error": None, "request_id": request.state.request_id}
