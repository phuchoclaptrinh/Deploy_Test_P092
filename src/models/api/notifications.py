"""Notification API schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.models.enums import NotificationChannel, NotificationStatus


class NotificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    ticket_id: UUID | None
    notification_type: str
    channel: NotificationChannel
    title: str
    body: str
    status: NotificationStatus
    created_at: datetime
    sent_at: datetime | None
