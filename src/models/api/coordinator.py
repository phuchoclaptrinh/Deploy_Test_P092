"""Coordinator-only API contracts from Self Dev v3."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.models.api.tickets import TicketAttachmentResponse, TicketTimelineItem
from src.models.enums import (
    AssignmentStatus,
    ClassificationStatus,
    Priority,
    ResolutionSource,
    Severity,
    SeveritySource,
    TicketStatus,
    UserRole,
)


class CoordinatorAnalysisSummary(BaseModel):
    """Latest AI-only fields needed by the Coordinator, especially for P0 review."""

    model_config = ConfigDict(extra="forbid")

    run_number: int
    exit_reason: str | None = None
    text_categories: list[str]
    image_categories: list[str] | None
    red_flag_text: bool
    red_flag_signal: bool
    severity: Severity | None
    severity_source: SeveritySource | None
    is_confident: bool | None = None
    confidence_notes: str | None = None
    text_model_version: str | None
    vision_model_version: str | None
    error_code: str | None


class CoordinatorAgentQuestionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    question_type: str
    question_text: str
    options: list[str] | None
    allow_free_text_fallback: bool
    round_number: int
    status: str
    answer_type: str | None
    answer_text: str | None
    answer_upload_id: UUID | None
    asked_at: datetime
    answered_at: datetime | None
    expires_at: datetime | None


class CoordinatorTicketReporter(BaseModel):
    """Who reported, and from where. Coordinator-only (§0.4 grants this role the
    full ticket view); never attached to the resident or technician contracts."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    full_name: str | None = None
    phone_e164: str | None = None
    unit_code: str | None = None
    building_code: str | None = None
    floor_label: str | None = None


class CoordinatorTicketResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    reporter_user_id: UUID
    reporter: CoordinatorTicketReporter | None = None
    source_unit_id: UUID
    location_id: UUID
    location_label: str | None
    description: str | None
    status: TicketStatus
    classification_status: ClassificationStatus
    display_code: str | None = None
    category_id: UUID | None
    category: str | None
    priority: Priority | None
    severity: Severity | None
    red_flag_detected: bool
    score_total: float | None
    sla_started_at: datetime | None
    sla_due_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int
    available_actions: list[str]
    duplicate_of_ticket_id: UUID | None = None
    duplicate_master_display_code: str | None = None
    invalid_reason: str | None = None
    reassignment_count: int = 0
    auto_assignment_paused: bool = False
    auto_assignment_pause_reason: str | None = None
    active_assignment_id: UUID | None = None
    active_assignment_status: AssignmentStatus | None = None
    # §7.3 `assignment_source`: MANUAL, AI_AUTO or AI_PROPOSAL_CONFIRMED. The
    # panel shows who put this technician on the ticket.
    active_assignment_source: str | None = None
    active_technician_id: UUID | None = None
    active_technician_name: str | None = None
    latest_analysis: CoordinatorAnalysisSummary | None = None
    agent_questions: list[CoordinatorAgentQuestionSummary] = Field(default_factory=list)
    attachments: list[TicketAttachmentResponse] = Field(default_factory=list)
    timeline: list[TicketTimelineItem] = Field(default_factory=list)


class CoordinatorTicketListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CoordinatorTicketResponse]
    page: int
    page_size: int
    total: int


class CoordinatorClusterTicketResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    display_code: str
    description: str | None
    status: TicketStatus
    priority: Priority | None
    location_label: str | None
    unit_code: str | None
    floor_label: str | None
    created_at: datetime
    active_assignment_id: UUID | None = None
    active_assignment_status: AssignmentStatus | None = None
    active_technician_id: UUID | None = None
    active_technician_name: str | None = None


class CoordinatorClusterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    category_id: UUID
    category: str
    building_id: UUID
    building: str
    floor_label: str
    density: int
    status: str
    closed: bool
    window_start: datetime
    window_end: datetime
    created_at: datetime
    tickets: list[CoordinatorClusterTicketResponse]


class CoordinatorClusterAssignResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    technician_id: UUID
    assigned_ticket_ids: list[UUID]
    skipped_ticket_ids: list[UUID] = Field(default_factory=list)
    assignment_ids: list[UUID] = Field(default_factory=list)


class CoordinatorClusterApproveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    approved_ticket_ids: list[UUID]
    skipped_ticket_ids: list[UUID] = Field(default_factory=list)


class ManualReviewResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: UUID
    resolution_source: ResolutionSource
    # §8.3: required only when the analysis left the ticket without a severity —
    # a report that never got one cannot be scored otherwise. When the ticket
    # already carries a severity, that stored value is kept and this field is
    # ignored; changing an existing severity is the `classification` override.
    severity: Severity | None = None
    reason: str = Field(min_length=3, max_length=1000)


class RequestInformationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=3, max_length=2000)


class ManualReviewRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=1000)


class ClassificationOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: UUID | None = None
    priority: Priority | None = None
    reason: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def at_least_one_change(self):
        if self.category_id is None and self.priority is None:
            raise ValueError("category_id or priority is required.")
        return self


class AssignTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    technician_id: UUID


class DuplicateLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    master_ticket_id: UUID
    reason: str = Field(min_length=3, max_length=1000)


class AutoAssignmentSettingsResponse(BaseModel):
    """The DIRECT switch: may Backend assign an approved ticket by itself?

    Read-only as far as turning it *on* goes. `PATCH` may only ever disable it
    or change the delay while it is already running; activation happens as a
    consequence of confirming a proposal batch and nowhere else, which is what
    the three `activated_*` fields record.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    activation_delay: str
    version: int
    updated_at: datetime
    #: Which confirmed proposal authorised the current ON state, who confirmed
    #: it, and when. All null while DIRECT is off, and null for a switch that
    #: was already on before provenance was recorded.
    activated_by_batch_id: UUID | None = None
    activated_by_user_id: UUID | None = None
    activated_at: datetime | None = None


# §7.6 spells the delays IMMEDIATE / 2_HOURS / 5_HOURS / 1_DAY / 3_DAYS. The
# pre-v4 short forms stay accepted on input so the existing frontend keeps
# working; responses always carry the contract spelling.
ACTIVATION_DELAY_PATTERN = "^(IMMEDIATE|2_HOURS|5_HOURS|1_DAY|3_DAYS|2H|5H|1D|3D)$"


class AutoAssignmentSettingsUpdateRequest(BaseModel):
    """Disable DIRECT, or adjust the delay while it is already running.

    `enabled` still accepts `true` in the schema rather than being pinned to
    `false`, because "leave it on and change the delay" is a legitimate edit.
    An OFF -> ON transition is refused by the service with
    `AUTO_ASSIGNMENT_PROPOSAL_REQUIRED`; validating the shape here instead would
    only stop the honest callers, and the rule has to hold for every one.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    activation_delay: str = Field(default="IMMEDIATE", pattern=ACTIVATION_DELAY_PATTERN)


class AssignmentProposalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=100)


class AssignmentProposalConfirmRequest(BaseModel):
    """Confirming a batch assigns its placed rows.

    There is deliberately no `continue_auto_assignment` field any more. DIRECT
    turning on is a consequence of this confirmation succeeding, decided by the
    service from the batch it just assigned — never something a request body can
    ask for, because a body can be forged or replayed with no proposal behind it.
    """

    model_config = ConfigDict(extra="forbid")

    #: How long a ticket waits *once* DIRECT is running. Applied only in the
    #: moment the switch flips; ignored when it is already on.
    activation_delay: str = Field(default="IMMEDIATE", pattern=ACTIVATION_DELAY_PATTERN)
    # §4.6 item 5: optimistic concurrency. Omitted means "I have not edited the
    # table"; supplied and stale means 409 rather than confirming a view the
    # coordinator is no longer looking at.
    expected_version: int | None = Field(default=None, ge=1)


class AssignmentProposalCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class AssignmentProposalItemUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # §4.6 item 4: drop the row, or put a different technician on it.
    selected: bool | None = None
    technician_id: UUID | None = None


