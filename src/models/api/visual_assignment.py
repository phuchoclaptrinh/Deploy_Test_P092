"""Wire contracts for the Visual Assignment board (§1, §10).

Two endpoints, and the split between them is deliberate:

* `GET  /coordinator/visual-assignment/board`   -> `VisualBoardResponse`
* `POST /coordinator/visual-assignment/confirm` -> `VisualConfirmResponse`

The board response is **complete**: it carries, for every unit, what would
happen against every technician. That is more data than a lazier design would
send, and it is the point -- a board that had to ask the server what a drop
would do would either be slow or would let a manager drop first and find out
afterwards. §1 asks for warnings on the board, not on the way back from it.

The confirm request is deliberately minimal: unit id and technician id, nothing
else. It carries no planned times, no warnings and no acknowledgement flags,
because none of those is the client's to assert -- the server recomputes all of
them under lock. A field a client could lie about is a field the server must
not read.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BoardPlacementPreviewResponse(BaseModel):
    """What dropping one unit on one technician would do."""

    technician_id: UUID
    #: True when a §3 hard constraint fails. The board must refuse the drop --
    #: `POST /confirm` will reject the whole board otherwise.
    blocked: bool
    #: Hard violations first, then advisory ones. `MISSING_SKILL`,
    #: `TECHNICIAN_UNAVAILABLE` and `OUT_OF_SHIFT` block; `OVERLOADED` and
    #: `SCHEDULE_RISK` are shown and allowed.
    warnings: list[str] = Field(default_factory=list)
    planned_start_at: datetime | None = None
    #: Internal scheduling value (§4). Shown to Building Management, never to a
    #: resident, and never described as a completion promise.
    planned_finish_at: datetime | None = None
    #: Worst remaining slack across this technician's already-committed work if
    #: the unit landed here. Negative means an existing promise would break.
    worst_slack_seconds: int | None = None


class BoardUnitResponse(BaseModel):
    """One draggable item in the pool.

    A `GROUP` unit covers several tickets and is indivisible (§1). The client is
    given the member list for display only; there is no per-member placement,
    and the confirm endpoint accepts the unit id alone.
    """

    unit_id: str
    unit_type: str
    ticket_ids: list[UUID]
    display_codes: list[str]
    category_id: UUID | None = None
    category_code: str | None = None
    category_display_name: str | None = None
    priority: str | None = None
    score: float = 0.0
    submitted_at: datetime
    location_labels: list[str] = Field(default_factory=list)
    #: Internal P80 estimate for the whole unit, in seconds (§5).
    p80_seconds: int = 0
    member_count: int = 1
    eligible_technician_ids: list[UUID] = Field(default_factory=list)
    previews: list[BoardPlacementPreviewResponse] = Field(default_factory=list)


class BoardPlannedSlotResponse(BaseModel):
    assignment_id: UUID | None = None
    ticket_id: UUID | None = None
    order: int
    planned_start_at: datetime
    planned_finish_at: datetime
    slack_seconds: int | None = None
    in_progress: bool = False


class BoardTechnicianResponse(BaseModel):
    technician_id: UUID
    display_name: str
    is_active: bool
    is_available: bool
    skill_category_ids: list[UUID] = Field(default_factory=list)
    active_assignment_count: int = 0
    in_progress_count: int = 0
    planned_slots: list[BoardPlannedSlotResponse] = Field(default_factory=list)
    day_ends_at: datetime | None = None


class VisualBoardResponse(BaseModel):
    generated_at: datetime
    #: False outside 08:00-18:00 Vietnam time. Every placement is blocked with
    #: `OUT_OF_SHIFT` while it is false, so the board says so once at the top
    #: rather than repeating it on every card.
    within_working_shift: bool
    units: list[BoardUnitResponse] = Field(default_factory=list)
    technicians: list[BoardTechnicianResponse] = Field(default_factory=list)


class VisualPlacementRequest(BaseModel):
    unit_id: str = Field(min_length=1, max_length=100)
    technician_id: UUID


class VisualConfirmRequest(BaseModel):
    """Every manual placement, confirmed in one action (§1).

    `min_length=1`: confirming nothing is a client bug, not a no-op worth a
    success response. The board's confirm button is disabled until something is
    placed.
    """

    placements: list[VisualPlacementRequest] = Field(min_length=1, max_length=200)


class VisualConfirmResponse(BaseModel):
    assigned_unit_count: int
    assigned_ticket_count: int
    assignment_ids: list[UUID] = Field(default_factory=list)


__all__ = [
    "BoardPlacementPreviewResponse",
    "BoardPlannedSlotResponse",
    "BoardTechnicianResponse",
    "BoardUnitResponse",
    "VisualBoardResponse",
    "VisualConfirmRequest",
    "VisualConfirmResponse",
    "VisualPlacementRequest",
]
