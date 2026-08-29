"""Coordinator incident cluster routes."""

from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from src.api.dependencies.auth import CurrentActor, require_coordinator
from src.api.dependencies.database import get_db
from src.api.routes.coordinator._common import ok
from src.database.models.category import CategoryCatalog
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.location import Location
from src.database.models.technician import TechnicianProfile
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.models.api.common import ApiResponse
from src.models.api.coordinator import (
    AssignTicketRequest,
    CoordinatorClusterApproveResponse,
    CoordinatorClusterAssignResponse,
    CoordinatorClusterResponse,
    CoordinatorClusterTicketResponse,
)
from src.models.api.errors import INVALID_STATUS_TRANSITION, TICKET_NOT_FOUND, DomainError
from src.models.enums import TicketStatus
from src.services.agent_common import GROUPING_CODES
from src.services.assignment_service import AssignmentService
from src.services.coordinator_service import CoordinatorService

router = APIRouter()

TERMINAL_STATUSES = {
    TicketStatus.COMPLETED,
    TicketStatus.CANCELLED,
    TicketStatus.INVALID,
    TicketStatus.UNRESOLVABLE,
}


def _ticket_code(ticket_id: UUID) -> str:
    return f"PA-{str(ticket_id).replace('-', '').upper()[:6]}"


def _active_assignment(ticket: Ticket) -> TicketAssignment | None:
    active = [assignment for assignment in ticket.assignments if assignment.is_active]
    if not active:
        return None
    return max(active, key=lambda assignment: assignment.assigned_at)


def _can_join_open_cluster(ticket: Ticket) -> bool:
    return ticket.status not in TERMINAL_STATUSES and _active_assignment(ticket) is None


def _floor_label(tickets: list[Ticket]) -> str:
    floors = [
        ticket.location.floor
        for ticket in tickets
        if ticket.location is not None and ticket.location.floor is not None
    ]
    if not floors:
        return "Chưa xác định"

    ordered = sorted(floors, key=lambda item: item.adjacency_index)
    if ordered[0].id == ordered[-1].id:
        return ordered[0].display_name
    return f"{ordered[0].display_name} - {ordered[-1].display_name}"


def _cluster_ticket_response(ticket: Ticket) -> CoordinatorClusterTicketResponse:
    assignment = _active_assignment(ticket)
    return CoordinatorClusterTicketResponse(
        id=ticket.id,
        display_code=_ticket_code(ticket.id),
        description=ticket.description,
        status=ticket.status,
        priority=ticket.priority,
        location_label=ticket.location.label if ticket.location else None,
        unit_code=ticket.source_unit.unit_code if ticket.source_unit else None,
        floor_label=(
            ticket.location.floor.display_name
            if ticket.location and ticket.location.floor
            else None
        ),
        created_at=ticket.created_at,
        active_assignment_id=assignment.id if assignment else None,
        active_assignment_status=assignment.status if assignment else None,
        active_technician_id=assignment.technician_id if assignment else None,
        active_technician_name=(
            assignment.technician.user.full_name
            if assignment and assignment.technician and assignment.technician.user
            else None
        ),
    )


def _cluster_response(case: IncidentCase) -> CoordinatorClusterResponse:
    tickets = sorted(
        [member.ticket for member in case.members if member.ticket is not None],
        key=lambda item: item.created_at,
        reverse=True,
    )
    closed = bool(tickets) and all(ticket.status in TERMINAL_STATUSES for ticket in tickets)
    return CoordinatorClusterResponse(
        id=case.id,
        category_id=case.category_id,
        category=case.category.display_name if case.category else "Chưa xác định",
        floor_label=_floor_label(tickets),
        density=case.density_value,
        status=case.status,
        closed=closed,
        window_start=case.window_start,
        window_end=case.window_end,
        created_at=case.created_at,
        tickets=[_cluster_ticket_response(ticket) for ticket in tickets],
    )


