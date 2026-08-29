"""Ticket assignment persistence."""

from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.orm import Session, joinedload, selectinload

from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.domain.assignment_transitions import ACTIVE_ASSIGNMENT_STATUSES
from src.models.enums import AssignmentSource, Priority


class AssignmentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_assignment(
        self,
        *,
        ticket_id: UUID,
        technician_id: UUID,
        assigned_by_user_id: UUID | None,
        assignment_source: str = AssignmentSource.COORDINATOR_MANUAL.value,
        dispatch_event_id: UUID | None = None,
    ) -> TicketAssignment:
        row = TicketAssignment(
            ticket_id=ticket_id,
            technician_id=technician_id,
            assigned_by_user_id=assigned_by_user_id,
            assignment_source=assignment_source,
            dispatch_event_id=dispatch_event_id,
            is_active=True,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def list_active_for_technician(self, technician_id: UUID) -> list[TicketAssignment]:
        """The technician's live queue, ordered the way §4 asks it to be shown.

        `planned_order` first, so "Do now" and "Next" follow the scheduler
        rather than the order things happened to be created. Assignments with no
        planned order -- written before scheduling existed, or by a path that
        did not simulate -- sort last rather than at the front, which is what a
        plain ascending sort on a nullable column does on SQLite but not on
        PostgreSQL. Spelling it out keeps the two databases agreeing.
        """
        return list(
            self.db.scalars(
                self._base_query()
                .where(
                    TicketAssignment.technician_id == technician_id,
                    TicketAssignment.is_active.is_(True),
                    TicketAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                )
                .order_by(
                    TicketAssignment.planned_order.is_(None),
                    TicketAssignment.planned_order.asc(),
                    TicketAssignment.assigned_at.asc(),
                    TicketAssignment.id,
                )
            )
        )

    def get_active_for_ticket(self, ticket_id: UUID, *, lock: bool = False) -> TicketAssignment | None:
        query = select(TicketAssignment).where(TicketAssignment.ticket_id == ticket_id, TicketAssignment.is_active.is_(True))
        if lock:
            query = query.with_for_update()
        return self.db.scalar(query)

    def list_for_technician(self, technician_id: UUID) -> list[TicketAssignment]:
        priority_order = case(
            (Ticket.priority == Priority.P3, 0),
            (Ticket.priority == Priority.P2, 1),
            (Ticket.priority == Priority.P1, 2),
            else_=3,
        )
        return list(
            self.db.scalars(
                self._base_query()
                .where(TicketAssignment.technician_id == technician_id)
                .join(Ticket, Ticket.id == TicketAssignment.ticket_id)
                .order_by(priority_order, TicketAssignment.assigned_at.asc(), TicketAssignment.id)
            )
        )

    def get_for_technician(
        self,
        assignment_id: UUID,
        technician_id: UUID,
        *,
        lock: bool = False,
    ) -> TicketAssignment | None:
        query = self._base_query().where(
            TicketAssignment.id == assignment_id,
            TicketAssignment.technician_id == technician_id,
        )
        if lock:
            query = query.with_for_update(of=TicketAssignment)
        return self.db.scalar(query)

    def _base_query(self):
        return select(TicketAssignment).options(
            joinedload(TicketAssignment.ticket).options(
                joinedload(Ticket.category),
                joinedload(Ticket.location),
                selectinload(Ticket.attachments),
                selectinload(Ticket.status_history),
            ),
            joinedload(TicketAssignment.technician),
        ).execution_options(populate_existing=True)
