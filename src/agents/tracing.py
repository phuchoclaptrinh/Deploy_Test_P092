"""Tracing wrappers around the graph's nodes, routers and model client.

Kept apart from `src.agents.trace` (which only knows how to write a line) and
from the pipeline itself: every hook here is a decorator applied in
`build_graph`, so `nodes.py` and `llm_client.py` carry no logging code and stay
readable as pure business logic. Turning tracing off removes the wrappers
entirely rather than leaving dead `if enabled` branches in the hot path.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from src.agents.llm_client import (
    AgentLLMClient,
    DuplicateJudgement,
    GroupingProposal,
    UnifiedClassification,
)
from src.agents.state import AgentState
from src.agents.trace import Tracer

# Fields worth showing on entry to every node. The rest of AgentState is either
# static for the session (catalog, model_version) or already visible as the
# output of the node that set it.
_DIGEST_FIELDS = (
    "iterations",
    "category_id",
    "confirmed_category_id",
    "severity",
    "red_flag",
    "location_id",
    "evidence_revision",
    "duplicate_verdict",
    "duplicate_master_ticket_id",
    "recent_completion_master_id",
    "pending_question_kind",
    "pending_question_id",
    "exit_reason",
)


def _digest(state: AgentState) -> dict[str, Any]:
    return {key: state[key] for key in _DIGEST_FIELDS if key in state}


def traced_node(name: str, fn: Callable[[AgentState], dict[str, Any]], tracer: Tracer) -> Callable[[AgentState], dict[str, Any]]:
    """Emit node_enter / node_exit (or node_error) around one graph node."""

    def wrapper(state: AgentState) -> dict[str, Any]:
        tracer.emit("node_enter", node=name, state=_digest(state))
        started = time.perf_counter()
        try:
            updates = fn(state)
        except BaseException as exc:
            # BaseException, not Exception: LangGraph signals the ask_resident
            # pause by raising a control-flow exception out of interrupt(). It
            # must be re-raised untouched, but the trace should still show that
            # the node stopped here rather than ending silently mid-file.
            tracer.emit(
                "node_error",
                node=name,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        tracer.emit(
            "node_exit",
            node=name,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            updates=updates,
        )
        return updates

    wrapper.__name__ = f"traced_{name}"
    return wrapper


def traced_router(name: str, fn: Callable[[AgentState], str], tracer: Tracer) -> Callable[[AgentState], str]:
    """Emit the branch a conditional edge picked, and the state it read."""

    def wrapper(state: AgentState) -> str:
        target = fn(state)
        tracer.emit("route", router=name, target=target, state=_digest(state))
        return target

    wrapper.__name__ = f"traced_{name}"
    return wrapper


class TracingLLMClient:
    """`AgentLLMClient` decorator that records each call and its latency.

    Records the structured output the model returned, not the raw completion:
    the pipeline only ever acts on the parsed object, so that is what explains a
    downstream decision.
    """

    def __init__(self, inner: AgentLLMClient, tracer: Tracer) -> None:
        self._inner = inner
        self._tracer = tracer

    def _traced(self, call: str, fn: Callable[[], Any], request: dict[str, Any]) -> Any:
        self._tracer.emit("llm_request", call=call, **request)
        started = time.perf_counter()
        try:
            result = fn()
        except Exception as exc:
            self._tracer.emit(
                "llm_error",
                call=call,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        self._tracer.emit(
            "llm_response",
            call=call,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            result=result.model_dump(),
        )
        return result

    def classify(
        self,
        *,
        description: str,
        image_urls: list[str],
        catalog_names: list[str],
        location_label: str,
        floor_label: str,
        unit_code: str | None,
        conversation: list[dict[str, object]],
        confirmed_category: str | None = None,
    ) -> UnifiedClassification:
        return self._traced(
            "classify",
            lambda: self._inner.classify(
                description=description,
                image_urls=image_urls,
                catalog_names=catalog_names,
                location_label=location_label,
                floor_label=floor_label,
                unit_code=unit_code,
                conversation=conversation,
                confirmed_category=confirmed_category,
            ),
            {
                "description": description,
                "image_count": len(image_urls),
                "catalog_size": len(catalog_names),
                "location_label": location_label,
                "floor_label": floor_label,
                # Recorded because "did the model try to overrule the
                # resident's own Category?" is answerable only by comparing
                # this against what came back.
                "confirmed_category": confirmed_category,
                # The whole conversation is recorded because "did the model see
                # the previous answer?" is the question a repeated-question bug
                # always turns out to hinge on.
                "conversation": list(conversation),
            },
        )

    def judge_duplicate(
        self,
        *,
        evidence: dict[str, object],
        candidates: list[dict[str, object]],
    ) -> DuplicateJudgement:
        return self._traced(
            "judge_duplicate",
            lambda: self._inner.judge_duplicate(evidence=evidence, candidates=candidates),
            {"evidence": dict(evidence), "candidate_count": len(candidates)},
        )

    def judge_grouping(
        self,
        *,
        evidence: dict[str, object],
        candidates: list[dict[str, object]],
    ) -> GroupingProposal:
        return self._traced(
            "judge_grouping",
            lambda: self._inner.judge_grouping(evidence=evidence, candidates=candidates),
            {"evidence": dict(evidence), "candidate_count": len(candidates)},
        )


__all__ = ["TracingLLMClient", "traced_node", "traced_router"]
