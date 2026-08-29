"""The §3 hard constraints, in exactly one place.

Both workflows come through here. Automatic Assignment uses it to build the
candidate set it hands the scheduler (and, for the at-risk subset, the agent);
Visual Assignment uses it to decide what the board blocks and what the bulk
confirm rejects. Two copies of these rules would eventually disagree, and the
half that disagreed would be the one an agent was allowed to reason against.

**What is hard, and what is not.** §3 enumerates the non-negotiable
constraints, and this module treats that list as closed:

* technician profile active, and the user account behind it active;
* technician available;
* technician holds the skill for the ticket's category;
* the moment is inside the 08:00-18:00 working shift;
* a technician may hold only one IN_PROGRESS ticket at a time;
* a ticket may not receive a second active assignment;
* P3 never enters the automatic workflow.

Workload and schedule risk are deliberately **not** in that list, because §3
does not put them there. They are real signals and the board shows them, but
they are advisory: a placement that merely makes someone's day long is a
judgement Building Management is entitled to make, while a placement that hands
work to someone without the skill is not.

`one IN_PROGRESS at a time` is checked when a technician *starts*, not when they
are assigned. A technician legitimately holds a queue of ASSIGNED work -- that
queue is the whole point of §4's "Do now / Next" -- and enforcing the rule at
assignment time would collapse it to a single ticket.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.dispatch.shift import is_within_shift
from src.models.enums import PlacementWarningCode


@dataclass(frozen=True)
class EligibilityInput:
    """Everything §3 needs about one technician, already loaded.

    A frozen snapshot rather than an ORM row: the scheduler runs in memory over
    a bulk-loaded picture of the world (§8), and a lazy-loading ORM object in
    this position is precisely the per-ticket query the contract forbids.
    """

    technician_id: UUID
    is_active: bool
    is_available: bool
    user_is_active: bool
    skill_category_ids: frozenset[UUID]


def hard_constraint_violations(
    technician: EligibilityInput,
    *,
    category_id: UUID | None,
    now: datetime,
    excluded_technician_ids: frozenset[UUID] = frozenset(),
) -> tuple[PlacementWarningCode, ...]:
    """Every §3 rule this technician fails for this work, in a stable order.

    Empty means eligible. The tuple is ordered most-explanatory first, because
    a UI showing one badge should show "no skill" rather than "out of shift"
    when both are true -- the first is about this pairing, the second is about
    the clock and will clear on its own.

    `excluded_technician_ids` carries the reassignment exclusion: anyone who
    already rejected this work item. It is
    scoped to one work item and is not a blacklist -- the same technician stays
    an ordinary candidate everywhere else, and Building Management may still
    place them by hand.
    """
    violations: list[PlacementWarningCode] = []
    if category_id is not None and category_id not in technician.skill_category_ids:
        violations.append(PlacementWarningCode.MISSING_SKILL)
    if not (technician.is_active and technician.is_available and technician.user_is_active):
        violations.append(PlacementWarningCode.TECHNICIAN_UNAVAILABLE)
    if technician.technician_id in excluded_technician_ids:
        violations.append(PlacementWarningCode.TECHNICIAN_UNAVAILABLE)
    if not is_within_shift(now):
        violations.append(PlacementWarningCode.OUT_OF_SHIFT)
    # `dict.fromkeys` rather than `set`: two different rules can both report
    # TECHNICIAN_UNAVAILABLE, and the caller wants it once, in this order.
    return tuple(dict.fromkeys(violations))


def is_eligible(
    technician: EligibilityInput,
    *,
    category_id: UUID | None,
    now: datetime,
    excluded_technician_ids: frozenset[UUID] = frozenset(),
) -> bool:
    return not hard_constraint_violations(
        technician,
        category_id=category_id,
        now=now,
        excluded_technician_ids=excluded_technician_ids,
    )


def eligible_technician_ids(
    technicians: list[EligibilityInput],
    *,
    category_id: UUID | None,
    now: datetime,
    excluded_technician_ids: frozenset[UUID] = frozenset(),
) -> list[UUID]:
    """The candidate set, sorted for determinism.

    This is the *only* set the at-risk agent is ever shown (§7). The agent
    cannot widen it: the validator rejects any answer outside it, and this
    function is what produced it.
    """
    return sorted(
        (
            technician.technician_id
            for technician in technicians
            if is_eligible(
                technician,
                category_id=category_id,
                now=now,
                excluded_technician_ids=excluded_technician_ids,
            )
        ),
        key=str,
    )


__all__ = [
    "EligibilityInput",
    "eligible_technician_ids",
    "hard_constraint_violations",
    "is_eligible",
]
