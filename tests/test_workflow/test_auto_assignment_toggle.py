"""The Automatic Assignment switch is now a plain ON/OFF (§2, §9).

§9 removes the rule that it could only be enabled after confirming a proposal
batch. What replaces that rule is a confirmation modal, enforced server-side by
`acknowledged` so a client calling the API directly cannot skip the explanation.

The two asymmetries that survive are about **side effects**, not permission:
turning it on enqueues the backlog and records who authorised autonomy; turning
it off stops future dispatch and never unwinds an existing assignment.
"""

from __future__ import annotations

import pytest

from src.api.routes.coordinator.dispatch import TOGGLE_CONFIRMATION_TEXT
from src.database.models.audit_log import AuditLog
from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.dispatch import DispatchEvent
from src.database.models.ticket_assignment import TicketAssignment
from src.models.api.errors import CONFLICT_VERSION, DomainError
from src.models.enums import ClassificationStatus, DispatchEventStatus, Priority, TicketStatus
from src.services.auto_approval import eligible_for_automatic_approval
from src.services.auto_assignment_settings_service import AutoAssignmentSettingsService
from tests.test_workflow.factories import build_world, make_assignment, make_ticket


@pytest.fixture
def world(db_session):
    return build_world(db_session, resident_count=4, technician_count=2)


def eligible(world, *, priority=Priority.P2, **kwargs):
    return make_ticket(
        world,
        category=world.water,
        status=TicketStatus.APPROVED,
        classification_status=ClassificationStatus.RESOLVED,
        priority=priority,
        **kwargs,
    )


@pytest.mark.parametrize("priority", [Priority.P1, Priority.P2, Priority.P3, Priority.P4])
def test_every_non_emergency_band_is_eligible_for_automatic_approval(world, priority):
    ticket = make_ticket(
        world,
        category=world.water,
        status=TicketStatus.NEW,
        classification_status=ClassificationStatus.RESOLVED,
        priority=priority,
    )

    assert eligible_for_automatic_approval(world.db, ticket) is True


def test_the_emergency_band_is_not_eligible_for_automatic_approval(world):
    ticket = make_ticket(
        world,
        category=world.water,
        status=TicketStatus.NEW,
        classification_status=ClassificationStatus.RESOLVED,
        priority=Priority.P5,
    )

    assert eligible_for_automatic_approval(world.db, ticket) is False


def test_the_switch_starts_off(world):
    """Autonomy is never the default, and a missing row reads as off."""
    assert AutoAssignmentSettingsService(world.db).get().enabled is False


def test_turning_it_on_no_longer_needs_a_proposal_first(world):
    """§9 deleted that rule; this is the test that says so."""
    row = AutoAssignmentSettingsService(world.db).set_enabled(world.coordinator.user_id, enabled=True)

    assert row.enabled is True
    assert row.enabled_by_user_id == world.coordinator.user_id
    assert row.enabled_at is not None


def test_turning_it_on_queues_the_backlog(world):
    """Otherwise the queue a manager just decided to automate would sit there."""
    waiting = eligible(world)
    AutoAssignmentSettingsService(world.db).set_enabled(world.coordinator.user_id, enabled=True)

    events = world.db.query(DispatchEvent).all()
    assert [event.ticket_id for event in events] == [waiting.id]
    assert events[0].status == DispatchEventStatus.PENDING.value


def test_the_backlog_sweep_skips_what_automation_must_not_touch(world):
    """§2's conditions apply to the backlog exactly as they do to new reports."""
    eligible(world, priority=Priority.P5)
    master = eligible(world)
    duplicate = eligible(world)
    duplicate.duplicate_of_ticket_id = master.id
    world.db.commit()

    AutoAssignmentSettingsService(world.db).set_enabled(world.coordinator.user_id, enabled=True)

    queued = {event.ticket_id for event in world.db.query(DispatchEvent).all()}
    assert queued == {master.id}


def test_turning_it_off_clears_the_provenance(world):
    """An explanation for a state that no longer holds is worse than none."""
    service = AutoAssignmentSettingsService(world.db)
    service.set_enabled(world.coordinator.user_id, enabled=True)
    row = service.set_enabled(world.coordinator.user_id, enabled=False)

    assert row.enabled is False
    assert row.enabled_by_user_id is None
    assert row.enabled_at is None


def test_turning_it_off_does_not_undo_existing_assignments(world):
    """§2 says so explicitly, and there is no column here that could."""
    ticket = eligible(world)
    assignment = make_assignment(world, ticket, world.technician(0))
    service = AutoAssignmentSettingsService(world.db)
    service.set_enabled(world.coordinator.user_id, enabled=True)

    service.set_enabled(world.coordinator.user_id, enabled=False)

    world.db.refresh(assignment)
    assert assignment.is_active is True
    assert world.db.query(TicketAssignment).count() == 1


def test_re_enabling_keeps_the_original_authoriser(world):
    """The record says who authorised the current ON state, not who re-saved it."""
    service = AutoAssignmentSettingsService(world.db)
    first = service.set_enabled(world.coordinator.user_id, enabled=True)
    stamped = first.enabled_at

    again = service.set_enabled(world.coordinator.user_id, enabled=True)
    assert again.enabled_at == stamped


def test_both_directions_are_audited(world):
    service = AutoAssignmentSettingsService(world.db)
    service.set_enabled(world.coordinator.user_id, enabled=True)
    service.set_enabled(world.coordinator.user_id, enabled=False)

    actions = [row.action for row in world.db.query(AuditLog).all()]
    assert "ENABLE_AUTO_ASSIGNMENT" in actions
    assert "DISABLE_AUTO_ASSIGNMENT" in actions


def test_a_no_op_write_is_not_audited_as_a_change(world):
    service = AutoAssignmentSettingsService(world.db)
    service.set_enabled(world.coordinator.user_id, enabled=False)

    assert [row.action for row in world.db.query(AuditLog).all()] == []


def test_a_stale_version_is_refused(world):
    """Two managers on the screen at once cannot silently undo each other."""
    service = AutoAssignmentSettingsService(world.db)
    row = service.get()
    service.set_enabled(world.coordinator.user_id, enabled=True)

    with pytest.raises(DomainError) as exc:
        service.set_enabled(world.coordinator.user_id, enabled=True, expected_version=row.version - 1)
    assert exc.value.code == CONFLICT_VERSION


def test_an_enabled_row_must_name_who_enabled_it(world):
    """The database refuses provenance-free autonomy, not just the service."""
    from sqlalchemy.exc import IntegrityError

    world.db.add(AutoAssignmentSetting(id=1, enabled=True, version=1))
    with pytest.raises(IntegrityError):
        world.db.commit()
    world.db.rollback()


def test_the_confirmation_text_is_the_one_the_contract_specifies(world):
    """§2's wording lives in the backend, not only in a frontend string.

    A redesign that dropped the modal would otherwise silently drop the
    explanation a manager is owed before autonomy is switched on.
    """
    for fragment in (
        "AI phân loại",
        "không trùng lặp",
        "không phải phản ánh khẩn cấp",
        "tự động duyệt",
        "bỏ qua bước gộp nhóm",
        "phân công ngay lập tức",
        "Ban quản lý",
    ):
        assert fragment in TOGGLE_CONFIRMATION_TEXT
