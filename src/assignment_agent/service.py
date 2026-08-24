"""Assignment Agent v4 orchestration (contract §4–§5).

One in-process entry point per mode:

* `decide_direct` — units that are ready to be assigned now: first assignment,
  reassignment after a rejection, reassignment after an acceptance timeout. A
  request may carry several work items; each decision is independent and is
  applied immediately, with no coordinator step.
* `decide_proposal` — one preview batch, built only when a coordinator turns
  auto-assignment on while a queue exists. Never used for reassignment, and
  nothing is written until a human confirms.

Between the model and Backend sits the partial-fallback rule that makes the
whole design work (§5.2 item 3–4):

1. The primary model answers the whole request once, with a hard deadline.
2. The envelope is checked for being an object with a `decisions` list, and for
   only carrying decisions this request asked about; then every decision is
   validated on its own. Valid ones are kept. A reply that answers work items
   outside the request is discarded whole — see `validator.validate_envelope`.
3. Only the missing or contract-breaking ones go to the fallback, carrying the
   same `request_id`/`batch_decision_id`/`decision_id` and, as context, the
   decisions already kept — so the fallback continues from the real projected
   load rather than a stale one, and cannot revise a decision that was fine.
4. If the primary envelope failed outright, the fallback receives everything.
5. Whatever still fails is simply absent from `decisions[]`. Backend turns that
   into MANUAL_REQUIRED (DIRECT) or an EMPTY row (PROPOSAL).

`NO_SUITABLE_CANDIDATE` is a business answer and never reaches the fallback.

What this module deliberately does not do, because it is Backend work: create
assignments, write anything to the database, keep a durable job/queue, persist
deadlines, schedule retries, expire proposal batches, confirm a proposal,
toggle the global auto-assignment switch, or resolve the manual-wins race. It
produces decisions; Backend re-checks integrity and applies them.

Backend must also drop any work item whose candidate list came out empty before
building a request: `candidates` has `min_length=1` precisely so a no-candidate
item cannot reach a model call, which §5.2 item 1 forbids.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.assignment_agent.config import AssignmentAgentSettings
from src.assignment_agent.model_client import (
    AssignmentModelClient,
    AssignmentModelEnvelope,
    AssignmentModelError,
    build_model_clients,
    parse_envelope,
)
from src.assignment_agent.prompts import (
    DIRECT_ASSIGNMENT_SYSTEM_PROMPT_V4,
    PROPOSAL_ASSIGNMENT_SYSTEM_PROMPT_V4,
    render_direct_request,
    render_proposal_request,
    render_retry_context,
)
from src.assignment_agent.schemas import (
    AssignmentDecisionV4,
    AssignmentProposalBatchRequestV4,
    AssignmentProposalBatchResultV4,
    DirectAssignmentBatchRequestV4,
    DirectAssignmentBatchResultV4,
)
from src.assignment_agent.validator import (
    DecisionFailure,
    ValidationOutcome,
    WorkItemRequest,
    validate_envelope,
)
from src.observability import annotate, root_span

logger = logging.getLogger(__name__)

PRIMARY_ENVELOPE_ERROR = "PRIMARY_ENVELOPE_ERROR"
FALLBACK_ENVELOPE_ERROR = "FALLBACK_ENVELOPE_ERROR"


@dataclass
class _Orchestration:
    decisions: list[AssignmentDecisionV4] = field(default_factory=list)
    failures: list[DecisionFailure] = field(default_factory=list)
    fallback_used: bool = False


def _trace_output(run: _Orchestration) -> dict[str, object]:
    """Counts and error codes, never a reason string or a technician name."""
    return {
        "decision_count": len(run.decisions),
        "failure_count": len(run.failures),
        "fallback_used": run.fallback_used,
        "error_codes": sorted({failure.error_code for failure in run.failures}),
    }


@dataclass
class DirectAssignmentOutcome:
    """`result` is the contract payload for Backend.

    `failures` is operational detail alongside it, not part of the contract:
    the work items with no decision, and why. Backend needs it to pause exactly
    those tickets and tell the coordinator what happened.
    """

    result: DirectAssignmentBatchResultV4
    failures: list[DecisionFailure]
    fallback_used: bool


@dataclass
class ProposalAssignmentOutcome:
    result: AssignmentProposalBatchResultV4
    failures: list[DecisionFailure]
    fallback_used: bool


class AssignmentAgentService:
    """Runs one assignment request against the primary model, then the fallback.

    Independent of the analysis agent in every way: no `AIAnalysisSession`, no
    tools, no graph, no shared budget. Both model clients and the clock are
    injectable, so the orchestration is testable without a real model.
    """

    def __init__(
        self,
        primary: AssignmentModelClient,
        fallback: AssignmentModelClient | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_settings(
        cls,
        settings: AssignmentAgentSettings | None = None,
        *,
        strict: bool = True,
        clock: Callable[[], datetime] | None = None,
    ) -> AssignmentAgentService:
        """Build from Agent-side configuration.

        `strict=True` is the production path and refuses to start unless a
        primary and a *different* fallback are configured, each with a deadline
        of at most 300 seconds and no implicit retry.
        """
        # `settings` is passed straight through rather than resolved here:
        # `build_model_clients` is the single place that decides what an
        # unusable pair costs, and two resolution points would be two rules.
        primary, fallback = build_model_clients(settings, strict=strict)
        return cls(primary, fallback, clock=clock)

    @property
    def engine_version(self) -> str:
        """What produced a decision, in the vocabulary both engines share.

        `RuleBasedAssignmentService` exposes the same pair, so the services
        that persist `primary_model`/`fallback_model` never have to know which
        engine they were handed.
        """
        return self.primary.model_version

    @property
    def fallback_version(self) -> str | None:
        return self.fallback.model_version if self.fallback else None

    def _now(self) -> datetime:
        return self._clock()

    # ------------------------------------------------------------------
    # Public entry points.
    # ------------------------------------------------------------------

    def decide_direct(self, request: DirectAssignmentBatchRequestV4) -> DirectAssignmentOutcome:
        # One trace per assignment request. The primary call and, when it
        # happens, the partial fallback both nest underneath — which is the
        # only way to see at a glance that a batch mixed two models.
        with root_span(
            "assignment.direct",
            request_id=str(request.request_id),
            work_item_count=len(request.work_items),
            primary_model=self.primary.model_version,
            fallback_model=self.fallback.model_version if self.fallback else None,
        ) as active:
            run = self._orchestrate(
                items=request.work_items,
                system_prompt=DIRECT_ASSIGNMENT_SYSTEM_PROMPT_V4,
                render=lambda items: render_direct_request(request.model_copy(update={"work_items": list(items)})),
            )
            result = DirectAssignmentBatchResultV4(
                # §5.2 item 3: the request id is carried through the fallback
                # unchanged, so the decisions stay attributable to one model call.
                request_id=request.request_id,
                decisions=run.decisions,
                completed_at=self._now(),
            )
            annotate(active, output=_trace_output(run))
            return DirectAssignmentOutcome(result=result, failures=run.failures, fallback_used=run.fallback_used)

    def decide_proposal(self, request: AssignmentProposalBatchRequestV4) -> ProposalAssignmentOutcome:
        with root_span(
            "assignment.proposal",
            batch_decision_id=str(request.batch_decision_id),
            proposal_batch_id=str(request.proposal_batch_id),
            work_item_count=len(request.work_items),
            primary_model=self.primary.model_version,
            fallback_model=self.fallback.model_version if self.fallback else None,
        ) as active:
            run = self._orchestrate(
                items=request.work_items,
                system_prompt=PROPOSAL_ASSIGNMENT_SYSTEM_PROMPT_V4,
                render=lambda items: render_proposal_request(request.model_copy(update={"work_items": list(items)})),
            )
            result = AssignmentProposalBatchResultV4(
                batch_decision_id=request.batch_decision_id,
                proposal_batch_id=request.proposal_batch_id,
                decisions=run.decisions,
                completed_at=self._now(),
            )
            annotate(active, output=_trace_output(run))
            return ProposalAssignmentOutcome(result=result, failures=run.failures, fallback_used=run.fallback_used)

    # ------------------------------------------------------------------
    # Primary, then partial fallback.
    # ------------------------------------------------------------------

    def _orchestrate(
        self,
        *,
        items: Sequence[WorkItemRequest],
        system_prompt: str,
        render,
    ) -> _Orchestration:
        primary_outcome = self._call_model(
            self.primary,
            items=items,
            system_prompt=system_prompt,
            user_prompt=render(items),
            envelope_error_code=PRIMARY_ENVELOPE_ERROR,
        )

        if not primary_outcome.envelope_trusted:
            # A reply carrying decisions for work items nobody asked about is
            # not a partially-right batch: there is no way to tell which of its
            # decisions belong to this request. Nothing from it is kept.
            logger.warning("Primary envelope rejected wholesale: %s", primary_outcome.envelope_failure)

        failed_ids = primary_outcome.failed_decision_ids
        if not failed_ids:
            return _Orchestration(decisions=self._ordered(items, primary_outcome.decisions))

        retry_items = [item for item in items if item.decision_id in failed_ids]

        if self.fallback is None:
            logger.warning(
                "No fallback model configured; %d assignment decision(s) go to the manual path: %s",
                len(retry_items),
                [(str(f.decision_id), f.error_code) for f in primary_outcome.failures],
            )
            # The original failures are passed through unchanged: their error
            # codes are what tells the coordinator why an item has no decision.
            return _Orchestration(
                decisions=self._ordered(items, primary_outcome.decisions),
                failures=list(primary_outcome.failures),
            )

        logger.info(
            "Assignment fallback engaged for %d of %d work item(s); %d primary decision(s) kept.",
            len(retry_items),
            len(items),
            len(primary_outcome.decisions),
        )
        fallback_outcome = self._call_model(
            self.fallback,
            items=retry_items,
            system_prompt=system_prompt,
            user_prompt=render_retry_context(self._retained_context(items, primary_outcome.decisions)) + render(retry_items),
            envelope_error_code=FALLBACK_ENVELOPE_ERROR,
        )

        # The fallback was only asked about `retry_items`, and `validate_envelope`
        # only accepts decision ids from that subset — so a fallback reply can
        # add to the kept primary decisions but never overwrite one. A batch may
        # legitimately end up mixing both models; each decision already carries
        # the model_version that produced it (§4.4).
        merged = {**primary_outcome.decisions, **fallback_outcome.decisions}
        return _Orchestration(
            decisions=self._ordered(items, merged),
            failures=fallback_outcome.failures,
            fallback_used=True,
        )

    def _call_model(
        self,
        client: AssignmentModelClient,
        *,
        items: Sequence[WorkItemRequest],
        system_prompt: str,
        user_prompt: str,
        envelope_error_code: str,
    ) -> ValidationOutcome:
        try:
            reply = client.decide(system_prompt=system_prompt, user_prompt=user_prompt)
            envelope = parse_envelope(reply) if not isinstance(reply, AssignmentModelEnvelope) else reply
        except AssignmentModelError as exc:
            logger.warning("Assignment model %s failed on %d item(s): %s", client.model_version, len(items), exc)
            # A dead envelope fails every item it was asked about, which is what
            # sends the whole request to the fallback.
            return ValidationOutcome(
                failures=[
                    DecisionFailure(item.decision_id, item.work_item.work_item_id, envelope_error_code, str(exc))
                    for item in items
                ]
            )
        return validate_envelope(
            envelope,
            items,
            model_version=client.model_version,
            decided_at=self._now(),
        )

    @staticmethod
    def _ordered(
        items: Sequence[WorkItemRequest],
        decisions: dict,
    ) -> list[AssignmentDecisionV4]:
        """Emit decisions in request order; work items with none are absent."""
        return [decisions[item.decision_id] for item in items if item.decision_id in decisions]

    @staticmethod
    def _retained_context(items: Sequence[WorkItemRequest], decisions: dict) -> list[dict[str, object]]:
        """Projected load the fallback must continue from.

        A kept primary decision still consumes that technician capacity, so the
        fallback has to see it or it would re-plan against the original,
        already-outdated `active_assignment_count` (§4.3a).
        """
        by_decision_id = {item.decision_id: item for item in items}
        context: list[dict[str, object]] = []
        for decision_id, decision in decisions.items():
            item = by_decision_id.get(decision_id)
            if item is None:
                continue
            context.append(
                {
                    "work_item_id": str(decision.work_item_id),
                    "selected_technician_id": str(decision.selected_technician_id) if decision.selected_technician_id else None,
                    "ticket_count": item.work_item.ticket_count,
                }
            )
        return context
