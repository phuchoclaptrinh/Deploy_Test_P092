"""Backend-side AI Agent v4 contract (agent_backend_contract_v4.md §1–§2).

V4 lives beside `src.models.agent_schemas` (V3) instead of replacing it: an
analysis session that started on the V3 graph must keep finalizing with the V3
payload during the transition window (Logic_xử_lý_chính_v4 §18.2).

Two deliberate differences from V3:

* The Agent no longer decides whether the text-derived and image-derived
  Category agree. `CONFIDENT_MATCH`/`CATEGORY_MISMATCH` are gone; a normal
  extraction round ends with `ANALYSIS_COMPLETE` and Backend does the
  reconciliation (contract §1.2, §3.3, business spec §4.4).
* Duplicate detection is now an Agent outcome (`DUPLICATE_EXISTING` /
  `DUPLICATE_UNCERTAIN`) carrying an evidence object, while Density leaves the
  payload entirely — Backend computes it from distinct units (contract §1.4).

Everything here uses `extra="forbid"`: an internal field such as
`text_understandable`, or a Density the Agent computed itself, is a contract
error rather than something Backend silently drops (contract §1.3).
"""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.models.agent_schemas import AgentSeveritySource
from src.models.enums import Severity

# Persisted by Backend on ai_analysis_runs.contract_version (contract §7.2).
# Deliberately NOT a payload field: §1.3 does not list it and the payload is
# extra="forbid".
ANALYSIS_CONTRACT_VERSION_V4 = "v4"

# Stamped on `ai_analysis_sessions.model_version` when a session starts on the
# v4 graph. It is the only per-session marker of which contract a session runs
# under, so `finalize_v4()` and the resume dispatcher both read it. Declared
# here rather than in the agent runtime so Backend can check it without
# importing the graph.
AGENT_MODEL_VERSION_V4 = "fixit-agent-v4-langgraph-1"

MAX_TOOL_CALLS_V4 = 5
MAX_ASK_ROUNDS_V4 = 3
MAX_ASK_WAIT_SECONDS_V4 = 300
MAX_SEARCH_RESULTS_V4 = 20


class AgentExitReasonV4(str, Enum):  # noqa: UP042
    """The six — and only six — ways a v4 analysis round may end (§1.2)."""

    RED_FLAG = "RED_FLAG"
    DUPLICATE_EXISTING = "DUPLICATE_EXISTING"
    DUPLICATE_UNCERTAIN = "DUPLICATE_UNCERTAIN"
    ANALYSIS_COMPLETE = "ANALYSIS_COMPLETE"
    LIMIT_REACHED = "LIMIT_REACHED"
    INSUFFICIENT_INPUT = "INSUFFICIENT_INPUT"


class AgentSearchPurpose(str, Enum):  # noqa: UP042
    """Why the Agent is searching. Backend applies a different filter per
    purpose (§2.2) — the Agent must never widen one into the other."""

    DUPLICATE = "DUPLICATE"
    GROUPING = "GROUPING"


class AgentTicketRelation(BaseModel):
    """Shared shape of `duplicate` (§1.5) and `red_flag_relation` (§1.5a).

    A single `master_ticket_id`, not a list: duplicates are N→1 onto one
    canonical master. Every other candidate stays in the session tool-call log.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    master_ticket_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class AgentGroupingResultV4(BaseModel):
    """§1.4. No `density`: Backend recomputes it per distinct unit from the
    `propose_case_grouping` tool log, so an Agent-supplied number would be a
    second source of truth for the same value."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    grouped: bool
    related_ticket_ids: list[UUID] = Field(default_factory=list, max_length=MAX_SEARCH_RESULTS_V4)
    reason: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_grouping(self):
        if not self.grouped:
            raise ValueError("Omit grouping entirely (grouping=null) instead of sending grouped=false.")
        if not self.related_ticket_ids:
            raise ValueError("grouped=true requires at least one related_ticket_id from search(purpose=GROUPING).")
        if len(set(self.related_ticket_ids)) != len(self.related_ticket_ids):
            raise ValueError("related_ticket_ids must be unique.")
        return self


class AgentToolUsageV4(BaseModel):
    """§1.6. Backend does not trust these numbers; it compares them with the
    session counters and tool-call log. They exist so a mismatch is detectable."""

    model_config = ConfigDict(extra="forbid")

    total_tool_calls: int = Field(ge=0, le=MAX_TOOL_CALLS_V4)
    ask_resident_rounds: int = Field(ge=0, le=MAX_ASK_ROUNDS_V4)
    ask_resident_elapsed_seconds: int = Field(ge=0, le=MAX_ASK_WAIT_SECONDS_V4)
    search_related_tickets_called: bool = False
    propose_case_grouping_called: bool = False


