"""The dispatch worker (§8).

    python -m src.workers.dispatch_worker            # loop
    python -m src.workers.dispatch_worker --once     # a single pass, for cron

Its own process, separate from the API, and the reason is not tidiness: every
unit of pending work is a database row (`dispatch_events`), so a restart resumes
exactly where it left off and two workers can run side by side without either
one assigning a ticket the other already took.

One pass, in order:

1. **Timeouts** -- resident question expiry. No assignment deadline is swept:
   the acceptance clock is gone and no start deadline has been approved to
   replace it (see `src.services.operational_timeout_service`).
2. **Backlog** -- enqueue anything eligible with no open event. The recovery
   path for tickets that became eligible while the toggle was off.
3. **Dispatch** -- one micro-batch: claim, bulk-load, schedule in memory, one
   agent call for the at-risk subset, write in one transaction.

**The poll interval is the micro-batch interval.** §8 asks for batches roughly
every 0.5-1 second, and that is what `dispatch_micro_batch_interval_ms`
configures. The two slower stages do not run every tick -- sweeping timeouts and
scanning the backlog at 1Hz would spend the §8 session budget on queries that
almost always find nothing -- so each has its own longer cadence and the
dispatch stage runs alone in between.

Each stage opens its own session and swallows its own exceptions: a failure in
the dispatch stage must not stop the timeout sweep from running, and vice versa.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from src.config import get_settings
from src.database.session import make_session_factory
from src.observability import flush_traces, setup_tracing

logger = logging.getLogger(__name__)

#: How often the slower stages run, in seconds. The timeout sweep holds the
#: resident question deadlines; the backlog scan is pure recovery and can be
#: lazy.
TIMEOUT_SWEEP_SECONDS = 30.0
BACKLOG_SWEEP_SECONDS = 120.0


@dataclass
class PassReport:
    started_at: datetime
    timeouts: dict[str, int] = field(default_factory=dict)
    backlog_enqueued: int = 0
    dispatch: dict[str, object] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["started_at"] = self.started_at.isoformat()
        return payload


def worker_identity() -> str:
    """Which process holds a claim. Free-text, for operators reading a queue."""
    return f"{socket.gethostname()}:{os.getpid()}"


class DispatchWorker:
    def __init__(self, session_factory=None, *, worker_id: str | None = None) -> None:
        self.settings = get_settings()
        # The worker budget, not the API's (§8). Sized so API pool + worker pool
        # stays under the Supabase session limit, which `validate_runtime_safety`
        # refuses to boot without.
        self.session_factory = session_factory or make_session_factory("worker")
        self.worker_id = worker_id or worker_identity()
        self._last_timeout_sweep = 0.0
        self._last_backlog_sweep = 0.0

    def run_once(self, now: datetime | None = None, *, force_all_stages: bool = True) -> PassReport:
        """One full pass. Safe to call from a test, a cron job, or the loop.

        `force_all_stages` defaults to true so `--once` and tests get every
        stage; the loop passes false and lets the cadences above decide.
        """
        now = now or datetime.now(UTC)
        report = PassReport(started_at=now)
        clock = time.monotonic()

        if force_all_stages or clock - self._last_timeout_sweep >= TIMEOUT_SWEEP_SECONDS:
            self._last_timeout_sweep = clock
            self._stage(report, "timeouts", lambda db: self._sweep_timeouts(db, now))

        if force_all_stages or clock - self._last_backlog_sweep >= BACKLOG_SWEEP_SECONDS:
            self._last_backlog_sweep = clock
            self._stage(report, "backlog", lambda db: self._sweep_backlog(db, now))

        self._stage(report, "dispatch", lambda db: self._dispatch(db, now))
        return report

    # ------------------------------------------------------------------

    def _sweep_timeouts(self, db, now: datetime) -> dict[str, int]:
        from src.services.operational_timeout_service import OperationalTimeoutService

        return OperationalTimeoutService(db).sweep(now)

    def _sweep_backlog(self, db, now: datetime) -> int:
        from src.dispatch.enqueue import enqueue_backlog

        return len(enqueue_backlog(db, now=now))

    def _dispatch(self, db, now: datetime) -> dict[str, object]:
        from src.dispatch.service import DispatchService

        return DispatchService(db, worker_id=self.worker_id).run_micro_batch(now).as_dict()

    def _stage(self, report: PassReport, name: str, work) -> None:
        """Run one stage in its own session, and never let it take the pass down."""
        db = self.session_factory()
        try:
            outcome = work(db)
            if name == "timeouts":
                report.timeouts = outcome
            elif name == "backlog":
                report.backlog_enqueued = outcome
            else:
                report.dispatch = outcome
        except Exception as exc:  # noqa: BLE001 - one failing stage must not stop the others
            logger.exception("Dispatch worker stage %s failed.", name)
            report.errors.append(f"{name}: {type(exc).__name__}: {exc}")
            db.rollback()
        finally:
            # Returned to the pool every pass. A session leaked here would burn
            # one of the fifteen §8 allows, permanently.
            db.close()


class _Stopper:
    """Finish the pass in flight, then exit -- no half-written batch."""

    def __init__(self) -> None:
        self.stopping = False
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._handle)
            except (ValueError, OSError):
                # Not the main thread, or the platform has no such signal.
                pass

    def _handle(self, *_args) -> None:
        logger.info("Dispatch worker asked to stop; finishing the current pass.")
        self.stopping = True


def run_forever(interval_ms: int | None = None) -> None:
    settings = get_settings()
    interval = (interval_ms or settings.dispatch_micro_batch_interval_ms) / 1000.0
    worker = DispatchWorker()
    stopper = _Stopper()
    logger.info(
        "Dispatch worker %s started; micro-batch every %.2fs, up to %d tickets.",
        worker.worker_id,
        interval,
        settings.dispatch_micro_batch_size,
    )
    while not stopper.stopping:
        started = time.monotonic()
        report = worker.run_once(force_all_stages=False)
        if report.errors:
            logger.warning("Dispatch pass finished with errors: %s", report.errors)
        elif report.dispatch.get("claimed"):
            logger.info("Dispatch pass: %s", report.dispatch)
        # Sleep the remainder, so a slow pass does not compound into drift.
        remaining = interval - (time.monotonic() - started)
        while remaining > 0 and not stopper.stopping:
            time.sleep(min(0.25, remaining))
            remaining -= 0.25
    logger.info("Dispatch worker stopped.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FixIt dispatch worker (§8).")
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit (for cron).")
    parser.add_argument("--interval-ms", type=int, default=None, help="Override the micro-batch interval.")
    args = parser.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    setup_tracing()

    try:
        # The same §8 pool check the API runs. A worker that happily claimed
        # events on a configuration the API refuses to boot on would drain the
        # queue while the deployment looked half-healthy.
        settings.validate_runtime_safety()
    except RuntimeError as exc:
        logger.error("Dispatch worker refusing to start: %s", exc)
        return 2

    try:
        if args.once:
            report = DispatchWorker().run_once()
            logger.info("Dispatch worker single pass: %s", report.as_dict())
            return 1 if report.errors else 0
        run_forever(args.interval_ms)
        return 0
    finally:
        # `--once` is short-lived: without this the last pass's spans die with it.
        flush_traces()


if __name__ == "__main__":
    sys.exit(main())
