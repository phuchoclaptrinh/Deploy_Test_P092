"""Bulk loading for the scheduler (§8).

The rule this module exists to keep: **the number of queries a pass makes does
not depend on how many tickets are in it.** A micro-batch of twenty tickets and
a micro-batch of one issue the same four statements, because the Supabase
session quota (§8) is spent by concurrency and round-trips, not by row count.

The four:

1. technicians, their user account, and their skills;
2. every active assignment those technicians hold, with the category code and
   ticket facts the scheduler needs to size it;
3. the reassignment exclusions for the tickets in this batch;
4. the category catalog rows for the codes involved.

Everything after that is arithmetic in `src.dispatch.scheduler`, over the frozen
snapshots below. They are frozen deliberately: an ORM instance in this position
would lazy-load on attribute access, and one `ticket.category.code` inside the
scheduler loop would silently reintroduce the per-ticket query the contract
forbids -- while still passing every functional test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models.category import CategoryCatalog
from src.database.models.technician import TechnicianProfile, TechnicianSkill
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.database.models.user_profile import UserProfile
from src.dispatch.durations import p80_for_code
from src.dispatch.eligibility import EligibilityInput
from src.dispatch.scheduler import WorkUnit
from src.dispatch.shift import as_utc
from src.domain.assignment_transitions import ACTIVE_ASSIGNMENT_STATUSES
from src.models.enums import EXCLUDING_END_REASONS, AssignmentStatus


@dataclass(frozen=True)
class TechnicianRow(EligibilityInput):
    """One technician, their skills, and the queue they are already holding."""

    display_name: str = ""
    queue: tuple[WorkUnit, ...] = ()

    @property
    def active_count(self) -> int:
        return len(self.queue)

    @property
    def in_progress_count(self) -> int:
        return sum(1 for unit in self.queue if unit.in_progress)

    @property
    def committed_ticket_ids(self) -> frozenset[UUID]:
        return frozenset(ticket_id for unit in self.queue for ticket_id in unit.ticket_ids)


@dataclass
class World:
    """The bulk-loaded picture one scheduling pass reasons over."""

    technicians: dict[UUID, TechnicianRow] = field(default_factory=dict)
    #: category id -> machine code, for `p80_for_code`.
    category_codes: dict[UUID, str] = field(default_factory=dict)
    #: ticket id -> technicians who already rejected it or let it time out.
    exclusions: dict[UUID, frozenset[UUID]] = field(default_factory=dict)
    #: How many statements produced this. Asserted by the load tests, so a
    #: future N+1 fails the suite instead of only the production latency graph.
    query_count: int = 0

    def eligibility_inputs(self) -> list[EligibilityInput]:
        return list(self.technicians.values())

    @property
    def assigned_ticket_ids(self) -> frozenset[UUID]:
        """Every ticket currently held by somebody, derived from what is loaded.

        The whole technician roster and every active assignment are already in
        memory, so "does this ticket have an active assignment?" is answerable
        without going back to the database -- which is what stops the batch's
        eligibility re-check from costing one statement per ticket (§8).
        """
        return frozenset(
            ticket_id for row in self.technicians.values() for ticket_id in row.committed_ticket_ids
        )

    def queues(self, technician_ids: list[UUID]) -> dict[UUID, list[WorkUnit]]:
        """The current queues of the named technicians, copied.

        Copied because the caller mutates them: placing ticket A onto a
        technician has to be visible when ticket B of the same batch is
        considered, or a batch of twenty would book the same free slot twenty
        times over.
        """
        return {tid: list(self.technicians[tid].queue) for tid in technician_ids if tid in self.technicians}

    def with_placement(self, queues: dict[UUID, list[WorkUnit]], technician_id: UUID, unit: WorkUnit) -> None:
        queues.setdefault(technician_id, []).append(unit)


class DispatchLoader:
    def __init__(self, db: Session) -> None:
        self.db = db

    def load(self, *, ticket_ids: list[UUID] | None = None) -> World:
        """Load the whole world for one pass.

        `ticket_ids` scopes only the exclusion lookup -- the technician roster
        and their existing queues are needed in full regardless, because a
        technician who holds no ticket from this batch still has a day that
        constrains where this batch can go.
        """
        world = World()
        self._load_technicians(world)
        self._load_queues(world)
        self._load_exclusions(world, ticket_ids or [])
        return world

    # ------------------------------------------------------------------

    def _load_technicians(self, world: World) -> None:
        rows = self.db.execute(
            select(
                TechnicianProfile.user_id,
                TechnicianProfile.is_active,
                TechnicianProfile.is_available,
                UserProfile.is_active.label("user_is_active"),
                UserProfile.full_name,
                TechnicianSkill.category_id,
            )
            .join(UserProfile, UserProfile.user_id == TechnicianProfile.user_id)
            # Outer, so a technician with no skills is still loaded. They are
            # eligible for nothing, but the board must still show their column
            # and their workload rather than making them vanish.
            .outerjoin(TechnicianSkill, TechnicianSkill.technician_id == TechnicianProfile.user_id)
            .order_by(TechnicianProfile.user_id)
        ).all()
        world.query_count += 1

        skills: dict[UUID, set[UUID]] = {}
        base: dict[UUID, tuple[bool, bool, bool, str]] = {}
        for user_id, is_active, is_available, user_is_active, full_name, category_id in rows:
            base[user_id] = (bool(is_active), bool(is_available), bool(user_is_active), full_name or "")
            if category_id is not None:
                skills.setdefault(user_id, set()).add(category_id)

        for user_id, (is_active, is_available, user_is_active, full_name) in base.items():
            world.technicians[user_id] = TechnicianRow(
                technician_id=user_id,
                is_active=is_active,
                is_available=is_available,
                user_is_active=user_is_active,
                skill_category_ids=frozenset(skills.get(user_id, set())),
                display_name=full_name,
                queue=(),
            )

    def _load_queues(self, world: World) -> None:
        if not world.technicians:
            return
        rows = self.db.execute(
            select(
                TicketAssignment.id,
                TicketAssignment.technician_id,
                TicketAssignment.status,
                TicketAssignment.started_at,
                TicketAssignment.planned_finish_at,
                Ticket.id.label("ticket_id"),
                Ticket.created_at,
                Ticket.score_total,
                Ticket.category_id,
                CategoryCatalog.code,
            )
            .join(Ticket, Ticket.id == TicketAssignment.ticket_id)
            .outerjoin(CategoryCatalog, CategoryCatalog.id == Ticket.category_id)
            .where(
                TicketAssignment.is_active.is_(True),
                TicketAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                TicketAssignment.technician_id.in_(list(world.technicians)),
            )
            .order_by(TicketAssignment.technician_id, TicketAssignment.assigned_at)
        ).all()
        world.query_count += 1

        queues: dict[UUID, list[WorkUnit]] = {}
        for row in rows:
            if row.category_id is not None and row.code:
                world.category_codes[row.category_id] = row.code
            queues.setdefault(row.technician_id, []).append(
                WorkUnit(
                    key=row.id,
                    ticket_ids=(row.ticket_id,),
                    duration=p80_for_code(row.code),
                    score=Decimal(row.score_total or 0),
                    submitted_at=as_utc(row.created_at),
                    # None for assignments written before scheduling existed, and
                    # for any path that placed work without simulating it. Such a
                    # unit still consumes the technician's time but claims no
                    # deadline, so it can never be the reason a placement is
                    # AT_RISK -- it makes no promise there is anything to break.
                    deadline=as_utc(row.planned_finish_at),
                    assignment_id=row.id,
                    in_progress=row.status == AssignmentStatus.IN_PROGRESS,
                    started_at=as_utc(row.started_at),
                )
            )
        for technician_id, queue in queues.items():
            world.technicians[technician_id] = _with_queue(world.technicians[technician_id], tuple(queue))

    def _load_exclusions(self, world: World, ticket_ids: list[UUID]) -> None:
        if not ticket_ids:
            return
        rows = self.db.execute(
            select(TicketAssignment.ticket_id, TicketAssignment.technician_id)
            .where(
                TicketAssignment.ticket_id.in_(ticket_ids),
                TicketAssignment.is_active.is_(False),
                TicketAssignment.end_reason.in_([reason.value for reason in EXCLUDING_END_REASONS]),
            )
            .distinct()
        ).all()
        world.query_count += 1

        grouped: dict[UUID, set[UUID]] = {}
        for ticket_id, technician_id in rows:
            grouped.setdefault(ticket_id, set()).add(technician_id)
        world.exclusions = {ticket_id: frozenset(ids) for ticket_id, ids in grouped.items()}

    def load_category_codes(self, world: World, category_ids: list[UUID]) -> None:
        """Fill in codes for categories no active assignment happened to cover."""
        missing = [cid for cid in category_ids if cid not in world.category_codes]
        if not missing:
            return
        rows = self.db.execute(
            select(CategoryCatalog.id, CategoryCatalog.code).where(CategoryCatalog.id.in_(missing))
        ).all()
        world.query_count += 1
        for category_id, code in rows:
            world.category_codes[category_id] = code


def _with_queue(row: TechnicianRow, queue: tuple[WorkUnit, ...]) -> TechnicianRow:
    return TechnicianRow(
        technician_id=row.technician_id,
        is_active=row.is_active,
        is_available=row.is_available,
        user_is_active=row.user_is_active,
        skill_category_ids=row.skill_category_ids,
        display_name=row.display_name,
        queue=queue,
    )


__all__ = ["ACTIVE_ASSIGNMENT_STATUSES", "DispatchLoader", "TechnicianRow", "World"]
