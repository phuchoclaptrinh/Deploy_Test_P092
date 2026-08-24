"""Assignment Agent v4 contract (agent_backend_contract_v4.md §4).

This is the second, entirely separate way the system calls AI. It shares
nothing with the analysis agent: no `AIAnalysisSession`, no tools, no graph, no
5-call budget. Backend filters the candidates, the model picks one of them, and
Backend writes the assignment.

Two request shapes, because they are two different problems:

* `DIRECT` — one or more work items that are each ready to be assigned right
  now. Batching is a technical optimisation; every decision is applied
  independently and immediately, with no coordinator step.
* `PROPOSAL` — one preview batch built when a coordinator turns auto-assignment
  on while a queue exists. Nothing is written until a human presses OK.

Both results are a flat `decisions[]` with one entry per work item.
`model_version` and `decided_at` sit on each decision rather than on the batch,
because a batch may legitimately mix items answered by the primary model with
items rescued by the fallback (§4.4).

Everything uses `extra="forbid"`: an unexpected field or an unknown enum value
is a contract error, not something to shrug off.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.models.enums import Priority

ASSIGNMENT_CONTRACT_VERSION_V4 = "v4"

# §5.2 config block.
DIRECT_REQUEST_MAX_TICKET_COUNT = 20
PROPOSAL_BATCH_MAX_TICKET_COUNT = 20
INCIDENT_CASE_MAX_TICKET_COUNT = 5


class AssignmentMode(str, Enum):  # noqa: UP042
    DIRECT = "DIRECT"
    PROPOSAL = "PROPOSAL"


class WorkItemType(str, Enum):  # noqa: UP042
    TICKET = "TICKET"
    INCIDENT_CASE = "INCIDENT_CASE"


class AssignmentTrigger(str, Enum):  # noqa: UP042
    """Why a DIRECT job exists (§4.2). PROPOSAL has no trigger: it is only ever
    a coordinator turning the switch on while a queue exists, and reassignment
    never goes through PROPOSAL."""

    INITIAL_AUTO = "INITIAL_AUTO"
    REASSIGN_REJECTED = "REASSIGN_REJECTED"
    REASSIGN_SILENT = "REASSIGN_SILENT"


class AssignmentDecisionType(str, Enum):  # noqa: UP042
    """The only two answers the model may give (§4.4).

    `NO_SUITABLE_CANDIDATE` is a valid business answer, not a model failure, so
    it never triggers the fallback (§5.2 item 7).
    """

    SELECTED = "SELECTED"
    NO_SUITABLE_CANDIDATE = "NO_SUITABLE_CANDIDATE"


class WorkItemV4(BaseModel):
    """One unit of work to assign: a single ticket, or an incident case whose
    members are all assigned to the same technician (§4.2)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    work_item_type: WorkItemType
    work_item_id: UUID
    ticket_ids: list[UUID] = Field(min_length=1, max_length=INCIDENT_CASE_MAX_TICKET_COUNT)
    category_id: UUID
    priority: Priority
    location_labels: list[str] = Field(default_factory=list, max_length=20)
    issue_summary: str = Field(default="", max_length=2000)
    required_skills: list[str] = Field(default_factory=list, max_length=20)
    # Mapped straight from tickets.sla_due_at; not a new persisted column (§7.1).
    current_due_at: datetime | None = None
    # When the work item entered the queue: the ticket's `created_at`, or the
    # earliest member's for a case. `RULE_ENGINE_V1` orders a batch by priority
    # then by this, so an older report is never overtaken by a newer one of the
    # same priority. Optional: the LLM engine relied on Backend having sorted
    # `work_items` beforehand (§4.3a) and predates the field.
    created_at: datetime | None = None

    @model_validator(mode="after")
    def validate_work_item(self):
        if len(set(self.ticket_ids)) != len(self.ticket_ids):
            raise ValueError("ticket_ids must be unique inside a work item.")
        if self.work_item_type is WorkItemType.TICKET:
            if len(self.ticket_ids) != 1:
                raise ValueError("A TICKET work item has exactly one ticket_id.")
            if self.work_item_id != self.ticket_ids[0]:
                raise ValueError("A TICKET work item uses the ticket id as work_item_id.")
        return self

    @property
    def ticket_count(self) -> int:
        """How much projected load this item adds to whoever is chosen."""
        return len(self.ticket_ids)