def _cluster_id_for_tickets(tickets: list[Ticket]) -> UUID:
    seed = "|".join(sorted(str(ticket.id) for ticket in tickets))
    return uuid5(NAMESPACE_URL, f"fixit-derived-cluster:{seed}")


def _same_cluster_window(seed: Ticket, candidate: Ticket) -> bool:
    if seed.location is None or candidate.location is None:
        return False
    if seed.location.floor is None or candidate.location.floor is None:
        return False
    if abs(seed.location.floor.adjacency_index - candidate.location.floor.adjacency_index) > 1:
        return False
    return abs(seed.created_at - candidate.created_at) <= timedelta(days=3)


def _derived_cluster_response(tickets: list[Ticket]) -> CoordinatorClusterResponse:
    ordered = sorted(tickets, key=lambda item: item.created_at, reverse=True)
    seed = ordered[0]
    density = len({ticket.source_unit_id for ticket in ordered})
    closed = all(ticket.status in TERMINAL_STATUSES for ticket in ordered)
    return CoordinatorClusterResponse(
        id=_cluster_id_for_tickets(ordered),
        category_id=seed.category_id,
        category=seed.category.display_name if seed.category else "Chưa xác định",
        floor_label=_floor_label(ordered),
        density=density,
        status="DERIVED",
        closed=closed,
        window_start=min(ticket.created_at for ticket in ordered),
        window_end=max(ticket.created_at for ticket in ordered),
        created_at=max(ticket.created_at for ticket in ordered),
        tickets=[_cluster_ticket_response(ticket) for ticket in ordered],
    )


def _derived_cluster_groups(
    db: Session,
    *,
    excluded_ticket_ids: set[UUID],
) -> list[list[Ticket]]:
    rows = list(
        db.scalars(
            select(Ticket)
            .join(Ticket.category)
            .where(
                Ticket.category_id.is_not(None),
                CategoryCatalog.code.in_(GROUPING_CODES),
                Ticket.id.not_in(excluded_ticket_ids) if excluded_ticket_ids else True,
            )
            .options(
                joinedload(Ticket.category),
                joinedload(Ticket.location).joinedload(Location.floor),
                joinedload(Ticket.source_unit),
                selectinload(Ticket.assignments)
                .joinedload(TicketAssignment.technician)
                .joinedload(TechnicianProfile.user),
            )
            .order_by(Ticket.created_at.desc())
            .limit(300)
        )
    )

    buckets: dict[UUID, list[Ticket]] = {}
    for ticket in rows:
        if ticket.location is None or ticket.location.floor is None:
            continue
        if not _can_join_open_cluster(ticket):
            continue
        buckets.setdefault(ticket.category_id, []).append(ticket)

    clusters: list[list[Ticket]] = []
    for bucket in buckets.values():
        used: set[UUID] = set()
        for seed in sorted(bucket, key=lambda item: item.created_at, reverse=True):
            if seed.id in used:
                continue
            grouped = [
                ticket
                for ticket in bucket
                if ticket.id not in used and _same_cluster_window(seed, ticket)
            ]
            if len(grouped) < 2:
                continue
            used.update(ticket.id for ticket in grouped)
            clusters.append(grouped)

    return sorted(clusters, key=lambda items: max(ticket.created_at for ticket in items), reverse=True)


def _derived_cluster_responses(
    db: Session,
    *,
    excluded_ticket_ids: set[UUID],
    limit: int,
) -> list[CoordinatorClusterResponse]:
    if limit <= 0:
        return []
    return [
        _derived_cluster_response(group)
        for group in _derived_cluster_groups(db, excluded_ticket_ids=excluded_ticket_ids)[:limit]
    ]


