"""Per-request trace context shared by API and audit persistence."""

from contextvars import ContextVar

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