class CandidateSnapshotV4(BaseModel):
    """A technician Backend already filtered in: active profile, availability
    on, skills matching the ticket Category (§4.1).

    The decision engine re-checks none of that. It weighs skill fit and current
    load, and nothing else — not geography, not shift, not personal data.

    The last three fields are optional additions for `RULE_ENGINE_V1` (§4.3):
    the per-priority splits let the per-priority caps bind, and
    `last_assigned_at` is the third key of the ranking. They are optional
    because the LLM engine predates them and a request built without them must
    still be valid; a cap whose count is missing simply cannot bind.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    technician_id: UUID
    display_name: str = Field(default="", max_length=200)
    matched_skills: list[str] = Field(default_factory=list, max_length=20)
    active_assignment_count: int = Field(ge=0)
    active_p3_count: int = Field(ge=0)
    is_available_snapshot: bool = True
    active_p1_count: int | None = Field(default=None, ge=0)
    active_p2_count: int | None = Field(default=None, ge=0)
    # MAX(ticket_assignments.assigned_at); null means never assigned, which
    # sorts ahead of everyone who ever was.
    last_assigned_at: datetime | None = None


class _WorkItemRequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: UUID
    work_item: WorkItemV4
    excluded_technician_ids: list[UUID] = Field(default_factory=list, max_length=100)
    candidates: list[CandidateSnapshotV4] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_candidates(self):
        ids = [item.technician_id for item in self.candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("candidate technician_id must be unique inside a work item.")
        excluded = set(self.excluded_technician_ids)
        if excluded & set(ids):
            # §4.3 item 4: everyone who already rejected or timed out on this
            # work item must be gone before the model ever sees the list.
            raise ValueError("candidates must not intersect excluded_technician_ids.")
        return self

    @property
    def candidate_ids(self) -> set[UUID]:
        return {item.technician_id for item in self.candidates}


class DirectWorkItemRequestV4(_WorkItemRequestBase):
    """One DIRECT unit. `decision_id` is the idempotency key that stays the same
    across the primary model, the fallback and the assignment transaction."""

    trigger: AssignmentTrigger
    reassignment_count: int = Field(default=0, ge=0)


class ProposalWorkItemRequestV4(_WorkItemRequestBase):
    """One PROPOSAL row. No trigger and no reassignment count: PROPOSAL is never
    used for reassignment (§4.2)."""


def _validate_batch_items(work_items, max_tickets: int) -> None:
    decision_ids = [item.decision_id for item in work_items]
    if len(set(decision_ids)) != len(decision_ids):
        raise ValueError("decision_id must be unique inside a request.")

    work_item_ids = [item.work_item.work_item_id for item in work_items]
    if len(set(work_item_ids)) != len(work_item_ids):
        raise ValueError("work_item_id must be unique inside a request.")

    seen: set[UUID] = set()
    for item in work_items:
        for ticket_id in item.work_item.ticket_ids:
            if ticket_id in seen:
                raise ValueError(f"Ticket {ticket_id} appears in more than one work item.")
            seen.add(ticket_id)
    if len(seen) > max_tickets:
        raise ValueError(f"A request carries at most {max_tickets} distinct ticket UUIDs; got {len(seen)}.")


class DirectAssignmentBatchRequestV4(BaseModel):
    """Backend → AI for direct assignment (§4.3).

    One or more units, at most 20 distinct tickets in total, no ticket in two
    units. A case is never split across two requests: if it does not fit in the
    remaining room, the whole case waits for the next one.
    """

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    assignment_mode: AssignmentMode = AssignmentMode.DIRECT
    work_items: list[DirectWorkItemRequestV4] = Field(min_length=1, max_length=DIRECT_REQUEST_MAX_TICKET_COUNT)
    requested_at: datetime

    @field_validator("assignment_mode")
    @classmethod
    def _must_be_direct(cls, value: AssignmentMode) -> AssignmentMode:
        if value is not AssignmentMode.DIRECT:
            raise ValueError("DirectAssignmentBatchRequestV4 requires assignment_mode=DIRECT.")
        return value

    @model_validator(mode="after")
    def validate_batch(self):
        _validate_batch_items(self.work_items, DIRECT_REQUEST_MAX_TICKET_COUNT)
        return self


class AssignmentProposalBatchRequestV4(BaseModel):
    """Backend → AI for the proposal table (§4.3a).

    A load-allocation problem over the whole batch, not 20 independent requests
    reusing one stale load snapshot. Backend has already sorted `work_items` by
    priority descending, then submission time ascending.
    """

    model_config = ConfigDict(extra="forbid")

    batch_decision_id: UUID
    proposal_batch_id: UUID
    assignment_mode: AssignmentMode = AssignmentMode.PROPOSAL
    work_items: list[ProposalWorkItemRequestV4] = Field(min_length=1, max_length=PROPOSAL_BATCH_MAX_TICKET_COUNT)
    requested_at: datetime

    @field_validator("assignment_mode")
    @classmethod
    def _must_be_proposal(cls, value: AssignmentMode) -> AssignmentMode:
        if value is not AssignmentMode.PROPOSAL:
            raise ValueError("AssignmentProposalBatchRequestV4 requires assignment_mode=PROPOSAL.")
        return value

    @model_validator(mode="after")
    def validate_batch(self):
        _validate_batch_items(self.work_items, PROPOSAL_BATCH_MAX_TICKET_COUNT)
        return self


class AssignmentDecisionV4(BaseModel):
    """One decision, for one work item (§4.4).

    The same technician may appear in several decisions: Backend does not
    rebalance the result, so spreading load is the model's job while it works
    through the batch.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision_id: UUID
    work_item_id: UUID
    selected_technician_id: UUID | None = None
    decision: AssignmentDecisionType
    reason: str = Field(min_length=1, max_length=500)
    model_version: str = Field(min_length=1, max_length=100)
    decided_at: datetime

    @field_validator("decided_at")
    @classmethod
    def _normalize_decided_at(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @model_validator(mode="after")
    def validate_decision(self):
        if self.decision is AssignmentDecisionType.SELECTED and self.selected_technician_id is None:
            raise ValueError("SELECTED requires selected_technician_id.")
        if self.decision is AssignmentDecisionType.NO_SUITABLE_CANDIDATE and self.selected_technician_id is not None:
            raise ValueError("NO_SUITABLE_CANDIDATE requires selected_technician_id=null.")
        return self


def _validate_decisions(decisions: list[AssignmentDecisionV4]) -> None:
    ids = [item.decision_id for item in decisions]
    if len(set(ids)) != len(ids):
        raise ValueError("Each decision_id appears exactly once in a result.")


class DirectAssignmentBatchResultV4(BaseModel):
    """AI → Backend for DIRECT (§4.4.1).

    A decision may be absent: an item the primary got wrong and the fallback
    could not rescue simply has no entry, and Backend moves that work item to
    MANUAL_REQUIRED. Absence never invalidates the decisions that did succeed.
    """

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    decisions: list[AssignmentDecisionV4] = Field(default_factory=list)
    completed_at: datetime

    @model_validator(mode="after")
    def validate_result(self):
        _validate_decisions(self.decisions)
        return self


class AssignmentProposalBatchResultV4(BaseModel):
    """AI → Backend for PROPOSAL (§4.4.2). A missing decision becomes an EMPTY
    row in the proposal table; it never blocks the other rows."""

    model_config = ConfigDict(extra="forbid")

    batch_decision_id: UUID
    proposal_batch_id: UUID
    decisions: list[AssignmentDecisionV4] = Field(default_factory=list)
    completed_at: datetime

    @model_validator(mode="after")
    def validate_result(self):
        _validate_decisions(self.decisions)
        return self
