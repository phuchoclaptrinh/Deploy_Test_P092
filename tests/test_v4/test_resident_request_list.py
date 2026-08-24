"""Resident request list: card data, privacy, server-side filtering, lifecycle.

Covers the contract the Resident "Phản ánh" screen depends on — every card field
comes from the backend, every filter is applied before `count`/`offset`/`limit`,
and nothing identifying another apartment or a phone number ever leaves here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.api.routes.tickets import _inclusive_day_end, _resident_actions, resident_ticket_response
from src.models.enums import (
    ClassificationStatus,
    InvalidReason,
    TicketLifecycleGroup,
    TicketStatus,
)
from src.services.ticket_service import TicketService
from tests.test_v4.factories import build_world, make_ticket


@pytest.fixture
def world(v4_env):
    db = v4_env.session()
    try:
        yield build_world(db, resident_count=4, technician_count=1)
    finally:
        db.close()


def _list(world, **kwargs):
    return TicketService(world.db).list_my_tickets(world.resident(0), world.resident(0).user_id, **kwargs)


# ---------------------------------------------------------------------------
# Card content
# ---------------------------------------------------------------------------


def test_list_and_detail_carry_the_location_label(world):
    ticket = make_ticket(world, location=world.elevator_a)

    items, _ = _list(world)
    assert [item.id for item in items] == [ticket.id]
    assert resident_ticket_response(items[0]).location_label == "Thang máy A"

    detail = TicketService(world.db).get_ticket(world.resident(0), ticket.id, world.resident(0).user_id)
    assert resident_ticket_response(detail).location_label == "Thang máy A"


def test_reporter_name_comes_from_the_sending_apartment_member(world):
    sender = world.resident(0)
    ticket = make_ticket(world, resident=sender)

    payload = resident_ticket_response(ticket, sender.user_id)

    assert payload.reporter_name == "Cư dân 0"
    assert payload.is_reporter is True


def test_is_reporter_is_false_for_another_member_of_the_same_apartment(world):
    sender = world.resident(0)
    ticket = make_ticket(world, resident=sender)

    # A different account viewing the same apartment's report.
    housemate_id = uuid4()
    payload = resident_ticket_response(ticket, housemate_id)

    assert payload.is_reporter is False
    # The name stays visible: apartment members may see who sent it.
    assert payload.reporter_name == "Cư dân 0"


def test_reporter_name_is_none_when_the_profile_has_no_name(world):
    sender = world.resident(0)
    sender.user.full_name = None
    world.db.commit()
    ticket = make_ticket(world, resident=sender)

    assert resident_ticket_response(ticket, sender.user_id).reporter_name is None


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------


def test_resident_payload_never_carries_a_phone_number(world):
    sender = world.resident(0)
    sender.user.phone_e164 = "+84901234567"
    world.db.commit()
    ticket = make_ticket(world, resident=sender)

    dumped = resident_ticket_response(ticket, sender.user_id).model_dump_json()

    assert "phone" not in dumped
    assert "+84901234567" not in dumped


def test_duplicate_master_never_leaks_the_other_apartment_reporter(world):
    other_apartment = world.resident(1)
    master = make_ticket(
        world,
        resident=other_apartment,
        location=world.elevator_a,
        description="Mô tả riêng của căn hộ khác.",
    )
    mine = make_ticket(world, resident=world.resident(0), location=world.elevator_a)
    mine.status = TicketStatus.LINKED_DUPLICATE
    mine.duplicate_of_ticket_id = master.id
    world.db.commit()

    detail = TicketService(world.db).get_ticket(world.resident(0), mine.id, world.resident(0).user_id)
    dumped = resident_ticket_response(detail, world.resident(0).user_id).model_dump_json()

    assert str(other_apartment.user_id) not in dumped
    assert "Cư dân 1" not in dumped
    assert "Mô tả riêng của căn hộ khác." not in dumped
    # Only the reduced reference code survives.
    assert resident_ticket_response(detail).duplicate_master_display_code is not None


# ---------------------------------------------------------------------------
# The supplement workflow is gone
# ---------------------------------------------------------------------------


def test_available_actions_never_offer_supplement_information(world):
    ticket = make_ticket(world, status=TicketStatus.WAITING_RESIDENT_INFO)

    assert _resident_actions(ticket, ticket.reporter_user_id) == []
    assert "SUPPLEMENT_INFORMATION" not in resident_ticket_response(
        ticket, ticket.reporter_user_id
    ).available_actions


def test_new_report_still_offers_cancel(world):
    ticket = make_ticket(world, status=TicketStatus.NEW)

    assert _resident_actions(ticket, ticket.reporter_user_id) == ["CANCEL"]
    # A UI hint for the sender alone: a housemate is offered nothing.
    assert _resident_actions(ticket, uuid4()) == []


# ---------------------------------------------------------------------------
# Lifecycle grouping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (TicketStatus.NEW, TicketLifecycleGroup.ACTIVE),
        (TicketStatus.APPROVED, TicketLifecycleGroup.ACTIVE),
        (TicketStatus.IN_PROGRESS, TicketLifecycleGroup.ACTIVE),
        (TicketStatus.WAITING_RESIDENT_INFO, TicketLifecycleGroup.ACTIVE),
        (TicketStatus.COMPLETED, TicketLifecycleGroup.FINISHED),
        (TicketStatus.UNRESOLVABLE, TicketLifecycleGroup.FINISHED),
        (TicketStatus.CANCELLED, TicketLifecycleGroup.FINISHED),
        (TicketStatus.INVALID, TicketLifecycleGroup.FINISHED),
    ],
)
def test_status_maps_to_a_lifecycle_group(world, status, expected):
    ticket = make_ticket(world, status=status)

    assert resident_ticket_response(ticket).lifecycle_group is expected


def test_linked_duplicate_follows_its_master_lifecycle(world):
    master = make_ticket(world, resident=world.resident(1), location=world.elevator_a)
    linked = make_ticket(world, resident=world.resident(0), location=world.elevator_a)
    linked.status = TicketStatus.LINKED_DUPLICATE
    linked.duplicate_of_ticket_id = master.id
    world.db.commit()

    active, total = _list(world, status_group=TicketLifecycleGroup.ACTIVE)
    assert total == 1
    assert [item.id for item in active] == [linked.id]
    assert resident_ticket_response(active[0]).lifecycle_group is TicketLifecycleGroup.ACTIVE

    master.status = TicketStatus.COMPLETED
    world.db.commit()

    finished, finished_total = _list(world, status_group=TicketLifecycleGroup.FINISHED)
    assert finished_total == 1
    assert [item.id for item in finished] == [linked.id]
    assert resident_ticket_response(finished[0]).lifecycle_group is TicketLifecycleGroup.FINISHED

    # And it must have left the active tab entirely.
    assert _list(world, status_group=TicketLifecycleGroup.ACTIVE)[1] == 0


# ---------------------------------------------------------------------------
# Server-side filtering, counting and pagination
# ---------------------------------------------------------------------------


def test_status_group_filters_and_counts_in_the_database(world):
    for status in (TicketStatus.NEW, TicketStatus.IN_PROGRESS, TicketStatus.COMPLETED, TicketStatus.CANCELLED):
        make_ticket(world, status=status)

    assert _list(world)[1] == 4
    assert _list(world, status_group=TicketLifecycleGroup.ACTIVE)[1] == 2
    assert _list(world, status_group=TicketLifecycleGroup.FINISHED)[1] == 2


def test_category_date_and_group_filters_combine(world):
    now = datetime.now(UTC)
    wanted = make_ticket(
        world,
        category=world.water,
        status=TicketStatus.IN_PROGRESS,
        created_at=now - timedelta(days=1),
    )
    # Same category and window, but finished.
    make_ticket(world, category=world.water, status=TicketStatus.COMPLETED, created_at=now - timedelta(days=1))
    # Same window and group, different category.
    make_ticket(world, category=world.electrical, status=TicketStatus.IN_PROGRESS, created_at=now - timedelta(days=1))
    # Same category and group, outside the window.
    make_ticket(world, category=world.water, status=TicketStatus.IN_PROGRESS, created_at=now - timedelta(days=30))

    items, total = _list(
        world,
        category_id=world.water.id,
        status_group=TicketLifecycleGroup.ACTIVE,
        created_from=now - timedelta(days=3),
        created_to=now,
    )

    assert total == 1
    assert [item.id for item in items] == [wanted.id]


def test_total_and_pages_describe_the_filtered_set(world):
    now = datetime.now(UTC)
    for index in range(5):
        make_ticket(world, status=TicketStatus.IN_PROGRESS, created_at=now - timedelta(minutes=index))
    for index in range(7):
        make_ticket(world, status=TicketStatus.COMPLETED, created_at=now - timedelta(minutes=index))

    first, total = _list(world, status_group=TicketLifecycleGroup.ACTIVE, page=1, page_size=2)
    second, second_total = _list(world, status_group=TicketLifecycleGroup.ACTIVE, page=2, page_size=2)
    third, _ = _list(world, status_group=TicketLifecycleGroup.ACTIVE, page=3, page_size=2)

    # `total` is the filtered count, not the apartment's 12 reports.
    assert total == 5
    assert second_total == 5
    assert len(first) == 2 and len(second) == 2 and len(third) == 1
    # Pages must not overlap.
    seen = [item.id for item in (*first, *second, *third)]
    assert len(set(seen)) == 5
    assert all(item.status == TicketStatus.IN_PROGRESS for item in (*first, *second, *third))


def test_list_is_ordered_by_most_recent_activity(world):
    now = datetime.now(UTC)
    older = make_ticket(world, created_at=now - timedelta(days=2))
    newer = make_ticket(world, created_at=now - timedelta(days=1))
    # The older report just moved forward, so it belongs on top.
    older.status = TicketStatus.IN_PROGRESS
    older.updated_at = now
    newer.updated_at = now - timedelta(days=1)
    world.db.commit()

    items, _ = _list(world)

    assert [item.id for item in items] == [older.id, newer.id]


def test_end_date_covers_the_whole_vietnam_day(world):
    # 23/08 16:30 UTC is 23/08 23:30 in Vietnam: inside "đến ngày 23/08".
    late_in_the_day = datetime(2026, 8, 23, 16, 30, tzinfo=UTC)
    ticket = make_ticket(world, created_at=late_in_the_day)

    items, total = _list(
        world,
        created_from=datetime(2026, 8, 23),
        created_to=_inclusive_day_end(datetime(2026, 8, 23)),
    )

    assert total == 1
    assert [item.id for item in items] == [ticket.id]


def test_a_bound_with_an_explicit_time_is_not_stretched():
    exact = datetime(2026, 8, 23, 9, 15, tzinfo=UTC)

    assert _inclusive_day_end(exact) == exact


# ---------------------------------------------------------------------------
# Building Management rejection
# ---------------------------------------------------------------------------


def test_rejected_report_is_invalid_with_resident_safe_copy(world):
    ticket = make_ticket(world, status=TicketStatus.NEW)
    ticket.status = TicketStatus.INVALID
    ticket.classification_status = ClassificationStatus.FAILED
    ticket.invalid_reason = InvalidReason.COORDINATOR_REJECTED.value
    world.db.commit()

    payload = resident_ticket_response(ticket, world.resident(0).user_id)

    assert payload.lifecycle_group is TicketLifecycleGroup.FINISHED
    assert payload.invalid_reason_text == "Phản ánh chưa được tiếp nhận sau khi Ban quản lý xem xét."
    assert payload.available_actions == []


def test_invalid_reason_text_is_absent_while_a_report_is_open(world):
    ticket = make_ticket(world, status=TicketStatus.NEW)

    assert resident_ticket_response(ticket).invalid_reason_text is None
