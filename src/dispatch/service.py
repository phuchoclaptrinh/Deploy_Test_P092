"""The micro-batch dispatcher (§6, §7, §8).

One pass, and the shape of it is §8 read top to bottom:

1. **Reclaim** any event whose worker died holding it.
2. **Gate on the working shift.** Outside 08:00-18:00 nobody satisfies §3, so
   the pass defers rather than escalating: the tickets are still automatically
   assignable, just not yet, and escalating them every night would hand
   Building Management a queue every morning that the system was going to
   handle by itself at 08:00.
3. **Claim** at most `dispatch_micro_batch_size` events (20, capped by config),
   with `SKIP LOCKED` so a second worker takes different rows instead of
   waiting behind this one.
4. **Bulk-load** the world in a fixed number of statements (§8), then
5. **schedule entirely in memory** (`src.dispatch.scheduler`).
6. **Call the agent once** for the AT_RISK subset only, never per ticket.
7. **Write the whole batch in one transaction.**

Two design decisions worth stating outright, because both look like accidents
otherwise:

* **The claim is committed before the work starts.** A single long transaction
  spanning the bulk load, the scheduling and a bounded agent call would hold a
  Supabase session open for the whole agent timeout, and §8's session budget is
  exactly what cannot afford that. So: claim, commit, work, commit.

* **Placements accumulate inside the pass.** Every SAFE placement is pushed
  into the in-memory queue before the next ticket is considered, so twenty
  tickets in one batch cannot all be booked into the same free slot. This is
  the reason `World.queues` hands out copies.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from src.config import Settings, get_settings
from src.database.models.dispatch import AtRiskDecision, DispatchEvent
from src.database.models.location import Location
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.dispatch.agent.schemas import (
    AtRiskBatchRequest,
    AtRiskDecisionError,
    AtRiskTicket,
    CandidateDispatchHistory,
    PlannedSlot,
)
from src.dispatch.agent.service import AtRiskAgent, AtRiskOutcome
from src.dispatch.agent.tool import get_candidate_dispatch_history
from src.dispatch.durations import p80_for_code
from src.dispatch.eligibility import eligible_technician_ids
from src.dispatch.enqueue import automatic_assignment_enabled
from src.dispatch.loader import DispatchLoader, World
from src.dispatch.planning import reindex_technicians
from src.dispatch.scheduler import Placement, PlacementDecision, WorkUnit, decide, place, simulate
from src.dispatch.shift import as_utc, is_within_shift, next_shift_open
from src.domain.assignment_guard import ticket_assignment_allowed
from src.models.enums import (
    AssignmentSource,
    ClassificationStatus,
    DispatchDecisionSource,
    DispatchEscalationReason,
    DispatchEventStatus,
    DispatchRiskState,
    Priority,
    TicketStatus,
)
from src.repositories.assignment_repository import AssignmentRepository
from src.services.assignment_support import (
    NEW_ASSIGNMENT_BODY_AUTOMATIC,
    NEW_ASSIGNMENT_TITLE,
    AssignmentSideEffects,
)
from src.services.emergency_review_guard import emergency_review_pending_ticket_ids

logger = logging.getLogger(__name__)

#: P3 is the emergency priority and never reaches here; P2 outranks P1.
#: Queue order, most urgent first. `docs/risk_scoring_v2.md` §6.3.
#: P5 has no rank because it is never enqueued -- a P5 reaching this map
#: would be a bug, and `.get(..., 9)` puts it last rather than first.
PRIORITY_RANK = {
    Priority.P4.value: 0,
    Priority.P3.value: 1,
    Priority.P2.value: 2,
    Priority.P1.value: 3,
}


@dataclass
class BatchReport:
    """What one pass did. Returned rather than logged, so tests can assert it."""

    batch_id: str | None = None
    claimed: int = 0
    reclaimed: int = 0
    assigned_safe: int = 0
    assigned_by_agent: int = 0
    assigned_by_fallback: int = 0
    at_risk: int = 0
    escalated: int = 0
    failed: int = 0
    #: True when the pass did nothing because the working shift was closed.
    out_of_shift: bool = False
    #: §8's guard rail, asserted by the load tests: the statement count must not
    #: grow with the number of tickets in the batch.
    query_count: int = 0
    agent_calls: int = 0
    agent_error: str | None = None
    duration_ms: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class _Pending:
    """One claimed event that survived the eligibility re-check."""

    event: DispatchEvent
    ticket: Ticket
    unit: WorkUnit
    category_code: str


@dataclass
class _AtRisk:
    pending: _Pending
    decision: PlacementDecision
    eligible_ids: list[UUID]


class DispatchService:
    def __init__(
        self,
        db: Session,
        *,
        agent: AtRiskAgent | None = None,
        settings: Settings | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.agent = agent or AtRiskAgent(settings=self.settings)
        self.worker_id = worker_id or "dispatch-worker"
        self.assignments = AssignmentRepository(db)
        self.side_effects = AssignmentSideEffects(db)
        self.buffer = timedelta(seconds=self.settings.dispatch_safety_buffer_seconds)
        #: Unit id -> residents to notify, resolved once per pass.
        self._recipients: dict[UUID, list[UUID]] = {}

    # ------------------------------------------------------------------
    # The pass.
    # ------------------------------------------------------------------

    def run_micro_batch(self, now: datetime | None = None) -> BatchReport:
        now = now or datetime.now(UTC)
        started = time.monotonic()
        report = BatchReport()
        report.reclaimed = self._reclaim_expired(now)

        if not is_within_shift(now):
            report.out_of_shift = True
            report.duration_ms = int((time.monotonic() - started) * 1000)
            self._defer_until_shift(now)
            return report

        events = self._claim(now, self.settings.dispatch_micro_batch_size)
        report.claimed = len(events)
        if not events:
            report.duration_ms = int((time.monotonic() - started) * 1000)
            return report

        try:
            self._process(events, now, report)
        except Exception as exc:  # noqa: BLE001 - a failed batch must not kill the worker
            logger.exception("Dispatch micro-batch failed.")
            self.db.rollback()
            report.errors.append(f"{type(exc).__name__}: {exc}")
            self._release(events, now, failed=True)
        report.duration_ms = int((time.monotonic() - started) * 1000)
        return report

    # ------------------------------------------------------------------
    # Claiming.
    # ------------------------------------------------------------------

    def _reclaim_expired(self, now: datetime) -> int:
        """Return events whose holder died back to the queue.

        A claim that has outlived `claim_expires_at` is evidence of a crash, not
        of slowness -- the timeout is set well beyond a whole batch including a
        full agent timeout. Past `dispatch_max_attempts` the event stops being
        retried and becomes Building Management's, because an event that has
        killed three workers will kill a fourth.
        """
        stale = list(
            self.db.scalars(
                select(DispatchEvent).where(
                    DispatchEvent.status == DispatchEventStatus.CLAIMED.value,
                    DispatchEvent.claim_expires_at.is_not(None),
                    DispatchEvent.claim_expires_at <= now,
                )
            )
        )
        for event in stale:
            if event.attempt_count >= self.settings.dispatch_max_attempts:
                self._escalate(
                    event,
                    DispatchEscalationReason.NO_FEASIBLE_PLACEMENT,
                    now,
                    error_code="DISPATCH_ATTEMPTS_EXHAUSTED",
                    error_detail=f"claim expired {event.attempt_count} times",
                )
                continue
            event.status = DispatchEventStatus.PENDING.value
            event.is_open = True
            event.claimed_at = None
            event.claim_expires_at = None
            event.claimed_by = None
            event.available_at = now
        if stale:
            self.db.commit()
        return len(stale)

    def _defer_until_shift(self, now: datetime) -> None:
        """Push pending events to the next shift opening.

        Not strictly required -- the shift gate above already stops the pass --
        but it keeps `available_at` honest, so an operator reading the queue at
        02:00 sees "waiting for 08:00" rather than a pile of overdue work.
        """
        opens = next_shift_open(now)
        rows = list(
            self.db.scalars(
                select(DispatchEvent).where(
                    DispatchEvent.status == DispatchEventStatus.PENDING.value,
                    DispatchEvent.available_at < opens,
                )
            )
        )
        for event in rows:
            event.available_at = opens
        if rows:
            self.db.commit()

    def _claim(self, now: datetime, limit: int) -> list[DispatchEvent]:
        query = (
            select(DispatchEvent)
            .where(
                DispatchEvent.status == DispatchEventStatus.PENDING.value,
                DispatchEvent.available_at <= now,
            )
            .order_by(DispatchEvent.available_at.asc(), DispatchEvent.enqueued_at.asc())
            .limit(limit)
        )
        # SKIP LOCKED is what lets a second worker take *different* rows rather
        # than block on this one. SQLite renders neither clause and needs
        # neither: there is one writer by construction.
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        events = list(self.db.scalars(query))
        expires = now + timedelta(seconds=self.settings.dispatch_claim_timeout_seconds)
        for event in events:
            event.status = DispatchEventStatus.CLAIMED.value
            event.is_open = True
            event.claimed_at = now
            event.claim_expires_at = expires
            event.claimed_by = self.worker_id
            event.attempt_count += 1
        # Committed immediately: holding this transaction open across the bulk
        # load and a bounded agent call would occupy a Supabase session for the
        # whole agent timeout, which is the budget §8 protects.
        self.db.commit()
        return events

    def _release(self, events: list[DispatchEvent], now: datetime, *, failed: bool) -> None:
        for event in events:
            fresh = self.db.get(DispatchEvent, event.id)
            if fresh is None or fresh.status != DispatchEventStatus.CLAIMED.value:
                continue
            if failed and fresh.attempt_count >= self.settings.dispatch_max_attempts:
                fresh.status = DispatchEventStatus.FAILED.value
                fresh.is_open = False
                fresh.decided_at = now
            else:
                fresh.status = DispatchEventStatus.PENDING.value
                fresh.is_open = True
                fresh.claimed_at = None
                fresh.claim_expires_at = None
                fresh.claimed_by = None
                fresh.available_at = now
        self.db.commit()

    # ------------------------------------------------------------------
    # Processing.
    # ------------------------------------------------------------------

    def _process(self, events: list[DispatchEvent], now: datetime, report: BatchReport) -> None:
        batch_id = uuid4()
        report.batch_id = str(batch_id)

        tickets = self._load_tickets([event.ticket_id for event in events])
        loader = DispatchLoader(self.db)
        world = loader.load(ticket_ids=[event.ticket_id for event in events])
        loader.load_category_codes(world, [event.category_id for event in events])
        report.query_count = world.query_count

        enabled = automatic_assignment_enabled(self.db)
        # Two batch-wide lookups instead of two per ticket (§8). The active
        # assignments are already in `world`; the emergency gate is one more
        # statement for the whole batch.
        gated = emergency_review_pending_ticket_ids(self.db, [event.ticket_id for event in events])
        report.query_count += 1
        self._recipients = self.side_effects.unit_recipients(list(tickets.values()))
        report.query_count += 1

        pending = self._recheck(events, tickets, world, now, enabled, gated, report)
        queues = {tid: list(row.queue) for tid, row in world.technicians.items()}

        safe: list[tuple[_Pending, Placement]] = []
        at_risk: list[_AtRisk] = []
        for item in pending:
            eligible = eligible_technician_ids(
                world.eligibility_inputs(),
                category_id=item.ticket.category_id,
                now=now,
                excluded_technician_ids=world.exclusions.get(item.ticket.id, frozenset()),
            )
            if not eligible:
                self._escalate(item.event, DispatchEscalationReason.NO_ELIGIBLE_TECHNICIAN, now, ticket=item.ticket)
                report.escalated += 1
                continue
            decision = decide(item.unit, {tid: queues[tid] for tid in eligible}, now, self.buffer)
            if not decision.is_feasible:
                self._escalate(item.event, DispatchEscalationReason.NO_FEASIBLE_PLACEMENT, now, ticket=item.ticket)
                report.escalated += 1
                continue
            if decision.risk_state is DispatchRiskState.SAFE:
                best = decision.best
                safe.append((item, best))
                # Booked now, so the next ticket in this batch sees the slot taken.
                queues[best.technician_id].append(
                    replace(item.unit, deadline=best.committed_deadline)
                )
            else:
                at_risk.append(_AtRisk(pending=item, decision=decision, eligible_ids=eligible))

        report.at_risk = len(at_risk)
        resolved = self._resolve_at_risk(at_risk, world, queues, now, batch_id, report)

        touched: set[UUID] = set()
        for item, placement in safe:
            assignment = self._write_assignment(
                item, placement, now, batch_id,
                source=AssignmentSource.AUTO_SCHEDULER,
                decision_source=DispatchDecisionSource.SCHEDULER,
                risk=DispatchRiskState.SAFE,
            )
            touched.add(assignment.technician_id)
            report.assigned_safe += 1

        for entry, placement, source, outcome in resolved:
            assignment = self._write_assignment(
                entry.pending, placement, now, batch_id,
                source=(
                    AssignmentSource.AUTO_AGENT
                    if source is DispatchDecisionSource.AGENT
                    else AssignmentSource.AUTO_FALLBACK
                ),
                decision_source=source,
                risk=DispatchRiskState.AT_RISK,
            )
            touched.add(assignment.technician_id)
            self._record_at_risk_decision(entry, assignment, placement, source, outcome, batch_id, report)
            self._notify_at_risk(entry, assignment, placement, source, outcome)
            if source is DispatchDecisionSource.AGENT:
                report.assigned_by_agent += 1
            else:
                report.assigned_by_fallback += 1

        self._reindex(touched, now)
        self.db.commit()

    def _load_tickets(self, ticket_ids: list[UUID]) -> dict[UUID, Ticket]:
        rows = self.db.scalars(
            select(Ticket)
            .where(Ticket.id.in_(ticket_ids))
            .options(joinedload(Ticket.category), joinedload(Ticket.location).joinedload(Location.floor))
        ).unique()
        return {ticket.id: ticket for ticket in rows}

    def _recheck(
        self,
        events: list[DispatchEvent],
        tickets: dict[UUID, Ticket],
        world: World,
        now: datetime,
        enabled: bool,
        gated: frozenset[UUID],
        report: BatchReport,
    ) -> list[_Pending]:
        """Re-verify every claimed event against the world as it is *now*.

        The event was written when the ticket became eligible; between then and
        this pass a coordinator may have assigned it by hand, a duplicate link
        may have been made, or the toggle may have been switched off. The event
        is a request to consider the ticket, never a right to assign it.
        """
        pending: list[_Pending] = []
        for event in sorted(
            events,
            key=lambda item: (
                PRIORITY_RANK.get(item.priority, 9),
                -(item.score_total or 0),
                as_utc(item.ticket_submitted_at),
            ),
        ):
            ticket = tickets.get(event.ticket_id)
            if ticket is None:
                self._escalate(event, DispatchEscalationReason.TICKET_NOT_ELIGIBLE, now)
                report.escalated += 1
                continue
            if not enabled:
                self._escalate(event, DispatchEscalationReason.AUTO_ASSIGNMENT_DISABLED, now, ticket=ticket)
                report.escalated += 1
                continue
            if not ticket_assignment_allowed(ticket):
                self._escalate(event, DispatchEscalationReason.P5_EMERGENCY, now, ticket=ticket)
                report.escalated += 1
                continue
            if not self._still_dispatchable(ticket, world, gated):
                # Most often a coordinator got there first. That is a normal
                # outcome, not a conflict: the ticket is handled.
                self._close(event, DispatchEventStatus.SUPERSEDED, now)
                continue
            code = world.category_codes.get(ticket.category_id) or ""
            pending.append(
                _Pending(
                    event=event,
                    ticket=ticket,
                    category_code=code,
                    unit=WorkUnit(
                        key=event.id,
                        ticket_ids=(ticket.id,),
                        duration=p80_for_code(code),
                        score=ticket.risk_score or 0,
                        submitted_at=as_utc(ticket.created_at),
                    ),
                )
            )
        return pending

    def _still_dispatchable(self, ticket: Ticket, world: World, gated: frozenset[UUID]) -> bool:
        """`ticket_is_dispatchable`, answered from what the batch already loaded.

        Identical rules -- deliberately the same list, in the same order -- but
        with the two database lookups replaced by set membership. Keeping the
        single-ticket version around matters: it is what the API paths use, and
        it is the definition this one has to agree with.
        """
        if ticket.status is not TicketStatus.APPROVED:
            return False
        if ticket.classification_status is not ClassificationStatus.RESOLVED:
            return False
        if ticket.category_id is None or ticket.priority is None:
            return False
        if ticket.duplicate_of_ticket_id is not None:
            return False
        if not ticket_assignment_allowed(ticket):
            return False
        if ticket.id in gated:
            return False
        return ticket.id not in world.assigned_ticket_ids

    # ------------------------------------------------------------------
    # The at-risk subset (§7).
    # ------------------------------------------------------------------

    def _resolve_at_risk(
        self,
        items: list[_AtRisk],
        world: World,
        queues: dict[UUID, list[WorkUnit]],
        now: datetime,
        batch_id: UUID,
        report: BatchReport,
    ) -> list[tuple[_AtRisk, Placement, DispatchDecisionSource, AtRiskOutcome | None]]:
        """One agent call for the whole subset, with a scheduler fallback.

        The fallback is not an error path bolted on: the agent is advisory over
        a decision the scheduler has already made a defensible choice for, so
        when the agent times out, fails, or answers something that does not
        survive validation, the ranked head of `decide()` -- the
        least-negative-slack technician -- is taken and recorded as
        `SCHEDULER_FALLBACK`. Building Management is told either way, and the
        `decision_source` on the row is what tells the two apart.
        """
        if not items:
            return []

        picks: dict[str, AtRiskOutcome] = {}
        try:
            request = self._build_agent_request(items, world, now, batch_id)
            report.agent_calls = 1
            picks = self.agent.decide(request)
        except AtRiskDecisionError as exc:
            report.agent_error = exc.code
            logger.warning("At-risk agent unusable for batch %s (%s); falling back.", batch_id, exc.code)
        except Exception as exc:  # noqa: BLE001 - never let the adviser stop the dispatch
            report.agent_error = "AGENT_ERROR"
            logger.exception("At-risk agent raised for batch %s; falling back.", batch_id)
            report.errors.append(f"agent: {type(exc).__name__}: {exc}")

        resolved: list[tuple[_AtRisk, Placement, DispatchDecisionSource, AtRiskOutcome | None]] = []
        for entry in items:
            ref = str(entry.pending.event.id)
            outcome = picks.get(ref)
            source = DispatchDecisionSource.AGENT if outcome else DispatchDecisionSource.SCHEDULER_FALLBACK
            technician_id = outcome.technician_id if outcome else entry.decision.best.technician_id
            # Re-placed against the queues as they stand *after* the earlier
            # tickets of this batch were booked. The agent reasoned over a
            # snapshot taken before those bookings; the times written to the
            # database have to describe the schedule that actually results.
            placement = self._replace_against(entry, technician_id, queues, now)
            queues[technician_id].append(replace(entry.pending.unit, deadline=placement.committed_deadline))
            resolved.append((entry, placement, source, outcome))
        return resolved

    def _replace_against(
        self,
        entry: _AtRisk,
        technician_id: UUID,
        queues: dict[UUID, list[WorkUnit]],
        now: datetime,
    ) -> Placement:
        return place(technician_id, queues.get(technician_id, []), entry.pending.unit, now, self.buffer)

    def _build_agent_request(
        self,
        items: list[_AtRisk],
        world: World,
        now: datetime,
        batch_id: UUID,
    ) -> AtRiskBatchRequest:
        candidate_ids = sorted({tid for entry in items for tid in entry.eligible_ids}, key=str)
        # One statement for the whole subset, every window (§7, §8).
        history = get_candidate_dispatch_history(
            self.db,
            candidate_ids,
            items[0].pending.ticket.category_id if len(items) == 1 else None,
            now,
        )
        # The projected numbers differ per ticket; the agent is shown the worst
        # case across the subset, which is the figure that decides the trade-off.
        projected: dict[UUID, tuple[int | None, datetime | None]] = {}
        for entry in items:
            for placement in entry.decision.placements:
                current = projected.get(placement.technician_id)
                worst = placement.worst_committed_slack
                if current is None or (worst is not None and (current[0] is None or worst < current[0])):
                    projected[placement.technician_id] = (worst, placement.candidate.planned_start_at)

        candidates = []
        for technician_id in candidate_ids:
            row = world.technicians.get(technician_id)
            slots = simulate(list(row.queue), now, self.buffer) if row else ()
            worst, start = projected.get(technician_id, (None, None))
            candidates.append(
                CandidateDispatchHistory(
                    technician_id=technician_id,
                    active_assignment_count=row.active_count if row else 0,
                    in_progress_count=row.in_progress_count if row else 0,
                    planned_schedule=[
                        PlannedSlot(
                            order=slot.order,
                            planned_start_at=slot.planned_start_at,
                            planned_finish_at=slot.planned_finish_at,
                            slack_seconds=slot.slack_seconds,
                        )
                        for slot in slots
                    ],
                    projected_worst_slack_seconds=worst,
                    projected_start_at=start,
                    history=history.get(technician_id, []),
                )
            )

        return AtRiskBatchRequest(
            batch_id=batch_id,
            current_time=now,
            tickets=[
                AtRiskTicket(
                    # The event id, never the ticket id: the agent has no reason
                    # to learn a resident-addressable identifier, and this one
                    # is what the answer is matched back against anyway.
                    ticket_ref=str(entry.pending.event.id),
                    category_code=entry.pending.category_code or "UNKNOWN",
                    priority=entry.pending.event.priority,
                    score=float(entry.pending.unit.score),
                    submitted_at=entry.pending.unit.submitted_at,
                    p80_working_seconds=int(entry.pending.unit.duration.total_seconds()),
                    eligible_technician_ids=entry.eligible_ids,
                )
                for entry in items
            ],
            candidates=candidates,
        )

    # ------------------------------------------------------------------
    # Writing.
    # ------------------------------------------------------------------

    def _write_assignment(
        self,
        item: _Pending,
        placement: Placement,
        now: datetime,
        batch_id: UUID,
        *,
        source: AssignmentSource,
        decision_source: DispatchDecisionSource,
        risk: DispatchRiskState,
    ) -> TicketAssignment:
        assignment = self.assignments.create_assignment(
            ticket_id=item.ticket.id,
            technician_id=placement.technician_id,
            # No human decided this one, so no human is named. The audit actor
            # is SYSTEM, and the database constraint permits null only for the
            # three automatic sources.
            assigned_by_user_id=None,
            assignment_source=source.value,
            dispatch_event_id=item.event.id,
        )
        assignment.planned_start_at = placement.candidate.planned_start_at
        assignment.planned_finish_at = placement.committed_deadline
        assignment.planned_order = placement.candidate.order
        assignment.risk_state = risk.value
        assignment.slack_seconds = placement.worst_committed_slack
        self.db.flush()

        event = item.event
        event.status = DispatchEventStatus.ASSIGNED.value
        event.is_open = False
        event.batch_id = batch_id
        event.risk_state = risk.value
        event.decision_source = decision_source.value
        event.selected_technician_id = placement.technician_id
        event.assignment_id = assignment.id
        event.planned_start_at = assignment.planned_start_at
        event.planned_finish_at = assignment.planned_finish_at
        event.slack_seconds = placement.worst_committed_slack
        event.decided_at = now

        self.side_effects.audit(
            None,
            "AUTO_ASSIGN_TECHNICIAN",
            "TICKET_ASSIGNMENT",
            assignment.id,
            None,
            {
                "ticket_id": str(item.ticket.id),
                "technician_id": str(placement.technician_id),
                "assignment_source": source.value,
                "risk_state": risk.value,
                "dispatch_event_id": str(event.id),
                "batch_id": str(batch_id),
            },
            None,
            "SYSTEM",
        )
        self.side_effects.notify_technician(
            assignment,
            "ASSIGNMENT_CREATED",
            NEW_ASSIGNMENT_TITLE,
            NEW_ASSIGNMENT_BODY_AUTOMATIC,
        )
        self.side_effects.notify_unit(
            item.ticket,
            "TICKET_ASSIGNED",
            "Phản ánh đã được gán kỹ thuật viên",
            "Kỹ thuật viên đã được phân công và sẽ bắt đầu xử lý theo lịch dự kiến.",
            recipients=self._recipients.get(item.ticket.source_unit_id, []),
        )
        return assignment

    def _record_at_risk_decision(
        self,
        entry: _AtRisk,
        assignment: TicketAssignment,
        placement: Placement,
        source: DispatchDecisionSource,
        outcome: AtRiskOutcome | None,
        batch_id: UUID,
        report: BatchReport,
    ) -> None:
        self.db.add(
            AtRiskDecision(
                dispatch_event_id=entry.pending.event.id,
                ticket_id=entry.pending.ticket.id,
                batch_id=batch_id,
                technician_id=placement.technician_id,
                decision_source=source.value,
                reason=(
                    outcome.reason
                    if outcome
                    else "Agent không phản hồi kịp; hệ thống chọn kỹ thuật viên có mức trễ thấp nhất."
                ),
                model_name=outcome.model_name if outcome else None,
                latency_ms=outcome.latency_ms if outcome else None,
                candidate_technician_ids=[str(tid) for tid in entry.eligible_ids],
                slack_seconds=placement.worst_committed_slack,
                tool_snapshot=None,
                raw_model_output=None,
                error_code=None if outcome else (report.agent_error or "AGENT_NO_DECISION"),
            )
        )

    def _notify_at_risk(
        self,
        entry: _AtRisk,
        assignment: TicketAssignment,
        placement: Placement,
        source: DispatchDecisionSource,
        outcome: AtRiskOutcome | None,
    ) -> None:
        """§7: Building Management is told whenever an AT_RISK decision is made."""
        self.side_effects.notify_coordinators(
            entry.pending.ticket,
            "DISPATCH_AT_RISK_DECISION",
            "Phân việc tự động có rủi ro trễ lịch",
            "Hệ thống đã phân công một phản ánh dù lịch của kỹ thuật viên bị trễ. Vui lòng xem lại.",
            {
                "assignment_id": str(assignment.id),
                "dispatch_event_id": str(entry.pending.event.id),
                "technician_id": str(placement.technician_id),
                "decision_source": source.value,
                "slack_seconds": placement.worst_committed_slack,
                "reason": (outcome.reason if outcome else "")[:200],
            },
        )

    def _escalate(
        self,
        event: DispatchEvent,
        reason: DispatchEscalationReason,
        now: datetime,
        *,
        ticket: Ticket | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        event.status = DispatchEventStatus.ESCALATED.value
        event.is_open = False
        event.escalation_reason = reason.value
        event.error_code = error_code
        event.error_detail = error_detail
        event.decided_at = now
        if ticket is not None:
            # Reusing the preserved pause columns rather than adding a status:
            # this is exactly what they mean, and the manager list already
            # surfaces them.
            ticket.auto_assignment_paused = True
            ticket.auto_assignment_pause_reason = reason.value
            ticket.version += 1
            self.side_effects.notify_coordinators(
                ticket,
                "DISPATCH_ESCALATED",
                "Cần phân việc thủ công",
                "Một phản ánh không thể phân công tự động và đang chờ Ban quản lý xử lý.",
                {"dispatch_event_id": str(event.id), "reason": reason.value},
            )

    def _close(self, event: DispatchEvent, status: DispatchEventStatus, now: datetime) -> None:
        event.status = status.value
        event.is_open = False
        event.decided_at = now

    def _reindex(self, technician_ids: set[UUID], now: datetime) -> None:
        """Renumber "Do now" / "Next" for every technician this batch touched.

        Delegated so that the manual and Visual Assignment paths renumber
        identically -- three copies of this would eventually disagree about
        what a technician's queue looks like, and the technician screen would
        show whichever one wrote last.
        """
        reindex_technicians(self.db, technician_ids, now)


__all__ = ["PRIORITY_RANK", "BatchReport", "DispatchService"]
