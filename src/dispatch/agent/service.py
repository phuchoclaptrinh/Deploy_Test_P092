"""The at-risk agent (§7).

Called for the AT_RISK subset of a micro-batch and for nothing else. A SAFE
ticket never reaches this module -- not as an optimisation, but because there is
no judgement to make: the scheduler found a placement that breaks no
commitment, and asking a model to second-guess it would add latency and a
failure mode in exchange for nothing.

Three properties this module is responsible for:

* **The agent cannot widen its own candidate set.** The eligible ids come from
  `src.dispatch.eligibility`, travel in the request, and every returned pick is
  checked back against them in `_validate`. An answer naming anyone else is
  discarded, not clamped and not looked up -- §3 says the agent may never
  select a technician who fails a hard constraint, and the only way to
  guarantee that is to refuse the answer rather than repair it.

* **One call per micro-batch.** §7 asks the agent to weigh the trade-off across
  the batch, and §8 caps the cost. Both point at the same design: the whole
  at-risk subset goes in one request, and `_SEMAPHORE` bounds how many such
  requests exist at once so a burst cannot open more concurrent work than the
  §8 session budget allows.

* **A failure is a fact, not an outcome.** Timeout, transport error and an
  answer nobody can use all raise `AtRiskDecisionError`. Deciding what to do
  next is the dispatcher's job (`src.dispatch.service`), which falls back to
  the scheduler's least-negative-slack candidate and tells Building Management
  it did so. This module never assigns anything itself.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from uuid import UUID

from src.config import Settings, get_settings
from src.dispatch.agent.schemas import (
    AtRiskBatchDecision,
    AtRiskBatchRequest,
    AtRiskDecisionError,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the dispatch adviser for a residential building's maintenance team.

Every ticket in this batch is AT RISK: the scheduler has already established
that there is no way to assign it without pushing some already-promised job
past its planned finish time. You are not being asked whether to accept that
cost. You are being asked which technician should absorb it.

Rules you must follow exactly:

1. For each ticket, choose a technician ONLY from that ticket's
   eligible_technician_ids. Every other technician has already failed a hard
   constraint (skill, availability, working shift). Never name an id that is
   not in that ticket's list.
2. You may leave a ticket out of your answer if you genuinely cannot choose.
   Do not invent an id to avoid an empty slot.
3. Weigh, for each candidate: how negative their projected worst slack is, how
   much work they are already holding, their planned schedule, and their
   history - completion counts, P50/P80 handling time in this ticket's
   category, start performance (how much assigned work they actually start,
   how often they start by the planned time, and how long they take to get
   going), and how often work has been rejected, reassigned away, or reported
   unhandleable.
4. Consider the batch as a whole. Two tickets competing for the same
   technician's only remaining slot is the trade-off you exist to resolve;
   spreading them is usually better than stacking them.
5. Prefer the least total damage: the smallest breach of existing commitments,
   on the technician most likely to actually absorb it.

For each ticket you answer, give a short reason in Vietnamese, at most two
sentences, naming the operational fact that decided it. Do not mention
residents, addresses, or ticket contents - you have not been given any, and
inventing them would make the audit record false.
"""

USER_PROMPT = """\
Current time (UTC): {current_time}

At-risk tickets in this batch:
{tickets}

Candidate technicians (aggregated operational data only):
{candidates}

Return one assignment per ticket you can decide, using the ticket_ref values
exactly as written above.
"""


@dataclass(frozen=True)
class AtRiskOutcome:
    """One validated pick, plus what it cost to get it."""

    technician_id: UUID
    reason: str
    model_name: str | None
    latency_ms: int


class _BoundedRunner:
    """Runs the model call off-thread so a hung provider cannot hang a pass.

    A `ThreadPoolExecutor` future with a timeout rather than a signal alarm:
    the worker is not guaranteed to be on the main thread, and `signal.alarm`
    is both main-thread-only and absent on Windows. The abandoned thread is
    left to finish on its own -- there is no safe way to kill it -- which is
    exactly why `_SEMAPHORE` caps how many can be in flight.
    """

    def __init__(self, max_workers: int) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="at-risk-agent")
        self._semaphore = threading.Semaphore(max_workers)

    def run(self, work, timeout: float):
        if not self._semaphore.acquire(timeout=timeout):
            raise AtRiskDecisionError("AGENT_BUSY", "at-risk agent concurrency limit reached")
        future = self._pool.submit(work)
        try:
            return future.result(timeout=timeout)
        except FutureTimeout as exc:
            future.cancel()
            raise AtRiskDecisionError("AGENT_TIMEOUT", f"no answer within {timeout}s") from exc
        finally:
            self._semaphore.release()


_RUNNER: _BoundedRunner | None = None
_RUNNER_LOCK = threading.Lock()


def _runner(settings: Settings) -> _BoundedRunner:
    global _RUNNER
    with _RUNNER_LOCK:
        if _RUNNER is None:
            _RUNNER = _BoundedRunner(settings.at_risk_agent_max_concurrency)
        return _RUNNER


