"""HTTP-level coverage for GET /api/v1/tickets.

The service-layer tests exercise filtering and grouping directly. This module
exists because the route is where the wiring can go wrong without any service
test noticing — `list_my_tickets(profile, page, page, page_size, ...)` passed
`page` twice and still returned rows, so every page looked like page 1's size.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.dependencies.auth import CurrentActor, require_resident
from src.api.dependencies.database import get_db
from src.main import app
from src.models.enums import AssignmentStatus, ClassificationStatus, Priority, TicketStatus
from src.security.supabase_jwt import AuthenticatedPrincipal
from tests.test_workflow.factories import build_world, make_assignment, make_ticket


def _as_utc(value: str) -> datetime:
    """Parse a serialized timestamp; SQLite may hand back a naive one."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@pytest.fixture
def world(db_session):
    return build_world(db_session, resident_count=2, technician_count=1)


@pytest_asyncio.fixture
async def resident_client(db_session, world):
    """A client authenticated as the first resident, on the test database."""
    resident = world.resident(0)
    actor = CurrentActor(
        actor_type="resident",
        user=resident.user,
        principal=AuthenticatedPrincipal(
            auth_user_id=resident.user_id,
            email=None,
            phone="+84900000000",
            issuer="test",
            audience="authenticated",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        resident_profile=resident,
    )
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[require_resident] = lambda: actor
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_pagination_returns_distinct_pages(resident_client, world):
    now = datetime.now(UTC)
    for index in range(5):
        make_ticket(world, created_at=now - timedelta(minutes=index))

    first = await resident_client.get("/api/v1/tickets", params={"page": 1, "page_size": 2})
    second = await resident_client.get("/api/v1/tickets", params={"page": 2, "page_size": 2})
    third = await resident_client.get("/api/v1/tickets", params={"page": 3, "page_size": 2})

    assert first.status_code == 200
    first_ids = [item["id"] for item in first.json()["data"]["items"]]
    second_ids = [item["id"] for item in second.json()["data"]["items"]]
    third_ids = [item["id"] for item in third.json()["data"]["items"]]

    assert len(first_ids) == 2
    assert len(second_ids) == 2
    assert len(third_ids) == 1
    # The regression: page 2 used to repeat page 1 because `page` was passed
    # where `page_size` was expected.
    assert not set(first_ids) & set(second_ids)
    assert len({*first_ids, *second_ids, *third_ids}) == 5
    assert first.json()["data"]["total"] == 5
    assert second.json()["data"]["page"] == 2


@pytest.mark.asyncio
async def test_page_size_is_honoured_independently_of_page(resident_client, world):
    for _ in range(4):
        make_ticket(world)

    response = await resident_client.get("/api/v1/tickets", params={"page": 2, "page_size": 3})

    body = response.json()["data"]
    assert body["page"] == 2
    assert body["page_size"] == 3
    assert len(body["items"]) == 1


@pytest.mark.asyncio
async def test_status_group_filter_is_applied_by_the_backend(resident_client, world):
    make_ticket(world, status=TicketStatus.NEW)
    make_ticket(world, status=TicketStatus.IN_PROGRESS)
    make_ticket(world, status=TicketStatus.COMPLETED)

    active = await resident_client.get("/api/v1/tickets", params={"status_group": "ACTIVE"})
    finished = await resident_client.get("/api/v1/tickets", params={"status_group": "FINISHED"})
    everything = await resident_client.get("/api/v1/tickets")

    assert active.json()["data"]["total"] == 2
    assert finished.json()["data"]["total"] == 1
    assert everything.json()["data"]["total"] == 3
    assert all(item["lifecycle_group"] == "ACTIVE" for item in active.json()["data"]["items"])
    assert all(item["lifecycle_group"] == "FINISHED" for item in finished.json()["data"]["items"])


@pytest.mark.asyncio
async def test_category_filter_narrows_the_total(resident_client, world):
    make_ticket(world, category=world.water)
    make_ticket(world, category=world.water)
    make_ticket(world, category=world.electrical)

    response = await resident_client.get("/api/v1/tickets", params={"category_id": str(world.water.id)})

    assert response.json()["data"]["total"] == 2


@pytest.mark.asyncio
async def test_cards_carry_location_and_sender_but_never_a_phone_number(resident_client, world):
    world.resident(0).user.phone_e164 = "+84901234567"
    world.db.commit()
    make_ticket(world, location=world.elevator_a)

    response = await resident_client.get("/api/v1/tickets")
    payload = response.json()["data"]["items"][0]

    assert payload["location_label"] == "Thang máy A"
    assert payload["reporter_name"] == "Cư dân 0"
    assert payload["is_reporter"] is True
    assert "+84901234567" not in response.text
    assert "phone" not in response.text


@pytest.mark.asyncio
async def test_rejected_report_offers_no_supplement_action(resident_client, world):
    make_ticket(world, status=TicketStatus.WAITING_RESIDENT_INFO)

    response = await resident_client.get("/api/v1/tickets")
    payload = response.json()["data"]["items"][0]

    assert "SUPPLEMENT_INFORMATION" not in payload["available_actions"]
    assert "SUPPLEMENT_INFORMATION" not in response.text


@pytest.mark.asyncio
async def test_invalid_page_size_is_rejected(resident_client):
    response = await resident_client.get("/api/v1/tickets", params={"page_size": 500})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_finds_a_report_by_its_visible_code(resident_client, world):
    wanted = make_ticket(world, description="Đèn hành lang tầng 1 bị tắt hoàn toàn.")
    make_ticket(world, description="Nước rò rỉ ở tầng hầm.")
    listed = await resident_client.get("/api/v1/tickets", params={"search": "hành lang"})
    code = listed.json()["data"]["items"][0]["display_code"]

    response = await resident_client.get("/api/v1/tickets", params={"search": code})

    body = response.json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(wanted.id)


@pytest.mark.asyncio
async def test_search_also_matches_the_description(resident_client, world):
    make_ticket(world, description="Đèn hành lang tầng 1 bị tắt hoàn toàn.")
    make_ticket(world, description="Nước rò rỉ ở tầng hầm.")

    response = await resident_client.get("/api/v1/tickets", params={"search": "hành lang"})

    assert response.json()["data"]["total"] == 1


@pytest.mark.asyncio
async def test_search_narrows_the_total_before_paging(resident_client, world):
    for index in range(4):
        make_ticket(world, description=f"Thang máy kêu to {index}")
    make_ticket(world, description="Vòi nước hỏng")

    response = await resident_client.get("/api/v1/tickets", params={"search": "Thang máy", "page_size": 2})

    body = response.json()["data"]
    assert body["total"] == 4
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_display_code_is_the_same_reference_in_the_list_and_the_detail(resident_client, world):
    ticket = make_ticket(world)

    listed = await resident_client.get("/api/v1/tickets")
    detail = await resident_client.get(f"/api/v1/tickets/{ticket.id}")

    code = listed.json()["data"]["items"][0]["display_code"]
    # One reference, derived from the id, shown wherever the report appears.
    assert code == f"PA-{str(ticket.id).replace('-', '')[:6].upper()}"
    assert detail.json()["data"]["display_code"] == code


@pytest.mark.asyncio
async def test_a_resident_is_never_promised_a_completion_time(resident_client, world):
    """§4: the completion promise is gone, not renamed.

    The old payload carried `expected_resolution_at` and
    `estimated_resolution_text`, both of which told a resident when their report
    would be *finished*. §4 replaces that with an expected *start* and a
    description of what is happening now, so the two old keys must be absent
    rather than repurposed -- a client still reading them would otherwise keep
    showing a promise nobody is making.
    """
    make_ticket(
        world,
        status=TicketStatus.APPROVED,
        classification_status=ClassificationStatus.RESOLVED,
        priority=Priority.P1,
        sla_due_at=datetime.now(UTC) + timedelta(hours=5),
    )

    payload = (await resident_client.get("/api/v1/tickets")).json()["data"]["items"][0]

    assert "expected_resolution_at" not in payload
    assert "estimated_resolution_text" not in payload
    # `planned_finish_at` is internal capacity arithmetic (§4) and must not
    # reach a resident under any name.
    assert "planned_finish_at" not in payload
    assert "sla_due_at" not in payload


@pytest.mark.asyncio
async def test_progress_text_follows_the_state_the_report_is_actually_in(resident_client, world):
    """§4's progression: a technician is on it, then they are handling it.

    Two states, not three. The resident is never told to wait for a technician
    to confirm, because there is nothing left for a technician to confirm.
    """
    technician = world.technician(0)
    ticket = make_ticket(
        world,
        status=TicketStatus.APPROVED,
        classification_status=ClassificationStatus.RESOLVED,
        priority=Priority.P1,
    )
    assignment = make_assignment(world, ticket, technician)
    assignment.planned_start_at = datetime.now(UTC) + timedelta(hours=2)
    world.db.commit()

    payload = (await resident_client.get(f"/api/v1/tickets/{ticket.id}")).json()["data"]
    assert payload["display_status"] == "Đã gán kỹ thuật viên"
    assert payload["progress_text"] == "Đã có kỹ thuật viên, chờ tới lịch xử lý"
    # The expected start is available immediately -- the assignment is real work
    # on a real queue from the moment it is written.
    assert _as_utc(payload["expected_start_at"]) == _as_utc(assignment.planned_start_at.isoformat())
    # No acceptance vocabulary reaches the resident, in any field.
    assert "acceptance_due_at" not in payload
    assert "nhận việc" not in payload["progress_text"]

    ticket.status = TicketStatus.IN_PROGRESS
    assignment.status = AssignmentStatus.IN_PROGRESS
    world.db.commit()

    payload = (await resident_client.get(f"/api/v1/tickets/{ticket.id}")).json()["data"]
    assert payload["progress_text"] == "Kỹ thuật viên đang xử lý"
    # Once work has started, a start estimate is history rather than news.
    assert payload["expected_start_at"] is None


@pytest.mark.asyncio
async def test_the_resident_payload_never_carries_internal_planning(resident_client, world):
    """§5: planned finish, slack and risk are Building Management's, not theirs."""
    technician = world.technician(0)
    ticket = make_ticket(
        world,
        status=TicketStatus.APPROVED,
        classification_status=ClassificationStatus.RESOLVED,
        priority=Priority.P1,
    )
    assignment = make_assignment(world, ticket, technician)
    assignment.planned_start_at = datetime.now(UTC) + timedelta(hours=2)
    assignment.planned_finish_at = datetime.now(UTC) + timedelta(hours=6)
    assignment.planned_order = 0
    assignment.risk_state = "AT_RISK"
    assignment.slack_seconds = -3600
    world.db.commit()

    payload = (await resident_client.get(f"/api/v1/tickets/{ticket.id}")).json()["data"]

    for internal in ("planned_finish_at", "planned_order", "risk_state", "slack_seconds"):
        assert internal not in payload


@pytest.mark.asyncio
async def test_no_start_estimate_while_analysing_or_once_finished(resident_client, world):
    make_ticket(world, classification_status=ClassificationStatus.PROCESSING)
    make_ticket(
        world,
        status=TicketStatus.COMPLETED,
        classification_status=ClassificationStatus.RESOLVED,
        priority=Priority.P1,
    )

    items = (await resident_client.get("/api/v1/tickets")).json()["data"]["items"]

    assert all(item["expected_start_at"] is None for item in items)
    assert {item["progress_text"] for item in items} == {"Đang phân tích phản ánh...", "Đã hoàn thành"}