def _materialized_case_tickets(db: Session, case_id: UUID) -> list[Ticket] | None:
    case = db.scalar(
        select(IncidentCase)
        .where(IncidentCase.id == case_id)
        .options(
            selectinload(IncidentCase.members)
            .joinedload(IncidentCaseMember.ticket)
            .joinedload(Ticket.location)
            .joinedload(Location.floor),
            selectinload(IncidentCase.members)
            .joinedload(IncidentCaseMember.ticket)
            .joinedload(Ticket.source_unit),
            selectinload(IncidentCase.members)
            .joinedload(IncidentCaseMember.ticket)
            .selectinload(Ticket.assignments)
            .joinedload(TicketAssignment.technician)
            .joinedload(TechnicianProfile.user),
            selectinload(IncidentCase.members).joinedload(IncidentCaseMember.ticket).joinedload(Ticket.category),
        )
    )
    if case is None:
        return None
    return [member.ticket for member in case.members if member.ticket is not None]


def _cluster_tickets(db: Session, case_id: UUID) -> list[Ticket]:
    tickets = _materialized_case_tickets(db, case_id)
    if tickets is not None:
        return tickets
    for group in _derived_cluster_groups(db, excluded_ticket_ids=set()):
        if _cluster_id_for_tickets(group) == case_id:
            return group
    raise DomainError(TICKET_NOT_FOUND, "Không tìm thấy cụm ticket.", 404)


@router.get("/clusters", response_model=ApiResponse[list[CoordinatorClusterResponse]])
def list_clusters(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
    include_closed: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=200),
):
    query = (
        select(IncidentCase)
        .options(
            joinedload(IncidentCase.category),
            selectinload(IncidentCase.members)
            .joinedload(IncidentCaseMember.ticket)
            .joinedload(Ticket.location)
            .joinedload(Location.floor),
            selectinload(IncidentCase.members)
            .joinedload(IncidentCaseMember.ticket)
            .joinedload(Ticket.source_unit),
            selectinload(IncidentCase.members)
            .joinedload(IncidentCaseMember.ticket)
            .selectinload(Ticket.assignments)
            .joinedload(TicketAssignment.technician)
            .joinedload(TechnicianProfile.user),
        )
        .order_by(IncidentCase.status.asc(), IncidentCase.created_at.desc())
        .limit(limit)
    )
    materialized = [_cluster_response(row) for row in db.scalars(query)]
    materialized_ticket_ids = {
        ticket.id
        for case in materialized
        for ticket in case.tickets
    }
    cases = [
        *materialized,
        *_derived_cluster_responses(
            db,
            excluded_ticket_ids=materialized_ticket_ids,
            limit=limit - len(materialized),
        ),
    ]
    if not include_closed:
        cases = [case for case in cases if not case.closed]
    return ok(request, cases)


