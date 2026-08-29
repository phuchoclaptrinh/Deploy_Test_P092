"""Shared setup for the dispatch tests.

Two things every dispatch test needs and neither should re-derive:

* a clock **inside the working shift**. §3 makes the 08:00-18:00 window a hard
  constraint, so a pass run at the wall-clock time of a CI job would legitimately
  do nothing at all -- and a suite that passed only between 08:00 and 18:00
  Vietnam time would be worse than no suite.
* the Automatic Assignment switch **on**, with provenance, because the database
  refuses an enabled row that cannot name who enabled it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.dispatch import DispatchEvent
from src.dispatch.enqueue import enqueue
from src.dispatch.shift import VN_TZ
from src.models.enums import ClassificationStatus, Priority, TicketStatus
from tests.test_workflow.factories import build_world, make_ticket


def local(text: str) -> datetime:
    """A Vietnam wall-clock time as the UTC instant the system stores."""
    return datetime.fromisoformat(text).replace(tzinfo=VN_TZ).astimezone(UTC)


#: Mid-morning on a Wednesday: inside the shift, with the whole day ahead.
NOW = local("2026-08-26T08:00")


@pytest.fixture
def world(db_session):
    return build_world(db_session, resident_count=6, technician_count=3)


@pytest.fixture
def automatic_on(world):
    """The switch, enabled by a real coordinator."""
    row = AutoAssignmentSetting(
        id=1,
        enabled=True,
        version=1,
        enabled_by_user_id=world.coordinator.user_id,
        enabled_at=NOW,
    )
    world.db.add(row)
    world.db.commit()
    return row


def dispatchable_ticket(world, *, category=None, priority=Priority.P2, **kwargs):
    """An approved, classified, non-duplicate, non-P3 ticket."""
    return make_ticket(
        world,
        category=category or world.water,
        status=TicketStatus.APPROVED,
        classification_status=ClassificationStatus.RESOLVED,
        priority=priority,
        **kwargs,
    )


def queue(world, ticket, *, now: datetime = NOW) -> DispatchEvent | None:
    event = enqueue(world.db, ticket, now=now)
    world.db.commit()
    return event
