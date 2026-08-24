"""Coordinator audit log routes."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor, require_coordinator
from src.api.dependencies.database import get_db
from src.api.routes.coordinator._common import ok
from src.models.api.common import ApiResponse
from src.models.api.coordinator import AuditLogResponse
from src.services.coordinator_service import CoordinatorService

router = APIRouter()


@router.get("/audit-logs", response_model=ApiResponse[list[AuditLogResponse]])
def audit_logs(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
    actor_user_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    entity_id: UUID | None = Query(default=None),
    created_from: datetime | None = Query(default=None, alias="from"),
    created_to: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=500),
):
    rows = CoordinatorService(db).list_audit_logs(
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
    )
    return ok(request, [AuditLogResponse.model_validate(row, from_attributes=True) for row in rows])
