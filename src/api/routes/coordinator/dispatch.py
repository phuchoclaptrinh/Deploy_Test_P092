"""Automatic Assignment: the toggle, and visibility into what it did (§2, §10).

The toggle is one endpoint in each direction and carries no schedule, no delay
and no dependency on a prior workflow step -- §9 removed the rule that it could
only be enabled after confirming a proposal. What replaces that rule is
`acknowledged`: the server refuses to enable autonomy unless the client says the
confirmation modal was shown and accepted, so §2's explanation cannot be skipped
by calling the API directly.

The two read endpoints exist because "the system assigned this by itself" is
only acceptable if a person can go and look at what it did. `/dispatch/events`
is the queue and its outcomes; `/dispatch/at-risk-decisions` is the subset where
a trade-off was made, which is the list a manager actually reviews.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from src.api.dependencies.auth import CurrentActor, require_coordinator
from src.api.dependencies.database import get_db
from src.api.routes.coordinator._common import ok
from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.dispatch import AtRiskDecision, DispatchEvent
from src.database.models.technician import TechnicianProfile
from src.database.models.user_profile import UserProfile
from src.models.api.common import ApiResponse
from src.models.api.dispatch import (
    AtRiskDecisionResponse,
    AutoAssignmentToggleRequest,
    AutoAssignmentToggleResponse,
    DispatchEventResponse,
)
from src.models.api.errors import VALIDATION_ERROR, DomainError
from src.services.auto_assignment_settings_service import AutoAssignmentSettingsService

router = APIRouter()

#: §2, verbatim. Returned with the 400 a client gets for skipping the modal, so
#: the text a manager must be shown lives in the backend rather than only in a
#: frontend string a future redesign could quietly drop.
TOGGLE_CONFIRMATION_TEXT = (
    "Những phản ánh đã được AI phân loại xác định, không trùng lặp và không phải phản ánh khẩn cấp "
    "sẽ được tự động duyệt, bỏ qua bước gộp nhóm và được phân công ngay lập tức. "
    "Những phản ánh không đáp ứng các điều kiện này sẽ được chuyển cho Ban quản lý."
)


def _ticket_code(ticket_id: UUID) -> str:
    return f"PA-{str(ticket_id).replace('-', '').upper()[:6]}"


def toggle_response(db: Session, row: AutoAssignmentSetting) -> AutoAssignmentToggleResponse:
    name = None
    if row.enabled_by_user_id is not None:
        name = db.scalar(select(UserProfile.full_name).where(UserProfile.user_id == row.enabled_by_user_id))
    count = db.scalar(select(func.count(DispatchEvent.id)).where(DispatchEvent.is_open.is_(True))) or 0
    return AutoAssignmentToggleResponse(
        enabled=bool(row.enabled),
        version=row.version,
        enabled_at=row.enabled_at,
        enabled_by_user_id=row.enabled_by_user_id,
        enabled_by_name=name,
        updated_at=row.updated_at,
        open_event_count=count,
    )


@router.get(
    "/auto-assignment",
    response_model=ApiResponse[AutoAssignmentToggleResponse],
    summary="Trạng thái phân việc tự động",
)
def get_toggle(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, toggle_response(db, AutoAssignmentSettingsService(db).get()))


@router.put(
    "/auto-assignment",
    response_model=ApiResponse[AutoAssignmentToggleResponse],
    summary="Bật/tắt phân việc tự động",
    description=(
        "Bật yêu cầu `acknowledged=true`, tương ứng với việc người dùng đã đọc và xác nhận hộp "
        "thoại giải thích. Tắt luôn được phép và không hoàn tác các phân công đã tạo."
    ),
)
def set_toggle(
    request: Request,
    body: AutoAssignmentToggleRequest,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    if body.enabled and not body.acknowledged:
        # Refused in the backend rather than only in the modal, so the
        # explanation §2 requires cannot be skipped by any client.
        raise DomainError(VALIDATION_ERROR, TOGGLE_CONFIRMATION_TEXT, 400)
    row = AutoAssignmentSettingsService(db).set_enabled(
        actor.user.user_id,
        enabled=body.enabled,
        expected_version=body.expected_version,
    )
    return ok(request, toggle_response(db, row))


@router.get(
    "/dispatch/events",
    response_model=ApiResponse[list[DispatchEventResponse]],
    summary="Hàng đợi phân việc tự động",
)
def list_events(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    query = (
        select(DispatchEvent)
        .options(joinedload(DispatchEvent.selected_technician).joinedload(TechnicianProfile.user))
        .order_by(DispatchEvent.enqueued_at.desc())
        .limit(limit)
    )
    if status:
        query = query.where(DispatchEvent.status == status)
    rows = db.scalars(query).unique()
    return ok(
        request,
        [
            DispatchEventResponse(
                id=row.id,
                ticket_id=row.ticket_id,
                ticket_display_code=_ticket_code(row.ticket_id),
                status=row.status,
                priority=row.priority,
                risk_state=row.risk_state,
                decision_source=row.decision_source,
                selected_technician_id=row.selected_technician_id,
                selected_technician_name=(
                    row.selected_technician.user.full_name
                    if row.selected_technician and row.selected_technician.user
                    else None
                ),
                assignment_id=row.assignment_id,
                batch_id=row.batch_id,
                attempt_count=row.attempt_count,
                planned_start_at=row.planned_start_at,
                planned_finish_at=row.planned_finish_at,
                slack_seconds=row.slack_seconds,
                escalation_reason=row.escalation_reason,
                error_code=row.error_code,
                enqueued_at=row.enqueued_at,
                available_at=row.available_at,
                decided_at=row.decided_at,
            )
            for row in rows
        ],
    )


@router.get(
    "/dispatch/at-risk-decisions",
    response_model=ApiResponse[list[AtRiskDecisionResponse]],
    summary="Các quyết định phân việc có rủi ro trễ lịch",
    description=(
        "Chỉ những ticket rơi vào trạng thái AT_RISK. `decision_source=AGENT` nghĩa là agent đã "
        "cân nhắc và chọn; `SCHEDULER_FALLBACK` nghĩa là agent không phản hồi kịp và hệ thống đã "
        "chọn kỹ thuật viên có mức trễ thấp nhất."
    ),
)
def list_at_risk_decisions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(AtRiskDecision)
        .options(joinedload(AtRiskDecision.technician).joinedload(TechnicianProfile.user))
        .order_by(AtRiskDecision.created_at.desc())
        .limit(limit)
    ).unique()
    return ok(
        request,
        [
            AtRiskDecisionResponse(
                id=row.id,
                dispatch_event_id=row.dispatch_event_id,
                ticket_id=row.ticket_id,
                ticket_display_code=_ticket_code(row.ticket_id),
                batch_id=row.batch_id,
                technician_id=row.technician_id,
                technician_name=(
                    row.technician.user.full_name if row.technician and row.technician.user else None
                ),
                decision_source=row.decision_source,
                reason=row.reason,
                model_name=row.model_name,
                latency_ms=row.latency_ms,
                candidate_technician_ids=[UUID(value) for value in (row.candidate_technician_ids or [])],
                slack_seconds=row.slack_seconds,
                error_code=row.error_code,
                created_at=row.created_at,
            )
            for row in rows
        ],
    )
