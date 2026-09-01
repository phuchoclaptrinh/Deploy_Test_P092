"""Ticket persistence operations for the Self Dev v3 workflow."""

import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import String, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from src.database.models.ai_agent_session import AIAnalysisSession
from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.information_request import InformationRequest
from src.database.models.notification import Notification
from src.database.models.technician import TechnicianProfile
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.database.models.ticket_status_history import TicketStatusHistory
from src.database.models.unit import Unit
from src.models.enums import ClassificationStatus, Priority, TicketLifecycleGroup, TicketStatus
from src.services.resident_lifecycle import apply_lifecycle_group_filter
from src.services.ticket_visibility import published_predicate, resident_visibility_predicate


class TicketRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_ticket(
        self,
        *,
        reporter_user_id: UUID,
        source_unit_id: UUID,
        location_id: UUID,
        description: str | None,
    ) -> Ticket:
        now = datetime.now(UTC)
        ticket = Ticket(
            reporter_user_id=reporter_user_id,
            source_unit_id=source_unit_id,
            location_id=location_id,
            description=description.strip() if description else None,
            status=TicketStatus.NEW,
            classification_status=ClassificationStatus.PROCESSING,
            sla_started_at=now,
        )
        self.db.add(ticket)
        self.db.flush()
        return ticket

    def count_created_by_reporter_since(self, reporter_user_id: UUID, since: datetime) -> int:
        return int(
            self.db.scalar(
                select(func.count())
                .select_from(Ticket)
                .where(Ticket.reporter_user_id == reporter_user_id, Ticket.created_at >= since)
            )
            or 0
        )

    def append_status_history(
        self,
        ticket: Ticket,
        *,
        from_status: TicketStatus | None,
        to_status: TicketStatus,
        changed_by: UUID | None,
        reason: str | None,
    ) -> TicketStatusHistory:
        row = TicketStatusHistory(
            ticket_id=ticket.id,
            from_status=from_status,
            to_status=to_status,
            changed_by=changed_by,
            reason=reason,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def list_resident_tickets(
        self,
        source_unit_id: UUID,
        viewer_user_id: UUID,
        page: int,
        page_size: int,
        *,
        status: TicketStatus | None = None,
        status_group: TicketLifecycleGroup | None = None,
        category_id: UUID | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        search: str | None = None,
    ) -> tuple[list[Ticket], int]:
        """One page of the apartment's reports, filtered and counted in SQL.

        Every filter is applied before `count`, `offset` and `limit`, so `total`
        describes the filtered set and page 2 of a filtered list is not page 2
        of the whole history.

        The visibility predicate is one of those filters: a housemate's report
        that is still in the private AI phase is excluded in SQL, so it neither
        appears in `total` nor consumes a slot on somebody else's page.
        """
        query = select(Ticket).where(resident_visibility_predicate(source_unit_id, viewer_user_id))
        query = self._apply_filters(
            query,
            status=status,
            category_id=category_id,
            priority=None,
            classification_status=None,
            created_from=created_from,
            created_to=created_to,
        )
        query = apply_lifecycle_group_filter(query, status_group)
        query = self._apply_resident_search(query, search)
        total = int(self.db.scalar(select(func.count()).select_from(query.subquery())) or 0)
        items = list(
            self.db.scalars(
                query.options(
                    selectinload(Ticket.attachments),
                    selectinload(Ticket.assignments)
                    .joinedload(TicketAssignment.technician)
                    .joinedload(TechnicianProfile.user),
                    joinedload(Ticket.category),
                    joinedload(Ticket.location),
                    # Card fields. Loaded up front so rendering N cards costs
                    # one query each rather than one per card.
                    joinedload(Ticket.reporter),
                    selectinload(Ticket.duplicate_master),
                )
                # Newest activity first: a report that just moved forward should
                # not sit below an older one that has not changed in days.
                .order_by(Ticket.updated_at.desc(), Ticket.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return items, total

    @staticmethod
    def _apply_resident_search(query, search: str | None):
        """Match the visible report code or the description, in the database.

        Residents read a code like `PA-4A9C21`, which is a slice of the ticket
        UUID. Comparing against the de-hyphenated id text lets a full id, either
        end of it, or the pasted code all find the same row, while the search
        still runs before `count`/`offset`/`limit`.
        """
        term = (search or "").strip()
        if not term:
            return query
        conditions = [Ticket.description.ilike(f"%{term}%")]
        hex_term = re.sub(r"[^0-9a-fA-F]", "", term.removeprefix("PA-").removeprefix("pa-"))
        if len(hex_term) >= 4:
            conditions.append(
                func.replace(func.cast(Ticket.id, String), "-", "").ilike(f"%{hex_term}%")
            )
        return query.where(or_(*conditions))

    def get_resident_ticket(self, source_unit_id: UUID, ticket_id: UUID, *, lock: bool = False) -> Ticket | None:
        """Unit-scoped read with no actor check.

        Used to re-load a row the caller has already been authorized for — the
        report they just created, cancelled or answered. Anything serving a
        client-supplied ticket ID must use :meth:`get_resident_visible_ticket`
        instead, or a housemate can read a private report by direct URL.
        """
        return self._resident_ticket_query(Ticket.source_unit_id == source_unit_id, ticket_id, lock=lock)

    def get_resident_visible_ticket(
        self,
        source_unit_id: UUID,
        viewer_user_id: UUID,
        ticket_id: UUID,
        *,
        lock: bool = False,
    ) -> Ticket | None:
        """The detail-endpoint read: same rule as the list, one row at a time.

        Returns None — not a permission error — when the caller may not see it,
        so an unauthorized read is indistinguishable from a ticket that does not
        exist and cannot be used to probe for one.
        """
        return self._resident_ticket_query(
            resident_visibility_predicate(source_unit_id, viewer_user_id), ticket_id, lock=lock
        )

    def _resident_ticket_query(self, scope, ticket_id: UUID, *, lock: bool = False) -> Ticket | None:
        query = (
            select(Ticket)
            .where(Ticket.id == ticket_id, scope)
            .options(
                selectinload(Ticket.attachments),
                selectinload(Ticket.assignments)
                .joinedload(TicketAssignment.technician)
                .joinedload(TechnicianProfile.user),
                selectinload(Ticket.status_history),
                joinedload(Ticket.category),
                joinedload(Ticket.location),
                joinedload(Ticket.reporter),
                selectinload(Ticket.duplicate_master),
            )
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update(of=Ticket)
        return self.db.scalar(query)

    def list_coordinator_tickets(
        self,
        page: int,
        page_size: int,
        *,
        status: TicketStatus | None = None,
        category_id: UUID | None = None,
        priority: Priority | None = None,
        classification_status: ClassificationStatus | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        search: str | None = None,
    ) -> tuple[list[Ticket], int]:
        """Building Management's queue: published reports only.

        A ticket in the private AI phase has not been handed over yet, so it is
        excluded here — before `count`, `offset` and `limit`, so it cannot show
        up in a total or push a real row onto the next page. Internal workers
        read through :meth:`get_coordinator_ticket`, which stays unfiltered.
        """
        query = self._apply_filters(
            select(Ticket).where(published_predicate()),
            status=status,
            category_id=category_id,
            priority=priority,
            classification_status=classification_status,
            created_from=created_from,
            created_to=created_to,
        )
        if search:
            pattern = f"%{search.strip()}%"
            query = query.where(Ticket.description.ilike(pattern))
        total = int(self.db.scalar(select(func.count()).select_from(query.subquery())) or 0)
        items = list(
            self.db.scalars(
                query.options(
                    selectinload(Ticket.attachments),
                    selectinload(Ticket.assignments)
                    .joinedload(TicketAssignment.technician)
                    .joinedload(TechnicianProfile.user),
                    selectinload(Ticket.status_history),
                    selectinload(Ticket.ai_analysis_runs)
                    .selectinload(AIAnalysisRun.analysis_session)
                    .selectinload(AIAnalysisSession.questions),
                    joinedload(Ticket.category),
                    joinedload(Ticket.location),
                    # Reporter identity, apartment and floor travel with the row:
                    # the coordinator panel shows them for every ticket it opens.
                    joinedload(Ticket.reporter),
                    joinedload(Ticket.source_unit).joinedload(Unit.floor),
                )
                .order_by(Ticket.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return items, total

    def get_coordinator_ticket(self, ticket_id: UUID, *, lock: bool = False) -> Ticket | None:
        """Unscoped read for internal services and workers."""
        return self._coordinator_ticket_query(ticket_id, lock=lock)

    def get_coordinator_visible_ticket(self, ticket_id: UUID, *, lock: bool = False) -> Ticket | None:
        """The human coordinator read: private AI-phase tickets do not exist.

        Returns None rather than raising, so a coordinator guessing a ticket ID
        during analysis gets the same answer as for an ID that was never issued.
        """
        return self._coordinator_ticket_query(ticket_id, lock=lock, published_only=True)

    def _coordinator_ticket_query(
        self, ticket_id: UUID, *, lock: bool = False, published_only: bool = False
    ) -> Ticket | None:
        query = (
            select(Ticket)
            .where(Ticket.id == ticket_id)
            .options(
                selectinload(Ticket.attachments),
                selectinload(Ticket.assignments)
                .joinedload(TicketAssignment.technician)
                .joinedload(TechnicianProfile.user),
                selectinload(Ticket.status_history),
                selectinload(Ticket.information_requests),
                selectinload(Ticket.ai_analysis_runs)
                .selectinload(AIAnalysisRun.analysis_session)
                .selectinload(AIAnalysisSession.questions),
                joinedload(Ticket.category),
                joinedload(Ticket.location),
                joinedload(Ticket.reporter),
                joinedload(Ticket.source_unit).joinedload(Unit.floor),
            )
            .execution_options(populate_existing=True)
        )
        if published_only:
            query = query.where(published_predicate())
        if lock:
            query = query.with_for_update(of=Ticket)
        return self.db.scalar(query)

    def get_information_request(self, ticket_id: UUID, request_id: UUID, *, lock: bool = False) -> InformationRequest | None:
        query = select(InformationRequest).where(
            InformationRequest.id == request_id,
            InformationRequest.ticket_id == ticket_id,
        )
        if lock:
            query = query.with_for_update()
        return self.db.scalar(query)

    def add_notification(self, notification: Notification) -> None:
        self.db.add(notification)
        self.db.flush()

    def _apply_filters(
        self,
        query,
        *,
        status,
        category_id,
        priority,
        classification_status,
        created_from,
        created_to,
    ):
        if status is not None:
            query = query.where(Ticket.status == status)
        if category_id is not None:
            query = query.where(Ticket.category_id == category_id)
        if priority is not None:
            query = query.where(Ticket.priority == priority)
        if classification_status is not None:
            query = query.where(Ticket.classification_status == classification_status)
        if created_from is not None:
            query = query.where(Ticket.created_at >= created_from)
        if created_to is not None:
            query = query.where(Ticket.created_at <= created_to)
        return query
