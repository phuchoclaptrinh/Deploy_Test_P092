"""§2.13 technician productivity: every column counted from persisted rows."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.database.models.category import CategoryCatalog
from src.database.models.floor import Floor
from src.database.models.location import Location
from src.database.models.location_type import LocationType
from src.database.models.resident_profile import ResidentProfile
from src.database.models.technician import TechnicianProfile
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.database.models.unit import Unit
from src.database.models.user_profile import UserProfile
from src.models.enums import (
    AssignmentStatus,
    Category,
    ClassificationStatus,
    Priority,
    TicketStatus,
    UserRole,
)
from src.services.technician_report_service import TechnicianReportService, record_availability, resolve_period

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _seed(db_session):
    floor = Floor(floor_code="10", display_name="Floor 10", adjacency_index=10)
    unit = Unit(floor=floor, unit_code="A-1001")
    location_type = LocationType(code="CORRIDOR", display_name="Corridor")
    location = Location(floor=floor, location_type=location_type, label="Corridor 10")
    water = CategoryCatalog(code=Category.WATER, display_name="Water leak")
    resident_user = UserProfile(user_id=uuid4(), role=UserRole.RESIDENT)
    resident = ResidentProfile(user=resident_user, unit=unit, is_primary=True)
    db_session.add_all([floor, unit, location_type, location, water, resident_user, resident])
    db_session.commit()
    return resident, location, water


def _technician(db_session, name: str, *, is_available: bool = True):
    user = UserProfile(user_id=uuid4(), full_name=name, role=UserRole.TECHNICIAN)
    profile = TechnicianProfile(user=user, is_active=True, is_available=is_available)
    db_session.add_all([user, profile])
    db_session.commit()
    return profile


def _ticket(db_session, resident, location, category, *, sla_due_at=None):
    ticket = Ticket(
        reporter_user_id=resident.user_id,
        source_unit_id=resident.unit_id,
        location_id=location.id,
        description="Issue",
        status=TicketStatus.APPROVED,
        classification_status=ClassificationStatus.RESOLVED,
        category_id=category.id,
        priority=Priority.P2,
        created_at=NOW - timedelta(days=2),
        sla_started_at=NOW - timedelta(days=2),
        sla_due_at=sla_due_at,
    )
    db_session.add(ticket)
    db_session.commit()
    return ticket


def _assignment(
    db_session,
    ticket,
    technician,
    *,
    assigned_at,
    status=AssignmentStatus.ASSIGNED,
    started_at=None,
    completed_at=None,
    is_active=True,
):
    assignment = TicketAssignment(
        ticket_id=ticket.id,
        technician_id=technician.user_id,
        assigned_by_user_id=None,
        assignment_source="AUTO_SCHEDULER",
        status=status,
        assigned_at=assigned_at,
        started_at=started_at,
        completed_at=completed_at,
        is_active=is_active,
    )
    db_session.add(assignment)
    db_session.commit()
    return assignment


def test_resolve_period_returns_the_calendar_week_and_month_holding_the_moment():
    week_start, week_end = resolve_period("week", NOW)
    month_start, month_end = resolve_period("month", NOW)

    assert (week_start, week_end) == (datetime(2026, 8, 17, tzinfo=UTC), datetime(2026, 8, 24, tzinfo=UTC))
    assert (month_start, month_end) == (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC))


def test_active_days_count_only_days_readiness_was_recorded_as_on(db_session):
    _seed(db_session)
    technician = _technician(db_session, "Available", is_available=True)
    record_availability(db_session, technician.user_id, is_available=True, changed_at=datetime(2026, 8, 17, 8, tzinfo=UTC))
    record_availability(db_session, technician.user_id, is_available=False, changed_at=datetime(2026, 8, 19, 9, tzinfo=UTC))
    silent = _technician(db_session, "No history", is_available=True)
    db_session.commit()

    report = TechnicianReportService(db_session).productivity("week", NOW)
    rows = {row["technician_id"]: row for row in report["rows"]}

    # 17, 18 and the part of 19 before the switch-off.
    assert rows[technician.user_id]["active_days"] == 3
    # Nothing was written down for this one, so the report does not claim any.
    assert rows[silent.user_id]["active_days"] == 0


def test_completed_and_late_counts_use_the_latest_assignment(db_session):
    resident, location, water = _seed(db_session)
    first = _technician(db_session, "First")
    second = _technician(db_session, "Second")
    ticket = _ticket(db_session, resident, location, water, sla_due_at=NOW - timedelta(days=1))
    _assignment(
        db_session,
        ticket,
        first,
        assigned_at=NOW - timedelta(days=2),
        status=AssignmentStatus.REASSIGNED,
        completed_at=None,
        is_active=False,
    )
    _assignment(
        db_session,
        ticket,
        second,
        assigned_at=NOW - timedelta(hours=20),
        status=AssignmentStatus.COMPLETED,
        # Started nineteen hours ago, a day after the deadline passed.
        started_at=NOW - timedelta(hours=19),
        completed_at=NOW - timedelta(hours=2),
    )

    rows = {row["technician_id"]: row for row in TechnicianReportService(db_session).productivity("week", NOW)["rows"]}

    assert rows[second.user_id]["completed_tickets"] == 1
    assert rows[second.user_id]["sla_late_tickets"] == 1
    # The hand-over happened inside the period and came from another technician.
    assert rows[second.user_id]["reassigned_from_other_tickets"] == 1
    assert rows[first.user_id]["completed_tickets"] == 0
    assert rows[first.user_id]["reassigned_from_other_tickets"] == 0


def test_an_on_time_start_is_not_counted_as_late(db_session):
    resident, location, water = _seed(db_session)
    technician = _technician(db_session, "On time")
    ticket = _ticket(db_session, resident, location, water, sla_due_at=NOW + timedelta(days=1))
    _assignment(
        db_session,
        ticket,
        technician,
        assigned_at=NOW - timedelta(days=1),
        status=AssignmentStatus.COMPLETED,
        started_at=NOW - timedelta(hours=20),
        completed_at=NOW - timedelta(hours=1),
    )

    rows = {row["technician_id"]: row for row in TechnicianReportService(db_session).productivity("week", NOW)["rows"]}

    assert rows[technician.user_id]["completed_tickets"] == 1
    assert rows[technician.user_id]["sla_late_tickets"] == 0


def test_a_long_repair_started_on_time_is_not_late(db_session):
    """The measurement point that changed in risk scoring v2 §6.

    The promise is that somebody arrives. A job begun an hour before the
    deadline that turned out to need three days is not a missed promise, and
    counting it as one makes a difficult fault look like a broken process.
    """
    resident, location, water = _seed(db_session)
    technician = _technician(db_session, "Slow but punctual")
    ticket = _ticket(db_session, resident, location, water, sla_due_at=NOW - timedelta(days=2))
    _assignment(
        db_session,
        ticket,
        technician,
        assigned_at=NOW - timedelta(days=4),
        status=AssignmentStatus.COMPLETED,
        # Started before the deadline, finished long after it.
        started_at=NOW - timedelta(days=3),
        completed_at=NOW - timedelta(hours=1),
    )

    rows = {row["technician_id"]: row for row in TechnicianReportService(db_session).productivity("week", NOW)["rows"]}

    assert rows[technician.user_id]["completed_tickets"] == 1
    assert rows[technician.user_id]["sla_late_tickets"] == 0


def test_a_quick_repair_started_late_is_late(db_session):
    """The other half of the same rule: finishing fast does not undo arriving
    two days after it was promised."""
    resident, location, water = _seed(db_session)
    technician = _technician(db_session, "Fast but tardy")
    ticket = _ticket(db_session, resident, location, water, sla_due_at=NOW - timedelta(days=3))
    _assignment(
        db_session,
        ticket,
        technician,
        assigned_at=NOW - timedelta(days=4),
        status=AssignmentStatus.COMPLETED,
        started_at=NOW - timedelta(hours=3),
        completed_at=NOW - timedelta(hours=2),
    )

    rows = {row["technician_id"]: row for row in TechnicianReportService(db_session).productivity("week", NOW)["rows"]}

    assert rows[technician.user_id]["sla_late_tickets"] == 1


def test_an_assignment_with_no_recorded_start_is_not_charged_as_late(db_session):
    """A gap in the record is not evidence of a violation."""
    resident, location, water = _seed(db_session)
    technician = _technician(db_session, "No start stamp")
    ticket = _ticket(db_session, resident, location, water, sla_due_at=NOW - timedelta(days=3))
    _assignment(
        db_session,
        ticket,
        technician,
        assigned_at=NOW - timedelta(days=4),
        status=AssignmentStatus.COMPLETED,
        completed_at=NOW - timedelta(hours=2),
    )

    rows = {row["technician_id"]: row for row in TechnicianReportService(db_session).productivity("week", NOW)["rows"]}

    assert rows[technician.user_id]["completed_tickets"] == 1
    assert rows[technician.user_id]["sla_late_tickets"] == 0


def test_an_emergency_is_not_in_a_technicians_denominator(db_session):
    """A P5 is never dispatched, so it is neither a technician's success nor
    their failure. `docs/risk_scoring_v2.md` §6.2."""
    from src.models.enums import Priority

    resident, location, water = _seed(db_session)
    technician = _technician(db_session, "Emergency responder")
    ticket = _ticket(db_session, resident, location, water, sla_due_at=NOW - timedelta(days=3))
    ticket.priority = Priority.P5
    db_session.commit()
    _assignment(
        db_session,
        ticket,
        technician,
        assigned_at=NOW - timedelta(days=4),
        status=AssignmentStatus.COMPLETED,
        started_at=NOW - timedelta(hours=3),
        completed_at=NOW - timedelta(hours=2),
    )

    rows = {row["technician_id"]: row for row in TechnicianReportService(db_session).productivity("week", NOW)["rows"]}

    assert rows[technician.user_id]["completed_tickets"] == 1
    assert rows[technician.user_id]["sla_late_tickets"] == 0


def test_a_ticket_with_no_sla_due_date_is_never_counted_late(db_session):
    resident, location, water = _seed(db_session)
    technician = _technician(db_session, "No deadline")
    ticket = _ticket(db_session, resident, location, water, sla_due_at=None)
    _assignment(
        db_session,
        ticket,
        technician,
        assigned_at=NOW - timedelta(days=1),
        status=AssignmentStatus.COMPLETED,
        completed_at=NOW - timedelta(hours=1),
    )

    rows = {row["technician_id"]: row for row in TechnicianReportService(db_session).productivity("week", NOW)["rows"]}

    assert rows[technician.user_id]["sla_late_tickets"] == 0


def test_roster_response_exposes_the_identity_phone(db_session):
    from src.api.routes.coordinator.technicians import _technician_response

    user = UserProfile(user_id=uuid4(), full_name="KTV Phone", phone_e164="+84901234567", role=UserRole.TECHNICIAN)
    profile = TechnicianProfile(user=user, phone_number="0901234567", is_active=True, is_available=True)
    db_session.add_all([user, profile])
    db_session.commit()

    assert _technician_response(profile).phone_e164 == "+84901234567"


def test_roster_response_ignores_a_profile_phone_that_is_not_e164(db_session):
    from src.api.routes.coordinator.technicians import _technician_response

    user = UserProfile(user_id=uuid4(), full_name="KTV Legacy", role=UserRole.TECHNICIAN)
    profile = TechnicianProfile(user=user, phone_number="0901234567", is_active=True, is_available=True)
    db_session.add_all([user, profile])
    db_session.commit()

    # Better an empty cell than a number labelled E.164 that is not one.
    assert _technician_response(profile).phone_e164 is None
