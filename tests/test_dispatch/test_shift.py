"""The working window: 08:00-18:00, every day, Vietnam time (§3, §6).

These are the arithmetic the whole scheduler stands on. Getting `advance` wrong
by one overnight gap does not fail loudly -- it produces a plausible-looking
schedule that books fourteen hours of night as capacity, which is exactly the
kind of bug that only surfaces as unexplained lateness weeks later.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.dispatch.shift import (
    SHIFT_LENGTH,
    VN_TZ,
    advance,
    is_within_shift,
    next_shift_open,
    to_local,
    working_seconds_between,
)


def local(text: str) -> datetime:
    """A Vietnam wall-clock time, as the UTC instant the system stores."""
    return datetime.fromisoformat(text).replace(tzinfo=VN_TZ).astimezone(UTC)


def stamp(moment: datetime) -> str:
    return to_local(moment).strftime("%Y-%m-%d %H:%M")


def test_the_window_is_ten_hours_long():
    assert SHIFT_LENGTH == timedelta(hours=10)


@pytest.mark.parametrize(
    ("moment", "inside"),
    [
        ("2026-08-26T08:00", True),
        ("2026-08-26T09:30", True),
        ("2026-08-26T17:59", True),
        ("2026-08-26T07:59", False),
        # 18:00 sharp is the end of the day, not a moment work may still start.
        ("2026-08-26T18:00", False),
        ("2026-08-26T23:00", False),
    ],
)
def test_shift_boundaries_are_half_open(moment, inside):
    assert is_within_shift(local(moment)) is inside


@pytest.mark.parametrize("day", ["2026-08-29", "2026-08-30"])
def test_the_shift_runs_at_weekends_too(day):
    """§3 says every day of the week, and means it.

    A weekend rule is the most tempting thing to add to a scheduler and the one
    §3 explicitly does not ask for.
    """
    assert is_within_shift(local(f"{day}T10:00")) is True


def test_next_open_is_today_before_the_shift_and_tomorrow_after_it():
    assert stamp(next_shift_open(local("2026-08-26T06:00"))) == "2026-08-26 08:00"
    assert stamp(next_shift_open(local("2026-08-26T19:00"))) == "2026-08-27 08:00"
    # Inside the window it is the moment itself, untouched.
    inside = local("2026-08-26T09:15")
    assert next_shift_open(inside) == inside


def test_work_spills_across_the_overnight_gap_rather_than_into_it():
    """Six hours begun at 15:00 finishes at 11:00 the next morning.

    Not 21:00 the same evening -- there are only three working hours left in the
    day, and the other three belong to tomorrow.
    """
    assert stamp(advance(local("2026-08-26T15:00"), timedelta(hours=6))) == "2026-08-27 11:00"


def test_a_full_day_of_work_lands_exactly_on_the_close():
    assert stamp(advance(local("2026-08-26T08:00"), timedelta(hours=10))) == "2026-08-26 18:00"


def test_work_starting_outside_the_window_begins_at_the_next_opening():
    assert stamp(advance(local("2026-08-26T22:00"), timedelta(hours=3))) == "2026-08-27 11:00"


def test_multi_day_work_consumes_whole_days():
    # 25 hours = two full 10-hour days plus five hours of the third.
    assert stamp(advance(local("2026-08-26T08:00"), timedelta(hours=25))) == "2026-08-28 13:00"


def test_working_seconds_ignore_the_hours_nobody_is_on_shift():
    # 15:00 to 11:00 next day is 20 wall-clock hours and 6 working ones.
    assert working_seconds_between(local("2026-08-26T15:00"), local("2026-08-27T11:00")) == 6 * 3600
    # A span entirely outside the window is worth nothing at all.
    assert working_seconds_between(local("2026-08-26T19:00"), local("2026-08-27T07:00")) == 0


def test_working_seconds_are_signed():
    """Slack is `deadline - finish`, and a missed deadline must read negative."""
    forward = working_seconds_between(local("2026-08-26T09:00"), local("2026-08-26T12:00"))
    backward = working_seconds_between(local("2026-08-26T12:00"), local("2026-08-26T09:00"))
    assert forward == 3 * 3600
    assert backward == -forward