class AgentAnalysisResultV4(BaseModel):
    """Agent → Backend payload for one analysis session (§1.3).

    The validators below cover only invariants the Agent can check on its own
    output. Anything needing the database or the real tool log — master ticket
    still active, category IDs inside the pinned catalog, declared tool usage
    matching session counters, `analyzed_at` close to server time — stays with
    Backend `finalize_v4()` inside its transaction (§1.7).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ticket_id: UUID
    analysis_session_id: UUID
    exit_reason: AgentExitReasonV4
    text_categories: list[UUID] | None = Field(default=None, max_length=20)
    red_flag_text: bool = False
    image_categories: list[UUID] | None = Field(default=None, max_length=20)
    red_flag_signal: bool | None = None
    is_relevant: bool | None = None
    severity: Severity | None = None
    severity_source: AgentSeveritySource | None = None
    is_confident: bool
    confidence_notes: str | None = Field(default=None, max_length=500)
    grouping: AgentGroupingResultV4 | None = None
    duplicate: AgentTicketRelation | None = None
    red_flag_relation: AgentTicketRelation | None = None
    tool_usage: AgentToolUsageV4
    category_catalog_version: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=100)
    analyzed_at: datetime

    @field_validator("analyzed_at")
    @classmethod
    def _normalize_analyzed_at(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @model_validator(mode="after")
    def validate_v4_invariants(self):
        self._validate_image_group()
        self._validate_red_flag()
        self._validate_duplicate()
        self._validate_grouping_source()
        self._validate_extraction_completeness()
        self._validate_limit_reached()
        return self

    # --- §1.7.6: the three image fields are null together, or none of them is.
    def _validate_image_group(self) -> None:
        image_fields = (self.image_categories, self.red_flag_signal, self.is_relevant)
        if any(item is None for item in image_fields) and any(item is not None for item in image_fields):
            raise ValueError("image_categories, red_flag_signal and is_relevant must be null together or set together.")

    # --- §1.7.1 / §1.7.2: red flag beats everything, including duplicate.
    def _validate_red_flag(self) -> None:
        has_red_flag = self.red_flag_text or bool(self.red_flag_signal)
        if has_red_flag and self.exit_reason != AgentExitReasonV4.RED_FLAG:
            raise ValueError("A red flag on any source forces exit_reason=RED_FLAG.")
        if self.exit_reason == AgentExitReasonV4.RED_FLAG:
            if not has_red_flag:
                raise ValueError("RED_FLAG requires red_flag_text or red_flag_signal to be true.")
            if self.duplicate is not None:
                raise ValueError("RED_FLAG must not close the new ticket as a duplicate; use red_flag_relation.")
        if self.red_flag_relation is not None and self.exit_reason != AgentExitReasonV4.RED_FLAG:
            raise ValueError("red_flag_relation is only valid together with exit_reason=RED_FLAG.")

    # --- §1.5 / §1.7.3 / §1.7.4.
    def _validate_duplicate(self) -> None:
        if self.exit_reason == AgentExitReasonV4.DUPLICATE_EXISTING:
            if self.duplicate is None:
                raise ValueError("DUPLICATE_EXISTING requires the duplicate object.")
            if not self.is_confident:
                raise ValueError("DUPLICATE_EXISTING requires is_confident=true.")
            if self.grouping is not None:
                raise ValueError("DUPLICATE_EXISTING must not carry grouping; a duplicate is not a spreading case.")
            if self.red_flag_relation is not None:
                raise ValueError("DUPLICATE_EXISTING requires red_flag_relation=null.")
            if self.duplicate.master_ticket_id == self.ticket_id:
                raise ValueError("A ticket cannot be its own duplicate master.")
        elif self.duplicate is not None:
            raise ValueError("duplicate is only valid with exit_reason=DUPLICATE_EXISTING.")

        if self.exit_reason == AgentExitReasonV4.DUPLICATE_UNCERTAIN:
            if self.is_confident:
                raise ValueError("DUPLICATE_UNCERTAIN requires is_confident=false.")
            if not (self.confidence_notes or "").strip():
                raise ValueError("DUPLICATE_UNCERTAIN requires confidence_notes explaining the doubt.")
            if not self.tool_usage.search_related_tickets_called:
                raise ValueError("DUPLICATE_UNCERTAIN requires a search_related_tickets(purpose=DUPLICATE) call.")

    # --- §1.4: grouping may only echo what propose_case_grouping accepted.
    def _validate_grouping_source(self) -> None:
        if self.grouping is not None and not self.tool_usage.propose_case_grouping_called:
            raise ValueError("grouping requires a propose_case_grouping call in this session.")

    # --- §1.7.7 plus "INSUFFICIENT_INPUT must not invent extracted data".
    def _validate_extraction_completeness(self) -> None:
        if self.exit_reason == AgentExitReasonV4.INSUFFICIENT_INPUT:
            if self.text_categories is not None:
                raise ValueError("INSUFFICIENT_INPUT must not report extracted text_categories.")
            if self.severity is not None or self.severity_source is not None:
                raise ValueError("INSUFFICIENT_INPUT must not report a severity it could not establish.")
            if self.is_confident:
                raise ValueError("INSUFFICIENT_INPUT requires is_confident=false.")
            if self.grouping is not None or self.duplicate is not None or self.red_flag_relation is not None:
                raise ValueError("INSUFFICIENT_INPUT must not carry duplicate/grouping/red-flag relations.")
            return

        if self.text_categories is None:
            raise ValueError("text_categories is required unless exit_reason is INSUFFICIENT_INPUT.")
        if self.severity is None or self.severity_source is None:
            raise ValueError("severity and severity_source are required unless exit_reason is INSUFFICIENT_INPUT.")

    # --- §1.7.5.
    def _validate_limit_reached(self) -> None:
        if self.exit_reason != AgentExitReasonV4.LIMIT_REACHED:
            return
        if self.is_confident:
            raise ValueError("LIMIT_REACHED requires is_confident=false.")
        budget_hit = (
            self.tool_usage.total_tool_calls >= MAX_TOOL_CALLS_V4
            or self.tool_usage.ask_resident_rounds >= MAX_ASK_ROUNDS_V4
            or self.tool_usage.ask_resident_elapsed_seconds >= MAX_ASK_WAIT_SECONDS_V4
        )
        if not budget_hit:
            raise ValueError("LIMIT_REACHED requires the tool, ask-resident or wait budget to actually be exhausted.")


# ---------------------------------------------------------------------------
# Tool contracts (§2). Request shapes are what the Agent is allowed to send;
# response shapes are what Backend returns.
# ---------------------------------------------------------------------------


class SearchRelatedTicketsRequestV4(BaseModel):
    """§2.2. Note what is *absent*: building, floor and location.

    Backend derives those from the ticket itself so the Agent cannot widen the
    search radius, and `purpose` selects which filter Backend applies.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    ticket_id: UUID
    purpose: AgentSearchPurpose
    category_ids: list[UUID] = Field(min_length=1, max_length=20)
    limit: int = Field(default=MAX_SEARCH_RESULTS_V4, ge=1, le=MAX_SEARCH_RESULTS_V4)


