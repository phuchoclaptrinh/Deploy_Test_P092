"""`SERVICE_HOURS_RISK_V2`: the clock production writes deadlines under.

`docs/risk_scoring_v2.md` §6. Three things this file holds:

* the durations are the ones the contract publishes, per band;
* P1-P4 consume *service* minutes and pause overnight, while P5 runs on the wall
  clock — a five-minute emergency promise that paused at 18:00 would not be an
  emergency promise;
* the two v1 policies are untouched, because a v1 simulator run has to reproduce
  its recorded output exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.dispatch.shift import VN_TZ
from src.domain.sla_clock import (
    COMPLIANCE_PRIORITIES,
    CURRENT_POLICY,
    POLICY_SLA_MINUTES,
    SlaPolicy,
    add_sla_duration,
    counts_toward_compliance,
    runs_on_service_hours,
    sla_duration,
)
from src.models.enums import Priority

V2 = SlaPolicy.SERVICE_HOURS_RISK_V2


def local(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=VN_TZ).astimezone(UTC)


def as_local(moment: datetime) -> str:
    return moment.astimezone(VN_TZ).strftime("%Y-%m-%dT%H:%M")


def _ticket(*, priority, created_at):
    """A detached Ticket row. `recalculate_sla` reads four fields and writes one,
    so nothing here needs a session."""
    from uuid import uuid4

    from src.database.models.ticket import Ticket

    return Ticket(
        reporter_user_id=uuid4(),
        source_unit_id=uuid4(),
        location_id=uuid4(),
        priority=priority,
        sla_started_at=created_at,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# The published table.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("priority", "minutes"),
    [
        (Priority.P1, 1800),
        (Priority.P2, 1200),
        (Priority.P3, 600),
        (Priority.P4, 180),
        (Priority.P5, 5),
    ],
)
def test_each_band_has_the_duration_the_contract_publishes(priority, minutes):
    assert POLICY_SLA_MINUTES[V2][priority] == minutes
    assert sla_duration(priority, V2) == timedelta(minutes=minutes)


def test_production_writes_deadlines_under_the_v2_policy():
    assert CURRENT_POLICY is V2


def test_the_v2_policy_covers_every_band():
    assert set(POLICY_SLA_MINUTES[V2]) == set(Priority)


# ---------------------------------------------------------------------------
# Which clock runs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("priority", [Priority.P1, Priority.P2, Priority.P3, Priority.P4])
def test_the_working_bands_pause_outside_the_service_window(priority):
    assert runs_on_service_hours(priority, V2) is True


def test_the_emergency_band_runs_continuously():
    assert runs_on_service_hours(Priority.P5, V2) is False


def test_the_exemption_moved_with_the_scale():
    """Under v1 the 24/7 band was P3; under v2 it is P5.

    A hard-coded `is not Priority.P3` would have put v2's five-minute emergency
    promise on a clock that pauses overnight, and put v2's ten-hour P3 on one
    that never pauses.
    """
    assert runs_on_service_hours(Priority.P3, SlaPolicy.SERVICE_HOURS_DRAFT_V1) is False
    assert runs_on_service_hours(Priority.P3, V2) is True
    assert runs_on_service_hours(Priority.P5, V2) is False


# ---------------------------------------------------------------------------
# Deadlines across the overnight gap.
# ---------------------------------------------------------------------------


def test_a_p4_reported_mid_afternoon_falls_due_the_same_day():
    due = add_sla_duration(local("2026-08-26T14:00"), sla_duration(Priority.P4, V2), Priority.P4, V2)
    assert as_local(due) == "2026-08-26T17:00"


def test_a_p4_reported_late_in_the_day_finishes_its_clock_the_next_morning():
    """Two of its three hours are left when the window closes, so it falls due
    at 10:00 rather than at 20:00 with nobody in the building."""
    due = add_sla_duration(local("2026-08-26T17:00"), sla_duration(Priority.P4, V2), Priority.P4, V2)
    assert as_local(due) == "2026-08-27T10:00"


def test_a_report_arriving_after_hours_starts_its_clock_at_the_next_opening():
    due = add_sla_duration(local("2026-08-26T22:00"), sla_duration(Priority.P4, V2), Priority.P4, V2)
    assert as_local(due) == "2026-08-27T11:00"


def test_a_p3_is_exactly_one_working_day():
    """600 service minutes is one whole ten-hour window: open to close."""
    due = add_sla_duration(local("2026-08-26T08:00"), sla_duration(Priority.P3, V2), Priority.P3, V2)
    assert as_local(due) == "2026-08-26T18:00"


def test_a_p1_is_three_working_days():
    """1800 service minutes over a ten-hour window, ending at the third close."""
    due = add_sla_duration(local("2026-08-26T08:00"), sla_duration(Priority.P1, V2), Priority.P1, V2)
    assert as_local(due) == "2026-08-28T18:00"


def test_an_emergency_reported_at_midnight_is_due_five_minutes_later():
    """Not at 08:05 the next morning. The whole point of the exemption."""
    due = add_sla_duration(local("2026-08-26T00:00"), sla_duration(Priority.P5, V2), Priority.P5, V2)
    assert as_local(due) == "2026-08-26T00:05"


# ---------------------------------------------------------------------------
# Who is measured.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("priority", [Priority.P1, Priority.P2, Priority.P3, Priority.P4])
def test_the_dispatched_bands_count_toward_compliance(priority):
    assert counts_toward_compliance(priority) is True


def test_the_emergency_band_is_excluded_from_compliance():
    """Excluded from the denominator, not scored as a pass. An emergency nobody
    was dispatched to is not a technician's success or failure."""
    assert counts_toward_compliance(Priority.P5) is False
    assert Priority.P5 not in COMPLIANCE_PRIORITIES


