"""The Backend <-> Agent contract. There is exactly one, and this is it.

One analysis pipeline, one result schema, one persistence path. Nothing here is
versioned or switchable: a ticket is analysed by `src.agents`, the graph hands
this payload to `AgentResultService.finalize()`, and that is the whole story.

Three rules shape the payload:

* **One final Category.** `category_id` is the answer. `text_category_id` and
  `image_category_id` are *evidence* -- what the description suggested and what
  the photos suggested -- kept so a coordinator can see why the Agent asked what
  it asked. They are never intersected, merged or reconciled into the final
  Category by anything; when they disagree the resident is asked to choose.
* **A technical failure is not a business outcome.** Every exit reason below
  describes something the Agent concluded about the ticket. A model that
  errored, a tool that failed, a database that refused: those produce no result
  at all, are recorded with an error code, and stay retryable.
* **The Agent never invents a location.** The resident picked `location_id`
  from the fixed selector. The Agent may only ask them to confirm or re-pick
  it, and Backend validates the id it gets back.

`extra="forbid"` everywhere: a field the contract does not name is a bug to
surface, not something to drop quietly.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.models.enums import Severity

# Stamped on `ai_analysis_runs.contract_version`. Not a switch -- there is one
# contract -- but the column is part of the audit trail, so rows written by the
# old dual-runtime era stay distinguishable from rows written by this one.
ANALYSIS_CONTRACT_VERSION = "canonical"

MAX_DUPLICATE_CANDIDATES = 10
MAX_GROUPING_CANDIDATES = 5
MAX_TOOL_CALLS = 5
MAX_ASK_ROUNDS = 3
MAX_ASK_WAIT_SECONDS = 300

#: A candidate that finished less than this ago is not silently treated as the
#: same live incident; the resident is asked whether the problem came back.
RECENT_COMPLETION_WINDOW_MINUTES = 60


class AgentExitReason(str, Enum):  # noqa: UP042
    """The seven ways one analysis round can end. All seven are business
    answers."""

    RED_FLAG = "RED_FLAG"
    #: Classification finished and the ticket scores P3. P3 is the emergency
    #: priority here (five-minute SLA), so the round stops before the duplicate
    #: stage and hands the ticket to a human. See `P3ReviewStatus`.
    P3_REVIEW_REQUIRED = "P3_REVIEW_REQUIRED"
    DUPLICATE_EXISTING = "DUPLICATE_EXISTING"
    DUPLICATE_UNCERTAIN = "DUPLICATE_UNCERTAIN"
    ANALYSIS_COMPLETE = "ANALYSIS_COMPLETE"
    LIMIT_REACHED = "LIMIT_REACHED"
    INSUFFICIENT_INPUT = "INSUFFICIENT_INPUT"


class P3ReviewStatus(str, Enum):  # noqa: UP042
    """The mandatory human gate in front of the emergency priority.

    P3 means "respond within five minutes". Nothing automatic should decide
    that on its own and nothing automatic should keep running behind it, so a
    P3 classification parks the ticket here until a coordinator either confirms
    the emergency or downgrades it.
    """

    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    DOWNGRADED = "DOWNGRADED"


class P3Decision(str, Enum):  # noqa: UP042
    """The only two things a coordinator may do at the P3 gate.

    Confirming ends the automation deliberately: correlating an emergency with
    other tickets is not worth the minutes it costs. Downgrading is the only
    way back into the pipeline, and it cannot land on P3 again -- confirming is
    the action for that.
    """

    CONFIRM_P3 = "CONFIRM_P3"
    DOWNGRADE_SEVERITY = "DOWNGRADE_SEVERITY"


class AgentSeveritySource(str, Enum):  # noqa: UP042
    IMAGE = "IMAGE"
    TEXT = "TEXT"


class AgentQuestionKind(str, Enum):  # noqa: UP042
    """The only four things a resident may be asked.

    Notably absent: "are these two reports the same?" and "do these tickets
    belong to one case?". Duplicate and grouping are judgements about other
    people's tickets, which the resident cannot see and must not be asked to
    adjudicate.
    """

    CATEGORY_CONFIRMATION = "CATEGORY_CONFIRMATION"
    SEVERITY_CONFIRMATION = "SEVERITY_CONFIRMATION"
    LOCATION_CONFIRMATION = "LOCATION_CONFIRMATION"
    RECENT_COMPLETION = "RECENT_COMPLETION"


#: The two -- and only two -- answers to a `LOCATION_CONFIRMATION`.
#:
#: They live in the contract rather than in the Agent because both ends need
#: them: the Agent offers them, and Backend enforces what each one implies when
#: the answer comes back. "Keep" must arrive with no replacement id and "change"
#: must arrive with one, and a client calling the API directly is held to that
#: just as the UI is.
LOCATION_KEEP_OPTION = "Giữ nguyên vị trí đã chọn"
LOCATION_CHANGE_OPTION = "Chọn vị trí khác"
LOCATION_OPTIONS = [LOCATION_KEEP_OPTION, LOCATION_CHANGE_OPTION]


class DuplicateVerdict(str, Enum):  # noqa: UP042
    SAME_INCIDENT = "SAME_INCIDENT"
    DIFFERENT_INCIDENT = "DIFFERENT_INCIDENT"
    UNCERTAIN = "UNCERTAIN"


class AgentSearchPurpose(str, Enum):  # noqa: UP042
    """Two different questions with two different Backend filters.

    `DUPLICATE` asks "is this the same incident on the same asset?" -- same
    `location_id`, same `category_id`, live tickets plus anything finished
    within the last hour. `GROUPING` asks "is one physical problem spreading?"
    -- same `category_id`, same floor first then adjacent ones. Confusing the
    two is how a ticket gets folded into a master nobody checked.
    """

    DUPLICATE = "DUPLICATE"
    GROUPING = "GROUPING"


# ---------------------------------------------------------------------------
# Candidate snapshots handed to the Agent.
# ---------------------------------------------------------------------------


class CandidateTicket(BaseModel):
    """One other ticket, reduced to what a judgement actually needs.

    Sanitized by construction: no reporter, no apartment, no raw description,
    no attachment. `summary` is a short redacted phenomenon excerpt built by
    `agent_common.redact_phenomenon_excerpt`, which is what lets "same
    phenomenon" be judged at all without handing over somebody's report.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ticket_id: UUID
    display_code: str = ""
    category_id: UUID | None = None
    category_name: str = ""
    location_id: UUID | None = None
    location_label: str = ""
    floor_label: str = ""
    status: str = ""
    summary: str = ""
    created_at: datetime | None = None
    completed_at: datetime | None = None
    #: Finished inside `RECENT_COMPLETION_WINDOW_MINUTES`. Backend computes it
    #: so the recurrence rule cannot hinge on the Agent doing clock arithmetic.
    recently_completed: bool = False


