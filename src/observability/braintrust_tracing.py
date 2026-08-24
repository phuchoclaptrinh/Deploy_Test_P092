"""Braintrust tracing for the analysis and assignment agents.

One initialization point, called from the two processes that run agent work:
the FastAPI lifespan and the standalone assignment worker. `setup_tracing()` is
idempotent and safe to call from anywhere.

**It is optional by construction.** Without `BRAINTRUST_API_KEY` every function
here is a no-op and the application behaves exactly as it did before — same
models, same control flow, same latency. That is not politeness: analysis runs
on a resident's ticket, and a telemetry outage must not become a failed ticket.
Every span helper swallows its own errors for the same reason.

**Why `auto_instrument(langchain=True, openai=False, anthropic=False)`.** Every
model call in this codebase goes through LangChain — `ChatOpenAI` and
`ChatAnthropic` in `src/services/llm.py`, and `with_structured_output` in both
agents. Leaving the provider-level instrumentation on as well would wrap the
same HTTP call a second time inside the LangChain span. The LangChain layer is
the better of the two here: it sees the structured-output schema, the fallback
chain and the streamed reassembly, and it reports the model name and token
usage from `llm_output`.

**What is deliberately not logged.** No API keys, no `Authorization` headers,
no resident free text, no signed storage URLs. Spans carry identifiers, counts,
outcomes and durations. `src/agents/trace.py` remains the place where full
sanitized payloads are written, on disk, under the operator's control.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# The Braintrust project these traces belong to. An id rather than a name:
# `init_logger` accepts `project_id`, and an id cannot be silently pointed at a
# different project by someone renaming one.
BRAINTRUST_PROJECT_ID = "e755825b-7f83-42f3-b749-72b46f83b633"

API_KEY_ENV = "BRAINTRUST_API_KEY"

_state: dict[str, Any] = {"initialized": False, "enabled": False}


def is_tracing_enabled() -> bool:
    return bool(_state["enabled"])


def setup_tracing(*, force: bool = False) -> bool:
    """Initialize Braintrust once. Returns whether tracing is active.

    Call as early as the process has a startup hook — the FastAPI lifespan and
    the worker's `main()`. Provider clients here are all constructed lazily,
    per run, so they are built well after this point and pick up the
    instrumentation.
    """
    if _state["initialized"] and not force:
        return bool(_state["enabled"])
    _state["initialized"] = True

    api_key = _api_key()
    if not api_key:
        # Info, not warning: running without tracing is a supported mode.
        logger.info("%s is not set; Braintrust tracing is off.", API_KEY_ENV)
        _state["enabled"] = False
        return False

    try:
        import braintrust

        # Order matters for `from x import Y` style imports, which is why this
        # runs before the first agent request rather than at first use.
        braintrust.auto_instrument(langchain=True, openai=False, anthropic=False)
        braintrust.init_logger(project_id=BRAINTRUST_PROJECT_ID, api_key=api_key)
    except Exception:  # noqa: BLE001 - telemetry must never stop the app
        logger.exception("Braintrust initialization failed; continuing without tracing.")
        _state["enabled"] = False
        return False

    # The key itself is never logged, here or anywhere else.
    logger.info("Braintrust tracing enabled for project %s.", BRAINTRUST_PROJECT_ID)
    _state["enabled"] = True
    return True


def _api_key() -> str:
    """The environment first, then the repo's own dotenv convention.

    Backend settings come from `.env` through pydantic-settings, which never
    populates `os.environ`. Someone who writes the key into `.env` or
    `.env.braintrust` reasonably expects it to be picked up, so both are read —
    the value is used and never logged, echoed or returned.
    """
    from_env = os.environ.get(API_KEY_ENV, "").strip()
    if from_env:
        return from_env

    from pathlib import Path

    from dotenv import dotenv_values

    root = Path(__file__).resolve().parents[2]
    for name in (".env.braintrust", ".env"):
        path = root / name
        if not path.exists():
            continue
        value = (dotenv_values(path).get(API_KEY_ENV) or "").strip()
        # A placeholder file is the documented default; treat it as unset.
        if value and not value.startswith("<"):
            return value
    return ""


@contextmanager
def root_span(name: str, **metadata: Any) -> Iterator[Any]:
    """One trace per agent request or run.

    Everything the run does — LLM calls via LangChain, tool calls, nested
    spans — attaches underneath it, so a trace reads as the shape of the run
    rather than as a pile of unrelated model calls.
    """
    with _span(name, is_root=True, metadata=metadata) as active:
        yield active


@contextmanager
def span(name: str, **metadata: Any) -> Iterator[Any]:
    """A child span: a tool call, a retrieval, or a step worth seeing."""
    with _span(name, is_root=False, metadata=metadata) as active:
        yield active


@contextmanager
def _span(name: str, *, is_root: bool, metadata: dict[str, Any]) -> Iterator[Any]:
    if not _state["enabled"]:
        yield None
        return

    try:
        import braintrust

        started = braintrust.start_span(name=name, metadata=_clean(metadata))
    except Exception:  # noqa: BLE001
        logger.debug("Braintrust span %r could not be started.", name, exc_info=True)
        yield None
        return

    try:
        with started as active:
            try:
                yield active
            except Exception as exc:
                # The error belongs on the span; re-raising is what keeps the
                # application's own error handling in charge of it.
                _log(active, error=f"{type(exc).__name__}: {exc}")
                raise
    finally:
        if is_root:
            # A root span is the natural boundary to push at: a background task
            # or a worker pass may end the process shortly after.
            flush_traces()


def annotate(active: Any, **fields: Any) -> None:
    """Add outputs or metadata to a live span, ignoring a `None` span."""
    _log(active, **fields)


def _log(active: Any, **fields: Any) -> None:
    if active is None:
        return
    try:
        cleaned = {key: _clean(value) for key, value in fields.items() if value is not None}
        if cleaned:
            active.log(**cleaned)
    except Exception:  # noqa: BLE001
        logger.debug("Braintrust span logging failed.", exc_info=True)


_REDACTED_HINTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "cookie",
    "credential",
)


def _clean(value: Any) -> Any:
    """Drop anything whose *name* says it is a credential.

    A blunt instrument on purpose. Callers here pass identifiers and counts, so
    this is a backstop against a future caller passing a settings object or a
    request header dict, not the primary defence.
    """
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if _is_secret(key) else _clean(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _is_secret(key: Any) -> bool:
    lowered = str(key).lower()
    return any(hint in lowered for hint in _REDACTED_HINTS)


def flush_traces() -> None:
    """Push pending spans. Safe to call when tracing is off.

    Belongs in a `finally`: a run that raised is the one whose trace is most
    worth having.
    """
    if not _state["enabled"]:
        return
    try:
        import braintrust

        braintrust.flush()
    except Exception:  # noqa: BLE001
        logger.debug("Braintrust flush failed.", exc_info=True)
