"""The in-memory scheduler (§6).

Pure functions over plain dataclasses. Nothing here touches a `Session`, issues
a query or reads a clock: `now` is always passed in. That is what lets §8's
"run scheduling in memory after the bulk query" hold, and it is also what makes
the whole of §6 testable without a database.

The model, stated once so the rest of the module can be read against it:

* A technician's queue is a **sequence**, not a set. Work is simulated from the
  next working instant, one unit after another, spilling across the overnight
  gap (`shift.advance`).
* Every already-assigned unit carries a **committed deadline** -- the
  `planned_finish_at` written when it was placed. That commitment is the thing
  slack is measured against, and it is the reason `planned_finish_at` is
  persisted rather than recomputed on read.
* **Slack is the gap between a unit's committed deadline and where the current
  simulation actually lands it**, counted in *working* seconds. Wall-clock
  seconds would score the fourteen hours a technician is off shift as capacity.
* Placement commits `planned_finish_at = simulated finish + safety buffer`, so
  a freshly placed unit starts life with exactly one buffer of headroom. Later
  insertions ahead of it eat that headroom first and only then go negative.
  This is what makes the buffer a buffer rather than a constant offset.

From those, §6's two definitions fall out directly:

* **SAFE** -- some eligible technician absorbs the new unit while every unit
  already committed to them keeps slack >= 0.
* **AT_RISK** -- every eligible technician has at least one committed unit
  pushed into negative slack. The unit is still assignable; a trade-off is
  simply being made, and that is the only case §7 lets an agent near.

A unit that no technician can take *at all* is neither: it is infeasible, and
the caller escalates it to Building Management rather than asking an agent to
choose from an empty set (§3).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import inf
from uuid import UUID

from src.dispatch.shift import advance, next_shift_open, working_seconds_between
from src.models.enums import DispatchRiskState

#: §6 asks for "a safety buffer" without naming one. Thirty minutes: comfortably
#: shorter than the shortest P80 in §5 (three hours) so it never dominates the
#: arithmetic, and long enough to absorb travel and handover between two jobs in
#: the same building. Overridable through `Settings.dispatch_safety_buffer_seconds`;
#: this constant is the default that value falls back to.
DEFAULT_SAFETY_BUFFER = timedelta(minutes=30)


@dataclass(frozen=True)
class WorkUnit:
    """One indivisible piece of work in a technician's queue.

    "Indivisible" is load-bearing for the Visual Assignment board: a grouped
    cluster is a single `WorkUnit` covering several `ticket_ids`, which is how
    §1's "must not be split across technicians" is expressed here. Automatic
    Assignment skips grouping (§2), so on that path every unit holds exactly one
    ticket.
    """

    key: UUID
    ticket_ids: tuple[UUID, ...]
    duration: timedelta
    score: Decimal
    submitted_at: datetime
    #: The `planned_finish_at` already committed for this unit, or None for a
    #: unit being placed for the first time.
    deadline: datetime | None = None
    assignment_id: UUID | None = None
    #: An IN_PROGRESS unit is pinned to the front: a technician holding a live
    #: job finishes it before anything the scheduler adds, and re-ordering it
    #: would be describing a schedule nobody will follow.
    in_progress: bool = False
    started_at: datetime | None = None

    def __post_init__(self) -> None:
        """Normalize every timestamp on the way in.

        PostgreSQL hands back aware datetimes and SQLite naive ones, and this
        struct is built from both -- a bulk-loaded row, a freshly written
        assignment, a test fixture. Comparing the two raises, and the raise
        surfaces deep inside a sort where the origin of the bad value is
        invisible. Normalizing at the boundary means the scheduler's internals
        can assume aware datetimes everywhere.
        """
        for field_name in ("submitted_at", "deadline", "started_at"):
            value = getattr(self, field_name)
            if value is not None and value.tzinfo is None:
                object.__setattr__(self, field_name, value.replace(tzinfo=UTC))

    @property
    def is_committed(self) -> bool:
        return self.deadline is not None


@dataclass(frozen=True)
class Slot:
    """Where one unit lands in a simulated queue."""

    unit: WorkUnit
    order: int
    planned_start_at: datetime
    planned_finish_at: datetime
    #: Working seconds between the simulated finish and the committed deadline.
    #: Positive is headroom, negative is a broken commitment. None for a unit
    #: that has no commitment yet, because "slack" is not defined for it.
    slack_seconds: int | None


@dataclass(frozen=True)
class Placement:
    """The result of trying one unit against one technician."""

    technician_id: UUID
    slots: tuple[Slot, ...]
    candidate: Slot
    #: The lowest slack across every *already committed* unit after inserting.
    #: None when the technician held no committed work, which is vacuously safe.
    worst_committed_slack: int | None

    @property
    def is_safe(self) -> bool:
        return self.worst_committed_slack is None or self.worst_committed_slack >= 0

    @property
    def committed_deadline(self) -> datetime:
        """What `planned_finish_at` this placement would write for the unit."""
        return self.candidate.planned_finish_at


@dataclass(frozen=True)
class PlacementDecision:
    """What the scheduler concluded about one unit across all technicians."""

    unit: WorkUnit
    risk_state: DispatchRiskState | None
    #: Ranked best-first under the rule documented on `rank_placements`. Empty
    #: only when no technician could take the unit at all.
    placements: tuple[Placement, ...] = field(default_factory=tuple)

    @property
    def is_feasible(self) -> bool:
        return bool(self.placements)

    @property
    def best(self) -> Placement | None:
        return self.placements[0] if self.placements else None


# ----------------------------------------------------------------------------
# Ordering (§6 items 1-4).
# ----------------------------------------------------------------------------


def provisional_deadline(unit: WorkUnit, buffer: timedelta) -> datetime:
    """The commitment a not-yet-placed unit would have been given on arrival.

    Measured from the unit's own **submission time**, not from now: that is what
    turns waiting into urgency. A report that has sat for three working hours
    gets a deadline three working hours in the past relative to a fresh one, so
    it sorts ahead of it -- which is §6 item 1 doing the work item 3 would
    otherwise have to do as a tie-break.

    Placement commits `simulated finish + buffer` (see `place`), so this uses
    the same shape. That symmetry is what makes a brand-new unit and a
    just-placed one tie at slack zero and fall through to score, instead of the
    new one always jumping the queue.
    """
    return advance(unit.submitted_at, unit.duration + buffer)


def sort_slack_seconds(unit: WorkUnit, now: datetime, buffer: timedelta) -> int:
    """"Remaining slack" for ordering purposes -- §6 item 1.

    Position-independent on purpose. Slack computed from a unit's place in the
    queue could not be used to *decide* that place without circularity, so the
    ordering key is the classic minimum-slack-time form: the working time still
    available before the deadline, less the work still to do, less the buffer.

    A unit with no commitment yet is measured against `provisional_deadline`.
    Returning a flat zero here instead -- the obvious shortcut -- makes every
    new unit more urgent than every on-time commitment, so *every* insertion
    onto a non-empty technician breaks something and reports AT_RISK. That would
    send the whole queue to the agent and empty §7 of meaning.
    """
    remaining = _remaining_duration(unit, now)
    deadline = unit.deadline if unit.deadline is not None else provisional_deadline(unit, buffer)
    available = working_seconds_between(now, deadline)
    return available - int(remaining.total_seconds()) - int(buffer.total_seconds())


def _remaining_duration(unit: WorkUnit, now: datetime) -> timedelta:
    """How much work is left in a unit.

    Only an IN_PROGRESS unit can be part-done. Its elapsed time is counted in
    working seconds like everything else, and floored at zero so a job that has
    overrun its P80 estimate is treated as "about to finish" rather than as
    negative capacity that would let extra work be booked for free.
    """
    if not unit.in_progress or unit.started_at is None:
        return unit.duration
    elapsed = working_seconds_between(unit.started_at, now)
    remaining = int(unit.duration.total_seconds()) - max(elapsed, 0)
    return timedelta(seconds=max(remaining, 0))


def order_units(units: list[WorkUnit], now: datetime, buffer: timedelta) -> list[WorkUnit]:
    """§6 items 1-4, with IN_PROGRESS pinned in front.

    Item 4 says "if still equal, either order is acceptable"; the unit key is
    appended anyway so that two runs over the same data produce the same
    schedule. An acceptable-but-unstable order would make every scheduler test
    flaky for no gain.
    """
    return sorted(
        units,
        key=lambda unit: (
            0 if unit.in_progress else 1,
            sort_slack_seconds(unit, now, buffer),
            -unit.score,
            unit.submitted_at,
            str(unit.key),
        ),
    )


# ----------------------------------------------------------------------------
# Simulation.
# ----------------------------------------------------------------------------


def simulate(units: list[WorkUnit], now: datetime, buffer: timedelta) -> tuple[Slot, ...]:
    """Walk one technician's ordered queue through the working window."""
    cursor = next_shift_open(now)
    slots: list[Slot] = []
    for order, unit in enumerate(order_units(units, now, buffer)):
        remaining = _remaining_duration(unit, now)
        start = cursor
        finish = advance(cursor, remaining)
        slack = working_seconds_between(finish, unit.deadline) if unit.deadline is not None else None
        slots.append(
            Slot(
                unit=unit,
                order=order,
                # A live job reports the moment it really began, not the moment
                # the simulation happened to run. The technician screen shows
                # this, and "started at 14:05" turning into "starts at 15:30"
                # on every refresh would be nonsense.
                planned_start_at=unit.started_at if unit.in_progress and unit.started_at else start,
                planned_finish_at=finish,
                slack_seconds=slack,
            )
        )
        cursor = finish
    return tuple(slots)


