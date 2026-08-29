"""Per-user notification APIs from Self Dev v2."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor, get_current_actor
from src.api.dependencies.database import get_db
from src.database.models.notification import Notification
from src.models.api.common import ApiResponse
from src.models.api.errors import FORBIDDEN, DomainError
from src.models.api.notifications import NotificationResponse
from src.models.enums import NotificationStatus

router = APIRouter()


def _serialize(row: Notification) -> NotificationResponse:
    return NotificationResponse(
        id=row.id,
        ticket_id=row.ticket_id,
        notification_type=row.notification_type,
        channel=row.channel,
        title=row.title,
        body=row.body,
        status=row.status,
        created_at=row.created_at,
        sent_at=row.sent_at,
    )


@router.get("", response_model=ApiResponse[list[NotificationResponse]], summary="Danh sách thông báo")
def list_notifications(
    http_request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
):
    rows = list(
        db.scalars(
            select(Notification)
            .where(Notification.recipient_user_id == actor.user.user_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
    )
    return {
        "data": [_serialize(row) for row in rows],
        "meta": {"count": len(rows)},
        "error": None,
        "request_id": http_request.state.request_id,
    }


@router.post("/{notification_id}/read", response_model=ApiResponse[NotificationResponse], summary="Đánh dấu thông báo đã đọc")
def mark_read(
    http_request: Request,
    notification_id: UUID,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(Notification).where(Notification.id == notification_id).with_for_update())
    if row is None or row.recipient_user_id != actor.user.user_id:
        raise DomainError(FORBIDDEN, "Không có quyền truy cập thông báo này.", 404)
    row.status = NotificationStatus.READ
    db.commit()
    db.refresh(row)
    return {"data": _serialize(row), "meta": {}, "error": None, "request_id": http_request.state.request_id}
