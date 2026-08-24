"""Building Management rejecting a report ends it.

There is no supplement step any more: a rejected report becomes INVALID, the
resident is told to send a new one, and the coordinator's internal reason never
reaches them.
"""

from __future__ import annotations

import pytest

from src.api.routes.tickets import resident_ticket_response
from src.database.models.notification import Notification
from src.models.enums import (
    ClassificationStatus,
    InvalidReason,
    TicketLifecycleGroup,
    TicketStatus,
)
from src.services.agent_backend_service import AgentBackendService
from tests.test_v4.factories import build_world, make_ticket

INTERNAL_REASON = "Ảnh mờ, mô tả không khớp — nghi ngờ spam nội bộ."


@pytest.fixture
def world(v4_env):
    db = v4_env.session()
    try:
        yield build_world(db, resident_count=2, technician_count=1)
    finally:
        db.close()


def _reject(world):
    ticket = make_ticket(world, status=TicketStatus.NEW, classification_status=ClassificationStatus.MANUAL_REVIEW)
    AgentBackendService(world.db).manual_review_reject(world.coordinator.user_id, ticket.id, INTERNAL_REASON)
    world.db.refresh(ticket)
    return ticket


def test_rejection_ends_the_report_as_invalid(world):
    ticket = _reject(world)

    assert ticket.status == TicketStatus.INVALID
    assert ticket.invalid_reason == InvalidReason.COORDINATOR_REJECTED.value
    assert ticket.classification_status == ClassificationStatus.FAILED


def test_rejected_report_shows_resident_safe_copy_and_only_a_new_report_action(world):
    ticket = _reject(world)

    payload = resident_ticket_response(ticket, world.resident(0).user_id)

    assert payload.lifecycle_group is TicketLifecycleGroup.FINISHED
    assert payload.invalid_reason_text == "Phản ánh chưa được tiếp nhận sau khi Ban quản lý xem xét."
    # No supplement action, and nothing else the resident could act on here:
    # the only way forward is a new report, which the UI offers as a link.
    assert payload.available_actions == []
    assert "SUPPLEMENT_INFORMATION" not in payload.available_actions


def test_internal_rejection_reason_never_reaches_the_resident(world):
    ticket = _reject(world)

    dumped = resident_ticket_response(ticket, world.resident(0).user_id).model_dump_json()

    assert INTERNAL_REASON not in dumped
    assert "spam" not in dumped.lower()


def test_rejection_notifies_the_apartment_to_send_a_new_report(world):
    ticket = _reject(world)

    notifications = [
        row for row in world.db.query(Notification).filter(Notification.ticket_id == ticket.id).all()
    ]

    assert notifications, "the apartment must be told the report was not accepted"
    bodies = {row.body for row in notifications}
    assert "Phản ánh chưa được tiếp nhận. Vui lòng tạo phản ánh mới với thông tin rõ hơn." in bodies
    assert all(INTERNAL_REASON not in row.body for row in notifications)


def test_the_internal_reason_is_still_recorded_for_audit(world):
    ticket = _reject(world)

    # The resident-facing timeline hides it, but the status history keeps it so
    # Building Management can answer for the decision later.
    reasons = [row.reason for row in ticket.status_history]
    assert INTERNAL_REASON in reasons