def place(
    technician_id: UUID,
    existing: list[WorkUnit],
    unit: WorkUnit,
    now: datetime,
    buffer: timedelta,
) -> Placement:
    """Insert one unit into one technician's queue and report the damage."""
    slots = simulate([*existing, unit], now, buffer)
    candidate = next(slot for slot in slots if slot.unit.key == unit.key)
    committed = [slot.slack_seconds for slot in slots if slot.unit.key != unit.key and slot.slack_seconds is not None]
    # The commitment this placement would write: where the simulation lands the
    # unit, plus one buffer of working time. See the module docstring.
    committed_finish = advance(candidate.planned_finish_at, buffer)
    return Placement(
        technician_id=technician_id,
        slots=slots,
        candidate=replace(candidate, planned_finish_at=committed_finish),
        worst_committed_slack=min(committed) if committed else None,
    )


def rank_placements(placements: list[Placement]) -> tuple[Placement, ...]:
    """Order technicians best-first for one unit.

    §6 defines the ordering *inside* a technician's queue and defines SAFE and
    AT_RISK, but says nothing about choosing between two technicians who are
    both safe. The rule here, in order:

    1. **Safe before at-risk.** Never trade a kept commitment for a broken one.
    2. **Earliest start.** The expected start time is the one forward-looking
       value a resident is shown (§4), so serving soonest is the objective that
       matches what was promised.
    3. **Most headroom left.** Between two technicians who would start at the
       same moment, leave the schedule that survives the next arrival better.
    4. **Least loaded, then id.** A deterministic tail, so the same inputs rank
       the same way twice.

    For an AT_RISK unit this ranking is also the fallback order: the head of the
    list is the least-negative-slack technician, which is what the dispatcher
    assigns to when the agent times out or fails.
    """
    return tuple(
        sorted(
            placements,
            key=lambda placement: (
                0 if placement.is_safe else 1,
                placement.candidate.planned_start_at,
                # `None` means the technician holds no commitment at all, which
                # is unlimited headroom rather than none -- treating it as zero
                # would rank an empty queue below a busy one that happens to
                # have slack left.
                -(placement.worst_committed_slack if placement.worst_committed_slack is not None else inf),
                len(placement.slots),
                str(placement.technician_id),
            ),
        )
    )


def decide(
    unit: WorkUnit,
    queues: dict[UUID, list[WorkUnit]],
    now: datetime,
    buffer: timedelta = DEFAULT_SAFETY_BUFFER,
) -> PlacementDecision:
    """§6's verdict for one unit over the technicians that passed §3.

    `queues` must already be filtered to technicians satisfying every hard
    constraint. This function never re-checks skill, availability or shift --
    not because it trusts the caller, but because a scheduler that could
    *reinstate* a technician the eligibility layer removed would be a second
    place where §3 is decided, and §3 must have exactly one.
    """
    if not queues:
        return PlacementDecision(unit=unit, risk_state=None, placements=())
    placements = rank_placements([place(tid, existing, unit, now, buffer) for tid, existing in queues.items()])
    risk = DispatchRiskState.SAFE if placements[0].is_safe else DispatchRiskState.AT_RISK
    return PlacementDecision(unit=unit, risk_state=risk, placements=placements)


__all__ = [
    "DEFAULT_SAFETY_BUFFER",
    "Placement",
    "PlacementDecision",
    "Slot",
    "WorkUnit",
    "decide",
    "order_units",
    "provisional_deadline",
    "place",
    "rank_placements",
    "simulate",
    "sort_slack_seconds",
]
