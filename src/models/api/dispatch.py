"""Wire contracts for dispatch and at-risk visibility (§10).

Building Management needs to be able to answer three questions about the
automatic path without reading a log file:

* what is queued, and what happened to it (`DispatchEventResponse`);
* which decisions were AT_RISK, who made them, and why
  (`AtRiskDecisionResponse`);
* is the switch on, and who turned it on (`AutoAssignmentToggleResponse`).

`AtRiskDecisionResponse.decision_source` is the field that matters most in
review: `AGENT` means a model weighed the trade-off, `SCHEDULER_FALLBACK` means
it did not answer in time and the least-negative-slack candidate was taken
instead. Both are legitimate outcomes and both notify Building Management, but
they are not the same event and the payload never blurs them.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DispatchEventResponse(BaseModel):
    id: UUID
    ticket_id: UUID
    ticket_display_code: str | None = None
    status: str
    priority: str
    risk_state: str | None = None
    decision_source: str | None = None
    selected_technician_id: UUID | None = None
    selected_technician_name: str | None = None
    assignment_id: UUID | None = None
    batch_id: UUID | None = None
    attempt_count: int = 0
    planned_start_at: datetime | None = None
    planned_finish_at: datetime | None = None
    slack_seconds: int | None = None
    escalation_reason: str | None = None
    error_code: str | None = None
    enqueued_at: datetime
    available_at: datetime
    decided_at: datetime | None = None


class AtRiskDecisionResponse(BaseModel):
    id: UUID
    dispatch_event_id: UUID
    ticket_id: UUID
    ticket_display_code: str | None = None
    batch_id: UUID
    technician_id: UUID | None = None
    technician_name: str | None = None
    #: `AGENT` or `SCHEDULER_FALLBACK`. See the module docstring.
    decision_source: str
    reason: str | None = None
    model_name: str | None = None
    latency_ms: int | None = None
    #: The eligible set the backend allowed. Present so a reviewer can see the
    #: choice was made from an authorised list rather than from the whole roster.
    candidate_technician_ids: list[UUID] = Field(default_factory=list)
    slack_seconds: int | None = None
    error_code: str | None = None
    created_at: datetime


class AutoAssignmentToggleResponse(BaseModel):
    """§2's switch. One boolean and its provenance -- no delay, no batch."""

    enabled: bool
    version: int
    enabled_at: datetime | None = None
    enabled_by_user_id: UUID | None = None
    enabled_by_name: str | None = None
    updated_at: datetime | None = None
    #: How many dispatch events are waiting right now. Rendered next to the
    #: toggle so turning it off is an informed act rather than a blind one.
    open_event_count: int = 0


class AutoAssignmentToggleRequest(BaseModel):
    """The confirmation modal's outcome.

    `acknowledged` exists because §2 requires the manager to be shown what
    turning the switch on means before it is turned on. The server refuses
    `enabled=true` without it, so a client that skips the modal cannot enable
    autonomy by accident -- and, unlike the rule it replaces, this one is about
    informed consent rather than about a prior workflow step.
    """

    enabled: bool
    acknowledged: bool = False
    #: The version the client rendered the toggle from, for optimistic
    #: concurrency. Optional: a client that does not send it simply gets
    #: last-write-wins.
    expected_version: int | None = None


class DispatchWorkerRunResponse(BaseModel):
    """The result of one manually triggered micro-batch, for operations."""

    batch_id: str | None = None
    claimed: int = 0
    reclaimed: int = 0
    assigned_safe: int = 0
    assigned_by_agent: int = 0
    assigned_by_fallback: int = 0
    at_risk: int = 0
    escalated: int = 0
    out_of_shift: bool = False
    query_count: int = 0
    agent_calls: int = 0
    agent_error: str | None = None
    duration_ms: int = 0
    errors: list[str] = Field(default_factory=list)


__all__ = [
    "AtRiskDecisionResponse",
    "AutoAssignmentToggleRequest",
    "AutoAssignmentToggleResponse",
    "DispatchEventResponse",
    "DispatchWorkerRunResponse",
]
