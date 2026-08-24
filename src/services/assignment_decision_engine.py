"""Which engine picks the technician (`ASSIGNMENT_DECISION_ENGINE`).

Two implementations answer the same question and return the same
`DirectAssignmentOutcome` / `ProposalAssignmentOutcome`:

* `RULE` — `RuleBasedAssignmentService`, the default. A documented ordering
  over the candidate snapshot: no network call, no deadline, no fallback, and
  no `MANUAL_REQUIRED` that means "the model timed out".
* `AI` — `AssignmentAgentService`, the original contract §4–§5 path, primary
  model then partial fallback.

The switch exists because a ranking rule is a business judgement, not a proof.
If `RULE_ENGINE_V1` turns out to spread work in a way the building manager
disagrees with, `ASSIGNMENT_DECISION_ENGINE=AI` restores the previous behaviour
with a restart instead of a revert — and the AI path is unchanged underneath,
still validated by the same failover check it always had.

`build_decision_engine` is the only place that reads the setting, so the API,
the worker and both services cannot disagree about which engine is live.
"""

from __future__ import annotations

import logging
from typing import Protocol

from src.assignment_agent.schemas import (
    AssignmentProposalBatchRequestV4,
    DirectAssignmentBatchRequestV4,
)
from src.assignment_agent.service import DirectAssignmentOutcome, ProposalAssignmentOutcome
from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

RULE_ENGINE = "RULE"
AI_ENGINE = "AI"


class AssignmentDecisionEngine(Protocol):
    """What the DIRECT and PROPOSAL services need from an engine."""

    @property
    def engine_version(self) -> str: ...

    @property
    def fallback_version(self) -> str | None: ...

    def decide_direct(self, request: DirectAssignmentBatchRequestV4) -> DirectAssignmentOutcome: ...

    def decide_proposal(self, request: AssignmentProposalBatchRequestV4) -> ProposalAssignmentOutcome: ...


def build_decision_engine(settings: Settings | None = None) -> AssignmentDecisionEngine:
    """Construct the configured engine.

    Imports are local so that a deployment running on rules never loads the
    model client, and so `src.config` keeps not depending on either package.
    """
    settings = settings or get_settings()
    if settings.assignment_decision_engine == AI_ENGINE:
        from src.assignment_agent.service import AssignmentAgentService

        logger.info("Assignment decisions run on the AI engine (contract §4-§5).")
        # §5.2: production still refuses a missing or self-identical fallback.
        return AssignmentAgentService.from_settings(strict=settings.require_assignment_failover)

    from src.assignment_rules.service import RuleBasedAssignmentService

    engine = RuleBasedAssignmentService()
    logger.info("Assignment decisions run on %s.", engine.engine_version)
    return engine


__all__ = [
    "AI_ENGINE",
    "RULE_ENGINE",
    "AssignmentDecisionEngine",
    "build_decision_engine",
]
