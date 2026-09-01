"""Every constant the code writes has to fit the column it is written to.

This is the test that was missing when `WAITING_EMERGENCY_MANAGEMENT_REVIEW`
(35 characters) was added to a `String(30)` column. The backend suite runs on
SQLite, which does not enforce `VARCHAR` limits, so the value stored fine and
every emergency-gate test passed. PostgreSQL refused it -- and because the
insert is part of the finalize transaction, the refusal rolled back a correctly
scored P5 emergency and left the ticket with a null priority.

So the check cannot be "does it round-trip through the test database". It has to
compare the literal against the declared column length, which is true on every
engine and needs no database at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy import String

from src.database.models.ai_analysis import AIAnalysisRun
from src.services import agent_result_service


def _column_length(model, name: str) -> int:
    column = model.__table__.columns[name]
    assert isinstance(column.type, String), f"{name} is not a String column"
    assert column.type.length is not None, f"{name} has no declared length"
    return column.type.length


#: Every module-level GROUPING_* string constant, which is the full set of
#: values `finalize` and the grouping stage can assign. Collected by reflection
#: rather than listed here on purpose: a new status added to the service is then
#: covered by this test the moment it exists.
GROUPING_VALUES = sorted(
    value
    for name, value in vars(agent_result_service).items()
    if name.startswith("GROUPING_") and isinstance(value, str)
)


def test_the_grouping_constants_were_actually_found():
    """A reflection bug that collected nothing would make the test below vacuous."""
    assert len(GROUPING_VALUES) >= 7
    assert "WAITING_EMERGENCY_MANAGEMENT_REVIEW" in GROUPING_VALUES


@pytest.mark.parametrize("value", GROUPING_VALUES)
def test_every_grouping_status_fits_its_column(value):
    limit = _column_length(AIAnalysisRun, "grouping_status")
    assert len(value) <= limit, (
        f"grouping_status is String({limit}) but the code writes {value!r} "
        f"({len(value)} chars). PostgreSQL would reject the whole finalize "
        f"transaction; SQLite would not notice."
    )


def test_the_emergency_status_is_the_one_that_needs_the_room():
    """Pins why the column is 50 and not 30, so a future narrowing has to argue.

    The emergency value is the longest by a wide margin. If it ever stops being
    the reason for the column width, this test is the place that says so.
    """
    longest = max(GROUPING_VALUES, key=len)
    assert longest == "WAITING_EMERGENCY_MANAGEMENT_REVIEW"
    assert len(longest) == 35
    assert _column_length(AIAnalysisRun, "grouping_status") >= 35
