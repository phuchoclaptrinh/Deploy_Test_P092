"""Model abstraction for the Assignment Agent v4 (contract §5.2).

Two independent clients, primary and fallback, each with its own model name and
its own hard 300-second deadline. Deliberately *not* built with
`Runnable.with_fallbacks()` like the analysis agent: v4 fallback is not "retry
the same request elsewhere". It is a partial re-ask carrying only the decisions
the primary got wrong, and each decision must record which model produced it —
neither of which survives a transparent provider-level retry.

Retries are disabled on both clients: the contract allows two sequential
requests with fixed deadlines and no extension.

**Two parsing layers.** The envelope model checks one thing only: the reply is
an object whose `decisions` is a list. Its elements stay raw dicts. That is
what makes a partial fallback possible — one malformed decision must not take
the well-formed ones down with it. The strict per-decision model lives in
`validator.py` and is applied element by element, with `extra="forbid"`, so an
out-of-contract field fails that one decision instead of being quietly dropped.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.assignment_agent.config import (
    AssignmentAgentSettings,
    AssignmentConfigurationError,
    enforce_failover,
)

logger = logging.getLogger(__name__)


class AssignmentModelError(RuntimeError):
    """The model call itself failed, or its envelope was unusable.

    Distinct from a per-decision contract violation: this one means nothing
    from that call can be trusted, so the whole request goes to the fallback.
    """


class AssignmentModelEnvelope(BaseModel):
    """Layer one. The reply is an object and `decisions` is a list — no more.

    Elements are `Any` on purpose, not `dict`. Requiring dict here would make a
    single garbage element invalidate the entire envelope and send every
    decision to the fallback, which is exactly the failure mode §5.2 item 3
    forbids. An element that is not an object is rejected one layer down, as
    that decision's own failure.
    """

    model_config = ConfigDict(extra="forbid")

    decisions: list[Any] = Field(default_factory=list)


def parse_envelope(payload: object) -> AssignmentModelEnvelope:
    """Validate the envelope layer of a raw model reply."""
    if isinstance(payload, AssignmentModelEnvelope):
        return payload
    if isinstance(payload, BaseModel):
        payload = payload.model_dump()
    if not isinstance(payload, dict):
        raise AssignmentModelError(f"Model reply is not an object: {type(payload).__name__}.")
    try:
        return AssignmentModelEnvelope.model_validate(payload)
    except ValidationError as exc:
        raise AssignmentModelError(f"Model envelope is unusable: {exc.errors()[:2]}") from exc


class AssignmentModelClient(Protocol):
    model_version: str

    def decide(self, *, system_prompt: str, user_prompt: str) -> AssignmentModelEnvelope: ...


class LangChainAssignmentModelClient:
    """Structured-output client over one configured chat model."""

    def __init__(
        self,
        model_version: str,
        *,
        timeout_seconds: int,
        temperature: float = 0.0,
        llm=None,
    ) -> None:
        self.model_version = model_version
        self.timeout_seconds = timeout_seconds
        self._llm = llm or _build_chat_model(model_version, timeout_seconds=timeout_seconds, temperature=temperature)

    def decide(self, *, system_prompt: str, user_prompt: str) -> AssignmentModelEnvelope:
        try:
            # The envelope deliberately leaves each decision as ``Any`` so one
            # malformed decision can go to the fallback without discarding a
            # valid neighbour.  OpenAI's default ``json_schema`` mode rejects
            # that intentionally-open shape with HTTP 400.  Function calling
            # accepts the same Pydantic envelope and is also supported by the
            # Anthropic client, so the primary request no longer fails before
            # the assignment agent has a chance to validate each decision.
            structured = self._llm.with_structured_output(
                AssignmentModelEnvelope,
                method="function_calling",
            )
            reply = structured.invoke(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
            )
        except Exception as exc:  # noqa: BLE001 - normalized into one failure type
            raise AssignmentModelError(f"Assignment model {self.model_version} call failed: {type(exc).__name__}") from exc
        if reply is None:
            raise AssignmentModelError(f"Assignment model {self.model_version} returned no envelope.")
        return parse_envelope(reply)


def _build_chat_model(model_name: str, *, timeout_seconds: int, temperature: float):
    """Pick the provider from the model name.

    Primary and fallback are meant to be different models, often from different
    providers, so provider selection cannot be a single global setting.
    """
    from src.config import get_settings

    settings = get_settings()
    if model_name.lower().startswith("claude"):
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model_name,
            api_key=settings.anthropic_api_key,
            temperature=temperature,
            timeout=timeout_seconds,
            max_retries=0,
        )

    from langchain_openai import ChatOpenAI

    from src.services.llm import openai_no_thinking_kwargs

    return ChatOpenAI(
        model=model_name,
        api_key=settings.openai_api_key,
        temperature=temperature,
        timeout=timeout_seconds,
        max_retries=0,
        **openai_no_thinking_kwargs(model_name),
    )


def build_model_clients(
    settings: AssignmentAgentSettings | None = None,
    *,
    strict: bool = True,
) -> tuple[AssignmentModelClient, AssignmentModelClient | None]:
    """Build the (primary, fallback) pair from Agent-side configuration.

    `strict=True` — the production path — refuses to build anything unless both
    models are configured and different, because a failover onto the same model
    fails the same way. `strict=False` is for local runs: it returns a `None`
    fallback and logs loudly, and primary failures then go straight to manual.

    Either way an unusable configuration raises `AssignmentConfigurationError`,
    never `AssignmentModelError` — the callers treat the two very differently.
    """
    settings = enforce_failover(strict=strict, settings=settings)

    if not settings.assignment_primary_model:
        # Reachable only when `strict` is false: a configuration problem, not a
        # model failure, so it must not be mistaken for a provider outage.
        raise AssignmentConfigurationError("ASSIGNMENT_PRIMARY_MODEL is not configured.")

    primary = LangChainAssignmentModelClient(
        settings.assignment_primary_model,
        timeout_seconds=settings.assignment_model_timeout_seconds,
        temperature=settings.assignment_model_temperature,
    )
    if not settings.failover_ready:
        logger.warning("Assignment fallback model unavailable; running without failover.")
        return primary, None

    fallback = LangChainAssignmentModelClient(
        settings.assignment_fallback_model,
        timeout_seconds=settings.assignment_model_timeout_seconds,
        temperature=settings.assignment_model_temperature,
    )
    return primary, fallback
