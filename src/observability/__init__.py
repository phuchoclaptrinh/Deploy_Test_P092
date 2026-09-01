"""Observability wiring that the application can run entirely without.

Everything exported here degrades to a no-op when `BRAINTRUST_API_KEY` is
absent, which is the normal state in tests, in CI and on a developer machine
that has not opted in. A ticket must never fail because a trace could not be
written.
"""

from src.observability.braintrust_tracing import (
    BRAINTRUST_PROJECT_ID,
    annotate,
    flush_traces,
    is_tracing_enabled,
    root_span,
    setup_tracing,
    span,
)

__all__ = [
    "BRAINTRUST_PROJECT_ID",
    "annotate",
    "flush_traces",
    "is_tracing_enabled",
    "root_span",
    "setup_tracing",
    "span",
]
