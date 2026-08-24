"""The visibility rule as SQL, checked at the layer that writes the SQL.

The HTTP suite proves the routes wire the right actor through. This one proves
the predicate itself is part of the query — applied before `count`, `offset` and
`limit` — rather than a filter someone could later move into Python and quietly
break pagination with.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.database.models.resident_profile import ResidentProfile
from src.database.models.user_profile import UserProfile
from src.models.enums import ClassificationStatus, UserRole
from src.repositories.ticket_repository import TicketRepository
from src.services.ticket_visibility import (
    PRIVATE_AI_PHASE,
    is_private_ai_phase,
    is_published,
    resident_can_view,
)
from tests.test_v4.factories import build_world, make_ticket


@pytest.fixture
def world(db_session):
    built = build_world(db_session, resident_count=3, technician_count=1)
    reporter = built.resident(0)
    user = UserProfile(user_id=uuid4(), role=UserRole.RESIDENT, full_name="Người nhà")
    built.housemate = ResidentProfile(user=user, unit=reporter.unit, is_primary=False)
    db_session.add_all([user, built.housemate])
    db_session.commit()
    return built


def test_the_private_phase_is_exactly_pending_and_processing():
    assert {status.value for status in PRIVATE_AI_PHASE} == {"PENDING", "PROCESSING"}


@pytest.mark.parametrize(
    ("status", "private"),
    [
        (ClassificationStatus.PENDING, True),
        (ClassificationStatus.PROCESSING, True),
        (ClassificationStatus.RESOLVED, False),
        (ClassificationStatus.MANUAL_REVIEW, False),
        (ClassificationStatus.FAILED, False),
    ],
)
def test_python_and_sql_agree_on_every_classification_status(world, status, private):
    """The row check and the query must never disagree about one status."""
    reporter = world.resident(0)
    ticket = make_ticket(world, resident=reporter, classification_status=status)
    repository = TicketRepository(world.db)

    assert is_private_ai_phase(ticket) is private
    assert is_published(ticket) is not private
    assert resident_can_view(ticket, reporter.unit_id, reporter.user_id) is True
    assert resident_can_view(ticket, reporter.unit_id, world.housemate.user_id) is not private

    _items, housemate_total = repository.list_resident_tickets(
        reporter.unit_id, world.housemate.user_id, 1, 20
    )
    assert housemate_total == (0 if private else 1)

    _items, coordinator_total = repository.list_coordinator_tickets(1, 20)
    assert coordinator_total == (0 if private else 1)


def test_a_ticket_from_another_apartment_is_never_visible(world):
    other = world.resident(1)
    make_ticket(world, resident=other, classification_status=ClassificationStatus.RESOLVED)
    reporter = world.resident(0)
    repository = TicketRepository(world.db)

    _items, total = repository.list_resident_tickets(reporter.unit_id, reporter.user_id, 1, 20)
    assert total == 0
    assert repository.get_resident_visible_ticket(reporter.unit_id, reporter.user_id, uuid4()) is None


def test_the_predicate_runs_before_offset_and_limit(world):
    """Interleave private and published rows and walk the pages.

    If the filter ran after `limit`, page 1 would come back half empty and the
    pages would not partition the visible set.
    """
    now = datetime.now(UTC)
    reporter = world.resident(0)
    visible, private = [], []
    for index in range(8):
        stamp = now - timedelta(minutes=index)
        published = index % 2 == 0
        ticket = make_ticket(
            world,
            resident=reporter,
            classification_status=(
                ClassificationStatus.RESOLVED if published else ClassificationStatus.PROCESSING
            ),
            created_at=stamp,
        )
        ticket.updated_at = stamp
        (visible if published else private).append(ticket)
    world.db.commit()

    repository = TicketRepository(world.db)
    seen: list = []
    for page in (1, 2):
        items, total = repository.list_resident_tickets(
            reporter.unit_id, world.housemate.user_id, page, 3
        )
        assert total == len(visible) == 4
        seen.extend(items)
    assert len(seen) == 4
    assert {item.id for item in seen} == {item.id for item in visible}
    assert not {item.id for item in seen} & {item.id for item in private}


def test_internal_reads_stay_unfiltered(world):
    """Background analysis and workers must still see the ticket they work on."""
    reporter = world.resident(0)
    ticket = make_ticket(
        world, resident=reporter, classification_status=ClassificationStatus.PROCESSING
    )
    repository = TicketRepository(world.db)

    assert repository.get_coordinator_ticket(ticket.id) is not None
    assert repository.get_coordinator_visible_ticket(ticket.id) is None
    # The unit-scoped read is for rows the caller is already authorized for.
    assert repository.get_resident_ticket(reporter.unit_id, ticket.id) is not None
    assert (
        repository.get_resident_visible_ticket(reporter.unit_id, world.housemate.user_id, ticket.id)
        is None
    )
