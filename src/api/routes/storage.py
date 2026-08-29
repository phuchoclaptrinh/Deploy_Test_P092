"""Private signed-upload extension used by resident ticket APIs.

Self Dev describes multipart image upload on POST /tickets. This backend
retains a stricter two-step signed-upload contract so the frontend never sends a
private Storage object path. Functional rules (resident ownership, image-only,
one-time consumption) remain backend controlled.
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request, status
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor, require_resident, require_technician
from src.api.dependencies.database import get_db
from src.models.api.common import ApiResponse
from src.models.api.storage import SignedUploadRequest, SignedUploadResponse
from src.repositories.upload_session_repository import UploadSessionRepository
from src.services.storage_service import StorageService

router = APIRouter()


def get_storage_service() -> StorageService:
    return StorageService()


SignedUploadBody = Annotated[SignedUploadRequest, Body()]


@router.post(
    "/ticket-attachments/upload-url",
    response_model=ApiResponse[SignedUploadResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Tạo signed upload URL cho ảnh phản ánh",
)
def create_ticket_attachment_upload_url(
    http_request: Request,
    body: SignedUploadBody,
    actor: CurrentActor = Depends(require_resident),
    db: Session = Depends(get_db),
    storage_service: StorageService = Depends(get_storage_service),
):
    target = storage_service.create_signed_upload_target(actor.user.user_id, body)
    try:
        upload_session = UploadSessionRepository(db).create_upload_session(
            owner_user_id=actor.user.user_id,
            storage_path=target.storage_path,
            original_filename=body.original_filename,
            mime_type=body.mime_type,
            file_size=body.file_size,
        )
        db.commit()
        db.refresh(upload_session)
    except Exception:
        db.rollback()
        raise
    data = SignedUploadResponse(
        upload_id=upload_session.id,
        signed_upload_url=target.signed_upload_url,
        signed_upload_token=target.signed_upload_token,
        expires_in=target.expires_in,
        required_headers=target.required_headers,
    )
    return {"data": data, "meta": {}, "error": None, "request_id": http_request.state.request_id}


@router.post(
    "/completion-evidence/upload-url",
    response_model=ApiResponse[SignedUploadResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Tạo signed upload URL cho ảnh hoàn thành của Kỹ thuật viên",
)
def create_completion_evidence_upload_url(
    http_request: Request,
    body: SignedUploadBody,
    actor: CurrentActor = Depends(require_technician),
    db: Session = Depends(get_db),
    storage_service: StorageService = Depends(get_storage_service),
):
    target = storage_service.create_completion_evidence_upload_target(actor.user.user_id, body)
    try:
        upload_session = UploadSessionRepository(db).create_upload_session(
            owner_user_id=actor.user.user_id,
            storage_path=target.storage_path,
            original_filename=body.original_filename,
            mime_type=body.mime_type,
            file_size=body.file_size,
        )
        db.commit()
        db.refresh(upload_session)
    except Exception:
        db.rollback()
        raise
    data = SignedUploadResponse(
        upload_id=upload_session.id,
        signed_upload_url=target.signed_upload_url,
        signed_upload_token=target.signed_upload_token,
        expires_in=target.expires_in,
        required_headers=target.required_headers,
    )
    return {"data": data, "meta": {}, "error": None, "request_id": http_request.state.request_id}
