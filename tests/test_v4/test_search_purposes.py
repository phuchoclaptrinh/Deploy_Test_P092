"""`search_related_tickets(purpose=...)` — contract §2.2.

The two purposes answer different questions, so the tests below are mostly
about what each filter *refuses* to return. A DUPLICATE search that leaks a
neighbouring asset, and a GROUPING search that reaches outside the three-day
window, both look like working code right up until they auto-link the wrong
ticket.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.models.agent_schemas_v4 import AgentSearchPurpose, SearchRelatedTicketsRequestV4
from src.models.api.errors import CATEGORY_REQUIRED, DomainError
from src.models.enums import TicketStatus
from src.services.agent_backend_service import AgentBackendService
from tests.test_v4.factories import add_status_history, build_world, make_ticket


class _StorageStub:
    def create_signed_download_url(self, _path):
        return "https://example.invalid/signed"


def _service(db_session):
    return AgentBackendService(db_session, _StorageStub())


def _session_for(db_session, ticket):
    service = _service(db_session)
    return service, service.start_session(ticket.id, model_version="fixit-agent-v4-langgraph-1")


def test_duplicate_search_matches_the_exact_asset_and_not_its_neighbour(db_session):
    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
    )
    # Same building, same floor, same Category — different lift.
    other_asset = make_ticket(
        world,
        resident=world.resident(2),
        location=world.elevator_b,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
    )
    new_ticket = make_ticket(world, resident=world.resident(0), location=world.elevator_a, category=world.elevator)

    service, session = _session_for(db_session, new_ticket)
    response = service.search_related_tickets(
        session.id,
        ticket_id=new_ticket.id,
        category_ids=[world.elevator.id],
        purpose=AgentSearchPurpose.DUPLICATE.value,
    )

    ids = {item["ticket_id"] for item in response["related_tickets"]}
    assert response["purpose"] == "DUPLICATE"
    assert str(master.id) in ids
    assert str(other_asset.id) not in ids


def test_duplicate_search_ignores_the_three_day_window_and_closed_tickets(db_session):
    world = build_world(db_session)
    now = datetime.now(UTC)
    # §2.2: a master still being worked on stays a master however old it is.
    old_but_live = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
        created_at=now - timedelta(days=30),
    )
    finished = make_ticket(
        world,
        resident=world.resident(2),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.COMPLETED,
    )
    new_ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)

    service, session = _session_for(db_session, new_ticket)
    response = service.search_related_tickets(
        session.id,
        ticket_id=new_ticket.id,
        category_ids=[world.elevator.id],
        purpose="DUPLICATE",
    )

    ids = {item["ticket_id"] for item in response["related_tickets"]}
    assert str(old_but_live.id) in ids
    assert str(finished.id) not in ids


def test_duplicate_search_returns_any_category_on_the_same_asset(db_session):
    """§2.2: DUPLICATE does not filter by Category.

    Whether two reports are the same incident is the Agent's judgement, and the
    same lift can be reported under the elevator Category by one resident and
    the electrical Category by another.
    """
    world = build_world(db_session)
    other_category = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.electrical,
        status=TicketStatus.APPROVED,
    )
    new_ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)

    service, session = _session_for(db_session, new_ticket)
    response = service.search_related_tickets(
        session.id,
        ticket_id=new_ticket.id,
        category_ids=[world.elevator.id],
        purpose="DUPLICATE",
    )

    assert str(other_category.id) in {item["ticket_id"] for item in response["related_tickets"]}


def test_duplicate_search_normalizes_a_duplicate_candidate_onto_its_master(db_session):
    """§1.5 item 7: the Agent never sees a candidate that is itself a duplicate."""
    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
    )
    linked = make_ticket(
        world,
        resident=world.resident(2),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.APPROVED,
    )
    # Status and master move together: ck_tickets_linked_duplicate_needs_master
    # rejects a LINKED_DUPLICATE row that points at nothing.
    linked.duplicate_of_ticket_id = master.id
    linked.status = TicketStatus.LINKED_DUPLICATE
    db_session.commit()

    new_ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service, session = _session_for(db_session, new_ticket)
    response = service.search_related_tickets(
        session.id, ticket_id=new_ticket.id, category_ids=[world.elevator.id], purpose="DUPLICATE"
    )

    ids = [item["ticket_id"] for item in response["related_tickets"]]
    assert ids.count(str(master.id)) == 1
    assert str(linked.id) not in ids


def test_duplicate_search_returns_the_evidence_the_contract_requires(db_session):
    world = build_world(db_session)
    due = datetime.now(UTC) + timedelta(hours=3)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
        sla_due_at=due,
    )
    add_status_history(world, master, [TicketStatus.APPROVED, TicketStatus.IN_PROGRESS])
    new_ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)

    service, session = _session_for(db_session, new_ticket)
    response = service.search_related_tickets(
        session.id, ticket_id=new_ticket.id, category_ids=[world.elevator.id], purpose="DUPLICATE"
    )
    hit = next(item for item in response["related_tickets"] if item["ticket_id"] == str(master.id))

    assert hit["location_id"] == str(world.elevator_a.id)
    assert hit["status"] == TicketStatus.IN_PROGRESS.value
    assert hit["current_due_at"] is not None
    assert [entry["status"] for entry in hit["status_history"]] == ["APPROVED", "IN_PROGRESS"]
    assert all("changed_at" in entry for entry in hit["status_history"])


def test_search_never_leaks_reporter_text_or_coordinator_notes(db_session):
    """§2.2: no name, phone, source unit, full text or photo of the earlier ticket."""
    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
        description="Tôi là Nguyễn Văn A, căn hộ A-1001, số 0901234567, thang máy kẹt.",
    )
    add_status_history(world, master, [TicketStatus.APPROVED])
    new_ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)

    service, session = _session_for(db_session, new_ticket)
    response = service.search_related_tickets(
        session.id, ticket_id=new_ticket.id, category_ids=[world.elevator.id], purpose="DUPLICATE"
    )

    blob = repr(response)
    assert "Nguyễn Văn A" not in blob
    assert "0901234567" not in blob
    assert "A-1001" not in blob
    assert "Ghi chú nội bộ" not in blob
    assert str(master.reporter_user_id) not in blob
    assert str(master.source_unit_id) not in blob


def test_grouping_search_keeps_the_v3_filter(db_session):
    world = build_world(db_session)
    now = datetime.now(UTC)
    adjacent = make_ticket(
        world,
        resident=world.resident(1),
        location=world.corridor_11,
        category=world.water,
        status=TicketStatus.APPROVED,
        created_at=now - timedelta(hours=2),
    )
    too_old = make_ticket(
        world,
        resident=world.resident(2),
        location=world.corridor_11,
        category=world.water,
        status=TicketStatus.APPROVED,
        created_at=now - timedelta(days=5),
    )
    new_ticket = make_ticket(world, location=world.corridor_10, category=world.water, created_at=now)

    service, session = _session_for(db_session, new_ticket)
    response = service.search_related_tickets(
        session.id, ticket_id=new_ticket.id, category_ids=[world.water.id], purpose="GROUPING"
    )

    ids = {item["ticket_id"] for item in response["related_tickets"]}
    assert response["purpose"] == "GROUPING"
    assert str(adjacent.id) in ids
    assert str(too_old.id) not in ids


def test_grouping_is_the_default_so_the_v3_agent_is_unchanged(db_session):
    world = build_world(db_session)
    now = datetime.now(UTC)
    adjacent = make_ticket(
        world,
        resident=world.resident(1),
        location=world.corridor_11,
        category=world.water,
        status=TicketStatus.APPROVED,
        created_at=now - timedelta(hours=1),
    )
    new_ticket = make_ticket(world, location=world.corridor_10, category=world.water, created_at=now)

    service, session = _session_for(db_session, new_ticket)
    # No purpose argument at all: exactly how the v3 graph calls it.
    response = service.search_related_tickets(
        session.id, ticket_id=new_ticket.id, category_ids=[world.water.id]
    )

    hit = next(item for item in response["related_tickets"] if item["ticket_id"] == str(adjacent.id))
    # The v3 graph reads these two keys; they must survive the v4 additions.
    assert hit["floor"] == "11"
    assert hit["location"] == "Hành lang tầng 11"


def test_agent_cannot_widen_the_radius(db_session):
    """§2.2: building, floor and location come from the ticket, not the request."""
    world = build_world(db_session)
    other_floor = make_ticket(
        world,
        resident=world.resident(1),
        location=world.corridor_11,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
    )
    new_ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)

    service, session = _session_for(db_session, new_ticket)
    response = service.search_related_tickets(
        session.id,
        ticket_id=new_ticket.id,
        category_ids=[world.elevator.id],
        purpose="DUPLICATE",
        # A hostile Agent trying to reach another location. Both are ignored.
        floor="11",
        location="Hành lang tầng 11",
    )

    assert str(other_floor.id) not in {item["ticket_id"] for item in response["related_tickets"]}


def test_unknown_purpose_is_rejected(db_session):
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service, session = _session_for(db_session, ticket)

    with pytest.raises(DomainError) as exc:
        service.search_related_tickets(
            session.id, ticket_id=ticket.id, category_ids=[world.elevator.id], purpose="EVERYTHING"
        )
    assert exc.value.code == CATEGORY_REQUIRED
    assert exc.value.status_code == 400


def test_category_outside_the_pinned_catalog_is_rejected(db_session):
    world = build_world(db_session)
    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service, session = _session_for(db_session, ticket)

    from uuid import uuid4

    with pytest.raises(DomainError) as exc:
        service.search_related_tickets(
            session.id, ticket_id=ticket.id, category_ids=[uuid4()], purpose="DUPLICATE"
        )
    assert exc.value.code == CATEGORY_REQUIRED


def test_every_search_is_recorded_in_the_session_tool_log(db_session):
    """§1.5 item 3: finalize only accepts a master this session actually saw."""
    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
    )
    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service, session = _session_for(db_session, ticket)

    service.search_related_tickets(
        session.id, ticket_id=ticket.id, category_ids=[world.elevator.id], purpose="DUPLICATE"
    )
    db_session.refresh(session)

    call = next(item for item in session.tool_calls if item.tool_name == "search_related_tickets")
    assert call.sanitized_request["purpose"] == "DUPLICATE"
    assert str(master.id) in {row["ticket_id"] for row in call.sanitized_response["related_tickets"]}


def test_v4_tool_adapter_serves_both_purposes(db_session):
    from src.agents.v4.tools import BackendAnalysisToolAdapterV4

    world = build_world(db_session)
    master = make_ticket(
        world,
        resident=world.resident(1),
        location=world.elevator_a,
        category=world.elevator,
        status=TicketStatus.IN_PROGRESS,
    )
    ticket = make_ticket(world, location=world.elevator_a, category=world.elevator)
    service, session = _session_for(db_session, ticket)
    adapter = BackendAnalysisToolAdapterV4(service)

    assert AgentSearchPurpose.DUPLICATE in adapter.supported_purposes
    assert AgentSearchPurpose.GROUPING in adapter.supported_purposes

    response = adapter.search_related_tickets(
        SearchRelatedTicketsRequestV4(
            session_id=session.id,
            ticket_id=ticket.id,
            purpose=AgentSearchPurpose.DUPLICATE,
            category_ids=[world.elevator.id],
        )
    )
    hit = next(item for item in response.related_tickets if item.ticket_id == master.id)
    assert hit.location_id == world.elevator_a.id
    assert hit.status == TicketStatus.IN_PROGRESS.value
