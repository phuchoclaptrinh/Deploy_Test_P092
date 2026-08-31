"""Deterministic risk scoring. The whole rubric, and nothing but the rubric.

This module is the executable half of `docs/risk_scoring_v2.md`. Five criteria
scored 0-4 by the analysis Agent, five fixed weights summing to 100, one
threshold table, and a blocker list that can only ever raise a priority.

**What is deliberately absent, and why the absence is the design.**

* **Category.** Nothing here imports `src.models.enums.Category`. A category is
  a routing and reporting label, kept deliberately broad; the day it starts
  adding points is the day "which bucket did the Agent pick" becomes a priority
  decision again, which is exactly what v2 removed. The import list at the top
  of this file is the enforcement.
* **Location.** No function takes one. A rooftop leak and a bedroom leak differ
  in `property_spread` and `affected_scope`, and the Agent says so with
  evidence -- the backend does not add a bonus behind its back. In particular a
  common area is *not* automatically `affected_scope=4`: a dead corridor
  lightbulb affects nobody's apartment.
* **A total from the Agent.** `calculate_risk_score` takes five integers. There
  is no parameter it could be handed a pre-computed score through, so "the AI
  decided the priority" cannot happen by accident.

**Arithmetic.** `Decimal` throughout, with exactly one quantization at the end.
Every contribution is `score / 4 x weight` where the current weights are
35/35/20/5/5, so every reachable value is an exact multiple of `1.25` and the
final `quantize` is lossless. Rounding halfway through -- five times, once per
criterion -- is how a ticket lands one side of a threshold in the calculator and
the other side in a report.

`priority_from_score` is applied to the *quantized* score, so the priority
stored next to a `NUMERIC(5,2)` is always the priority that number implies.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from src.models.enums import Priority

#: Stamped on every `ticket_risk_assessments` row. Bump when a weight, a
#: threshold or an anchor changes, never for a refactor.
RUBRIC_VERSION = "risk-v2.1"

#: Weights, in the order they appear in the contract. They sum to 100, which is
#: what makes `risk_score` a percentage of the worst possible incident rather
#: than an arbitrary index.
#: Declaration order is the contract's field order, not the weight order. The
#: payload, the table columns and the audit view all read the criteria in this
#: sequence, and reordering them to follow a weight change would churn four
#: unrelated surfaces every time the rubric is retuned. Display order lives in
#: the frontend, where it belongs.
CRITERION_WEIGHTS: dict[str, Decimal] = {
    "human_safety": Decimal(35),
    "property_spread": Decimal(5),
    "essential_function": Decimal(35),
    "affected_scope": Decimal(20),
    "deterioration_speed": Decimal(5),
}

CRITERION_NAMES: tuple[str, ...] = tuple(CRITERION_WEIGHTS)

MIN_CRITERION_SCORE = 0
MAX_CRITERION_SCORE = 4

#: The single place the P1 < P2 < P3 < P4 < P5 direction is written down.
#:
#: Worth stating loudly because v1 ran the other way: P3 was the five-minute
#: emergency and P1 was the routine multi-day promise. Any comparison that
#: predates v2 means the opposite of what it says now.
PRIORITY_RANK: dict[Priority, int] = {
    Priority.P1: 1,
    Priority.P2: 2,
    Priority.P3: 3,
    Priority.P4: 4,
    Priority.P5: 5,
}

#: Lower bound of each band, highest first. Bands are `[lower, next_lower)`,
#: with the top band closed at 100.
SCORE_THRESHOLDS: tuple[tuple[Decimal, Priority], ...] = (
    (Decimal(80), Priority.P5),
    (Decimal(60), Priority.P4),
    (Decimal(40), Priority.P3),
    (Decimal(20), Priority.P2),
    (Decimal(0), Priority.P1),
)

MIN_RISK_SCORE = Decimal(0)
MAX_RISK_SCORE = Decimal(100)

#: Two decimal places, matching `ticket_risk_assessments.risk_score`.
SCORE_QUANTUM = Decimal("0.01")


class BlockerCode(str, Enum):  # noqa: UP042
    """Facts that set a floor under the priority regardless of the score.

    A blocker is not evidence to be weighed -- it is a named situation whose
    handling is already decided. "There is smoke" does not need a rubric.

    The score still runs. A blockered ticket that scores *higher* than its floor
    keeps the higher priority, because the floor is a minimum and never a cap.
    """

    # --- floor P5 -----------------------------------------------------------
    FIRE_OR_SMOKE = "FIRE_OR_SMOKE"
    ELECTRIC_SHOCK_OR_LIVE_WIRE = "ELECTRIC_SHOCK_OR_LIVE_WIRE"
    GAS_LEAK_OR_ASPHYXIATION = "GAS_LEAK_OR_ASPHYXIATION"
    SERIOUS_INJURY = "SERIOUS_INJURY"
    PERSON_TRAPPED_IN_ELEVATOR = "PERSON_TRAPPED_IN_ELEVATOR"
    SOLE_ESCAPE_ROUTE_BLOCKED = "SOLE_ESCAPE_ROUTE_BLOCKED"
    ONGOING_VIOLENCE = "ONGOING_VIOLENCE"

    # --- floor P4 -----------------------------------------------------------
    SEWAGE_OVERFLOW = "SEWAGE_OVERFLOW"
    HEAVY_WATER_FLOW_SPREAD_RISK = "HEAVY_WATER_FLOW_SPREAD_RISK"
    TOTAL_UNPLANNED_UTILITY_LOSS = "TOTAL_UNPLANNED_UTILITY_LOSS"
    SOLE_TOILET_UNUSABLE = "SOLE_TOILET_UNUSABLE"


BLOCKER_FLOORS: dict[BlockerCode, Priority] = {
    BlockerCode.FIRE_OR_SMOKE: Priority.P5,
    BlockerCode.ELECTRIC_SHOCK_OR_LIVE_WIRE: Priority.P5,
    BlockerCode.GAS_LEAK_OR_ASPHYXIATION: Priority.P5,
    BlockerCode.SERIOUS_INJURY: Priority.P5,
    BlockerCode.PERSON_TRAPPED_IN_ELEVATOR: Priority.P5,
    BlockerCode.SOLE_ESCAPE_ROUTE_BLOCKED: Priority.P5,
    BlockerCode.ONGOING_VIOLENCE: Priority.P5,
    BlockerCode.SEWAGE_OVERFLOW: Priority.P4,
    BlockerCode.HEAVY_WATER_FLOW_SPREAD_RISK: Priority.P4,
    BlockerCode.TOTAL_UNPLANNED_UTILITY_LOSS: Priority.P4,
    BlockerCode.SOLE_TOILET_UNUSABLE: Priority.P4,
}

#: The priority that means "a human handles this outside the dispatch queue".
EMERGENCY_PRIORITY = Priority.P5

#: A case holds at most five apartments, so the confirmed count saturates the
#: 0-4 scale exactly at the case limit.
MAX_AFFECTED_UNITS = 5


class RiskScoringError(ValueError):
    """A score the rubric cannot accept. Never a clamped value."""


@dataclass(frozen=True)
class RiskCriterionScores:
    """The five integers the Agent is allowed to produce.

    Out-of-range values raise rather than clamp. A model that answers `7` for a
    0-4 question has not produced a slightly-too-high score; it has produced a
    payload that does not mean what the rubric thinks it means, and clamping it
    to 4 would silently promote the ticket on the strength of a bug.

    `bool` is rejected explicitly because `True == 1` in Python, and a boolean
    arriving where a 0-4 judgement belongs is a serialization mistake worth
    seeing.
    """

    human_safety: int
    property_spread: int
    essential_function: int
    affected_scope: int
    deterioration_speed: int

    def __post_init__(self) -> None:
        for name in CRITERION_NAMES:
            _validate_criterion(name, getattr(self, name))

    def as_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in CRITERION_NAMES}

    def with_scope(self, affected_scope: int) -> RiskCriterionScores:
        """A copy whose scope is the backend-confirmed one."""
        return RiskCriterionScores(
            human_safety=self.human_safety,
            property_spread=self.property_spread,
            essential_function=self.essential_function,
            affected_scope=affected_scope,
            deterioration_speed=self.deterioration_speed,
        )


@dataclass(frozen=True)
class RiskScoringResult:
    """One scoring pass, with every intermediate a reviewer would ask about.

    `score_priority` and `final_priority` are kept apart on purpose: "the rubric
    said P2 and a blocker made it P5" is a different story from "the rubric said
    P5", and only the first one is worth a coordinator's attention.
    """

    risk_score: Decimal
    score_priority: Priority
    final_priority: Priority
    criteria: RiskCriterionScores
    ai_scope_score: int
    backend_scope_score: int | None
    effective_scope_score: int
    blocker_codes: tuple[BlockerCode, ...] = ()
    blocker_floor: Priority | None = None
    contributions: dict[str, Decimal] = field(default_factory=dict)
    rubric_version: str = RUBRIC_VERSION

    @property
    def blocker_raised_priority(self) -> bool:
        """True when the floor, not the score, decided the outcome."""
        return self.final_priority is not self.score_priority


def _validate_criterion(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RiskScoringError(f"{name} must be an int in 0-4, got {value!r}.")
    if not MIN_CRITERION_SCORE <= value <= MAX_CRITERION_SCORE:
        raise RiskScoringError(
            f"{name}={value} is outside the {MIN_CRITERION_SCORE}-{MAX_CRITERION_SCORE} rubric scale."
        )
    return value


def priority_from_score(risk_score: Decimal) -> Priority:
    """Which band a score falls in. Bands are left-closed, right-open."""
    if not isinstance(risk_score, Decimal):
        raise RiskScoringError("risk_score must be a Decimal; float arithmetic moves thresholds.")
    if risk_score < MIN_RISK_SCORE or risk_score > MAX_RISK_SCORE:
        raise RiskScoringError(f"risk_score={risk_score} is outside 0-100.")
    for lower, priority in SCORE_THRESHOLDS:
        if risk_score >= lower:
            return priority
    # Unreachable: the last threshold is 0 and the range check above rejects
    # anything below it.
    raise RiskScoringError(f"No priority band contains {risk_score}.")


def blocker_floor(blocker_codes: Iterable[BlockerCode | str]) -> Priority | None:
    """The highest floor the given blockers impose, or None for no blockers.

    Unknown codes raise. A blocker the backend does not recognise is a contract
    mismatch between the prompt and this table, and treating it as "no floor"
    would drop an emergency on the floor quietly.
    """
    highest: Priority | None = None
    for raw in blocker_codes:
        try:
            code = BlockerCode(raw)
        except ValueError as exc:
            raise RiskScoringError(f"Unknown blocker code {raw!r}.") from exc
        floor = BLOCKER_FLOORS[code]
        if highest is None or PRIORITY_RANK[floor] > PRIORITY_RANK[highest]:
            highest = floor
    return highest


def backend_scope_score(confirmed_affected_unit_count: int) -> int:
    """Turn a confirmed apartment count into the 0-4 scope criterion.

    `clamp(n - 1, 0, 4)`: one apartment is the baseline (0), and the scale tops
    out at five, which is also the maximum number of members a case may hold.
    Counting is by distinct apartment, so ten reports from one household stay a
    single unit -- that rule lives in whoever computes `density_value`, not
    here.
    """
    if isinstance(confirmed_affected_unit_count, bool) or not isinstance(confirmed_affected_unit_count, int):
        raise RiskScoringError(
            f"confirmed_affected_unit_count must be an int, got {confirmed_affected_unit_count!r}."
        )
    if confirmed_affected_unit_count < 1:
        raise RiskScoringError("confirmed_affected_unit_count starts at 1: a ticket always affects its own unit.")
    return min(max(confirmed_affected_unit_count - 1, MIN_CRITERION_SCORE), MAX_CRITERION_SCORE)


def effective_scope_score(ai_scope_score: int, backend_scope_score: int | None) -> int:
    """Counted evidence beats an estimate.

    The Agent sees one report and guesses how far the problem reaches. The
    backend can count distinct apartments in a closed case. When the count
    exists it wins -- in *both* directions: a resident's "the whole floor is
    out" is not scope, and a case that really did collect five apartments raises
    the scope of every member even though none of them said so.
    """
    _validate_criterion("ai_scope_score", ai_scope_score)
    if backend_scope_score is None:
        return ai_scope_score
    return _validate_criterion("backend_scope_score", backend_scope_score)


def calculate_risk_score(
    criteria: RiskCriterionScores,
    *,
    blocker_codes: Iterable[BlockerCode | str] = (),
    backend_scope_score: int | None = None,
) -> RiskScoringResult:
    """The one entry point. Five criteria in, one priority out.

    `criteria.affected_scope` is what the Agent estimated;
    `backend_scope_score` is what the case actually counted. The formula uses
    whichever `effective_scope_score` selects, and the result carries all three
    so a reviewer can see an estimate that was overruled.
    """
    codes = tuple(BlockerCode(code) for code in blocker_codes)
    if len(set(codes)) != len(codes):
        raise RiskScoringError("blocker_codes must not repeat a code.")

    ai_scope = criteria.affected_scope
    effective_scope = effective_scope_score(ai_scope, backend_scope_score)
    scored = criteria.with_scope(effective_scope)

    contributions = {
        name: (Decimal(getattr(scored, name)) / Decimal(4)) * weight for name, weight in CRITERION_WEIGHTS.items()
    }
    # One quantization, after the sum. Every term is an exact multiple of 1.25,
    # so this cannot move a score across a threshold -- it only fixes the scale
    # the number is stored and compared at.
    risk_score = sum(contributions.values(), Decimal(0)).quantize(SCORE_QUANTUM)

    score_priority = priority_from_score(risk_score)
    floor = blocker_floor(codes)
    final_priority = score_priority
    if floor is not None and PRIORITY_RANK[floor] > PRIORITY_RANK[score_priority]:
        final_priority = floor

    return RiskScoringResult(
        risk_score=risk_score,
        score_priority=score_priority,
        final_priority=final_priority,
        criteria=scored,
        ai_scope_score=ai_scope,
        backend_scope_score=backend_scope_score,
        effective_scope_score=effective_scope,
        blocker_codes=codes,
        blocker_floor=floor,
        contributions=contributions,
    )


def is_emergency(priority: Priority | None) -> bool:
    """The one place "is this the manual-only priority" is decided."""
    return priority is EMERGENCY_PRIORITY


__all__ = [
    "BLOCKER_FLOORS",
    "CRITERION_NAMES",
    "CRITERION_WEIGHTS",
    "EMERGENCY_PRIORITY",
    "MAX_AFFECTED_UNITS",
    "MAX_CRITERION_SCORE",
    "MIN_CRITERION_SCORE",
    "PRIORITY_RANK",
    "RUBRIC_VERSION",
    "SCORE_THRESHOLDS",
    "BlockerCode",
    "RiskCriterionScores",
    "RiskScoringError",
    "RiskScoringResult",
    "backend_scope_score",
    "blocker_floor",
    "calculate_risk_score",
    "effective_scope_score",
    "is_emergency",
    "priority_from_score",
]