class AgentTicketRelation(BaseModel):
    """A single master, never a list: duplicates are N->1 onto one canonical
    ticket, and every other candidate stays in the session tool-call log."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    master_ticket_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class AgentGroupingResult(BaseModel):
    """Produced by the background grouping stage, never by the foreground round.

    No `density`: Backend recomputes it from distinct apartments when it writes
    the incident case, so an Agent-supplied number would be a second source of
    truth for the same value.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    grouped: bool
    related_ticket_ids: list[UUID] = Field(default_factory=list, max_length=MAX_GROUPING_CANDIDATES)
    reason: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_grouping(self):
        if not self.grouped:
            raise ValueError("Omit grouping entirely instead of sending grouped=false.")
        if not self.related_ticket_ids:
            raise ValueError("grouped=true requires at least one related_ticket_id from the grouping candidates.")
        if len(set(self.related_ticket_ids)) != len(self.related_ticket_ids):
            raise ValueError("related_ticket_ids must be unique.")
        return self


class AgentToolUsage(BaseModel):
    """Declared counters. Backend compares them with its own session counters
    rather than trusting them; they exist so a mismatch is detectable."""

    model_config = ConfigDict(extra="forbid")

    total_tool_calls: int = Field(ge=0, le=MAX_TOOL_CALLS)
    ask_resident_rounds: int = Field(ge=0, le=MAX_ASK_ROUNDS)
    ask_resident_elapsed_seconds: int = Field(ge=0, le=MAX_ASK_WAIT_SECONDS)
    search_related_tickets_called: bool = False
    propose_case_grouping_called: bool = False


# ---------------------------------------------------------------------------
# The one result payload.
# ---------------------------------------------------------------------------


