"""The deterministic selection rule (RULE_ENGINE_V1).

This replaces a language model with an ordering. Nothing here does I/O, reads a
clock or touches a database: it takes the candidate snapshot Backend already
built and returns the same `AssignmentDecisionV4` the LLM path returned, which
is what lets both engines sit behind one switch.

Three ideas, in the order they apply.

**1. Hard filter.** `AssignmentCandidateService` has already applied the three
conditions of contract §4.1 — active profile, availability on, skill matching
the Category — plus the per-work-item exclusion list of §4.3. What is left for
this module is the one filter that has no snapshot column: configured load
caps, checked against *projected* load so a technician chosen earlier in the
same batch already counts.

**2. Work item order.** P3 first, then P2, then P1; inside one priority, the
older work item first. An incident case is one work item and is never split,
exactly as it was for the model.

**3. A lexicographic key, and no randomness.** Per priority:

| Priority | Key, in order |
| --- | --- |
| P3 | fewest projected P3, fewest projected total, longest since last assigned, technician_id |
| P2 | fewest projected total, fewest projected P3, longest since last assigned, technician_id |
| P1 | fewest projected total, longest since last assigned, technician_id |

P3 leads with the P3 count because five minutes is not a workload, it is an
interrupt: whoever is already running one emergency is the worst person to
hand a second one, whatever their total looks like. P1 and P2 lead with total
load, and P2 keeps P3 as its second key so ordinary work drifts away from
whoever is mid-emergency. `technician_id` last makes the whole thing a total
order — two runs on identical input pick the same person, which is what makes
a decision explainable after the fact.

**Projected load** carries the batch balancing the contract used to ask the
model for (§4.3a): after each decision the winner's projected counters grow by
the work item's ticket count, so the next work item in the same batch sees the
load it just created rather than one frozen snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from src.assignment_agent.schemas import (
    AssignmentDecisionType,
    AssignmentDecisionV4,
    CandidateSnapshotV4,
    WorkItemV4,
)
from src.assignment_rules.config import AssignmentRuleConfig
from src.models.enums import Priority

# Never assigned beats everyone who ever was, so `None` has to sort first
# rather than last.
NEVER_ASSIGNED = datetime.min.replace(tzinfo=UTC)

# P3 before P2 before P1 — the numbering is inherited from the scoring rules and
# runs the opposite way from urgency.
PRIORITY_RANK = {Priority.P3: 0, Priority.P2: 1, Priority.P1: 2}

# Sorts a work item with no creation timestamp behind the ones that have one,
# instead of letting a missing field jump the queue.
UNDATED = datetime.max.replace(tzinfo=UTC)


def _aware(value: datetime | None, default: datetime) -> datetime:
    if value is None:
        return default
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass
class ProjectedLoad:
    """Running load per technician: the snapshot plus this batch so far.

    Seeded from the candidate snapshot the first time a technician is seen. The
    same technician may appear in several work items with their own candidate
    lists; the first seeding wins, because those counts describe the technician
    rather than the item.
    """

    total: dict[UUID, int] = field(default_factory=dict)
    by_priority: dict[tuple[UUID, str], int] = field(default_factory=dict)
    seeded: set[UUID] = field(default_factory=set)

    def seed(self, candidate: CandidateSnapshotV4) -> None:
        technician_id = candidate.technician_id
        if technician_id in self.seeded:
            return
        self.seeded.add(technician_id)
        self.total[technician_id] = candidate.active_assignment_count
        self.by_priority[(technician_id, Priority.P3.value)] = candidate.active_p3_count
        # §4.3 only ever carried the total and the P3 count. The P1/P2 splits
        # are optional additions for the per-priority caps; when Backend does
        # not send them that cap simply cannot bind, which is better than
        # inventing a number and refusing work over it.
        if candidate.active_p1_count is not None:
            self.by_priority[(technician_id, Priority.P1.value)] = candidate.active_p1_count
        if candidate.active_p2_count is not None:
            self.by_priority[(technician_id, Priority.P2.value)] = candidate.active_p2_count

    def total_for(self, technician_id: UUID) -> int:
        return self.total.get(technician_id, 0)

    def priority_for(self, technician_id: UUID, priority: Priority) -> int:
        return self.by_priority.get((technician_id, priority.value), 0)

    def knows_priority(self, technician_id: UUID, priority: Priority) -> bool:
        return (technician_id, priority.value) in self.by_priority

    def add(self, technician_id: UUID, priority: Priority, ticket_count: int) -> None:
        """§4.3a: one decision consumes capacity for every ticket it hands over.

        A five-member incident case is one decision and five tickets, and the
        next work item in the batch has to see all five.
        """
        self.total[technician_id] = self.total_for(technician_id) + ticket_count
        if self.knows_priority(technician_id, priority):
            self.by_priority[(technician_id, priority.value)] += ticket_count


def sort_work_items(items: list) -> list:
    """P3, then P2, then P1; inside a priority, oldest first.

    Takes anything carrying a `.work_item`. `work_item_id` is the final key so
    two work items created in the same millisecond still have one fixed order.
    """
    return sorted(
        items,
        key=lambda item: (
            PRIORITY_RANK.get(item.work_item.priority, len(PRIORITY_RANK)),
            _aware(item.work_item.created_at, UNDATED),
            str(item.work_item.work_item_id),
        ),
    )


def within_caps(
    candidate: CandidateSnapshotV4,
    priority: Priority,
    projected: ProjectedLoad,
    config: AssignmentRuleConfig,
) -> bool:
    """Does this technician have room for one more work item?

    "One more work item", not "one more ticket": a cap of 5 must not make a
    five-member incident case unassignable to an idle technician. The cap asks
    whether someone is already at their limit, and a case is one piece of work
    landing on one person.
    """
    technician_id = candidate.technician_id
    total_cap = config.max_active_assignments
    if total_cap is not None and projected.total_for(technician_id) >= total_cap:
        return False
    priority_cap = config.cap_for(priority.value)
    if priority_cap is None or not projected.knows_priority(technician_id, priority):
        return True
    return projected.priority_for(technician_id, priority) < priority_cap


def rank_key(
    candidate: CandidateSnapshotV4,
    priority: Priority,
    projected: ProjectedLoad,
    config: AssignmentRuleConfig,
) -> tuple:
    technician_id = candidate.technician_id
    total = projected.total_for(technician_id)
    p3 = projected.priority_for(technician_id, Priority.P3)
    idle_since = (
        _aware(candidate.last_assigned_at, NEVER_ASSIGNED)
        if config.tie_break_on_last_assigned_at
        else NEVER_ASSIGNED
    )
    if priority is Priority.P3:
        head: tuple = (p3, total)
    elif priority is Priority.P2:
        head = (total, p3)
    else:
        head = (total,)
    return (*head, idle_since, str(technician_id))


@dataclass(frozen=True)
class Selection:
    """One work item resolved. `technician_id is None` means nobody fit."""

    technician_id: UUID | None
    reason: str
    projected_total: int = 0
    projected_p3: int = 0
    overloaded: bool = False


def select(
    work_item: WorkItemV4,
    candidates: list[CandidateSnapshotV4],
    projected: ProjectedLoad,
    config: AssignmentRuleConfig,
) -> Selection:
    """Pick one technician for one work item, and record why.

    Does not mutate `projected`; the caller commits the choice with
    `projected.add(...)`, so a caller that discards a selection never has to
    unwind load it would have created.
    """
    for candidate in candidates:
        projected.seed(candidate)

    priority = work_item.priority
    eligible = [item for item in candidates if within_caps(item, priority, projected, config)]
    overloaded = False

    if not eligible:
        if priority is not Priority.P3 or not config.allow_p3_overload_when_all_capped:
            return Selection(
                technician_id=None,
                reason=(
                    f"Không chọn được theo {config.rule_version}: toàn bộ {len(candidates)} ứng viên hợp lệ "
                    "đã chạm giới hạn tải cấu hình."
                ),
            )
        # §11.7 and nghiệp vụ §0.1c: P3 promises five minutes, and that promise
        # is never traded for load balance. Placing it over the cap and saying
        # so is what the business rules ask for; the alternative is a P3 sitting
        # in the manual queue past its own deadline.
        eligible = list(candidates)
        overloaded = True

    winner = min(eligible, key=lambda item: rank_key(item, priority, projected, config))
    total = projected.total_for(winner.technician_id)
    p3 = projected.priority_for(winner.technician_id, Priority.P3)
    if overloaded:
        reason = (
            f"Chọn theo {config.rule_version}: tải dự kiến {total}, P3 dự kiến {p3}; "
            "mọi ứng viên đều đã chạm giới hạn nên áp ngoại lệ quá tải P3 để giữ cam kết 5 phút."
        )
    else:
        reason = (
            f"Chọn theo {config.rule_version}: tải dự kiến {total}, P3 dự kiến {p3}; "
            "ưu tiên thấp nhất trong nhóm ứng viên hợp lệ."
        )
    return Selection(
        technician_id=winner.technician_id,
        reason=reason,
        projected_total=total,
        projected_p3=p3,
        overloaded=overloaded,
    )


def decide_items(
    items: list,
    config: AssignmentRuleConfig,
    *,
    decided_at: datetime,
) -> list[AssignmentDecisionV4]:
    """Resolve a whole request, in work-item order, over one projected load.

    Returns one decision per work item in the order the caller passed them,
    rather than the order they were processed, so the result reads alongside
    the request. `SELECTED` and `NO_SUITABLE_CANDIDATE` are the only outcomes:
    there is no third "the engine broke" answer, which is the point of removing
    the model.
    """
    projected = ProjectedLoad()
    by_decision: dict[UUID, AssignmentDecisionV4] = {}

    for item in sort_work_items(items):
        selection = select(item.work_item, item.candidates, projected, config)
        by_decision[item.decision_id] = AssignmentDecisionV4(
            decision_id=item.decision_id,
            work_item_id=item.work_item.work_item_id,
            selected_technician_id=selection.technician_id,
            decision=(
                AssignmentDecisionType.SELECTED
                if selection.technician_id is not None
                else AssignmentDecisionType.NO_SUITABLE_CANDIDATE
            ),
            reason=selection.reason[:500],
            model_version=config.rule_version,
            decided_at=decided_at,
        )
        if selection.technician_id is not None:
            projected.add(selection.technician_id, item.work_item.priority, item.work_item.ticket_count)

    return [by_decision[item.decision_id] for item in items]


__all__ = [
    "NEVER_ASSIGNED",
    "PRIORITY_RANK",
    "ProjectedLoad",
    "Selection",
    "decide_items",
    "rank_key",
    "select",
    "sort_work_items",
    "within_caps",
]
