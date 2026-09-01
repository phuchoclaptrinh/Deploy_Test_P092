"""Tracing wrappers around the graph's nodes, routers and model client.

Kept apart from `src.agents.trace` (which only knows how to write a line) and
from the pipeline itself: every hook here is a decorator applied in
`build_graph`, so `nodes.py` and `llm_client.py` carry no logging code and stay
readable as pure business logic. Turning tracing off removes the wrappers
entirely rather than leaving dead `if enabled` branches in the hot path.

**Two channels, written from the same wrappers.**

* **JSONL** (`src.agents.trace.Tracer`) -- the full local record: every field of
  every node update, the sanitized model payloads, the routing branch taken.
  `scripts/read_agent_trace.py` replays this.
* **Braintrust** (`src.observability.span`) -- a child span per node and per
  model call, nested under the `analysis.run` root span that
  `src.agents.service` opens for the run, so a remote trace shows the *shape* of
  the run rather than one flat event. These spans carry only identifiers, enum
  outcomes, counts and durations: no resident free text, no model prose, no
  signed URLs. That boundary is owned by `src/observability/braintrust_tracing.py`
  and mirrored by the allowlists below. When `BRAINTRUST_API_KEY` is unset every
  `span()` here yields `None` and costs nothing.

Routers stay JSONL-only: a conditional edge is sub-millisecond work and the node
spans already spell out the path the run took.
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
from src.observability import annotate as bt_annotate
from src.observability import span as bt_span

# Fields worth showing on entry to every node. The rest of AgentState is either
# static for the session (catalog, model_version) or already visible as the
# output of the node that set it.
_DIGEST_FIELDS = (
    "iterations",
    "category_id",
    "confirmed_category_id",
    "criteria",
    "blockers",
    "unknown_facts",
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


# ---------------------------------------------------------------------------
# Braintrust span payloads. Everything below is an allowlist: a key that is not
# named here never reaches the remote trace. The excluded keys are the ones
# carrying prose derived from the resident's report -- `ai_reason`, `evidence`,
# `incident_facts`, `duplicate_reason`, and the whole `result` blob an exit node
# returns -- which `braintrust_tracing.py` forbids and `_trace_output` in
# `service.py` already keeps off the root span.
# ---------------------------------------------------------------------------

#: Node-update keys allowed onto a `node.<name>` span as its output.
_SPAN_SAFE_UPDATE_KEYS = frozenset(
    {
        "iterations",
        "confirmed_category_id",
        "category_id",
        "text_category_id",
        "image_category_id",
        "criteria",
        "blockers",
        "unknown_facts",
        "understandable",
        "image_relevant",
        "evidence_revision",
        "tool_calls_used",
        "ask_rounds_used",
        "ask_elapsed_seconds",
        "exit_reason",
        "duplicate_verdict",
        "duplicate_master_ticket_id",
        "recent_completion_master_id",
        "pending_question_kind",
        "pending_question_id",
        "location_id",
        "duplicate_candidates_revision",
        "duplicate_searched_revision",
    }
)

#: Exception class names (matched across the MRO) that mean "the graph paused for
#: a resident answer", not "the node failed". LangGraph's `interrupt()` raises
#: one of these; matched by name so this module imports nothing from langgraph's
#: internal error module, which has been relocated between releases.
_PAUSE_EXC_NAMES = frozenset({"GraphInterrupt", "GraphBubbleUp"})


def _is_graph_pause(exc: BaseException) -> bool:
    return any(cls.__name__ in _PAUSE_EXC_NAMES for cls in type(exc).__mro__)


def _span_updates(updates: object) -> dict[str, Any]:
    """The subset of a node's updates that may cross to Braintrust."""
    if not isinstance(updates, dict):
        return {}
    safe = {key: value for key, value in updates.items() if key in _SPAN_SAFE_UPDATE_KEYS}
    candidates = updates.get("duplicate_candidates")
    if isinstance(candidates, (list, tuple)):
        # The candidate rows carry redacted summaries; only their count travels.
        safe["duplicate_candidate_count"] = len(candidates)
    return safe


def _llm_span_fields(call: str, request: dict[str, Any]) -> dict[str, Any]:
    """Span metadata for one model call: sizes and flags, never the text."""
    if call == "classify":
        fields = {
            "call": call,
            "catalog_size": request.get("catalog_size"),
            "image_count": request.get("image_count"),
            "conversation_rounds": len(request.get("conversation") or []),
            "has_confirmed_category": bool(request.get("confirmed_category")),
        }
    else:
        fields = {"call": call, "candidate_count": request.get("candidate_count")}
    return {key: value for key, value in fields.items() if value is not None}


def _llm_result_summary(result: object) -> dict[str, Any]:
    """The structured verdict of a model call, with the prose fields dropped."""
    if isinstance(result, UnifiedClassification):
        return {
            "category_selected": result.category is not None,
            "criteria": result.criteria,
            "blocker_codes": result.blocker_codes,
            "unknown_facts": list(result.unknown_facts),
            "question_kind": result.question_kind,
            "understandable": result.understandable,
            "image_relevant": result.image_relevant,
            "location_consistent": result.location_consistent,
        }
    if isinstance(result, DuplicateJudgement):
        return {"verdict": result.verdict, "has_master": bool(result.master_ticket_id)}
    if isinstance(result, GroupingProposal):
        return {"grouped": result.grouped, "related_count": len(result.related_ticket_ids)}
    return {}


def traced_node(name: str, fn: Callable[[AgentState], dict[str, Any]], tracer: Tracer) -> Callable[[AgentState], dict[str, Any]]:
    """Emit node_enter / node_exit (or node_error) around one graph node, and
    open a matching `node.<name>` span on Braintrust."""

    def wrapper(state: AgentState) -> dict[str, Any]:
        tracer.emit("node_enter", node=name, state=_digest(state))
        started = time.perf_counter()
        paused: BaseException | None = None
        with bt_span(f"node.{name}", node=name) as active:
            try:
                updates = fn(state)
            except BaseException as exc:
                # BaseException, not Exception: LangGraph signals the ask_resident
                # pause by raising a control-flow exception out of interrupt(). It
                # must be re-raised untouched, but the trace should still show that
                # the node stopped here rather than ending silently mid-file.
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                tracer.emit(
                    "node_error",
                    node=name,
                    duration_ms=duration_ms,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                if not _is_graph_pause(exc):
                    raise
                # A resident-answer pause is normal control flow. Close the span
                # cleanly with a "paused" marker and bubble the signal only once
                # the `with` has exited, so a clarification round does not paint
                # the remote trace as a failure.
                bt_annotate(active, metadata={"outcome": "paused"}, metrics={"duration_ms": duration_ms})
                paused = exc
            else:
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                tracer.emit("node_exit", node=name, duration_ms=duration_ms, updates=updates)
                bt_annotate(active, output=_span_updates(updates), metrics={"duration_ms": duration_ms})
                return updates
        if paused is not None:
            raise paused
        raise AssertionError("traced_node: neither returned nor bubbled")  # pragma: no cover

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
        with bt_span(f"llm.{call}", **_llm_span_fields(call, request)) as active:
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
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            self._tracer.emit(
                "llm_response",
                call=call,
                duration_ms=duration_ms,
                result=result.model_dump(),
            )
            bt_annotate(active, output=_llm_result_summary(result), metrics={"duration_ms": duration_ms})
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
