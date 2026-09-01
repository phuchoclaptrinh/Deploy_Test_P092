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
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.risk_scoring import (
    CRITERION_NAMES,
    MAX_CRITERION_SCORE,
    MIN_CRITERION_SCORE,
    BlockerCode,
    RiskCriterionScores,
)
from src.models.enums import EmergencyDecision, EmergencyReviewStatus

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
    """The six ways one analysis round can end. All six are business answers.

    `RED_FLAG` is gone. Danger is no longer a separate exit: it is either a
    blocker code that floors the priority at P5, or a `human_safety` score that
    gets there on its own, and either way the round continues into the
    duplicate stage. `P3_REVIEW_REQUIRED` is gone with it -- same gate, new
    band, new name.
    """

    #: Classification finished and the ticket is P5. The emergency warning has
    #: already been raised; the round still runs duplicate (see
    #: `docs/risk_scoring_v2.md` §7) and then hands the ticket to a human.
    #: It never runs grouping. See `EmergencyReviewStatus`.
    EMERGENCY_REVIEW_REQUIRED = "EMERGENCY_REVIEW_REQUIRED"
    DUPLICATE_EXISTING = "DUPLICATE_EXISTING"
    DUPLICATE_UNCERTAIN = "DUPLICATE_UNCERTAIN"
    ANALYSIS_COMPLETE = "ANALYSIS_COMPLETE"
    LIMIT_REACHED = "LIMIT_REACHED"
    INSUFFICIENT_INPUT = "INSUFFICIENT_INPUT"


class AgentQuestionKind(str, Enum):  # noqa: UP042
    """The only eight things a resident may be asked.

    Five of them replace the single `SEVERITY_CONFIRMATION`, one per criterion.
    A question that asks "how serious is it?" gets an answer about how upset the
    resident is; a question that asks "is water still coming out right now?"
    gets an answer that moves exactly one score. The Agent must know which
    number it is missing before it is allowed to spend a question on it.

    Notably absent: "are these two reports the same?" and "do these tickets
    belong to one case?". Duplicate and grouping are judgements about other
    people's tickets, which the resident cannot see and must not be asked to
    adjudicate.
    """

    CATEGORY_CONFIRMATION = "CATEGORY_CONFIRMATION"
    LOCATION_CONFIRMATION = "LOCATION_CONFIRMATION"
    RECENT_COMPLETION = "RECENT_COMPLETION"
    SAFETY_CONFIRMATION = "SAFETY_CONFIRMATION"
    SPREAD_CONFIRMATION = "SPREAD_CONFIRMATION"
    ESSENTIAL_FUNCTION_CONFIRMATION = "ESSENTIAL_FUNCTION_CONFIRMATION"
    AFFECTED_SCOPE_CONFIRMATION = "AFFECTED_SCOPE_CONFIRMATION"
    DETERIORATION_CONFIRMATION = "DETERIORATION_CONFIRMATION"


#: Which criterion each targeted question is trying to pin down. The Agent may
#: only ask one of these when the named criterion is in `unknown_facts`.
QUESTION_KIND_CRITERION: dict[AgentQuestionKind, str] = {
    AgentQuestionKind.SAFETY_CONFIRMATION: "human_safety",
    AgentQuestionKind.SPREAD_CONFIRMATION: "property_spread",
    AgentQuestionKind.ESSENTIAL_FUNCTION_CONFIRMATION: "essential_function",
    AgentQuestionKind.AFFECTED_SCOPE_CONFIRMATION: "affected_scope",
    AgentQuestionKind.DETERIORATION_CONFIRMATION: "deterioration_speed",
}


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
# The risk assessment the Agent produces.
# ---------------------------------------------------------------------------


class RiskCriteriaPayload(BaseModel):
    """The five judgements, and the only numbers the Agent is allowed to send.

    There is no `risk_score`, no `priority` and no `severity` field, by
    construction: `extra="forbid"` means a model that invents one produces a
    validation error rather than a priority nobody computed. The weights, the
    thresholds and the arithmetic all live in `src.domain.risk_scoring`.
    """

    model_config = ConfigDict(extra="forbid")

    human_safety: int = Field(ge=MIN_CRITERION_SCORE, le=MAX_CRITERION_SCORE)
    property_spread: int = Field(ge=MIN_CRITERION_SCORE, le=MAX_CRITERION_SCORE)
    essential_function: int = Field(ge=MIN_CRITERION_SCORE, le=MAX_CRITERION_SCORE)
    affected_scope: int = Field(ge=MIN_CRITERION_SCORE, le=MAX_CRITERION_SCORE)
    deterioration_speed: int = Field(ge=MIN_CRITERION_SCORE, le=MAX_CRITERION_SCORE)

    def to_domain(self) -> RiskCriterionScores:
        return RiskCriterionScores(**{name: getattr(self, name) for name in CRITERION_NAMES})


