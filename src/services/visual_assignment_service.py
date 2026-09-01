"""Visual Assignment: the board, and the one action that confirms it (§1).

This replaces the proposal workspace outright. Nothing here ranks technicians,
proposes anyone, or creates a batch for a model to fill in -- §1 removes all
three. What it does instead:

* put every eligible unit of work into a **pool**, with grouped tickets kept
  together as one draggable unit that cannot be split (§1);
* show every technician's **current workload and planned day**;
* show, for each unit against each technician, what is **wrong or risky** about
  that pairing before anyone drags anything;
* accept **all** the placements in one action and persist them in **one
  transaction**.

**Hard constraints are enforced on confirm, not merely warned about.** Skill,
availability, working shift and one-active-assignment-per-ticket are §3
constraints, and a confirm that breaks any of them is rejected in full -- the
whole board, not the offending row, because §1 asks for one transaction and a
partial write would leave Building Management guessing which half landed.
Workload and schedule risk are *not* §3 constraints and stay advisory: a long
day is a judgement the manager is entitled to make.

The board is computed, never stored. There is no board entity, no expiry and no
version to confirm against -- a stale board fails at confirm time on the same
constraints a fresh one passes, which is a better guarantee than a timer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from src.config import get_settings
from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.location import Location
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.dispatch.durations import p80_for_unit
from src.dispatch.eligibility import hard_constraint_violations
from src.dispatch.loader import DispatchLoader, World
from src.dispatch.planning import apply_placement, load_queue, reindex_technicians, safety_buffer
from src.dispatch.scheduler import WorkUnit, place, simulate
from src.dispatch.shift import as_utc, is_within_shift, to_local
from src.domain.assignment_guard import (
    EMERGENCY_PRIORITY,
    assert_ticket_assignment_allowed,
)
from src.domain.risk_scoring import PRIORITY_RANK as DOMAIN_PRIORITY_RANK
from src.models.api.errors import (
    VISUAL_PLACEMENT_INVALID,
    VISUAL_UNIT_NOT_PLACEABLE,
    DomainError,
)
from src.models.enums import (
    AnalysisRunStatus,
    AssignmentSource,
    ClassificationStatus,
    DispatchRiskState,
    PlacementWarningCode,
    Priority,
    TicketStatus,
)
from src.repositories.assignment_repository import AssignmentRepository
from src.services.assignment_support import (
    NEW_ASSIGNMENT_BODY_COORDINATOR,
    NEW_ASSIGNMENT_TITLE,
    AssignmentSideEffects,
)
from src.services.dispatch_reassignment import supersede_open_event
from src.services.emergency_review_guard import emergency_review_is_pending

#: Warnings the board shows but does not block on. Everything else in
#: `PlacementWarningCode` is a §3 hard constraint and rejects on confirm.
ADVISORY_WARNINGS = frozenset({PlacementWarningCode.OVERLOADED, PlacementWarningCode.SCHEDULE_RISK})

#: A technician whose simulated day runs past today is flagged as overloaded.
#: Derived from the schedule rather than from a ticket-count cap, because §3
#: defines no cap and inventing one would make the board disagree with the
#: scheduler about what "too much" means.
OVERLOAD_SPILLS_TO_NEXT_DAY = True

GROUPING_READY_FOR_BOARD = frozenset({"NO_MATCH", "GROUPED", "NOT_ELIGIBLE"})
GROUPING_GROUPED = "GROUPED"
#: The emergency gate, named the way the band is named now. The wire code was
#: `P3_REVIEW_PENDING`, which under the inverted scale told a coordinator the
#: opposite of what was true about a routine P3.
EMERGENCY_REVIEW_PENDING_CODE = "EMERGENCY_REVIEW_PENDING"
GROUPING_NOT_READY_CODE = "GROUPING_NOT_READY"
GROUPING_CASE_NOT_OPEN_CODE = "GROUPING_CASE_NOT_OPEN"


@dataclass
class BoardUnit:
    """One draggable item: a single ticket, or a whole incident group."""

    unit_id: str
    unit_type: str
    ticket_ids: list[UUID]
    display_codes: list[str]
    category_id: UUID | None
    category_code: str | None
    category_display_name: str | None
    priority: Priority | None
    score: Decimal
    submitted_at: datetime
    location_labels: list[str]
    p80_seconds: int
    member_count: int
    #: Technicians who fail no §3 constraint for this unit. The board allows a
    #: drop only onto one of these, which is what makes the confirm's rejection
    #: unreachable through the UI rather than merely handled by it.
    eligible_technician_ids: list[UUID] = field(default_factory=list)
    previews: list[PlacementPreview] = field(default_factory=list)


@dataclass
class PlacementPreview:
    """What would happen if this unit were dropped on this technician."""

    technician_id: UUID
    blocked: bool
    warnings: list[PlacementWarningCode]
    planned_start_at: datetime | None
    planned_finish_at: datetime | None
    worst_slack_seconds: int | None


@dataclass
class BoardTechnician:
    technician_id: UUID
    display_name: str
    is_active: bool
    is_available: bool
    skill_category_ids: list[UUID]
    active_assignment_count: int
    in_progress_count: int
    planned_slots: list[dict[str, object]]
    #: Where the simulated day currently ends. The column header shows it, so
    #: "this person is booked until Thursday" is visible before anyone drags.
    day_ends_at: datetime | None


@dataclass
class Board:
    generated_at: datetime
    within_working_shift: bool
    units: list[BoardUnit]
    technicians: list[BoardTechnician]


@dataclass
class ConfirmResult:
    assigned_unit_count: int
    assigned_ticket_count: int
    assignment_ids: list[UUID]


class VisualAssignmentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.assignments = AssignmentRepository(db)
        self.side_effects = AssignmentSideEffects(db)

    # ------------------------------------------------------------------
    # Board.
    # ------------------------------------------------------------------

    def board(self, *, now: datetime | None = None, limit: int = 100) -> Board:
        now = now or datetime.now(UTC)
        tickets = self._pool_tickets(limit=limit)
        units = self._build_units(tickets)

        loader = DispatchLoader(self.db)
        world = loader.load(ticket_ids=[tid for unit in units for tid in unit.ticket_ids])
        loader.load_category_codes(world, [u.category_id for u in units if u.category_id])
        for unit in units:
            if unit.category_code is None and unit.category_id:
                unit.category_code = world.category_codes.get(unit.category_id)
                unit.p80_seconds = int(p80_for_unit([unit.category_code or ""]).total_seconds())

        self._attach_previews(units, world, now)
        return Board(
            generated_at=now,
            within_working_shift=is_within_shift(now),
            units=units,
            technicians=self._technician_columns(world, now),
        )

    def _pool_tickets(self, *, limit: int) -> list[Ticket]:
        """Everything Building Management may place by hand right now.

        Broader than the automatic path's eligibility on purpose. §2 sends
        anything that fails the automatic conditions to Building Management, and
        this board is where those land -- a ticket a dispatch pass escalated for
        want of an eligible technician belongs here even though it never
        reached the automatic workflow.

        **A P5 does not.** It used to: under v1 the board was where an
        emergency went precisely *because* automation refused it. v2 refuses it
        everywhere (`docs/risk_scoring_v2.md` §8) -- Building Management handles
        an emergency by walking there, not by dropping it on a technician's
        column -- so offering it as a draggable card would be offering an action
        the confirm step is required to reject.

        Still excluded: anything unapproved, unclassified, already assigned, or
        linked as a duplicate. None of those is a placement decision waiting to
        be made.
        """
        active_assignment = (
            select(TicketAssignment.id)
            .where(TicketAssignment.ticket_id == Ticket.id, TicketAssignment.is_active.is_(True))
            .exists()
        )
        rows = self.db.scalars(
            select(Ticket)
            .where(
                Ticket.status == TicketStatus.APPROVED,
                Ticket.classification_status == ClassificationStatus.RESOLVED,
                Ticket.category_id.is_not(None),
                Ticket.duplicate_of_ticket_id.is_(None),
                Ticket.priority != EMERGENCY_PRIORITY,
                ~active_assignment,
            )
            .options(
                joinedload(Ticket.category),
                joinedload(Ticket.location).joinedload(Location.floor),
                selectinload(Ticket.assignments),
            )
            .order_by(Ticket.created_at.asc())
            .limit(limit)
        ).unique()
        # A ticket parked at the emergency gate has no settled priority and no
        # decision a manager can make about it yet; it belongs in the review
        # queue, not the placement pool.
        candidates = [ticket for ticket in rows if not emergency_review_is_pending(self.db, ticket.id)]
        return self._filter_grouping_ready(candidates)

    def _filter_grouping_ready(self, tickets: list[Ticket]) -> list[Ticket]:
        """Only work whose duplicate/grouping stage is closed may enter the board."""
        if not tickets:
            return []
        grouping_statuses = self._latest_grouping_statuses([ticket.id for ticket in tickets])
        case_of = self._open_case_membership([ticket.id for ticket in tickets])
        return [
            ticket
            for ticket in tickets
            if self._grouping_readiness_code(ticket, grouping_statuses, case_of) is None
        ]

    def _latest_grouping_statuses(self, ticket_ids: list[UUID]) -> dict[UUID, str | None]:
        if not ticket_ids:
            return {}
        latest_runs = (
            select(AIAnalysisRun.ticket_id, func.max(AIAnalysisRun.run_number).label("run_number"))
            .where(
                AIAnalysisRun.ticket_id.in_(ticket_ids),
                AIAnalysisRun.status == AnalysisRunStatus.SUCCEEDED,
            )
            .group_by(AIAnalysisRun.ticket_id)
            .subquery()
        )
        rows = self.db.execute(
            select(AIAnalysisRun.ticket_id, AIAnalysisRun.grouping_status)
            .join(
                latest_runs,
                and_(
                    AIAnalysisRun.ticket_id == latest_runs.c.ticket_id,
                    AIAnalysisRun.run_number == latest_runs.c.run_number,
                ),
            )
            .where(AIAnalysisRun.status == AnalysisRunStatus.SUCCEEDED)
        ).all()
        return {ticket_id: grouping_status for ticket_id, grouping_status in rows}

    @staticmethod
    def _grouping_readiness_code(
        ticket: Ticket,
        grouping_statuses: dict[UUID, str | None],
        case_of: dict[UUID, UUID],
    ) -> str | None:
        grouping_status = grouping_statuses.get(ticket.id)
        if grouping_status not in GROUPING_READY_FOR_BOARD:
            return GROUPING_NOT_READY_CODE
        if grouping_status == GROUPING_GROUPED and ticket.id not in case_of:
            return GROUPING_CASE_NOT_OPEN_CODE
        return None

    def _build_units(self, tickets: list[Ticket]) -> list[BoardUnit]:
        """Fold grouped tickets into single units (§1).

        A ticket in an open incident case becomes part of that case's unit, and
        the unit is the only thing that can be dragged. That is how "grouped
        tickets must remain grouped as one draggable work unit" is enforced --
        not by validating afterwards that a group was not split, but by never
        offering the pieces separately.
        """
        by_id = {ticket.id: ticket for ticket in tickets}
        case_of = self._open_case_membership(list(by_id))

        grouped: dict[UUID, list[Ticket]] = {}
        singles: list[Ticket] = []
        for ticket in tickets:
            case_id = case_of.get(ticket.id)
            if case_id is None:
                singles.append(ticket)
            else:
                grouped.setdefault(case_id, []).append(ticket)

        units = [self._unit_from(f"ticket:{ticket.id}", "TICKET", [ticket]) for ticket in singles]
        units += [
            self._unit_from(f"case:{case_id}", "GROUP", sorted(members, key=lambda t: t.created_at))
            for case_id, members in grouped.items()
        ]
        # Oldest work first, so the pool reads like a queue rather than a set.
        units.sort(key=lambda unit: unit.submitted_at)
        return units

    def _open_case_membership(self, ticket_ids: list[UUID]) -> dict[UUID, UUID]:
        if not ticket_ids:
            return {}
        rows = self.db.execute(
            select(IncidentCaseMember.ticket_id, IncidentCaseMember.case_id)
            .join(IncidentCase, IncidentCase.id == IncidentCaseMember.case_id)
            .where(IncidentCaseMember.ticket_id.in_(ticket_ids), IncidentCase.status == "OPEN")
        ).all()
        return {ticket_id: case_id for ticket_id, case_id in rows}

    def _unit_from(self, unit_id: str, unit_type: str, members: list[Ticket]) -> BoardUnit:
        first = members[0]
        codes = [t.category.code for t in members if t.category]
        return BoardUnit(
            unit_id=unit_id,
            unit_type=unit_type,
            ticket_ids=[t.id for t in members],
            display_codes=[_ticket_code(t.id) for t in members],
            category_id=first.category_id,
            category_code=first.category.code if first.category else None,
            category_display_name=first.category.display_name if first.category else None,
            # The most urgent member decides the unit: a group is done when its
            # hardest promise is kept, not its easiest.
            priority=min(
                (t.priority for t in members if t.priority),
                key=_priority_rank,
                default=None,
            ),
            score=max((t.risk_score or Decimal(0) for t in members), default=Decimal(0)),
            submitted_at=as_utc(min(t.created_at for t in members)),
            location_labels=sorted({t.location.label for t in members if t.location and t.location.label}),
            p80_seconds=int(p80_for_unit(codes).total_seconds()),
            member_count=len(members),
        )

    def _attach_previews(self, units: list[BoardUnit], world: World, now: datetime) -> None:
        buffer = safety_buffer()
        inputs = world.eligibility_inputs()
        for unit in units:
            excluded = frozenset().union(
                *[world.exclusions.get(tid, frozenset()) for tid in unit.ticket_ids]
            ) if unit.ticket_ids else frozenset()
            work = WorkUnit(
                key=_unit_key(unit),
                ticket_ids=tuple(unit.ticket_ids),
                duration=p80_for_unit([unit.category_code or ""] * max(unit.member_count, 1)),
                score=unit.score,
                submitted_at=unit.submitted_at,
            )
            previews: list[PlacementPreview] = []
            for row in inputs:
                hard = list(
                    hard_constraint_violations(
                        row,
                        category_id=unit.category_id,
                        now=now,
                        excluded_technician_ids=excluded,
                    )
                )
                technician = world.technicians[row.technician_id]
                placement = place(row.technician_id, list(technician.queue), work, now, buffer)
                advisory: list[PlacementWarningCode] = []
                if not placement.is_safe:
                    advisory.append(PlacementWarningCode.SCHEDULE_RISK)
                if _spills_past_today(placement.candidate.planned_finish_at, now):
                    advisory.append(PlacementWarningCode.OVERLOADED)
                previews.append(
                    PlacementPreview(
                        technician_id=row.technician_id,
                        blocked=bool(hard),
                        warnings=hard + advisory,
                        planned_start_at=placement.candidate.planned_start_at,
                        planned_finish_at=placement.committed_deadline,
                        worst_slack_seconds=placement.worst_committed_slack,
                    )
                )
            unit.previews = previews
            unit.eligible_technician_ids = [p.technician_id for p in previews if not p.blocked]

    def _technician_columns(self, world: World, now: datetime) -> list[BoardTechnician]:
        buffer = safety_buffer()
        columns = []
        for row in sorted(world.technicians.values(), key=lambda r: (r.display_name, str(r.technician_id))):
            slots = simulate(list(row.queue), now, buffer)
            columns.append(
                BoardTechnician(
                    technician_id=row.technician_id,
                    display_name=row.display_name,
                    is_active=row.is_active and row.user_is_active,
                    is_available=row.is_available,
                    skill_category_ids=sorted(row.skill_category_ids, key=str),
                    active_assignment_count=row.active_count,
                    in_progress_count=row.in_progress_count,
                    planned_slots=[
                        {
                            "assignment_id": slot.unit.assignment_id,
                            "ticket_id": slot.unit.ticket_ids[0] if slot.unit.ticket_ids else None,
                            "order": slot.order,
                            "planned_start_at": slot.planned_start_at,
                            "planned_finish_at": slot.planned_finish_at,
                            "slack_seconds": slot.slack_seconds,
                            "in_progress": slot.unit.in_progress,
                        }
                        for slot in slots
                    ],
                    day_ends_at=slots[-1].planned_finish_at if slots else None,
                )
            )
        return columns

    # ------------------------------------------------------------------
    # Confirm.
    # ------------------------------------------------------------------

    def confirm(
        self,
        coordinator_user_id: UUID,
        placements: list[tuple[str, UUID]],
        *,
        now: datetime | None = None,
    ) -> ConfirmResult:
        """Validate and persist every placement, or none of them (§1).

        One transaction. Every precondition for every unit is checked, under
        lock, before a single row is written -- the same all-or-nothing shape
        `assign_case` already uses, for the same reason: a partial board is not
        an outcome Building Management asked for.
        """
        now = now or datetime.now(UTC)
        if not placements:
            return ConfirmResult(assigned_unit_count=0, assigned_ticket_count=0, assignment_ids=[])

        try:
            resolved = self._resolve_placements(placements, now)
            # Re-checked here and not only in the pool query: the board a
            # coordinator is looking at may be minutes old, and a ticket that
            # was a P4 when it was drawn can be a P5 by the time they drop it.
            # The pool decides what to offer; this decides what may be written.
            for _unit, _technician_id, tickets in resolved:
                for ticket in tickets:
                    assert_ticket_assignment_allowed(ticket)
            self._validate(resolved, now)

            assignment_ids: list[UUID] = []
            ticket_count = 0
            touched: set[UUID] = set()
            for unit, technician_id, tickets in resolved:
                for ticket in tickets:
                    assignment = self.assignments.create_assignment(
                        ticket_id=ticket.id,
                        technician_id=technician_id,
                        assigned_by_user_id=coordinator_user_id,
                        assignment_source=AssignmentSource.COORDINATOR_VISUAL.value,
                    )
                    if unit.member_count > 1:
                        assignment.case_member_count_snapshot = unit.member_count
                    self.db.flush()
                    placement = place(
                        technician_id,
                        # Excluding the row just written: it would otherwise be
                        # loaded back as existing work and booked twice, giving
                        # the resident a start time one job too late.
                        load_queue(self.db, technician_id, now, exclude_assignment_id=assignment.id),
                        WorkUnit(
                            key=assignment.id,
                            ticket_ids=(ticket.id,),
                            duration=p80_for_unit([unit.category_code or ""]),
                            score=unit.score,
                            submitted_at=unit.submitted_at,
                        ),
                        now,
                        safety_buffer(),
                    )
                    apply_placement(
                        assignment,
                        placement,
                        risk=DispatchRiskState.SAFE if placement.is_safe else DispatchRiskState.AT_RISK,
                    )
                    supersede_open_event(self.db, ticket.id, now=now)
                    self.side_effects.audit(
                        coordinator_user_id,
                        "ASSIGN_TECHNICIAN",
                        "TICKET_ASSIGNMENT",
                        assignment.id,
                        None,
                        {
                            "ticket_id": str(ticket.id),
                            "technician_id": str(technician_id),
                            "assignment_source": AssignmentSource.COORDINATOR_VISUAL.value,
                            "unit_id": unit.unit_id,
                            "unit_type": unit.unit_type,
                        },
                        None,
                        "COORDINATOR",
                    )
                    self.side_effects.notify_technician(
                        assignment,
                        "ASSIGNMENT_CREATED",
                        NEW_ASSIGNMENT_TITLE,
                        NEW_ASSIGNMENT_BODY_COORDINATOR,
                    )
                    self.side_effects.notify_unit(
                        ticket,
                        "TICKET_ASSIGNED",
                        "Phản ánh đã được gán kỹ thuật viên",
                        "Ban quản lý đã phân công kỹ thuật viên xử lý phản ánh.",
                    )
                    assignment_ids.append(assignment.id)
                    ticket_count += 1
                touched.add(technician_id)

            self.db.flush()
            reindex_technicians(self.db, touched, now)
            self.db.commit()
            return ConfirmResult(
                assigned_unit_count=len(resolved),
                assigned_ticket_count=ticket_count,
                assignment_ids=assignment_ids,
            )
        except Exception:
            self.db.rollback()
            raise

    def _resolve_placements(
        self,
        placements: list[tuple[str, UUID]],
        now: datetime,
    ) -> list[tuple[BoardUnit, UUID, list[Ticket]]]:
        seen_units: set[str] = set()
        seen_tickets: set[UUID] = set()
        resolved: list[tuple[BoardUnit, UUID, list[Ticket]]] = []
        for unit_id, technician_id in placements:
            if unit_id in seen_units:
                raise DomainError(
                    VISUAL_PLACEMENT_INVALID,
                    "Một hạng mục công việc được phân công hai lần trong cùng một lần xác nhận.",
                    400,
                    {"unit_id": unit_id},
                )
            seen_units.add(unit_id)
            tickets = self._lock_unit_tickets(unit_id)
            unit = self._unit_from(unit_id, "GROUP" if len(tickets) > 1 else "TICKET", tickets)
            for ticket in tickets:
                if ticket.id in seen_tickets:
                    raise DomainError(
                        VISUAL_PLACEMENT_INVALID,
                        "Một phản ánh xuất hiện trong hai hạng mục công việc khác nhau.",
                        400,
                        {"unit_id": unit_id, "ticket_id": str(ticket.id)},
                    )
                seen_tickets.add(ticket.id)
            resolved.append((unit, technician_id, tickets))
        return resolved

    def _lock_unit_tickets(self, unit_id: str) -> list[Ticket]:
        kind, _, raw = unit_id.partition(":")
        try:
            identifier = UUID(raw)
        except ValueError as exc:
            raise DomainError(VISUAL_UNIT_NOT_PLACEABLE, "Mã hạng mục công việc không hợp lệ.", 400,
                              {"unit_id": unit_id}) from exc

        if kind == "ticket":
            ticket_ids = [identifier]
        elif kind == "case":
            ticket_ids = list(
                self.db.scalars(
                    select(IncidentCaseMember.ticket_id)
                    .join(IncidentCase, IncidentCase.id == IncidentCaseMember.case_id)
                    .where(IncidentCaseMember.case_id == identifier, IncidentCase.status == "OPEN")
                )
            )
        else:
            raise DomainError(VISUAL_UNIT_NOT_PLACEABLE, "Loại hạng mục công việc không hợp lệ.", 400,
                              {"unit_id": unit_id})

        if not ticket_ids:
            raise DomainError(VISUAL_UNIT_NOT_PLACEABLE, "Hạng mục công việc không còn tồn tại.", 409,
                              {"unit_id": unit_id})

        # Locked in UUID order so two confirms touching overlapping units cannot
        # deadlock against each other.
        tickets = []
        for ticket_id in sorted(set(ticket_ids), key=str):
            ticket = self.db.scalar(
                select(Ticket)
                .where(Ticket.id == ticket_id)
                .options(joinedload(Ticket.category), joinedload(Ticket.location))
                .with_for_update(of=Ticket)
            )
            if ticket is None:
                raise DomainError(VISUAL_UNIT_NOT_PLACEABLE, "Phản ánh không còn tồn tại.", 409,
                                  {"unit_id": unit_id, "ticket_id": str(ticket_id)})
            tickets.append(ticket)
        return sorted(tickets, key=lambda t: t.created_at)

    def _validate(
        self,
        resolved: list[tuple[BoardUnit, UUID, list[Ticket]]],
        now: datetime,
    ) -> None:
        """Every §3 hard constraint, for every placement, before anything is written."""
        loader = DispatchLoader(self.db)
        world = loader.load(ticket_ids=[t.id for _, _, tickets in resolved for t in tickets])
        failures: list[dict[str, object]] = []
        all_ticket_ids = [ticket.id for _, _, tickets in resolved for ticket in tickets]
        grouping_statuses = self._latest_grouping_statuses(all_ticket_ids)
        case_of = self._open_case_membership(all_ticket_ids)

        for unit, technician_id, tickets in resolved:
            row = world.technicians.get(technician_id)
            if row is None:
                failures.append({"unit_id": unit.unit_id, "technician_id": str(technician_id),
                                 "codes": [PlacementWarningCode.TECHNICIAN_UNAVAILABLE.value]})
                continue
            excluded = frozenset().union(
                *[world.exclusions.get(t.id, frozenset()) for t in tickets]
            ) if tickets else frozenset()
            codes = [
                code.value
                for code in hard_constraint_violations(
                    row,
                    category_id=unit.category_id,
                    now=now,
                    excluded_technician_ids=excluded,
                )
                if code not in ADVISORY_WARNINGS
            ]
            for ticket in tickets:
                if ticket.status is not TicketStatus.APPROVED:
                    codes.append("TICKET_NOT_APPROVED")
                if ticket.duplicate_of_ticket_id is not None:
                    codes.append("TICKET_IS_DUPLICATE")
                if emergency_review_is_pending(self.db, ticket.id):
                    codes.append(EMERGENCY_REVIEW_PENDING_CODE)
                grouping_code = self._grouping_readiness_code(ticket, grouping_statuses, case_of)
                if grouping_code is not None:
                    codes.append(grouping_code)
                if self.assignments.get_active_for_ticket(ticket.id, lock=True) is not None:
                    codes.append("ACTIVE_ASSIGNMENT_EXISTS")
                # A group member whose category differs from the unit's would
                # need a different skill from the one just validated.
                if unit.category_id is not None and ticket.category_id != unit.category_id:
                    codes.append(PlacementWarningCode.MISSING_SKILL.value)
            if codes:
                failures.append(
                    {
                        "unit_id": unit.unit_id,
                        "technician_id": str(technician_id),
                        "codes": sorted(set(codes)),
                    }
                )

        if failures:
            raise DomainError(
                VISUAL_PLACEMENT_INVALID,
                "Một số phân công không hợp lệ. Không có thay đổi nào được lưu.",
                409,
                {"failures": failures},
            )


def _spills_past_today(finish: datetime | None, now: datetime) -> bool:
    if finish is None:
        return False
    return to_local(finish).date() > to_local(now).date()


def _unit_key(unit: BoardUnit) -> UUID:
    kind, _, raw = unit.unit_id.partition(":")
    return UUID(raw)


def _ticket_code(ticket_id: UUID) -> str:
    return f"PA-{str(ticket_id).replace('-', '').upper()[:6]}"


def _priority_rank(priority: Priority) -> int:
    """Most urgent first, for picking a group's headline priority.

    P5 is included even though no P5 reaches the board: this function is also
    the tie-break for a case whose members were re-scored mid-render, and a
    KeyError there would take down the whole board over one row.
    """
    return DOMAIN_PRIORITY_RANK[Priority.P5] - DOMAIN_PRIORITY_RANK[priority]


__all__ = [
    "ADVISORY_WARNINGS",
    "Board",
    "BoardTechnician",
    "BoardUnit",
    "ConfirmResult",
    "PlacementPreview",
    "VisualAssignmentService",
]
