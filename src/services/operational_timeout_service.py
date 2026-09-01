"""Durable timeout sweeps for V4 operational workflow.

**What is no longer here.** The acceptance sweeps -- the "please confirm"
warning and the deadline that released a silent assignment for reassignment --
went with the acceptance step itself. Nothing has replaced them, and that is
deliberate rather than an oversight:

* the old `acceptance_due_at` cannot be reused as a start deadline. A ticket
  legitimately sitting third in a valid queue is *planned* to begin hours after
  it was assigned, so a clock started at `assigned_at` would fire on work that
  is exactly on schedule;
* `planned_start_at` is an estimate, not a promise. Whether it becomes a hard
  deadline, what grace period follows it, and whether a missed start alerts
  Building Management or automatically re-dispatches, are business decisions
  that have not been made.

When they are made, the rule is enforced in two places -- synchronously in
`AssignmentService.start`, and as recovery in a sweep here -- because a sweep
that runs every thirty seconds cannot be the only thing holding a deadline.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from src.services.agent_question_service import AgentQuestionService


class OperationalTimeoutService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def sweep(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        return {"resident_question_timeouts": AgentQuestionService(self.db).handle_timeouts(now)}
