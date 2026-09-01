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
    started_at: datetime | None
    completed_at: datetime | None
    ended_at: datetime | None
    unable_reason: str | None
    reject_reason: str | None = None
    completion_note: str | None
    #: §4. The planned window is the scheduler's. `planned_order` is what
    #: "Làm ngay" (0) and "Tiếp theo" (1) are read from -- and the backend
    #: enforces it: only order 0 may be started.
    planned_start_at: datetime | None = None
    planned_finish_at: datetime | None = None
    planned_order: int | None = None
    #: SAFE or AT_RISK, plus the working seconds of headroom left. Negative
    #: slack is the risk warning §4 asks the technician screen to show.
    risk_state: str | None = None
    slack_seconds: int | None = None


class TechnicianQueueResponse(BaseModel):
    """§4's ordered work queue: what to do now, and what comes next.

    Returned as one ordered list rather than as separate `do_now`/`next` fields.
    The split is a rendering decision -- item 0 is "Do now", item 1 is "Next",
    the rest are "Sau đó" -- and encoding it in the payload would force a second
    shape on the API the first time a third bucket is wanted.
    """

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    #: False outside 08:00-18:00. The screen says the shift is closed rather
    #: than showing a start time in the middle of the night.
    within_working_shift: bool
    items: list[TechnicianAssignmentResponse] = Field(default_factory=list)


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
