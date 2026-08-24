"""Coordinator Technician roster routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor, require_coordinator
from src.api.dependencies.database import get_db
from src.api.routes.coordinator._common import ok
from src.models.api.common import ApiResponse
from src.models.api.coordinator import TechnicianSummaryResponse
from src.models.api.errors import TECHNICIAN_NOT_FOUND, DomainError
from src.repositories.technician_repository import TechnicianRepository
from src.services.phone import E164_RE

router = APIRouter()


def _e164_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    return candidate if E164_RE.fullmatch(candidate) else None


def _technician_response(row) -> TechnicianSummaryResponse:
    return TechnicianSummaryResponse(
        user_id=row.user_id,
        full_name=row.user.full_name if row.user else None,
        # The identity phone is authoritative; the profile column is the older
        # free-text field and only stands in when it is already E.164.
        phone_e164=(row.user.phone_e164 if row.user else None) or _e164_or_none(row.phone_number),
        is_active=row.is_active,
        is_available=row.is_available,
        skill_category_ids=[skill.category_id for skill in row.skills],
    )


@router.get("/technicians", response_model=ApiResponse[list[TechnicianSummaryResponse]])
def list_technicians(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, [_technician_response(row) for row in TechnicianRepository(db).list_technicians()])


@router.get("/technicians/{technician_id}", response_model=ApiResponse[TechnicianSummaryResponse])
def get_technician(
    request: Request,
    technician_id: UUID,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    row = TechnicianRepository(db).get_technician(technician_id)
    if row is None:
        raise DomainError(TECHNICIAN_NOT_FOUND, "Technician không tồn tại.", 404)
    return ok(request, _technician_response(row))
