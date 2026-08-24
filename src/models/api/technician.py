"""Technician assignment API contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.models.api.tickets import TicketAttachmentResponse
from src.models.enums import AssignmentStatus, Priority, TicketStatus


class AssignmentTicketSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    description: str | None
    status: TicketStatus
    category_display_name: str | None
    location_label: str | None
    priority: Priority | None
    sla_due_at: datetime | None
    attachments: list[TicketAttachmentResponse] = Field(default_factory=list)


class TechnicianAssignmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    ticket: AssignmentTicketSummary
    status: AssignmentStatus
    assigned_at: datetime
    accepted_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    ended_at: datetime | None
    unable_reason: str | None
    reject_reason: str | None = None
    completion_note: str | None


class UnableToHandleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)


class RejectAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)


class CompleteAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution_note: str = Field(min_length=1, max_length=5000)
    evidence_upload_ids: list[UUID] = Field(min_length=1, max_length=10)


class TechnicianAvailabilityResponse(BaseModel):
    """The technician's own readiness state, separate from account activity."""

    model_config = ConfigDict(extra="forbid")

    is_available: bool


class UpdateTechnicianAvailabilityRequest(BaseModel):
    """A technician may only change their own availability."""

    model_config = ConfigDict(extra="forbid")

    is_available: bool