class AssignmentProposalItemMemberResponse(BaseModel):
    """One ticket inside a proposal row."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID
    display_code: str | None = None
    location_label: str | None = None
    category: str | None = None
    priority: Priority | None = None
    # The draft board shows the same facts the dashboard row does, so a
    # coordinator can place the ticket without opening it first.
    created_at: datetime | None = None
    sla_due_at: datetime | None = None


class AssignmentProposalItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    decision_id: UUID
    status: str
    work_item_type: str
    work_item_id: UUID
    ticket_id: UUID | None = None
    ticket_display_code: str | None = None
    ticket_description: str | None = None
    ticket_location_label: str | None = None
    ticket_category: str | None = None
    ticket_priority: Priority | None = None
    # What the model suggested, and what the coordinator settled on. They are
    # two different facts (§4.6 item 4) and both are shown.
    proposed_technician_id: UUID | None = None
    proposed_technician_name: str | None = None
    final_technician_id: UUID | None = None
    final_technician_name: str | None = None
    # Kept as an alias of the final choice so the existing frontend field works.
    selected_technician_id: UUID | None = None
    selected_technician_name: str | None = None
    completed_model: str | None = None
    decided_at: datetime | None = None
    ticket_ids: list[UUID] = Field(default_factory=list)
    # Every member of the work item, not just the first. An INCIDENT_CASE row
    # stands for up to five tickets (§4.2) and a coordinator confirming the
    # batch has to see all of them.
    members: list[AssignmentProposalItemMemberResponse] = Field(default_factory=list)
    reason: str | None = None
    created_at: datetime
    updated_at: datetime


class AssignmentProposalBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: str
    # Null while the batch is still BUILDING: §4.6 item 3 only sets these when
    # the model has answered and the ten-minute window opens.
    ready_at: datetime | None = None
    expires_at: datetime | None = None
    # Whether confirming *this* batch is what turned DIRECT on. Null until it
    # is confirmed; opening or cancelling a batch answers nothing.
    continue_auto_assignment: bool | None = None
    activation_delay: str | None = None
    version: int
    created_at: datetime | None = None
    confirmed_at: datetime | None = None
    cancelled_at: datetime | None = None
    # §8.1 distinguishes SYSTEM from a named actor. A confirmed batch is always
    # a coordinator's act, and the history has to be able to say whose.
    confirmed_by_user_id: UUID | None = None
    confirmed_by_name: str | None = None
    items: list[AssignmentProposalItemResponse] = Field(default_factory=list)


# The recurring proposal schedule is a different feature from the V4 activation
# delay above, and its pattern is deliberately narrower: no IMMEDIATE, because
# "open a new draft table immediately, forever" is not a schedule.
PROPOSAL_SCHEDULE_INTERVAL_PATTERN = "^(2_HOURS|1_DAY|3_DAYS)$"


class AssignmentScheduleResponse(BaseModel):
    """How often Backend opens a new *draft* proposal for review.

    Never an assignment: a due run produces a batch a coordinator still has to
    confirm. `AutoAssignmentSettingsResponse` is the switch that assigns.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    #: Null when the schedule is off. The UI's "Không tự động".
    interval: str | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    version: int
    updated_at: datetime


class AssignmentScheduleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    interval: str | None = Field(default=None, pattern=PROPOSAL_SCHEDULE_INTERVAL_PATTERN)
    #: Optimistic concurrency, so two coordinators cannot configure it twice.
    expected_version: int | None = Field(default=None, ge=1)
    #: The batch whose result modal this answer came from. Recorded on that
    #: batch once so its history record can say which repeat followed it.
    after_batch_id: UUID | None = None


