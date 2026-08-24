"""HTTP coverage for the private-AI-phase visibility rule.

A report belongs to its sender alone while `classification_status` is PENDING or
PROCESSING — the whole time the Agent is analysing and the whole time it is
waiting for an answer to a follow-up question. When classification finishes, by
any route, the report is published: the rest of the apartment may read it and
Building Management takes it over.

These tests drive the real routes rather than the services, because the leak
this rule guards against is a route wiring the wrong actor into a service call:
a list that filters correctly next to a detail endpoint that does not is exactly
how a private ticket becomes readable by direct URL.

The `make_ticket` factory defaults to PROCESSING, so anything meant to be
published says so explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.dependencies.auth import CurrentActor, require_coordinator, require_resident
from src.api.dependencies.database import get_db
from src.api.routes.storage import get_storage_service
from src.database.models.ai_agent_session import AIAgentQuestion, AIAnalysisSession
from src.database.models.resident_profile import ResidentProfile
from src.database.models.user_profile import UserProfile
from src.main import app
from src.models.enums import ClassificationStatus, TicketStatus, UserRole
from src.security.supabase_jwt import AuthenticatedPrincipal
from tests.test_v4.factories import attach_image, build_world, make_ticket

#: Every way an analysis can finish. Each one must publish the ticket.
PUBLISHED_STATUSES = [
    ClassificationStatus.RESOLVED,
    ClassificationStatus.MANUAL_REVIEW,
    # An invalid terminal result: the v3/v4 paths record it as FAILED.
    ClassificationStatus.FAILED,
]


class _StorageStub:
    """Signs anything. The test is about who reaches the signing, not the URL."""

    class settings:  # noqa: N801 - mirrors the attribute the route reads
        supabase_signed_download_ttl_seconds = 60

    def create_signed_download_url(self, object_path: str) -> str:
        return f"https://storage.test/{object_path}?token=stub"


def _principal(user_id):
    return AuthenticatedPrincipal(
        auth_user_id=user_id,
        email=None,
        phone="+84900000000",
        issuer="test",
        audience="authenticated",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _resident_actor(profile: ResidentProfile) -> CurrentActor:
    return CurrentActor(
        actor_type="resident",
        user=profile.user,
        principal=_principal(profile.user_id),
        resident_profile=profile,
    )


def _coordinator_actor(user: UserProfile) -> CurrentActor:
    return CurrentActor(
        actor_type="coordinator",
        user=user,
        principal=_principal(user.user_id),
        resident_profile=None,
    )


@pytest.fixture
def world(db_session):
    """Two apartments, plus a second account inside the reporting apartment."""
    built = build_world(db_session, resident_count=3, technician_count=1)
    reporter = built.resident(0)
    housemate_user = UserProfile(user_id=uuid4(), role=UserRole.RESIDENT, full_name="Người nhà")
    housemate = ResidentProfile(user=housemate_user, unit=reporter.unit, is_primary=False)
    db_session.add_all([housemate_user, housemate])
    db_session.commit()
    built.housemate = housemate
    return built


@pytest.fixture(autouse=True)
def no_background_analysis(monkeypatch):
    """Answering a question schedules `resume_analysis`, which opens its own
    session against the real database. These tests are about authorization, so
    the dispatch is stubbed out rather than run."""
    monkeypatch.setattr("src.api.routes.tickets.resume_analysis", lambda *_a, **_k: None)
    monkeypatch.setattr("src.api.routes.tickets.run_analysis", lambda *_a, **_k: None)


@pytest_asyncio.fixture
async def clients(db_session, world):
    """One authenticated client per actor, sharing the test database.

    FastAPI dependency overrides are process-global, so the actor is swapped
    per request through a mutable holder rather than by re-registering.
    """
    current: dict[str, CurrentActor] = {}
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[require_resident] = lambda: current["resident"]
    app.dependency_overrides[require_coordinator] = lambda: current["coordinator"]
    app.dependency_overrides[get_storage_service] = _StorageStub

    transport = ASGITransport(app=app)

    class _Clients:
        def __init__(self, http: AsyncClient) -> None:
            self.http = http

        def as_resident(self, profile: ResidentProfile) -> AsyncClient:
            current["resident"] = _resident_actor(profile)
            return self.http

        def as_coordinator(self) -> AsyncClient:
            current["coordinator"] = _coordinator_actor(world.coordinator)
            return self.http

    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield _Clients(http)
    app.dependency_overrides.clear()


def _private_ticket(world, **kwargs):
    """A report the Agent is still working on, sent by resident(0)."""
    kwargs.setdefault("classification_status", ClassificationStatus.PROCESSING)
    return make_ticket(world, resident=world.resident(0), **kwargs)


def _pending_question(world, ticket) -> AIAgentQuestion:
    session = AIAnalysisSession(ticket_id=ticket.id, status="RUNNING")
    world.db.add(session)
    world.db.flush()
    question = AIAgentQuestion(
        session_id=session.id,
        ticket_id=ticket.id,
        question_type="MULTIPLE_CHOICE",
        question_text="Nước chảy ở đâu?",
        options=["Trần nhà", "Sàn nhà"],
        round_number=1,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    world.db.add(question)
    world.db.commit()
    return question


def _publish(world, ticket, status: ClassificationStatus) -> None:
    ticket.classification_status = status
    world.db.commit()


# ---------------------------------------------------------------------------
# 1. The reporter keeps full access to their own private report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reporter_sees_their_processing_ticket_everywhere(clients, world):
    ticket = _private_ticket(world)
    attach_image(world, ticket)
    world.db.refresh(ticket)
    attachment = ticket.attachments[0]
    question = _pending_question(world, ticket)
    http = clients.as_resident(world.resident(0))

    listed = (await http.get("/api/v1/tickets")).json()["data"]
    assert listed["total"] == 1
    assert [item["id"] for item in listed["items"]] == [str(ticket.id)]

    detail = await http.get(f"/api/v1/tickets/{ticket.id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["is_reporter"] is True

    download = await http.get(
        f"/api/v1/tickets/{ticket.id}/attachments/{attachment.id}/download-url"
    )
    assert download.status_code == 200

    asked = await http.get(f"/api/v1/tickets/{ticket.id}/agent-question")
    assert asked.status_code == 200
    assert asked.json()["data"]["id"] == str(question.id)

    answered = await http.post(
        f"/api/v1/tickets/{ticket.id}/agent-question/{question.id}/answer",
        json={"answer_type": "OPTION", "answer_text": "Trần nhà"},
    )
    assert answered.status_code == 200


@pytest.mark.asyncio
async def test_reporter_can_cancel_their_private_ticket(clients, world):
    ticket = _private_ticket(world, status=TicketStatus.NEW)
    http = clients.as_resident(world.resident(0))

    detail = await http.get(f"/api/v1/tickets/{ticket.id}")
    assert "CANCEL" in detail.json()["data"]["available_actions"]

    cancelled = await http.post(f"/api/v1/tickets/{ticket.id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["display_status"]
    world.db.refresh(ticket)
    assert ticket.status is TicketStatus.CANCELLED


# ---------------------------------------------------------------------------
# 2. A housemate cannot reach it at all while it is private
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_housemate_cannot_reach_a_private_ticket_by_any_route(clients, world):
    ticket = _private_ticket(world, status=TicketStatus.NEW)
    attach_image(world, ticket)
    world.db.refresh(ticket)
    attachment = ticket.attachments[0]
    question = _pending_question(world, ticket)
    http = clients.as_resident(world.housemate)

    listed = (await http.get("/api/v1/tickets")).json()["data"]
    assert listed["total"] == 0
    assert listed["items"] == []

    assert (await http.get(f"/api/v1/tickets/{ticket.id}")).status_code == 404
    assert (
        await http.get(f"/api/v1/tickets/{ticket.id}/attachments/{attachment.id}/download-url")
    ).status_code == 404
    assert (await http.get(f"/api/v1/tickets/{ticket.id}/agent-question")).status_code == 404
    assert (
        await http.post(
            f"/api/v1/tickets/{ticket.id}/agent-question/{question.id}/answer",
            json={"answer_type": "OPTION", "answer_text": "Trần nhà"},
        )
    ).status_code == 404
    assert (await http.post(f"/api/v1/tickets/{ticket.id}/cancel")).status_code == 404

    world.db.refresh(ticket)
    assert ticket.status is TicketStatus.NEW


@pytest.mark.asyncio
async def test_a_resident_of_another_apartment_never_gets_access(clients, world):
    ticket = _private_ticket(world)
    attach_image(world, ticket)
    world.db.refresh(ticket)
    attachment = ticket.attachments[0]
    outsider = world.resident(1)
    http = clients.as_resident(outsider)

    assert (await http.get("/api/v1/tickets")).json()["data"]["total"] == 0
    assert (await http.get(f"/api/v1/tickets/{ticket.id}")).status_code == 404

    # ...and publication changes nothing for them.
    _publish(world, ticket, ClassificationStatus.RESOLVED)
    assert (await http.get("/api/v1/tickets")).json()["data"]["total"] == 0
    assert (await http.get(f"/api/v1/tickets/{ticket.id}")).status_code == 404
    assert (
        await http.get(f"/api/v1/tickets/{ticket.id}/attachments/{attachment.id}/download-url")
    ).status_code == 404


# ---------------------------------------------------------------------------
# 3. Building Management is not handed the report until analysis finishes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [ClassificationStatus.PENDING, ClassificationStatus.PROCESSING])
async def test_coordinator_cannot_reach_a_private_ticket(clients, world, phase):
    ticket = _private_ticket(world, classification_status=phase, status=TicketStatus.NEW)
    attach_image(world, ticket)
    world.db.refresh(ticket)
    attachment = ticket.attachments[0]
    http = clients.as_coordinator()

    listed = (await http.get("/api/v1/coordinator/tickets")).json()["data"]
    assert listed["total"] == 0
    assert listed["items"] == []

    assert (await http.get(f"/api/v1/coordinator/tickets/{ticket.id}")).status_code == 404
    assert (
        await http.get(
            f"/api/v1/coordinator/tickets/{ticket.id}/attachments/{attachment.id}/download-url"
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_coordinator_mutations_cannot_operate_on_a_private_ticket(clients, world):
    ticket = _private_ticket(world, category=world.water, status=TicketStatus.NEW)
    master = make_ticket(
        world,
        resident=world.resident(1),
        category=world.water,
        classification_status=ClassificationStatus.RESOLVED,
        status=TicketStatus.APPROVED,
    )
    http = clients.as_coordinator()

    assert (await http.post(f"/api/v1/coordinator/tickets/{ticket.id}/approve")).status_code == 404
    assert (
        await http.patch(
            f"/api/v1/coordinator/tickets/{ticket.id}/classification",
            json={"category_id": str(world.water.id), "priority": "P2", "reason": "Thử ép phân loại"},
        )
    ).status_code == 404
    assert (
        await http.post(
            f"/api/v1/coordinator/tickets/{ticket.id}/manual-review/reject",
            json={"reason": "Thử từ chối phản ánh đang phân tích"},
        )
    ).status_code == 404
    assert (
        await http.post(
            f"/api/v1/coordinator/tickets/{ticket.id}/duplicate-link",
            json={"master_ticket_id": str(master.id), "reason": "Thử gộp phản ánh đang phân tích"},
        )
    ).status_code == 404
    assert (
        await http.post(
            f"/api/v1/coordinator/tickets/{ticket.id}/assign",
            json={"technician_id": str(world.technician(0).user_id)},
        )
    ).status_code == 404

    world.db.refresh(ticket)
    assert ticket.status is TicketStatus.NEW
    assert ticket.classification_status is ClassificationStatus.PROCESSING


# ---------------------------------------------------------------------------
# 4-5. Publication opens it up, but reporter-only actions stay reporter-only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("published", PUBLISHED_STATUSES)
async def test_publication_shares_the_ticket_with_apartment_and_coordinator(clients, world, published):
    ticket = _private_ticket(world, status=TicketStatus.NEW)
    attach_image(world, ticket)
    world.db.refresh(ticket)
    attachment = ticket.attachments[0]
    _publish(world, ticket, published)

    housemate = clients.as_resident(world.housemate)
    listed = (await housemate.get("/api/v1/tickets")).json()["data"]
    assert listed["total"] == 1
    detail = await housemate.get(f"/api/v1/tickets/{ticket.id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["is_reporter"] is False
    assert (
        await housemate.get(f"/api/v1/tickets/{ticket.id}/attachments/{attachment.id}/download-url")
    ).status_code == 200

    coordinator = clients.as_coordinator()
    assert (await coordinator.get("/api/v1/coordinator/tickets")).json()["data"]["total"] == 1
    assert (await coordinator.get(f"/api/v1/coordinator/tickets/{ticket.id}")).status_code == 200
    assert (
        await coordinator.get(
            f"/api/v1/coordinator/tickets/{ticket.id}/attachments/{attachment.id}/download-url"
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_a_published_ticket_is_still_only_cancellable_by_its_reporter(clients, world):
    ticket = _private_ticket(world, status=TicketStatus.NEW)
    _publish(world, ticket, ClassificationStatus.RESOLVED)
    question = _pending_question(world, ticket)

    housemate = clients.as_resident(world.housemate)
    detail = await housemate.get(f"/api/v1/tickets/{ticket.id}")
    assert detail.status_code == 200
    # The UI hint is withheld...
    assert "CANCEL" not in detail.json()["data"]["available_actions"]

    # ...and so is the operation itself, even though the status would allow it.
    refused = await housemate.post(f"/api/v1/tickets/{ticket.id}/cancel")
    assert refused.status_code == 403
    world.db.refresh(ticket)
    assert ticket.status is TicketStatus.NEW

    # The AI conversation stays reporter-only after publication too.
    assert (await housemate.get(f"/api/v1/tickets/{ticket.id}/agent-question")).status_code == 404
    assert (
        await housemate.post(
            f"/api/v1/tickets/{ticket.id}/agent-question/{question.id}/answer",
            json={"answer_type": "OPTION", "answer_text": "Trần nhà"},
        )
    ).status_code == 404

    reporter = clients.as_resident(world.resident(0))
    assert "CANCEL" in (
        await reporter.get(f"/api/v1/tickets/{ticket.id}")
    ).json()["data"]["available_actions"]
    assert (await reporter.post(f"/api/v1/tickets/{ticket.id}/cancel")).status_code == 200


@pytest.mark.asyncio
async def test_a_question_id_from_another_ticket_is_rejected(clients, world):
    mine = _private_ticket(world)
    other = make_ticket(world, resident=world.resident(0), location=world.elevator_b)
    foreign_question = _pending_question(world, other)
    http = clients.as_resident(world.resident(0))

    response = await http.post(
        f"/api/v1/tickets/{mine.id}/agent-question/{foreign_question.id}/answer",
        json={"answer_type": "OPTION", "answer_text": "Trần nhà"},
    )

    assert response.status_code == 404
    world.db.refresh(foreign_question)
    assert foreign_question.status == "PENDING"


# ---------------------------------------------------------------------------
# 6. Pagination: an invisible row must not consume a page slot
# ---------------------------------------------------------------------------


def _interleaved(world):
    """Six reports, alternating visible/private, newest first by `updated_at`.

    Interleaving matters: if the predicate ran after `offset`/`limit`, page 1
    would come back short and page 2 would start in the wrong place.
    """
    now = datetime.now(UTC)
    visible, private = [], []
    for index in range(6):
        stamp = now - timedelta(minutes=index)
        if index % 2 == 0:
            ticket = make_ticket(
                world,
                resident=world.housemate,
                classification_status=ClassificationStatus.RESOLVED,
                created_at=stamp,
            )
            visible.append(ticket)
        else:
            ticket = make_ticket(
                world,
                resident=world.resident(0),
                classification_status=ClassificationStatus.PROCESSING,
                created_at=stamp,
            )
            private.append(ticket)
        ticket.updated_at = stamp
    world.db.commit()
    return visible, private


@pytest.mark.asyncio
async def test_private_rows_do_not_consume_resident_page_slots(clients, world):
    visible, private = _interleaved(world)
    http = clients.as_resident(world.housemate)

    first = (await http.get("/api/v1/tickets", params={"page": 1, "page_size": 2})).json()["data"]
    second = (await http.get("/api/v1/tickets", params={"page": 2, "page_size": 2})).json()["data"]

    assert first["total"] == len(visible) == 3
    assert second["total"] == 3
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1

    seen = [item["id"] for item in [*first["items"], *second["items"]]]
    assert set(seen) == {str(item.id) for item in visible}
    assert not set(seen) & {str(item.id) for item in private}


@pytest.mark.asyncio
async def test_private_rows_do_not_consume_coordinator_page_slots(clients, world):
    visible, private = _interleaved(world)
    http = clients.as_coordinator()

    first = (
        await http.get("/api/v1/coordinator/tickets", params={"page": 1, "page_size": 2})
    ).json()["data"]
    second = (
        await http.get("/api/v1/coordinator/tickets", params={"page": 2, "page_size": 2})
    ).json()["data"]

    assert first["total"] == 3
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    seen = [item["id"] for item in [*first["items"], *second["items"]]]
    assert set(seen) == {str(item.id) for item in visible}
    assert not set(seen) & {str(item.id) for item in private}


@pytest.mark.asyncio
async def test_explicitly_filtering_for_a_private_phase_returns_nothing(clients, world):
    _private_ticket(world)
    http = clients.as_coordinator()

    response = await http.get(
        "/api/v1/coordinator/tickets", params={"classification_status": "PROCESSING"}
    )

    assert response.json()["data"]["total"] == 0


# ---------------------------------------------------------------------------
# 9. The appeal surface is gone from the contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_appeal_path_survives_in_the_openapi_schema(client):
    schema = (await client.get("/openapi.json")).json()
    paths = schema["paths"]

    for removed in (
        "/api/v1/tickets/{ticket_id}/duplicate-review",
        "/api/v1/tickets/{ticket_id}/duplicate-dispute",
        "/api/v1/coordinator/duplicate-disputes",
        "/api/v1/coordinator/duplicate-disputes/{dispute_id}/resolve",
    ):
        # Absent, not deprecated: a deprecated route is still callable.
        assert removed not in paths

    # Coordinator duplicate *linking* is not an appeal and stays.
    assert "/api/v1/coordinator/tickets/{ticket_id}/duplicate-link" in paths

    blob = str(schema)
    assert "DuplicateDispute" not in blob
    assert "duplicate_dispute_status" not in blob
    assert "DISPUTE_DUPLICATE" not in blob
