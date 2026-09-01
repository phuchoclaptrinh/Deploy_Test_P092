"""Coordinator-only API contracts from Self Dev v3."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.models.agent_schemas import RiskCriteriaPayload, RiskEvidencePayload
from src.models.api.tickets import TicketAttachmentResponse, TicketTimelineItem
from src.models.enums import (
    AssignmentStatus,
    ClassificationStatus,
    EmergencyDecision,
    EmergencyReviewStatus,
    Priority,
    ResolutionSource,
    TicketStatus,
    UserRole,
)


class RiskAssessmentResponse(BaseModel):
    """One scoring revision, with everything a coordinator needs to argue with it.

    Three scope numbers rather than one because they answer three questions:
    what the Agent estimated, what a case actually counted, and which of the two
    the formula used. An estimate that was overruled is the most interesting one
    on the screen.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    revision_no: int
    source: str
    human_safety_score: int
    property_spread_score: int
    essential_function_score: int
    deterioration_speed_score: int
    ai_scope_score: int
    backend_scope_score: int | None = None
    effective_scope_score: int
    confirmed_affected_unit_count: int | None = None
    blocker_codes: list[str] = Field(default_factory=list)
    evidence: dict[str, list[str] | dict[str, list[str]]] = Field(default_factory=dict)
    unknown_facts: list[str] = Field(default_factory=list)
    risk_score: float
    score_priority: Priority
    #: Set when a blocker, not the score, decided the outcome.
    blocker_floor: Priority | None = None
    final_priority: Priority
    rubric_version: str
    case_id_snapshot: UUID | None = None
    case_density_snapshot: int | None = None
    override_reason: str | None = None
    reviewed_by: UUID | None = None
    created_at: datetime


class CoordinatorDuplicateCandidate(BaseModel):
    """One candidate exactly as the Agent was shown it.

    Management reviewing an uncertain duplicate has to see the same evidence the
    Agent judged, not a fresh query that may have moved since -- otherwise the
    reason and the candidate list disagree and nobody can tell which is right.
    """

    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID
    display_code: str = ""
    category_name: str = ""
    location_label: str = ""
    floor_label: str = ""
    status: str = ""
    summary: str = ""
    created_at: datetime | None = None
    completed_at: datetime | None = None
    recently_completed: bool = False


class CoordinatorAnalysisSummary(BaseModel):
    """What the analysis concluded, and why. The P0 / duplicate review panel."""

    model_config = ConfigDict(extra="forbid")

    run_number: int
    exit_reason: str | None = None
    #: The one final Category, plus the two evidence fields that explain it.
    #: They are shown side by side and never merged.
    final_category_id: UUID | None = None
    text_category_id: UUID | None = None
    image_category_id: UUID | None = None
    #: The assessment this run produced, if it got far enough to score.
    risk_assessment: RiskAssessmentResponse | None = None
    #: Why the ticket was classified this way.
    ai_reason: str | None = None
    #: The verdict, and why duplicate status is or is not certain. A different
    #: question from `ai_reason`, and management needs both.
    duplicate_verdict: str | None = None
    duplicate_reason: str | None = None
    duplicate_candidates: list[CoordinatorDuplicateCandidate] = Field(default_factory=list)
    grouping_status: str | None = None
    # --- The emergency gate. ---
    #: NOT_REQUIRED / PENDING / CONFIRMED / DOWNGRADED. PENDING is the only one
    #: that means the two review buttons should be live.
    emergency_review_status: EmergencyReviewStatus | None = None
    emergency_decision: EmergencyDecision | None = None
    emergency_decision_reason: str | None = None
    emergency_reviewed_by: UUID | None = None
    emergency_reviewed_at: datetime | None = None
    #: What the pipeline scored, and what applies after a review. They differ
    #: only when a coordinator downgraded the ticket.
    ai_priority_before_review: Priority | None = None
    effective_priority: Priority | None = None
    model_version: str | None = None
    #: Set only on a FAILED run: the analysis errored rather than concluding
    #: anything, and the ticket can be retried.
    error_code: str | None = None


class CoordinatorAgentQuestionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    #: One of `AgentQuestionKind`. Null on questions asked before the kinds
    #: existed.
    question_kind: str | None = None
    question_type: str
    question_text: str
    options: list[str] | None
    allow_free_text_fallback: bool
    round_number: int
    status: str
    answer_type: str | None
    answer_text: str | None
    answer_payload: dict[str, object] | None = None
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
    risk_score: float | None
    #: The revision `priority` and `risk_score` were cached from.
    risk_assessment: RiskAssessmentResponse | None = None
    #: Distinct apartments in this ticket's open incident case, or null when it
    #: belongs to none. The number behind `backend_scope_score`, shown so a
    #: coordinator can see what the scope criterion was counted from rather than
    #: having to open the case.
    case_unit_count: int | None = None
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
    #: COORDINATOR_MANUAL, COORDINATOR_VISUAL, AUTO_SCHEDULER, AUTO_AGENT or
    #: AUTO_FALLBACK. The panel shows who put this technician on the ticket, and
    #: the last two are the ones a manager reviews.
    active_assignment_source: str | None = None
    active_technician_id: UUID | None = None
    active_technician_name: str | None = None
    #: §4. `planned_start_at` is when work is expected to begin -- the one
    #: forward-looking time a resident also sees. `planned_finish_at` is
    #: internal capacity arithmetic and appears here for Building Management
    #: only; it is absent from every resident payload and must never be
    #: presented as a completion promise.
    planned_start_at: datetime | None = None
    planned_finish_at: datetime | None = None
    planned_order: int | None = None
    #: SAFE, or AT_RISK when the scheduler could not place this without pushing
    #: some other commitment late.
    assignment_risk_state: str | None = None
    slack_seconds: int | None = None
    # The assignment lifecycle is separate from TicketStatus. The coordinator
    # panel uses this timestamp to place its current KTV state on the same
    # chronological timeline as ticket status changes.
    active_assignment_updated_at: datetime | None = None
    completion_note: str | None = None
    completed_technician_name: str | None = None
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
    #: Required only when the analysis left the ticket without an assessment --
    #: a report that was never scored cannot be prioritized otherwise. When the
    #: ticket already carries one, the stored revision is kept and this field is
    #: ignored; changing an existing score is the `classification` override.
    criteria: RiskCriteriaPayload | None = None
    blockers: list[str] = Field(default_factory=list)
    evidence: RiskEvidencePayload | None = None
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


class DuplicateDecisionRequest(BaseModel):
    """Management settling a `DUPLICATE_UNCERTAIN` ticket.

    `master_ticket_id` may be omitted when the Agent surfaced exactly one
    candidate; with several, management has to say which. Confirming *not*
    duplicate publishes the ticket and starts background grouping.
    """

    model_config = ConfigDict(extra="forbid")

    is_duplicate: bool
    master_ticket_id: UUID | None = None
    reason: str = Field(default="", max_length=1000)


class EmergencyReviewRequest(BaseModel):
    """Management settling the emergency gate.

    `CONFIRM_P5` needs nothing else. It deliberately ends the automation and,
    equally deliberately, does *not* unlock assignment: a confirmed emergency is
    still handled by hand, outside the dispatch queue.

    `DOWNGRADE_PRIORITY` requires both a target band below P5 and a written
    reason -- overruling the model is a decision somebody has to own.
    """

    model_config = ConfigDict(extra="forbid")

    decision: EmergencyDecision
    priority: Priority | None = None
    reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_decision(self):
        if self.decision is EmergencyDecision.CONFIRM_P5:
            if self.priority is not None:
                raise ValueError("CONFIRM_P5 does not take a priority; it keeps P5 by definition.")
            return self
        if self.priority not in {Priority.P1, Priority.P2, Priority.P3, Priority.P4}:
            raise ValueError("DOWNGRADE_PRIORITY requires a priority between P1 and P4.")
        if not self.reason.strip():
            raise ValueError("DOWNGRADE_PRIORITY requires a reason.")
        return self


class OperationalTimeoutSweepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resident_question_timeouts: int = 0


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
    is_primary: bool = True

    @field_validator("email", mode="before")
    @classmethod
    def normalize_resident_email(cls, value):
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("phone", "full_name", "unit_code", mode="before")
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
    floor_code: str | None = None
    is_primary: bool | None = None


class CoordinatorCategoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    code: str
    display_name: str
    is_active: bool


class CategoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Z][A-Z0-9_]*$")
    display_name: str = Field(min_length=1, max_length=150)

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value):
        if isinstance(value, str):
            return value.strip().upper()
        return value


class CategoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=150)
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
