"""The durable assignment worker (§5).

Runs as its own process, separate from the FastAPI application:

    python -m src.workers.assignment_worker            # loop forever
    python -m src.workers.assignment_worker --once     # a single pass, for cron

§5 rules out `FastAPI BackgroundTasks` and in-process timers for the 5-10 minute
assignment windows, and the reason is not tidiness: a web process restarting
mid-window would drop the ticket with no record that anything was pending. Here
every unit of pending work is a database row, so a restart resumes exactly where
it left off and two workers can run side by side.

One pass, in order:

1. **Timeouts** — resident question expiry, acceptance warnings, acceptance
   reassignment. These create the work the rest of the pass consumes.
2. **Triggers** — newly eligible tickets become DIRECT jobs once their
   activation delay has elapsed (P3 skips it).
3. **DIRECT** — claim due jobs, run the decision engine once per batch, write
   assignments.
4. **PROPOSAL** — expire stale batches, then build the ones still BUILDING.
5. **SCHEDULE** — if the recurring draft schedule is due, open one new proposal
   batch for a coordinator to review. It assigns nothing; that is what makes it
   a different feature from the DIRECT switch in stage 3.

Stage 5 runs last on purpose. Expiring stale batches first means a schedule that
comes due seconds after the previous table expired sees a clear board, rather
than skipping its turn because a batch nobody was ever going to confirm was
still sitting in READY.

Each stage opens its own database session and swallows its own exceptions: a
failure on the DIRECT stage must not stop proposals from expiring, because an
unexpired batch is a batch someone can still confirm against a stale snapshot.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from src.assignment_agent.config import AssignmentConfigurationError, enforce_failover
from src.assignment_rules.config import AssignmentRuleConfigError, get_rule_config
from src.config import get_settings
from src.database.session import SessionLocal
from src.observability import flush_traces, setup_tracing
from src.services.assignment_direct_service import DirectAssignmentService
from src.services.assignment_proposal_service import AssignmentProposalService
from src.services.assignment_schedule_service import AssignmentScheduleService
from src.services.assignment_trigger_service import AssignmentTriggerService
from src.services.operational_timeout_service import OperationalTimeoutService

logger = logging.getLogger(__name__)


@dataclass
class WorkerPassReport:
    started_at: datetime
    timeouts: dict[str, int] = field(default_factory=dict)
    jobs_scheduled: int = 0
    direct: dict[str, object] = field(default_factory=dict)
    proposal: dict[str, object] = field(default_factory=dict)
    schedule: dict[str, object] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["started_at"] = self.started_at.isoformat()
        return payload


def run_once(now: datetime | None = None) -> WorkerPassReport:
    """One full pass. Safe to call from a test, a cron job or the loop below."""
    now = now or datetime.now(UTC)
    report = WorkerPassReport(started_at=now)

    _stage(report, "timeouts", lambda db: OperationalTimeoutService(db).sweep(now))
    _stage(
        report,
        "triggers",
        lambda db: len(AssignmentTriggerService(db).enqueue_newly_eligible(now=now)),
    )
    _stage(report, "direct", lambda db: DirectAssignmentService(db).run_due_jobs(now=now))
    _stage(report, "proposal", lambda db: AssignmentProposalService(db).run_due_batches(now=now))
    _stage(report, "schedule", lambda db: AssignmentScheduleService(db).run_due(now=now))
    return report


def _stage(report: WorkerPassReport, name: str, work) -> None:
    """Run one stage in its own session, and never let it take the pass down."""
    db = SessionLocal()
    try:
        outcome = work(db)
        if name == "timeouts":
            report.timeouts = outcome
        elif name == "triggers":
            report.jobs_scheduled = outcome
        elif name == "direct":
            report.direct = asdict(outcome)
        elif name == "proposal":
            report.proposal = asdict(outcome)
        else:
            report.schedule = asdict(outcome)
    except (AssignmentConfigurationError, AssignmentRuleConfigError):
        # The one exception to "a stage never takes the pass down". Every later
        # pass would fail identically, and each one would leave MANUAL_REQUIRED
        # rows behind it. Stop, so a supervisor surfaces it.
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - one failing stage must not stop the others
        logger.exception("Assignment worker stage %s failed.", name)
        report.errors.append(f"{name}: {type(exc).__name__}: {exc}")
        db.rollback()
    finally:
        db.close()


class _Stopper:
    """Finish the pass in flight, then exit — no half-written assignment."""

    def __init__(self) -> None:
        self.stopping = False
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._handle)
            except (ValueError, OSError):
                # Not the main thread, or the platform has no such signal.
                pass

    def _handle(self, *_args) -> None:
        logger.info("Assignment worker asked to stop; finishing the current pass.")
        self.stopping = True


def verify_configuration() -> None:
    """Refuse to process jobs on a configuration the API would have rejected.

    The worker is a second process reading the same settings, so it runs the
    same startup check the API runs, on whichever engine is selected: the §5.2
    failover rule for `AI`, and a real parse of the rule file for `RULE`. A
    worker that happily claimed jobs on a configuration the API refuses to boot
    on would drain the queue into the manual pile while the deployment looked
    half-healthy.
    """
    settings = get_settings()
    if settings.assignment_decision_engine == "AI":
        enforce_failover(strict=settings.require_assignment_failover)
        return
    config = get_rule_config()
    logger.info(
        "Assignment worker running on %s (total cap=%s, P3 cap=%s).",
        config.rule_version,
        config.max_active_assignments,
        config.max_active_p3_assignments,
    )


def run_forever(poll_seconds: int | None = None) -> None:
    settings = get_settings()
    poll_seconds = poll_seconds or settings.assignment_worker_poll_seconds
    stopper = _Stopper()
    logger.info("Assignment worker started; polling every %ss.", poll_seconds)
    while not stopper.stopping:
        started = time.monotonic()
        report = run_once()
        if report.errors:
            logger.warning("Assignment worker pass finished with errors: %s", report.errors)
        else:
            logger.debug("Assignment worker pass: %s", report.as_dict())
        elapsed = time.monotonic() - started
        remaining = poll_seconds - elapsed
        while remaining > 0 and not stopper.stopping:
            time.sleep(min(1.0, remaining))
            remaining -= 1.0
    logger.info("Assignment worker stopped.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FixIt durable assignment worker (contract §5).")
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit (for cron).")
    parser.add_argument("--poll-seconds", type=int, default=None, help="Override the poll interval.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Before any job is claimed, so the model calls a pass makes are traced.
    setup_tracing()

    try:
        verify_configuration()
    except (AssignmentConfigurationError, AssignmentRuleConfigError) as exc:
        # Exit code, not a traceback: the message carries model and rule names
        # only, and a supervisor should see a clean configuration failure.
        logger.error("Assignment worker refusing to start: %s", exc)
        return 2

    try:
        if args.once:
            report = run_once()
            logger.info("Assignment worker single pass: %s", report.as_dict())
            return 1 if report.errors else 0
        run_forever(args.poll_seconds)
        return 0
    finally:
        # `--once` is a short-lived process: without this the last pass's spans
        # would die with it. In a `finally` because a pass that raised is the
        # one whose trace is worth the most.
        flush_traces()


if __name__ == "__main__":
    sys.exit(main())
