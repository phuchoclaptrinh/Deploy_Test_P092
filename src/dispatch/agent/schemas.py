"""The at-risk agent's data contract (§7).

Three shapes live here, and the boundary between them is a privacy boundary as
much as a typing one:

* `CandidateDispatchHistory` -- what `get_candidate_dispatch_history` returns
  for one technician. **Aggregated operational facts only.** §7 forbids resident
  descriptions, phone numbers, emails, addresses and raw ticket text from
  reaching the model, and the way that is enforced is that there is no field
  here capable of carrying any of them. A rule enforced by the absence of a
  field cannot be broken by a caller who forgets it.

  Technician display names are absent for the same reason, one step further:
  the agent has no use for them, so it is given opaque ids and the backend maps
  those back to names for the manager UI.

* `AtRiskBatchRequest` -- the batch put in front of the model. It carries the
  eligible candidate set the backend already filtered through §3, and nothing
  the model could use to widen it.

* `AtRiskBatchDecision` -- what comes back, and it is validated against the
  candidate set before it is allowed to become an assignment.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CategoryHandlingStats(BaseModel):
    """P50/P80 handling time for one technician in one category (§7)."""

    category_code: str
    completed_count: int = 0
    #: Working seconds, not wall-clock. A job spanning a night is not a
    #: fourteen-hour job, and the estimate the scheduler compares against is
    #: expressed in working time too.
    p50_working_seconds: int | None = None
    p80_working_seconds: int | None = None


class HistoryWindow(BaseModel):
    """One technician's operational record over one look-back window."""

    window_days: int
    completed_count: int = 0
    assigned_count: int = 0
    #: How much of what they were given they actually picked up. There is no
    #: acceptance step to measure any more; *starting* work is the first
    #: positive action a technician takes, so it is what the record is made of.
    started_count: int = 0
    #: Started at or before the `planned_start_at` the scheduler had committed
    #: to. An assignment with no planned start made no promise, so it cannot
    #: have broken one and counts as on time -- the same rule the old
    #: acceptance metric used for a missing deadline.
    #:
    #: Note what this is *not*: a count of missed SLAs. No start deadline has
    #: been approved, so there is no `start_timeout_count` here. It belongs
    #: beside this field the day that rule exists.
    started_on_time_count: int = 0
    #: Median **working** seconds from assignment to start. Working, not
    #: wall-clock: a job assigned at 17:30 and started at 08:10 the next morning
    #: waited forty working minutes, not fifteen hours, and a queue that spans
    #: the overnight gap is now the normal case rather than the exception.
    #: Null when nothing in the window was ever started -- deliberately not
    #: zero, which would read as "instant" rather than "no data".
    median_assignment_to_start_seconds: int | None = None
    rejected_count: int = 0
    unable_to_handle_count: int = 0
    reassigned_away_count: int = 0
    by_category: list[CategoryHandlingStats] = Field(default_factory=list)


class PlannedSlot(BaseModel):
    """One entry of a technician's current planned schedule."""

    order: int
    planned_start_at: datetime
    planned_finish_at: datetime
    #: Working seconds of headroom against this unit's committed deadline.
    #: Negative means the commitment is already broken.
    slack_seconds: int | None = None
    category_code: str | None = None


class CandidateDispatchHistory(BaseModel):
    """Everything the agent may know about one candidate technician."""

    model_config = ConfigDict(extra="forbid")

    technician_id: UUID
    #: Current state, straight from the in-memory simulation this batch ran.
    active_assignment_count: int = 0
    in_progress_count: int = 0
    planned_schedule: list[PlannedSlot] = Field(default_factory=list)
    #: The slack the *worst* already-committed unit would be left with if this
    #: batch's ticket went to this technician. Negative on every candidate here,
    #: by definition -- the ticket is AT_RISK precisely because no option leaves
    #: it non-negative. The agent's job is to choose which negative to accept.
    projected_worst_slack_seconds: int | None = None
    projected_start_at: datetime | None = None
    history: list[HistoryWindow] = Field(default_factory=list)


class AtRiskTicket(BaseModel):
    """One at-risk ticket, described without any resident-supplied text."""

    model_config = ConfigDict(extra="forbid")

    ticket_ref: str
    category_code: str
    priority: str
    score: float
    submitted_at: datetime
    p80_working_seconds: int
    #: Exactly the technicians §3 allows for this ticket. The model may return
    #: an id from this list and no other.
    eligible_technician_ids: list[UUID]


class AtRiskBatchRequest(BaseModel):
    """One micro-batch of at-risk tickets, evaluated together (§7).

    Together, not one at a time: §7 asks the agent to weigh the trade-off
    *across* the batch, and two tickets competing for the same technician's last
    comfortable slot is exactly the trade-off that per-ticket calls cannot see.
    """

    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    current_time: datetime
    tickets: list[AtRiskTicket]
    candidates: list[CandidateDispatchHistory]


class AtRiskAssignment(BaseModel):
    """The agent's choice for one ticket."""

    ticket_ref: str
    technician_id: UUID
    reason: str = Field(max_length=500)


class AtRiskBatchDecision(BaseModel):
    """What the agent returns for a whole micro-batch."""

    assignments: list[AtRiskAssignment] = Field(default_factory=list)


class AtRiskDecisionError(RuntimeError):
    """The agent produced no usable answer for this batch.

    Raised for a timeout, a transport failure, or an answer that named a
    technician outside the eligible set. All three are the same fact to the
    caller -- there is no agent decision -- and §7's escalation rule must not
    depend on telling them apart.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


__all__ = [
    "AtRiskAssignment",
    "AtRiskBatchDecision",
    "AtRiskBatchRequest",
    "AtRiskDecisionError",
    "AtRiskTicket",
    "CandidateDispatchHistory",
    "CategoryHandlingStats",
    "HistoryWindow",
    "PlannedSlot",
]
