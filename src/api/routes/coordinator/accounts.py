"""Coordinator-managed account routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor, require_coordinator
from src.api.dependencies.database import get_db
from src.api.routes.coordinator._common import ok
from src.models.api.common import ApiResponse
from src.models.api.coordinator import (
    CoordinatorResidentSummaryResponse,
    ManagerAccountResponse,
    ManagerAccountStatusUpdateRequest,
    ManagerCreateResidentRequest,
    ManagerCreateTechnicianRequest,
)
from src.services.manager_account_service import ManagerAccountService

router = APIRouter()


@router.get("/accounts/residents", response_model=ApiResponse[list[CoordinatorResidentSummaryResponse]])
def list_resident_accounts(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, ManagerAccountService(db).list_residents())


@router.post(
    "/accounts/residents",
    response_model=ApiResponse[ManagerAccountResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_resident_account(
    request: Request,
    body: ManagerCreateResidentRequest,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, ManagerAccountService(db).create_resident(body))


@router.post("/accounts/residents/{user_id}/reset-password", response_model=ApiResponse[ManagerAccountResponse])
def reset_resident_password(
    request: Request,
    user_id: UUID,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, ManagerAccountService(db).reset_resident_password(user_id))


@router.patch("/accounts/residents/{user_id}/status", response_model=ApiResponse[ManagerAccountResponse])
def update_resident_status(
    request: Request,
    user_id: UUID,
    body: ManagerAccountStatusUpdateRequest,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, ManagerAccountService(db).set_resident_active(user_id, body.is_active))


@router.post(
    "/accounts/technicians",
    response_model=ApiResponse[ManagerAccountResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_technician_account(
    request: Request,
    body: ManagerCreateTechnicianRequest,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, ManagerAccountService(db).create_technician(body))


@router.post("/accounts/technicians/{user_id}/reset-password", response_model=ApiResponse[ManagerAccountResponse])
def reset_technician_password(
    request: Request,
    user_id: UUID,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, ManagerAccountService(db).reset_technician_password(user_id))


@router.delete("/accounts/technicians/{user_id}", response_model=ApiResponse[ManagerAccountResponse])
def delete_technician_account(
    request: Request,
    user_id: UUID,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, ManagerAccountService(db).delete_technician(user_id))
