"""`RuleBasedAssignmentService` — the default assignment decision engine.

Same two entry points and the same two outcome objects as
`AssignmentAgentService`, so `DirectAssignmentService` and
`AssignmentProposalService` do not care which one they were handed. What
changes is everything underneath: no HTTP call, no 300-second deadline, no
primary/fallback pair, no envelope to validate, and therefore no
`MANUAL_REQUIRED` caused by a model that timed out or answered off-contract.

`failures` is always empty and `fallback_used` is always `False`. Both fields
stay on the outcome because the callers persist them and the AI engine still
fills them in; a rule run simply has nothing to put there. The failure modes
that remain are real business answers:

* the work item had no candidates at all — Backend already short-circuits that
  before either engine is called (§5.2 item 1);
* every candidate is over a configured cap — `NO_SUITABLE_CANDIDATE`, which
  §5.2 item 7 sends straight to the manual queue without a second window.

Latency is the reason this exists. The LLM path spent up to 300 seconds per
request, twice if the fallback engaged, on a decision whose inputs are four
integers and a timestamp per candidate.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from src.assignment_agent.schemas import (
    AssignmentProposalBatchRequestV4,
    AssignmentProposalBatchResultV4,
    DirectAssignmentBatchRequestV4,
    DirectAssignmentBatchResultV4,
)
from src.assignment_agent.service import DirectAssignmentOutcome, ProposalAssignmentOutcome
from src.assignment_rules.config import AssignmentRuleConfig, get_rule_config
from src.assignment_rules.engine import decide_items
from src.models.enums import Priority
from src.observability import annotate, root_span

logger = logging.getLogger(__name__)


class RuleBasedAssignmentService:
    """Deterministic technician selection over a Backend-built snapshot.

    The rule set and the clock are both injectable, which is what makes a
    decision reproducible in a test: same candidates, same config, same answer,
    every time.
    """

    def __init__(
        self,
        config: AssignmentRuleConfig | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config or get_rule_config()
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_settings(cls, *_args, clock: Callable[[], datetime] | None = None, **_kwargs) -> RuleBasedAssignmentService:
        """Mirror of `AssignmentAgentService.from_settings`.

        Accepts and ignores `settings`/`strict`: there is no model pair to
        validate, and refusing to start over a missing model name would be a
        strange way for the engine that removed the model to behave.
        """
        return cls(clock=clock)

    @property
    def engine_version(self) -> str:
        return self.config.rule_version

    @property
    def fallback_version(self) -> str | None:
        """No second engine. A rule run either answers or says nobody fits."""
        return None

    def _now(self) -> datetime:
        return self._clock()

    # ------------------------------------------------------------------
    # Public entry points.
    # ------------------------------------------------------------------

    def decide_direct(self, request: DirectAssignmentBatchRequestV4) -> DirectAssignmentOutcome:
        with root_span(
            "assignment.direct",
            request_id=str(request.request_id),
            work_item_count=len(request.work_items),
            decision_engine=self.engine_version,
        ) as active:
            decisions = decide_items(list(request.work_items), self.config, decided_at=self._now())
            result = DirectAssignmentBatchResultV4(
                request_id=request.request_id,
                decisions=decisions,
                completed_at=self._now(),
            )
            annotate(active, output=self._trace_output(decisions))
            return DirectAssignmentOutcome(result=result, failures=[], fallback_used=False)

    def decide_proposal(self, request: AssignmentProposalBatchRequestV4) -> ProposalAssignmentOutcome:
        with root_span(
            "assignment.proposal",
            batch_decision_id=str(request.batch_decision_id),
            proposal_batch_id=str(request.proposal_batch_id),
            work_item_count=len(request.work_items),
            decision_engine=self.engine_version,
        ) as active:
            decisions = decide_items(list(request.work_items), self.config, decided_at=self._now())
            result = AssignmentProposalBatchResultV4(
                batch_decision_id=request.batch_decision_id,
                proposal_batch_id=request.proposal_batch_id,
                decisions=decisions,
                completed_at=self._now(),
            )
            annotate(active, output=self._trace_output(decisions))
            return ProposalAssignmentOutcome(result=result, failures=[], fallback_used=False)

    def _trace_output(self, decisions: list) -> dict[str, object]:
        """Counts only — never a reason string or a technician name (§8, §9)."""
        selected = [item for item in decisions if item.selected_technician_id is not None]
        return {
            "decision_count": len(decisions),
            "selected_count": len(selected),
            "no_candidate_count": len(decisions) - len(selected),
            "distinct_technicians": len({item.selected_technician_id for item in selected}),
            "rule_version": self.engine_version,
        }


def priority_rank(priority: Priority) -> int:
    """P3 first. Re-exported so callers can order work items the same way."""
    from src.assignment_rules.engine import PRIORITY_RANK

    return PRIORITY_RANK.get(priority, len(PRIORITY_RANK))


__all__ = ["RuleBasedAssignmentService", "priority_rank"]