class RiskEvidencePayload(BaseModel):
    """What the Agent saw, per criterion, in its own words.

    Empty lists are allowed and mean "nothing in the report spoke to this",
    which is a legitimate reason for a zero. `unknown_facts` on the result is
    where "I could not tell" goes, and the two must not be confused: a
    coordinator reading a 0 needs to know which of the two produced it.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    human_safety: list[str] = Field(default_factory=list, max_length=10)
    property_spread: list[str] = Field(default_factory=list, max_length=10)
    essential_function: list[str] = Field(default_factory=list, max_length=10)
    affected_scope: list[str] = Field(default_factory=list, max_length=10)
    deterioration_speed: list[str] = Field(default_factory=list, max_length=10)
    #: Keyed by blocker code, not a flat list.
    #:
    #: Each code sets a different floor -- seven at P5, four at P4 -- so "which
    #: line justified which floor" is a question a coordinator will actually
    #: ask. A single pooled list cannot answer it: three blockers and two lines
    #: of evidence is a payload nobody can audit, and it validated.
    blockers: dict[str, list[str]] = Field(default_factory=dict)


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

    #: The five 0-4 judgements. Null only when the round ended before a
    #: classification could be established.
    criteria: RiskCriteriaPayload | None = None
    #: Named emergency facts. Each one sets a floor under the priority; none of
    #: them adds points. Backend rejects a code it does not know rather than
    #: ignoring it.
    blockers: list[BlockerCode] = Field(default_factory=list, max_length=len(BlockerCode))
    evidence: RiskEvidencePayload = Field(default_factory=RiskEvidencePayload)
    #: Criterion names the Agent could not establish, so a low score is not read
    #: as a checked-and-clear finding.
    unknown_facts: list[str] = Field(default_factory=list, max_length=len(CRITERION_NAMES))
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
        self._validate_risk()
        self._validate_duplicate()
        self._validate_classification()
        return self

    def _validate_risk(self) -> None:
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("blockers must not repeat a code.")
        unknown = set(self.unknown_facts) - set(CRITERION_NAMES)
        if unknown:
            raise ValueError(f"unknown_facts may only name criteria; got {sorted(unknown)}.")
        self._validate_blocker_evidence()
        self._validate_unknown_facts_agree_with_criteria()

    def _validate_blocker_evidence(self) -> None:
        """Every claimed blocker carries its own evidence, and only claimed ones do.

        A blocker floors the priority with no score behind it, so what the Agent
        saw is the only thing a reviewer has to check it against. Checked per
        code rather than in aggregate: the old rule accepted three blockers
        backed by one line, which reads as evidence and is not.
        """
        claimed = {code.value for code in self.blockers}
        evidenced = {
            code for code, lines in self.evidence.blockers.items() if [item for item in lines if item.strip()]
        }
        missing = sorted(claimed - evidenced)
        if missing:
            raise ValueError(f"Every blocker must come with its own evidence; missing {missing}.")
        # Evidence for a code that was not claimed is not harmless: it would
        # show under an emergency heading in the audit view for a floor nobody
        # applied.
        stray = sorted(set(self.evidence.blockers) - claimed)
        if stray:
            raise ValueError(f"evidence.blockers names codes that are not claimed; got {stray}.")

    def _validate_unknown_facts_agree_with_criteria(self) -> None:
        """A criterion is scored or it is unknown, and never both.

        `criteria` is all-or-nothing by construction, so this reduces to one
        rule at this layer: a payload that carries all five scores has nothing
        left to be unsure about. The model boundary in `agents/llm_client.py`
        enforces the same agreement field by field, where the scores are still
        individually nullable; this holds a caller who posts the result payload
        directly to the same contract.
        """
        if self.criteria is not None and self.unknown_facts:
            raise ValueError(
                "unknown_facts must be empty once all five criteria are scored; "
                f"got {sorted(self.unknown_facts)}."
            )

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
            if self.category_id is not None or self.criteria is not None:
                raise ValueError("INSUFFICIENT_INPUT must not report a classification it could not establish.")
            if self.blockers:
                raise ValueError("INSUFFICIENT_INPUT must not report blockers it could not establish.")
            if self.duplicate_candidates:
                raise ValueError("INSUFFICIENT_INPUT must not carry duplicate candidates.")
            return

        if self.exit_reason is AgentExitReason.LIMIT_REACHED:
            # The budget ran out mid-conversation, so whatever was established
            # is reported as-is and a coordinator finishes the job.
            return

        if self.exit_reason is AgentExitReason.EMERGENCY_REVIEW_REQUIRED:
            # An emergency is answered by speed, and the Category is not what
            # decides how it is handled. Rejecting a genuine danger report
            # because the Category could not be pinned down would turn it into a
            # technical failure, which is the one outcome an emergency must
            # never become.
            #
            # A blocker with no criteria is allowed here for the same reason:
            # the blocker floor is authoritative on its own, the alarm has
            # already fired, and `UnifiedClassification` deliberately accepts
            # such a payload upstream. Criteria are still required when no
            # blocker carries the priority.
            #
            # Note what is *not* asserted here any more: v1 refused an emergency
            # payload that carried duplicate work, because the gate sat in front
            # of the duplicate stage. v2 inverts that -- the warning fires first
            # and duplicate runs behind it -- so duplicate evidence on this exit
            # is expected rather than suspicious.
            if self.criteria is None and not self.blockers:
                raise ValueError("EMERGENCY_REVIEW_REQUIRED requires the five risk criteria or a blocker.")
            return

        if self.criteria is None:
            raise ValueError(f"{self.exit_reason.value} requires the five risk criteria.")

        if self.category_id is None:
            raise ValueError(f"{self.exit_reason.value} requires exactly one final category_id.")


# ---------------------------------------------------------------------------
# Category catalog handed to the Agent (display names only; `code` is
# Backend-internal and never reaches a prompt).
# ---------------------------------------------------------------------------


class CategoryCatalogToolItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: UUID
    display_name: str


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
    "QUESTION_KIND_CRITERION",
    "BlockerCode",
    "EmergencyDecision",
    "EmergencyReviewStatus",
    "RiskCriteriaPayload",
    "RiskEvidencePayload",
    "AgentSearchPurpose",
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
