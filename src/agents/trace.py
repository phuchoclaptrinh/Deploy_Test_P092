"""Structured JSONL tracing for the Agent v3 pipeline.

Every analysis session writes one append-only JSONL file under
``AGENT_TRACE_DIR`` (default ``.ai-log/agent/``), named after the session id.
One JSON object per line, so a run can be read with ``jq``, tailed live, or
replayed by ``scripts/read_agent_trace.py``.

Why a separate channel from the ``ai_analysis_sessions`` /
``ai_agent_tool_calls`` tables: those record what the Backend *accepted* — the
authoritative audit trail. This records what the Agent *did and why*, including
the steps the Backend rejected, the routing branches taken, and how long each
LLM call took. Debugging a bad classification needs the second kind; the first
kind only shows the outcome.

Nothing here may break an analysis run. Every public entry point swallows its
own errors and falls back to the module logger — a broken trace file must never
turn into a failed ticket.

Sanitization follows the ``sanitized_request`` / ``sanitized_response``
convention already used by the audit tables: signed storage URLs lose their
credentials, resident free text is truncated, and the pinned category catalog
is summarized rather than dumped on every event.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Keys whose values are storage URLs; the query string carries the signature.
_URL_KEYS = {"image_urls", "url", "signed_url", "image_url", "download_url"}

# Keys holding resident-authored or model-authored prose. Truncated, not dropped:
# the first few hundred characters are what makes a trace readable, and the tail
# of a long description has never been the reason a classification went wrong.
_TEXT_KEYS = {
    "description",
    "answer_notes",
    "confidence_notes",
    "action_reason",
    "reason",
    "action_question_text",
    "question_text",
    "prompt",
    "context_notes",
}

# Dumping the pinned catalog on every event would bury the trace: it repeats
# unchanged for the whole session and is already snapshotted in
# ai_analysis_sessions.category_catalog_snapshot.
_SUMMARIZE_KEYS = {"catalog"}

_REDACTED = "<redacted>"


def redact_url(value: str) -> str:
    """Drop the query string from a URL, keeping enough to identify the object.

    Supabase signed download URLs carry the signature in ``?token=``; that token
    grants read access to the object for its TTL, so it is a credential and must
    not reach a log file.
    """
    try:
        parts = urlsplit(value)
    except ValueError:
        return _REDACTED
    if not parts.scheme and not parts.netloc:
        return value
    base = f"{parts.scheme}://{parts.netloc}{parts.path}"
    return f"{base}?{_REDACTED}" if parts.query else base


def truncate(value: str, limit: int) -> str:
    if limit <= 0 or len(value) <= limit:
        return value
    return f"{value[:limit]}…(+{len(value) - limit} ký tự)"


def _summarize_catalog(value: Any) -> Any:
    if isinstance(value, list):
        return {"category_count": len(value)}
    return value


def sanitize(value: Any, *, text_limit: int, key: str | None = None) -> Any:
    """Recursively strip credentials and shorten prose in a trace payload."""
    if key in _SUMMARIZE_KEYS:
        return _summarize_catalog(value)
    if isinstance(value, dict):
        return {str(k): sanitize(v, text_limit=text_limit, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize(item, text_limit=text_limit, key=key) for item in value]
    if isinstance(value, str):
        if key in _URL_KEYS or value.startswith(("http://", "https://")):
            return redact_url(value)
        if key in _TEXT_KEYS:
            return truncate(value, text_limit)
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    # UUID, datetime, Enum, Pydantic models that slipped through, ...
    return truncate(str(value), text_limit)


class AgentTracer:
    """Append-only JSONL writer scoped to one analysis session."""

    def __init__(
        self,
        *,
        session_id: str,
        ticket_id: str,
        directory: Path,
        text_limit: int = 500,
    ) -> None:
        self.session_id = session_id
        self.ticket_id = ticket_id
        self.directory = directory
        self.text_limit = text_limit
        self._seq = 0
        self._lock = threading.Lock()
        self._disabled = False

    @property
    def path(self) -> Path:
        return self.directory / f"{self.session_id}.jsonl"

    def emit(self, event: str, /, **fields: Any) -> None:
        """Write one event. Never raises."""
        if self._disabled:
            return
        try:
            with self._lock:
                self._seq += 1
                record = {
                    "ts": datetime.now(UTC).isoformat(),
                    "seq": self._seq,
                    "session_id": self.session_id,
                    "ticket_id": self.ticket_id,
                    "event": event,
                    **sanitize(fields, text_limit=self.text_limit),
                }
                self.directory.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            # Disable after the first failure so a full disk or a read-only
            # directory does not produce one warning per event for the rest of
            # the run.
            self._disabled = True
            logger.warning("Agent trace disabled for session %s; could not write.", self.session_id, exc_info=True)


class NullTracer:
    """No-op tracer used when tracing is switched off, and in tests."""

    session_id = ""
    ticket_id = ""

    def emit(self, event: str, /, **fields: Any) -> None:  # noqa: ARG002
        return


Tracer = AgentTracer | NullTracer


def get_tracer(*, session_id: str, ticket_id: str) -> Tracer:
    """Build the tracer for one session, honouring configuration.

    Imported lazily inside the function so ``src.agents.trace`` stays importable
    (and unit-testable) without a populated environment.
    """
    from src.config import get_settings

    settings = get_settings()
    if not settings.agent_trace_enabled:
        return NullTracer()
    directory = Path(settings.agent_trace_dir)
    if not directory.is_absolute():
        # Anchored at the repo root (this file is src/agents/trace.py) so the
        # destination does not depend on the process working directory, which
        # differs between `make run`, uvicorn, pytest and the Docker image.
        directory = Path(__file__).resolve().parents[2] / directory
    return AgentTracer(
        session_id=session_id,
        ticket_id=ticket_id,
        directory=directory,
        text_limit=settings.agent_trace_text_limit,
    )


def list_trace_files(directory: str | os.PathLike[str]) -> list[Path]:
    """Session traces in the given directory, newest first."""
    path = Path(directory)
    if not path.is_dir():
        return []
    return sorted(path.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