class AtRiskAgent:
    """One model call over one micro-batch of at-risk tickets."""

    def __init__(self, llm=None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._llm = llm

    @property
    def model_name(self) -> str:
        return self.settings.at_risk_agent_model or self.settings.model_name

    def _structured_llm(self):
        if self._llm is not None:
            return self._llm
        # Imported lazily so that a deployment running with the agent disabled,
        # and every unit test that injects a stub, never loads the provider SDK.
        from langchain_openai import ChatOpenAI

        from src.services.llm import openai_no_thinking_kwargs

        model = ChatOpenAI(
            model=self.model_name,
            api_key=self.settings.openai_api_key,
            temperature=0,
            timeout=self.settings.at_risk_agent_timeout_seconds,
            **openai_no_thinking_kwargs(self.model_name),
        )
        return model.with_structured_output(AtRiskBatchDecision)

    def decide(self, request: AtRiskBatchRequest) -> dict[str, AtRiskOutcome]:
        """Pick a technician for as many of the batch's tickets as it can.

        Returns a map keyed by `ticket_ref`, holding only picks that survived
        validation. A ticket absent from the result has no agent decision and
        the dispatcher falls back for it -- which is the same handling a total
        failure gets, one ticket at a time.

        Raises `AtRiskDecisionError` when there is no answer at all.
        """
        if not request.tickets:
            return {}
        if not self.settings.at_risk_agent_enabled:
            raise AtRiskDecisionError("AGENT_DISABLED", "at-risk agent is switched off")
        if not self.model_name:
            raise AtRiskDecisionError("AGENT_UNCONFIGURED", "no at-risk agent model configured")

        import time

        started = time.monotonic()
        llm = self._structured_llm()
        messages = [
            ("system", SYSTEM_PROMPT),
            (
                "user",
                USER_PROMPT.format(
                    current_time=request.current_time.isoformat(),
                    tickets=_render_tickets(request),
                    candidates=_render_candidates(request),
                ),
            ),
        ]

        def call() -> AtRiskBatchDecision:
            return llm.invoke(messages)

        try:
            answer = _runner(self.settings).run(call, self.settings.at_risk_agent_timeout_seconds)
        except AtRiskDecisionError:
            raise
        except Exception as exc:  # noqa: BLE001 - every provider failure is one fact here
            raise AtRiskDecisionError("AGENT_ERROR", f"{type(exc).__name__}: {exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        return self._validate(request, answer, latency_ms)

    def _validate(
        self,
        request: AtRiskBatchRequest,
        answer: AtRiskBatchDecision,
        latency_ms: int,
    ) -> dict[str, AtRiskOutcome]:
        """Keep only picks the backend already authorised.

        Silently dropping a bad pick is the correct behaviour, not a lenient
        one: the alternative outcomes are assigning to an ineligible technician
        (forbidden by §3) or failing tickets the agent answered correctly
        because it got one wrong.
        """
        eligible = {ticket.ticket_ref: set(ticket.eligible_technician_ids) for ticket in request.tickets}
        outcomes: dict[str, AtRiskOutcome] = {}
        for pick in answer.assignments:
            allowed = eligible.get(pick.ticket_ref)
            if allowed is None:
                logger.warning("At-risk agent answered for unknown ticket_ref %s.", pick.ticket_ref)
                continue
            if pick.technician_id not in allowed:
                logger.warning(
                    "At-risk agent picked technician %s outside the eligible set for %s; discarding.",
                    pick.technician_id,
                    pick.ticket_ref,
                )
                continue
            if pick.ticket_ref in outcomes:
                logger.warning("At-risk agent answered twice for %s; keeping the first.", pick.ticket_ref)
                continue
            outcomes[pick.ticket_ref] = AtRiskOutcome(
                technician_id=pick.technician_id,
                reason=(pick.reason or "").strip()[:500],
                model_name=self.model_name,
                latency_ms=latency_ms,
            )
        if not outcomes:
            raise AtRiskDecisionError("AGENT_NO_VALID_PICK", "no answer survived candidate validation")
        return outcomes


def _render_tickets(request: AtRiskBatchRequest) -> str:
    lines = []
    for ticket in request.tickets:
        ids = ", ".join(str(tid) for tid in ticket.eligible_technician_ids)
        lines.append(
            f"- ticket_ref={ticket.ticket_ref} category={ticket.category_code} "
            f"priority={ticket.priority} score={ticket.score} "
            f"submitted_at={ticket.submitted_at.isoformat()} "
            f"p80_working_seconds={ticket.p80_working_seconds} "
            f"eligible_technician_ids=[{ids}]"
        )
    return "\n".join(lines)


def _render_candidates(request: AtRiskBatchRequest) -> str:
    lines = []
    for candidate in request.candidates:
        lines.append(
            f"- technician_id={candidate.technician_id} "
            f"active={candidate.active_assignment_count} in_progress={candidate.in_progress_count} "
            f"projected_worst_slack_seconds={candidate.projected_worst_slack_seconds} "
            f"projected_start_at={candidate.projected_start_at.isoformat() if candidate.projected_start_at else 'n/a'}"
        )
        for slot in candidate.planned_schedule:
            lines.append(
                f"    schedule[{slot.order}] {slot.category_code or 'n/a'} "
                f"{slot.planned_start_at.isoformat()} -> {slot.planned_finish_at.isoformat()} "
                f"slack={slot.slack_seconds}"
            )
        for window in candidate.history:
            categories = " ".join(
                f"{stat.category_code}(n={stat.completed_count},"
                f"p50={stat.p50_working_seconds},p80={stat.p80_working_seconds})"
                for stat in window.by_category
            )
            lines.append(
                f"    {window.window_days}d: completed={window.completed_count} "
                f"assigned={window.assigned_count} started={window.started_count} "
                f"started_on_time={window.started_on_time_count} "
                f"median_assign_to_start_s={window.median_assignment_to_start_seconds} "
                f"rejected={window.rejected_count} "
                f"unable={window.unable_to_handle_count} reassigned_away={window.reassigned_away_count} "
                f"{categories}"
            )
    return "\n".join(lines)


__all__ = ["SYSTEM_PROMPT", "AtRiskAgent", "AtRiskOutcome"]
