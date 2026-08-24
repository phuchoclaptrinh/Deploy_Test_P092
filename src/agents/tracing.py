"""Tracing wrappers around the graph's nodes, routers and LLM client.

Kept apart from ``src.agents.trace`` (which only knows how to write a line) and
from the pipeline itself: every hook here is a decorator applied in
``build_graph``, so ``nodes.py`` and ``llm_client.py`` carry no logging code and
stay readable as pure business logic. Turning tracing off removes the wrappers
entirely rather than leaving dead ``if enabled`` branches in the hot path.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from src.agents.llm_client import Action, ActionDecision, AgentLLMClient, ExtractionResult
from src.agents.state import AgentState
from src.agents.trace import Tracer

# Fields worth showing on entry to every node. The rest of AgentState is either
# static for the session (catalog, model_version) or already visible as the
# output of the node that set it.
_DIGEST_FIELDS = (
    "iterations",
    "next_action",
    "is_confident",
    "severity",
    "text_categories",
    "image_categories",
    "red_flag_text",
    "red_flag_signal",
    "pending_question_id",
    "reextraction",
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
    """``AgentLLMClient`` decorator that records each call and its latency.

    Records the structured output the model returned, not the raw completion:
    the pipeline only ever acts on the parsed object, so that is what explains a
    downstream decision. Prompt inputs are recorded in the sanitized form (image
    URLs stripped of their signatures, resident text truncated).
    """

    def __init__(self, inner: AgentLLMClient, tracer: Tracer) -> None:
        self._inner = inner
        self._tracer = tracer

    def extract(
        self,
        *,
        description: str,
        image_urls: list[str],
        catalog_names: list[str],
        context_notes: list[str],
    ) -> ExtractionResult:
        self._tracer.emit(
            "llm_request",
            call="extract",
            description=description,
            image_count=len(image_urls),
            image_urls=image_urls,
            catalog_size=len(catalog_names),
            context_notes=context_notes,
        )
        started = time.perf_counter()
        try:
            result = self._inner.extract(
                description=description,
                image_urls=image_urls,
                catalog_names=catalog_names,
                context_notes=context_notes,
            )
        except Exception as exc:
            self._tracer.emit(
                "llm_error",
                call="extract",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        self._tracer.emit(
            "llm_response",
            call="extract",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            result=result.model_dump(),
        )
        return result

    def decide_next_action(
        self,
        *,
        description: str,
        extraction: ExtractionResult,
        available_actions: list[Action],
        related_tickets: list[dict[str, object]],
        grouping_eligible: bool,
        answer_notes: list[str] | None = None,
        ask_rounds_used: int = 0,
        max_ask_rounds: int = 3,
    ) -> ActionDecision:
        self._tracer.emit(
            "llm_request",
            call="decide_next_action",
            description=description,
            extraction=extraction.model_dump(),
            available_actions=list(available_actions),
            related_ticket_count=len(related_tickets),
            grouping_eligible=grouping_eligible,
            # Ghi lại để trace cho thấy model CÓ nhìn thấy câu trả lời trước
            # hay không — thiếu chúng chính là thứ đã gây vòng hỏi lặp.
            answer_notes=list(answer_notes or []),
            ask_rounds_used=ask_rounds_used,
            max_ask_rounds=max_ask_rounds,
        )
        started = time.perf_counter()
        try:
            decision = self._inner.decide_next_action(
                description=description,
                extraction=extraction,
                available_actions=available_actions,
                related_tickets=related_tickets,
                grouping_eligible=grouping_eligible,
                answer_notes=answer_notes,
                ask_rounds_used=ask_rounds_used,
                max_ask_rounds=max_ask_rounds,
            )
        except Exception as exc:
            self._tracer.emit(
                "llm_error",
                call="decide_next_action",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        self._tracer.emit(
            "llm_response",
            call="decide_next_action",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            result=decision.model_dump(),
        )
        return decision