class RelatedTicketStatusChangeV4(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    changed_at: datetime


class RelatedTicketV4(BaseModel):
    """One search hit (§2.2). Sanitized: no reporter name, phone, unit, raw
    text or photo of the earlier ticket ever reaches the Agent.

    `location_id` is the asset identity used to tell elevator A from elevator B
    (§1.5 item 5, assumption 2). It is nullable only because a Backend that
    cannot resolve a distinct asset must be visible as such — an Agent that
    receives null there is not allowed to auto-link a duplicate.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ticket_id: UUID
    category_ids: list[UUID] = Field(default_factory=list, max_length=20)
    location_id: UUID | None = None
    location_label: str = ""
    status: str
    summary: str = ""
    status_history: list[RelatedTicketStatusChangeV4] = Field(default_factory=list)
    current_due_at: datetime | None = None
    created_at: datetime | None = None


class SearchRelatedTicketsResponseV4(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: AgentSearchPurpose
    related_tickets: list[RelatedTicketV4] = Field(default_factory=list, max_length=MAX_SEARCH_RESULTS_V4)


class ProposeCaseGroupingRequestV4(BaseModel):
    """§2.3. Unchanged from V3 on the wire."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    session_id: UUID
    ticket_id: UUID
    related_ticket_ids: list[UUID] = Field(min_length=1, max_length=MAX_SEARCH_RESULTS_V4)
    reason: str = Field(min_length=1, max_length=300)


class ProposeCaseGroupingResponseV4(BaseModel):
    """§2.3. `density` is Backend answering its own question; the Agent may read
    it for context and must never copy it into `AgentAnalysisResultV4`."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    density: int = Field(ge=1)
    category_id: UUID | None = None
    related_ticket_ids: list[UUID] = Field(default_factory=list)
    rejected_reason: str | None = None


class AskResidentRequestV4(BaseModel):
    """§2.4. `expires_at` is Backend to set: now + whatever is left of the one
    300-second session budget, never a fresh 300 seconds per question."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    session_id: UUID
    ticket_id: UUID
    question_type: str = Field(pattern="^(MULTIPLE_CHOICE|FREE_TEXT)$")
    question_text: str = Field(min_length=1, max_length=1000)
    options: list[str] | None = Field(default=None, max_length=6)
    allow_free_text_fallback: bool = False

    @model_validator(mode="after")
    def validate_question(self):
        if self.question_type == "MULTIPLE_CHOICE" and not self.options:
            raise ValueError("MULTIPLE_CHOICE questions require options.")
        return self
