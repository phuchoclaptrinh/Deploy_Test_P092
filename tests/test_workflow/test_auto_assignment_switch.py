"""The global auto-assignment switch has exactly one way in — §4.6 item 6.

`PATCH /coordinator/auto-assignment-settings` is a real endpoint a coordinator
uses to turn auto-assignment **off**. Turning it on is a different act: §2.12b
and §4.6 require the coordinator to see a proposal table first, so that a queue
of already-approved tickets is never silently assigned by pressing a toggle.

These tests pin both halves of that: off stays a plain setting change, on is
refused with a code the UI can turn into "create a proposal batch", and the
refusal writes nothing at all.
"""

from __future__ import annotations

import pytest
from src.services.v4_workflow_service import AutoAssignmentSettingsService

from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.models.api.errors import AUTO_ASSIGNMENT_PROPOSAL_REQUIRED, DomainError
from src.models.enums import ProposalBatchStatus
from tests.test_v4.factories import build_world
from tests.test_v4.test_assignment_proposal import _ready_batch, _service, _switch


def test_enabling_directly_while_off_is_refused(db_session):
    world = build_world(db_session)
    service = AutoAssignmentSettingsService(db_session)
    service.get()

    with pytest.raises(DomainError) as exc:
        service.update(world.coordinator.user_id, enabled=True, activation_delay="2_HOURS")

    assert exc.value.code == AUTO_ASSIGNMENT_PROPOSAL_REQUIRED
    assert exc.value.status_code == 409


def test_the_refused_patch_changes_nothing(db_session):
    """Not the flag, not the delay, not the version (§7.6)."""
    world = build_world(db_session)
    service = AutoAssignmentSettingsService(db_session)
    before = service.get()
    version_before, delay_before = before.version, before.activation_delay

    with pytest.raises(DomainError):
        service.update(world.coordinator.user_id, enabled=True, activation_delay="3_DAYS")

    row = db_session.get(AutoAssignmentSetting, 1)
    assert row.enabled is False
    assert row.version == version_before
    assert row.activation_delay == delay_before
    assert row.updated_by_user_id is None


def test_enabling_is_refused_even_before_the_row_exists(db_session):
    """A missing singleton reads as off, so it is the same refusal."""
    world = build_world(db_session)

    with pytest.raises(DomainError) as exc:
        AutoAssignmentSettingsService(db_session).update(
            world.coordinator.user_id, enabled=True, activation_delay="IMMEDIATE"
        )

    assert exc.value.code == AUTO_ASSIGNMENT_PROPOSAL_REQUIRED
    assert db_session.get(AutoAssignmentSetting, 1) is None


def test_disabling_still_works(db_session):
    """§2.12: a coordinator may always turn the feature off."""
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)
    service.confirm_batch(
        world.coordinator.user_id, batch.id, activation_delay="2_HOURS"
    )
    assert _switch(db_session).enabled is True

    row = AutoAssignmentSettingsService(db_session).update(
        world.coordinator.user_id, enabled=False, activation_delay="2_HOURS"
    )

    assert row.enabled is False


def test_changing_the_delay_while_already_on_is_not_a_transition(db_session):
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)
    service.confirm_batch(
        world.coordinator.user_id, batch.id, activation_delay="IMMEDIATE"
    )

    row = AutoAssignmentSettingsService(db_session).update(
        world.coordinator.user_id, enabled=True, activation_delay="5_HOURS"
    )

    assert row.enabled is True
    assert row.activation_delay == "5_HOURS"


def test_a_confirmed_proposal_is_the_way_in(db_session):
    """The path the refusal points at actually works (§4.6 item 6)."""
    world = build_world(db_session)
    service, batch = _ready_batch(db_session, world, ticket_count=1)

    confirmed = service.confirm_batch(
        world.coordinator.user_id, batch.id, activation_delay="1_DAY"
    )

    assert confirmed.status == ProposalBatchStatus.CONFIRMED.value
    row = _switch(db_session)
    assert row.enabled is True
    assert row.activation_delay == "1_DAY"


def test_opening_a_batch_does_not_enable_anything(db_session):
    """§4.6 item 1: BUILDING leaves the switch alone."""
    world = build_world(db_session)
    service = _service(db_session)

    service.create_batch(world.coordinator.user_id)

    row = _switch(db_session)
    assert row is None or row.enabled is False