class AssignmentHistoryMemberResponse(BaseModel):
    """One ticket as it read at confirmation time. Never re-read since."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID | None = None
    display_code: str | None = None
    category: str | None = None
    location_label: str | None = None
    priority: str | None = None
    created_at: str | None = None
    sla_due_at: str | None = None


class AssignmentHistoryItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str | None = None
    status: str | None = None
    work_item_type: str | None = None
    proposed_technician_id: str | None = None
    proposed_technician_name: str | None = None
    final_technician_id: str | None = None
    final_technician_name: str | None = None
    #: True when the coordinator put someone other than the model's suggestion
    #: on the row. Frozen with the rest, so it stays true after a later edit.
    coordinator_override: bool = False
    reason: str | None = None
    members: list[AssignmentHistoryMemberResponse] = Field(default_factory=list)


class AssignmentHistoryRecordResponse(BaseModel):
    """One confirmed batch, rendered entirely from its frozen snapshot."""

    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    confirmed_at: datetime | None = None
    confirmed_by_user_id: str | None = None
    confirmed_by_name: str | None = None
    #: SYSTEM when the recurring schedule opened the batch, COORDINATOR when a
    #: person did. The confirmation is always a person's (§8.1).
    created_by_type: str
    ticket_count: int = 0
    technician_count: int = 0
    items: list[AssignmentHistoryItemResponse] = Field(default_factory=list)
    #: The repeat the coordinator chose in the result modal after confirming.
    #: "NONE" when they declined; null when they were never asked.
    followup_schedule: str | None = None
    #: False for batches confirmed before snapshots existed. Their tickets are
    #: not reconstructed from live rows -- doing that is the bug snapshots fix.
    has_snapshot: bool = True


class OperationalTimeoutSweepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resident_question_timeouts: int
    technician_acceptance_warnings: int
    technician_acceptance_reassignments: int


class AssignmentJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    mode: str
    status: str
    trigger: str | None = None
    work_item_type: str | None = None
    work_item_id: UUID | None = None
    ticket_ids: list[UUID] = Field(default_factory=list)
    execute_after: datetime | None = None
    selected_technician_id: UUID | None = None
    selected_technician_name: str | None = None
    completed_model: str | None = None
    # The one-line business reason the model gave, capped at 500 characters by
    # the column. §9: prompts, raw responses, `error_detail` and stack traces
    # never leave the audit tables, so none of them are here.
    decision_reason: str | None = None
    # An error *code*, never a raw model response (§9).
    error_code: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    # §6.2: true only inside the P1/P2 grace window after a rejection. The
    # backend decides this rather than letting each client re-derive the rule.
    cancellable: bool = False


class AssignmentWorkerRunResponse(BaseModel):
    """Diagnostics for a manually triggered worker pass.

    §5 requires a durable worker; this endpoint exists so an operator can force
    a pass and see what happened, not as a substitute for running one.
    """

    model_config = ConfigDict(extra="forbid")

    started_at: datetime
    timeouts: dict[str, int] = Field(default_factory=dict)
    jobs_scheduled: int = 0
    direct: dict[str, object] = Field(default_factory=dict)
    proposal: dict[str, object] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class TechnicianSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    full_name: str | None
    # Identity phone in the same E.164 spelling the resident summary uses, so
    # the Coordinator roster can show a number to call.
    phone_e164: str | None = None
    is_active: bool
    is_available: bool
    skill_category_ids: list[UUID] = Field(default_factory=list)


class ManagerCreateResidentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, min_length=3, max_length=254)
    password: str | None = Field(default=None, min_length=6, max_length=128)
    phone: str | None = Field(default=None, min_length=8, max_length=32)
    full_name: str | None = Field(default=None, max_length=150)
    unit_id: UUID | None = None
    unit_code: str | None = Field(default=None, min_length=1, max_length=80)
    building_code: str | None = Field(default=None, min_length=1, max_length=50)
    is_primary: bool = True

    @field_validator("email", mode="before")
    @classmethod
    def normalize_resident_email(cls, value):
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("phone", "full_name", "unit_code", "building_code", mode="before")
    @classmethod
    def trim_text(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("email")
    @classmethod
    def validate_resident_email_shape(cls, value: str | None):
        if value is None:
            return value
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("email is invalid.")
        return value

    @model_validator(mode="after")
    def require_unit(self):
        if self.unit_id is None and self.unit_code is None:
            raise ValueError("unit_id or unit_code is required.")
        return self


class ManagerCreateTechnicianRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, min_length=3, max_length=254)
    password: str | None = Field(default=None, min_length=6, max_length=128)
    full_name: str | None = Field(default=None, max_length=150)
    phone_number: str | None = Field(default=None, max_length=32)
    skill_category_ids: list[UUID] = Field(default_factory=list, max_length=50)
    is_available: bool = True

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("full_name", "phone_number", mode="before")
    @classmethod
    def trim_optional_text(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("email")
    @classmethod
    def validate_email_shape(cls, value: str | None):
        if value is None:
            return value
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("email is invalid.")
        return value

    @field_validator("skill_category_ids")
    @classmethod
    def unique_skill_category_ids(cls, value: list[UUID]):
        return list(dict.fromkeys(value))


class ManagerAccountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    role: UserRole
    full_name: str | None
    phone_e164: str | None = None
    email: str | None = None
    temporary_password: str | None = None
    unit_id: UUID | None = None
    unit_code: str | None = None
    is_active: bool
    is_available: bool | None = None
    skill_category_ids: list[UUID] = Field(default_factory=list)


class ManagerAccountStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


class CoordinatorResidentSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    full_name: str | None
    phone_e164: str | None = None
    is_active: bool
    unit_id: UUID | None = None
    unit_code: str | None = None
    building_code: str | None = None
    floor_code: str | None = None
    is_primary: bool | None = None


class CoordinatorCategoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    code: str
    display_name: str
    base_score: int | None
    priority_ceiling: Priority | None
    is_active: bool


class CategoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Z][A-Z0-9_]*$")
    display_name: str = Field(min_length=1, max_length=150)
    base_score: int = Field(ge=0)
    priority_ceiling: Priority | None = None

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value):
        if isinstance(value, str):
            return value.strip().upper()
        return value


class CategoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=150)
    base_score: int | None = Field(default=None, ge=0)
    priority_ceiling: Priority | None = None
    is_active: bool | None = None


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    actor_user_id: UUID | None
    actor_role: str
    action: str
    entity_type: str
    entity_id: UUID
    before_data: dict[str, object] | None
    after_data: dict[str, object] | None
    reason: str | None
    request_id: UUID | None
    created_at: datetime


class TicketSummaryReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    by_category: dict[str, int]


class SlaPerformanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed_total: int
    completed_on_time: int
    compliance_rate: float | None


class TechnicianProductivityRow(BaseModel):
    """§2.13. One Technician, one reporting period. Every field is counted from
    persisted rows; there is no estimated column."""

    model_config = ConfigDict(extra="forbid")

    technician_id: UUID
    full_name: str | None = None
    is_active: bool
    active_days: int
    completed_tickets: int
    sla_late_tickets: int
    reassigned_from_other_tickets: int


class TechnicianProductivityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: str
    period_start: datetime
    period_end: datetime
    rows: list[TechnicianProductivityRow] = Field(default_factory=list)


class ExportReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: str = Field(default="tickets-summary", pattern="^(tickets-summary|sla-performance)$")
    format: str = Field(default="CSV", pattern="^(CSV|csv)$")
