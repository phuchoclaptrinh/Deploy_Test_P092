"""Send one identifiable trace to Braintrust and exit. Not for production.

Verifies the wiring, not the models: it makes **no** model request, so it costs
nothing and needs no OpenAI or Anthropic key. What it proves is the part that
actually breaks — the key is found, the project id is accepted, spans nest, and
a short-lived process flushes before it exits.

    python scripts/braintrust_smoke_test.py

Exit codes: 0 sent, 2 no BRAINTRUST_API_KEY, 1 initialization or send failed.
The key is never printed.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

# Same convention as the other scripts here: run from the repo root without
# needing an installed package.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.observability import (  # noqa: E402
    BRAINTRUST_PROJECT_ID,
    annotate,
    flush_traces,
    is_tracing_enabled,
    root_span,
    setup_tracing,
    span,
)

SOURCE = "braintrust-setup-smoke-test"

logger = logging.getLogger("braintrust_smoke_test")


def main() -> int:
    logging.basicConfig(level="INFO", format="%(levelname)s %(message)s")

    if not setup_tracing():
        print(
            "BRAINTRUST_API_KEY is not set (checked the environment, .env.braintrust "
            "and .env).\nSet it and re-run; nothing was sent."
        )
        return 2
    if not is_tracing_enabled():  # pragma: no cover - defensive
        return 1

    marker = datetime.now(UTC).isoformat()
    print(f"Sending one trace to Braintrust project {BRAINTRUST_PROJECT_ID} ...")

    try:
        # The same shape a real analysis run produces: a root span with nested
        # tool work underneath it, so the parent/child wiring is what gets
        # verified rather than a single flat event.
        with root_span("smoke_test.agent_run", source=SOURCE, marker=marker) as run:
            annotate(run, input={"scenario": "wiring check", "model_called": False})

            with span("smoke_test.tool_call", source=SOURCE, tool="search_related_tickets") as tool:
                annotate(tool, output={"candidate_count": 0})

            with span("smoke_test.application_step", source=SOURCE, step="finalize") as step:
                annotate(step, output={"exit_reason": "ANALYSIS_COMPLETE"})

            annotate(run, output={"status": "ok", "spans": 3})
    except Exception:  # noqa: BLE001 - report rather than traceback at a user
        logger.exception("Smoke test failed while building the trace.")
        return 1
    finally:
        # A `finally` so a failed run still reports: the broken trace is the
        # one worth looking at.
        flush_traces()

    print("Sent. Look in Braintrust > Logs for span name 'smoke_test.agent_run'")
    print(f"  filter on metadata source = {SOURCE!r}, marker = {marker}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