@router.post("/clusters/{case_id}/approve", response_model=ApiResponse[CoordinatorClusterApproveResponse])
def approve_cluster(
    request: Request,
    case_id: UUID,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    """Approve every member ticket that is individually approvable.

    Unlike `assign_cluster`, a partial outcome here is intentional, not a
    bug: a member still needing manual review (missing Category/Priority, or
    a P0 caught at extraction) is skipped rather than blocking the members
    that are ready. That member's `NEW` status is what keeps the *whole*
    case in the coordinator's "chờ duyệt" queue -- `case_draft` on the
    assignment side (and the frontend's `assignmentSourceQueues`) only treat
    a case as ready once *every* member is independently eligible, so an
    incompletely-approved case is never promoted to "chờ phân việc" by
    accident; it just sits here, visibly still needing the last member.
    """
    tickets = _cluster_tickets(db, case_id)
    service = CoordinatorService(db)
    approved_ticket_ids: list[UUID] = []
    skipped_ticket_ids: list[UUID] = []

    for ticket in sorted(tickets, key=lambda item: item.created_at):
        if ticket.status != TicketStatus.NEW:
            skipped_ticket_ids.append(ticket.id)
            continue
        try:
            service.approve(actor.user.user_id, ticket.id)
        except DomainError:
            skipped_ticket_ids.append(ticket.id)
            continue
        approved_ticket_ids.append(ticket.id)

    if not approved_ticket_ids:
        raise DomainError(
            INVALID_STATUS_TRANSITION,
            "Không có ticket nào trong cụm có thể duyệt.",
            409,
            {"skipped_ticket_ids": [str(item) for item in skipped_ticket_ids]},
        )

    return ok(
        request,
        CoordinatorClusterApproveResponse(
            case_id=case_id,
            approved_ticket_ids=approved_ticket_ids,
            skipped_ticket_ids=skipped_ticket_ids,
        ),
    )


@router.post("/clusters/{case_id}/assign", response_model=ApiResponse[CoordinatorClusterAssignResponse])
def assign_cluster(
    request: Request,
    case_id: UUID,
    body: AssignTicketRequest,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    """Assign every ticket in the cluster to one technician, all or nothing.

    This used to assign whatever tickets it could and report the rest as
    `skipped_ticket_ids` -- a coordinator asking for "one technician for this
    whole case" could get half a case handed out and half left behind with no
    single reason why. `AssignmentService.assign_case` checks every member's
    preconditions under lock before writing anything, so a rejection here
    always leaves the case exactly as it was; `skipped_ticket_ids` stays on
    the response shape for API compatibility but is always empty now.
    """
    tickets = _cluster_tickets(db, case_id)
    service = AssignmentService(db)
    assignments = service.assign_case(actor.user.user_id, [ticket.id for ticket in tickets], body.technician_id)

    return ok(
        request,
        CoordinatorClusterAssignResponse(
            case_id=case_id,
            technician_id=body.technician_id,
            assigned_ticket_ids=[assignment.ticket_id for assignment in assignments],
            skipped_ticket_ids=[],
            assignment_ids=[assignment.id for assignment in assignments],
        ),
    )


@router.delete("/clusters/{case_id}/tickets/{ticket_id}", response_model=ApiResponse[CoordinatorClusterResponse])
def remove_ticket_from_cluster(
    request: Request,
    case_id: UUID,
    ticket_id: UUID,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    case = db.scalar(
        select(IncidentCase)
        .where(IncidentCase.id == case_id)
        .options(
            joinedload(IncidentCase.category),
            selectinload(IncidentCase.members)
            .joinedload(IncidentCaseMember.ticket)
            .joinedload(Ticket.location)
            .joinedload(Location.floor),
            selectinload(IncidentCase.members)
            .joinedload(IncidentCaseMember.ticket)
            .joinedload(Ticket.source_unit),
            selectinload(IncidentCase.members)
            .joinedload(IncidentCaseMember.ticket)
            .selectinload(Ticket.assignments)
            .joinedload(TicketAssignment.technician)
            .joinedload(TechnicianProfile.user),
        )
    )
    if case is None:
        raise DomainError(
            INVALID_STATUS_TRANSITION,
            "Cụm suy luận chưa thể tách ticket vĩnh viễn.",
            409,
        )

    member = db.scalar(
        select(IncidentCaseMember).where(
            IncidentCaseMember.case_id == case_id,
            IncidentCaseMember.ticket_id == ticket_id,
        )
    )
    if member is None:
        raise DomainError(TICKET_NOT_FOUND, "Ticket không nằm trong cụm này.", 404)

    remaining = [item for item in case.members if item.ticket_id != ticket_id]
    if not remaining:
        # No empty-case policy exists yet: `ck_incident_cases_density_positive`
        # forbids a density of zero and nothing closes or deletes a case that
        # lost every member, so the last ticket stays until that rule is defined.
        raise DomainError(
            INVALID_STATUS_TRANSITION,
            "Không thể loại ticket cuối cùng khỏi cụm khi hệ thống chưa có quy tắc cho case rỗng.",
            409,
        )

    db.delete(member)
    db.flush()
    case.density_value = max(1, len({item.source_unit_id for item in remaining}))
    db.commit()
    db.refresh(case)

    return ok(request, _cluster_response(case))