class AgentAnalysisResult(BaseModel):
    """Agent -> Backend payload for one foreground analysis round.

    The validators here cover only what the Agent can check about its own
    output. Anything needing the database -- the master still exists, the
    Category is inside the pinned catalog, the declared usage matches the
    session counters -- stays with `AgentResultService.finalize()` inside its
    transaction.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ticket_id: UUID
    analysis_session_id: UUID
    exit_reason: AgentExitReason

    #: The answer. Exactly one Category, or null when the round ended before
    #: one could be established.
    category_id: UUID | None = None
    #: Evidence only, for explaining the decision to a coordinator. Never
    #: reconciled into `category_id` by Backend or by the graph.
    text_category_id: UUID | None = None
    image_category_id: UUID | None = None

    severity: Severity | None = None
    severity_source: AgentSeveritySource | None = None
    red_flag: bool = False
    #: Why the Agent classified the ticket the way it did. Shown to management.
    ai_reason: str | None = Field(default=None, max_length=1000)
    #: Echoed back so finalize can detect a location that moved mid-analysis.
    location_id: UUID | None = None

    duplicate: AgentTicketRelation | None = None
    duplicate_verdict: DuplicateVerdict | None = None
    #: Why duplicate status is (or is not) certain. Shown to management for
    #: DUPLICATE_UNCERTAIN alongside `duplicate_candidates`.
    duplicate_reason: str | None = Field(default=None, max_length=500)
    duplicate_candidates: list[CandidateTicket] = Field(default_factory=list, max_length=MAX_DUPLICATE_CANDIDATES)

    tool_usage: AgentToolUsage
    category_catalog_version: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=100)
    analyzed_at: datetime

    @field_validator("analyzed_at")
    @classmethod
    def _normalize_analyzed_at(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @model_validator(mode="after")
    def validate_invariants(self):
        self._validate_red_flag()
        self._validate_duplicate()
        self._validate_classification()
        return self

    def _validate_red_flag(self) -> None:
        if self.red_flag and self.exit_reason is not AgentExitReason.RED_FLAG:
            raise ValueError("A red flag forces exit_reason=RED_FLAG.")
        if self.exit_reason is AgentExitReason.RED_FLAG:
            if not self.red_flag:
                raise ValueError("RED_FLAG requires red_flag=true.")
            if self.duplicate is not None:
                raise ValueError("RED_FLAG must not close the new ticket as a duplicate.")

    def _validate_duplicate(self) -> None:
        if self.exit_reason is AgentExitReason.DUPLICATE_EXISTING:
            if self.duplicate is None:
                raise ValueError("DUPLICATE_EXISTING requires the duplicate object.")
            if self.duplicate.master_ticket_id == self.ticket_id:
                raise ValueError("A ticket cannot be its own duplicate master.")
            if self.duplicate_verdict is not DuplicateVerdict.SAME_INCIDENT:
                raise ValueError("DUPLICATE_EXISTING requires duplicate_verdict=SAME_INCIDENT.")
        elif self.duplicate is not None:
            raise ValueError("duplicate is only valid with exit_reason=DUPLICATE_EXISTING.")

        if self.exit_reason is AgentExitReason.DUPLICATE_UNCERTAIN:
            if self.duplicate_verdict is not DuplicateVerdict.UNCERTAIN:
                raise ValueError("DUPLICATE_UNCERTAIN requires duplicate_verdict=UNCERTAIN.")
            if not (self.duplicate_reason or "").strip():
                raise ValueError("DUPLICATE_UNCERTAIN requires duplicate_reason explaining the doubt.")
            if not self.duplicate_candidates:
                raise ValueError("DUPLICATE_UNCERTAIN requires the candidate snapshot the doubt is about.")

    def _validate_classification(self) -> None:
        if self.exit_reason is AgentExitReason.INSUFFICIENT_INPUT:
            if self.category_id is not None or self.severity is not None:
                raise ValueError("INSUFFICIENT_INPUT must not report a classification it could not establish.")
            if self.duplicate_candidates:
                raise ValueError("INSUFFICIENT_INPUT must not carry duplicate candidates.")
            return

        if self.exit_reason is AgentExitReason.LIMIT_REACHED:
            # The budget ran out mid-conversation, so whatever was established
            # is reported as-is and a coordinator finishes the job.
            return

        if self.severity is None or self.severity_source is None:
            raise ValueError(f"{self.exit_reason.value} requires severity and severity_source.")

        if self.exit_reason is AgentExitReason.RED_FLAG:
            # An emergency is answered by speed: the ticket goes straight to P3
            # with no score, so the Category is not what decides its handling.
            # Rejecting a genuine danger report because the Category could not
            # be pinned down would turn it into a technical failure, which is
            # the one outcome a red flag must never become.
            return

        if self.category_id is None:
            raise ValueError(f"{self.exit_reason.value} requires exactly one final category_id.")

        if self.exit_reason is AgentExitReason.P3_REVIEW_REQUIRED:
            # The gate is in front of the duplicate stage, so a P3 payload that
            # carries duplicate work is evidence the gate was bypassed. That is
            # worth failing on rather than storing.
            if self.duplicate_candidates or self.duplicate_verdict is not None or self.duplicate is not None:
                raise ValueError("P3_REVIEW_REQUIRED must stop before any duplicate processing.")


# ---------------------------------------------------------------------------
# Category catalog handed to the Agent (display names only; `code` is
# Backend-internal and never reaches a prompt).
# ---------------------------------------------------------------------------


class CategoryCatalogToolItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: UUID
    display_name: str
    priority_ceiling: Literal["P1", "P2", "P3", "UNLIMITED"]
    base_score: int


class CategoryCatalogToolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_version: str
    categories: list[CategoryCatalogToolItem]


# ---------------------------------------------------------------------------
# Tool request/response shapes.
# ---------------------------------------------------------------------------


class SearchRelatedTicketsRequest(BaseModel):
    """Note what is absent: floor, location, time window.

    Backend derives the whole search scope from the ticket itself, so the Agent
    cannot widen the radius; `purpose` selects which filter Backend applies.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    ticket_id: UUID
    purpose: AgentSearchPurpose
    category_id: UUID
    limit: int = Field(default=MAX_DUPLICATE_CANDIDATES, ge=1, le=MAX_DUPLICATE_CANDIDATES)


class SearchRelatedTicketsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: AgentSearchPurpose
    candidates: list[CandidateTicket] = Field(default_factory=list, max_length=MAX_DUPLICATE_CANDIDATES)


class ProposeCaseGroupingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    session_id: UUID
    ticket_id: UUID
    related_ticket_ids: list[UUID] = Field(min_length=1, max_length=MAX_GROUPING_CANDIDATES)
    reason: str = Field(min_length=1, max_length=300)


class ProposeCaseGroupingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    density: int = Field(ge=1)
    category_id: UUID | None = None
    related_ticket_ids: list[UUID] = Field(default_factory=list)
    rejected_reason: str | None = None


class AskResidentRequest(BaseModel):
    """`expires_at` is Backend's to set: now plus whatever is left of the one
    300-second session budget, never a fresh 300 seconds per question."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    session_id: UUID
    ticket_id: UUID
    question_kind: AgentQuestionKind
    question_type: str = Field(pattern="^(MULTIPLE_CHOICE|FREE_TEXT)$")
    question_text: str = Field(min_length=1, max_length=1000)
    options: list[str] | None = Field(default=None, max_length=6)
    allow_free_text_fallback: bool = False

    @model_validator(mode="after")
    def validate_question(self):
        if self.question_type == "MULTIPLE_CHOICE" and not self.options:
            raise ValueError("MULTIPLE_CHOICE questions require options.")
        if self.question_kind is AgentQuestionKind.LOCATION_CONFIRMATION and self.allow_free_text_fallback:
            # A location is only ever chosen from the fixed selector. Free text
            # here would be an invitation to type one, which is exactly the
            # inference path this design forbids.
            raise ValueError("LOCATION_CONFIRMATION must not allow a free-text answer.")
        return self


__all__ = [
    "ANALYSIS_CONTRACT_VERSION",
    "LOCATION_CHANGE_OPTION",
    "LOCATION_KEEP_OPTION",
    "LOCATION_OPTIONS",
    "MAX_ASK_ROUNDS",
    "MAX_ASK_WAIT_SECONDS",
    "MAX_DUPLICATE_CANDIDATES",
    "MAX_GROUPING_CANDIDATES",
    "MAX_TOOL_CALLS",
    "RECENT_COMPLETION_WINDOW_MINUTES",
    "AgentAnalysisResult",
    "AgentExitReason",
    "AgentGroupingResult",
    "AgentQuestionKind",
    "P3Decision",
    "P3ReviewStatus",
    "AgentSearchPurpose",
    "AgentSeveritySource",
    "AgentTicketRelation",
    "AgentToolUsage",
    "AskResidentRequest",
    "CandidateTicket",
    "CategoryCatalogToolItem",
    "CategoryCatalogToolResponse",
    "DuplicateVerdict",
    "ProposeCaseGroupingRequest",
    "ProposeCaseGroupingResponse",
    "SearchRelatedTicketsRequest",
    "SearchRelatedTicketsResponse",
]
