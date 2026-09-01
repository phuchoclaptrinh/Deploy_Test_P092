"""The SLA clock, boundary by boundary.

Every case is stated in Vietnam wall-clock time, because a deadline expressed in
epoch seconds is a deadline nobody will check when it moves.

The four cases the policy was specified against are pinned first and by name:

    P2 180 phút, tạo 07:00 -> due 11:00
    P2 180 phút, tạo 17:00 -> due 10:00 hôm sau
    P2 180 phút, tạo 18:00 -> due 11:00 hôm sau
    P3 5 phút,   tạo 17:58 -> due 18:03
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.dispatch.shift import VN_TZ, to_local
from src.domain.sla_clock import (
    POLICY_SLA_MINUTES,
    SlaPolicy,
    add_sla_duration,
    runs_on_service_hours,
    sla_duration,
    sla_late_seconds,
    sla_seconds_between,
    wall_late_seconds,
)
from src.models.enums import Priority

SERVICE = SlaPolicy.SERVICE_HOURS_DRAFT_V1
WALL = SlaPolicy.WALL_CLOCK_V1


def local(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=VN_TZ).astimezone(UTC)


def stamp(moment: datetime) -> str:
    return to_local(moment).strftime("%Y-%m-%d %H:%M")


def due(created: str, minutes: int, priority: Priority, policy: SlaPolicy) -> str:
    return stamp(add_sla_duration(local(created), timedelta(minutes=minutes), priority, policy))


# ----------------------------------------------------------------------------
# The four specified cases.
# ----------------------------------------------------------------------------


def test_a_p2_reported_before_opening_starts_its_clock_at_08_00():
    """07:00 + 3h = 11:00, not 10:00. The hour before opening is not SLA."""
    assert due("2026-09-01T07:00", 180, Priority.P2, SERVICE) == "2026-09-01 11:00"


def test_a_p2_reported_at_17_00_pauses_overnight():
    """One hour before 18:00, two hours after 08:00."""
    assert due("2026-09-01T17:00", 180, Priority.P2, SERVICE) == "2026-09-02 10:00"


def test_a_p2_reported_exactly_at_closing_starts_the_next_morning():
    """18:00 is the end of the day, not a moment still inside it -- the window's
    close is exclusive, so nothing is consumed today."""
    assert due("2026-09-01T18:00", 180, Priority.P2, SERVICE) == "2026-09-02 11:00"


def test_a_p3_at_17_58_is_due_at_18_03():
    """The emergency priority never pauses. A five-minute SLA that stopped at
    18:00 and resumed at 08:00 would not be an emergency SLA."""
    assert due("2026-09-01T17:58", 5, Priority.P3, SERVICE) == "2026-09-01 18:03"


# ----------------------------------------------------------------------------
# P3 is 24/7 under every policy.
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("policy", [WALL, SERVICE])
def test_p3_is_always_wall_clock(policy: SlaPolicy):
    assert runs_on_service_hours(Priority.P3, policy) is False
    assert due("2026-09-01T23:30", 5, Priority.P3, policy) == "2026-09-01 23:35"


def test_a_p3_reported_at_midnight_is_due_five_minutes_later():
    assert due("2026-09-02T00:00", 5, Priority.P3, SERVICE) == "2026-09-02 00:05"


# ----------------------------------------------------------------------------
# The wall-clock policy is unchanged production behaviour.
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("priority", [Priority.P1, Priority.P2, Priority.P3])
def test_the_wall_clock_policy_never_pauses(priority: Priority):
    assert runs_on_service_hours(priority, WALL) is False
    assert due("2026-09-01T17:00", 180, priority, WALL) == "2026-09-01 20:00"


def test_the_two_policies_agree_when_nothing_crosses_a_boundary():
    """A P2 reported mid-morning falls due at the same instant either way, which
    is why adopting the service clock is not a blanket loosening."""
    assert due("2026-09-01T09:00", 180, Priority.P2, WALL) == due("2026-09-01T09:00", 180, Priority.P2, SERVICE)


# ----------------------------------------------------------------------------
# Multi-day P1.
# ----------------------------------------------------------------------------


def test_a_p1_runs_across_several_days_on_the_service_clock():
    """1800 service minutes is exactly three ten-hour days.

    Started at opening, it therefore lands on the *closing* of the third day
    rather than the opening of the fourth: 08:00 Tuesday plus three full working
    days is 18:00 Thursday, with not one minute left over.
    """
    assert due("2026-09-01T08:00", 1800, Priority.P1, SERVICE) == "2026-09-03 18:00"


def test_the_canonical_p1_duration_differs_between_policies_on_purpose():
    """4320 wall-clock minutes means "three days". Run the same number through a
    ten-hour window and it silently becomes 7.2 working days, so the service
    policy restates it as 1800 service minutes -- the same three working days.
    """
    assert POLICY_SLA_MINUTES[WALL][Priority.P1] == 4320
    assert POLICY_SLA_MINUTES[SERVICE][Priority.P1] == 1800
    assert sla_duration(Priority.P1, SERVICE) == timedelta(minutes=1800)

    wall_p1 = due("2026-09-01T09:00", 4320, Priority.P1, WALL)
    service_p1 = due("2026-09-01T09:00", 1800, Priority.P1, SERVICE)
    assert wall_p1 == "2026-09-04 09:00"
    # Three working days from a 09:00 start: one hour of the first day is gone,
    # so it lands an hour into the fourth morning.
    assert service_p1 == "2026-09-04 09:00"


def test_p2_and_p3_durations_are_identical_across_policies():
    for priority in (Priority.P2, Priority.P3):
        assert POLICY_SLA_MINUTES[WALL][priority] == POLICY_SLA_MINUTES[SERVICE][priority]


# ----------------------------------------------------------------------------
# Elapsed and late.
# ----------------------------------------------------------------------------


def test_service_hours_elapsed_skips_the_night():
    """17:00 to 09:00 the next morning is two service hours, not sixteen."""
    elapsed = sla_seconds_between(local("2026-09-01T17:00"), local("2026-09-02T09:00"), Priority.P2, SERVICE)
    assert elapsed == 2 * 3600


def test_wall_clock_elapsed_counts_the_night():
    elapsed = sla_seconds_between(local("2026-09-01T17:00"), local("2026-09-02T09:00"), Priority.P2, WALL)
    assert elapsed == 16 * 3600


def test_lateness_is_counted_in_the_units_the_promise_was_made_in():
    """Due 17:50, finished 08:10 the next morning: twenty minutes late against
    the promise, fourteen hours and twenty minutes as the resident felt it. Both
    are true and they are never added together."""
    due_at, completed_at = local("2026-09-01T17:50"), local("2026-09-02T08:10")
    assert sla_late_seconds(due_at, completed_at, Priority.P2, SERVICE) == 20 * 60
    assert wall_late_seconds(due_at, completed_at) == (14 * 60 + 20) * 60


def test_a_job_finished_early_is_never_negatively_late():
    due_at, completed_at = local("2026-09-01T15:00"), local("2026-09-01T11:00")
    assert sla_late_seconds(due_at, completed_at, Priority.P2, SERVICE) == 0
    assert wall_late_seconds(due_at, completed_at) == 0


def test_a_late_p3_is_measured_on_the_wall_clock_even_overnight():
    """The emergency clock does not pause, so neither does its lateness."""
    due_at, completed_at = local("2026-09-01T23:05"), local("2026-09-02T00:05")
    assert sla_late_seconds(due_at, completed_at, Priority.P3, SERVICE) == 3600


# ----------------------------------------------------------------------------
# What the clock deliberately does not know about.
# ----------------------------------------------------------------------------


def test_no_lunch_break_is_deducted():
    """The service window is one unbroken block. 11:00 + 3h = 14:00; a
    one-hour lunch rule would make it 15:00, and the MVP has no such rule."""
    assert due("2026-09-01T11:00", 180, Priority.P2, SERVICE) == "2026-09-01 14:00"


def test_the_clock_takes_no_technician_and_no_availability():
    """`is_available` is a staffing fact; the SLA is a promise to a resident.

    Asserted against the signatures rather than by passing a technician in,
    because the guarantee is that there is nowhere to pass one.
    """
    import inspect

    for function in (add_sla_duration, sla_seconds_between, sla_late_seconds):
        parameters = set(inspect.signature(function).parameters)
        assert not parameters & {"technician", "technicians", "is_available", "availability"}


def test_every_weekday_is_a_service_day():
    """A Saturday report is due on Saturday: `shift.py` says every day of the
    week, and this module does not add a weekend rule behind its back."""
    # 2026-09-05 is a Saturday.
    assert due("2026-09-05T09:00", 180, Priority.P2, SERVICE) == "2026-09-05 12:00"