# ---------------------------------------------------------------------------
# The v1 policies are untouched.
# ---------------------------------------------------------------------------


def test_the_v1_policies_keep_their_recorded_durations():
    assert POLICY_SLA_MINUTES[SlaPolicy.WALL_CLOCK_V1] == {
        Priority.P1: 4320,
        Priority.P2: 180,
        Priority.P3: 5,
    }
    assert POLICY_SLA_MINUTES[SlaPolicy.SERVICE_HOURS_DRAFT_V1] == {
        Priority.P1: 1800,
        Priority.P2: 180,
        Priority.P3: 5,
    }


def test_the_v1_policies_still_only_know_three_bands():
    """They are read by a simulator replaying v1 scenarios. Widening them would
    let a v2 priority reach a policy that was never calibrated for it, and
    silently produce a comparison number that means nothing."""
    for policy in (SlaPolicy.WALL_CLOCK_V1, SlaPolicy.SERVICE_HOURS_DRAFT_V1):
        assert set(POLICY_SLA_MINUTES[policy]) == {Priority.P1, Priority.P2, Priority.P3}


def test_the_v1_wall_clock_deadline_is_unchanged():
    due = add_sla_duration(
        local("2026-08-26T17:00"),
        sla_duration(Priority.P2, SlaPolicy.WALL_CLOCK_V1),
        Priority.P2,
        SlaPolicy.WALL_CLOCK_V1,
    )
    assert as_local(due) == "2026-08-26T20:00"


def test_ticket_deadlines_are_written_through_the_same_function(db_session):
    """The live deadline and a simulated one come from one implementation.

    Two would eventually disagree, and the disagreement would surface as a
    report contradicting the screen a coordinator was looking at.
    """
    from src.services.risk_assessment_service import RiskAssessmentService

    created = local("2026-08-26T17:00")
    ticket = _ticket(priority=Priority.P4, created_at=created)

    RiskAssessmentService(db_session).recalculate_sla(ticket)

    expected = add_sla_duration(created, sla_duration(Priority.P4, V2), Priority.P4, V2)
    assert ticket.sla_due_at == expected
    assert as_local(ticket.sla_due_at) == "2026-08-27T10:00"


def test_an_emergency_ticket_deadline_does_not_pause_overnight(db_session):
    from src.services.risk_assessment_service import RiskAssessmentService

    created = local("2026-08-26T23:00")
    ticket = _ticket(priority=Priority.P5, created_at=created)

    RiskAssessmentService(db_session).recalculate_sla(ticket)

    assert ticket.sla_due_at == created + timedelta(minutes=5)


def test_a_ticket_with_no_priority_has_no_deadline(db_session):
    from src.services.risk_assessment_service import RiskAssessmentService

    ticket = _ticket(priority=None, created_at=datetime.now(UTC))
    ticket.sla_due_at = datetime.now(UTC)

    RiskAssessmentService(db_session).recalculate_sla(ticket)

    assert ticket.sla_due_at is None
