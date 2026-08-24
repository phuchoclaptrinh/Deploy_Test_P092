"""Resident-facing ticket contracts aligned with Self Dev v3."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.enums import ClassificationStatus, TicketLifecycleGroup, TicketStatus


class TicketCreateRequest(BaseModel):
    """Secure signed-upload variant of POST /tickets.

    Self Dev v3 requires text plus a canonical location. This project
    keeps the existing one-time signed-upload session instead of accepting raw
    private object paths from the client. Images are optional.
    """

    model_config = ConfigDict(extra="forbid")

    location_id: UUID
    description: str = Field(min_length=1, max_length=5000)
    attachment_upload_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def description_required_and_upload_ids_unique(self):
        text_ok = bool(self.description and self.description.strip())
        if not text_ok:
            raise ValueError("Description is required.")
        if len(self.attachment_upload_ids) != len(set(self.attachment_upload_ids)):
            raise ValueError("Duplicate attachment upload IDs are not allowed.")
        self.description = self.description.strip()
        return self


class TicketCreatedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID
    status: TicketStatus
    classification_status: ClassificationStatus
    display_status: str


class TicketAttachmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    mime_type: str | None
    size_bytes: int | None
    download_url_endpoint: str


class TicketTimelineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_status: TicketStatus | None
    to_status: TicketStatus
    reason: str | None
    created_at: datetime


class ResidentTimelineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_status: str
    reason: str | None
    created_at: datetime


class ResidentTechnicianSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    full_name: str | None


class ResidentTicketResponse(BaseModel):
    """What a member of the reporting apartment may see.

    Deliberately absent: the reporter's phone number, raw audit or coordinator
    reasons, AI reasoning, scores, priority codes, and any field of a duplicate
    master that belongs to another apartment.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    #: Short reference a resident can read out or search for, e.g. "PA-53F85D".
    #: The same code identifies the report everywhere it is shown.
    display_code: str
    description: str | None
    display_status: str
    category_display_name: str | None
    priority_description: str | None
    estimated_resolution_text: str
    #: Point in time the report is expected to be handled by. None while the
    #: report has no deadline yet (still being analysed, awaiting Building
    #: Management, or already finished); the client then falls back to
    #: `estimated_resolution_text`.
    expected_resolution_at: datetime | None = None
    location_label: str
    #: Full name of the apartment member who sent the report. None when the
    #: profile has no name; the client then shows "Thành viên trong căn hộ".
    reporter_name: str | None = None
    #: True when the caller sent this report, so the client can show "Bạn" and
    #: the backend can gate sender-only actions.
    is_reporter: bool = False
    lifecycle_group: TicketLifecycleGroup = TicketLifecycleGroup.ACTIVE
    #: Friendly explanation when the report ended as INVALID; never the raw reason.
    invalid_reason_text: str | None = None
    created_at: datetime
    updated_at: datetime
    available_actions: list[str]
    #: Set when the report was folded into another one. Only the reference
    #: code of that master travels with it — never its reporter, unit, text or
    #: photos, which may belong to a different apartment.
    duplicate_of_ticket_id: UUID | None = None
    duplicate_master_display_code: str | None = None
    technician: ResidentTechnicianSummary | None = None
    attachments: list[TicketAttachmentResponse] = Field(default_factory=list)
    timeline: list[ResidentTimelineItem] = Field(default_factory=list)


class TicketListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ResidentTicketResponse]
    page: int
    page_size: int
    total: int


class AttachmentDownloadUrlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attachment_id: UUID
    signed_download_url: str
    expires_in: int
    mime_type: str | None
    size_bytes: int | None


class TicketSupplementRequest(BaseModel):
    """Legacy. Building Management no longer requests extra information.

    Retained so tickets parked in WAITING_RESIDENT_INFO before the workflow was
    removed can still be closed out by an old client. The current Resident UI
    never sends this.
    """

    model_config = ConfigDict(extra="forbid")

    information_request_id: UUID
    description: str | None = Field(default=None, max_length=5000)
    attachment_upload_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def payload_required(self):
        if not (self.description and self.description.strip()) and not self.attachment_upload_ids:
            raise ValueError("Supplement must include description or image.")
        return self


class AgentQuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    question_type: str
    question_text: str
    options: list[str] | None
    allow_free_text_fallback: bool
    round_number: int
    expires_at: datetime | None


class AgentQuestionAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_type: str = Field(pattern="^(OPTION|FREE_TEXT|NEW_PHOTO)$")
    answer_text: str | None = Field(default=None, max_length=2000)
    upload_id: UUID | None = None
