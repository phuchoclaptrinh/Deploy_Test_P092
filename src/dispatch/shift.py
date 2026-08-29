"""The working window, as calendar arithmetic (§3, §6).

08:00 to 18:00, **every day of the week**, in Vietnam local time. Ten working
hours a day, no weekend rule, no holiday calendar -- §3 says "every day of the
week" and this module does not quietly add exceptions to that.

Everything here takes and returns timezone-aware UTC datetimes. Local time is
an implementation detail of where the window boundaries fall, and letting a
naive local datetime escape into the scheduler is how an off-by-seven-hours bug
gets written. The one place local time is visible is `shift_bounds`, and it
returns UTC too.

Vietnam has observed no daylight saving since 1975, so the fixed +07:00 offset
below is exact rather than an approximation. `ZoneInfo` is still preferred when
the platform has a tz database, because a fixed offset would be wrong if that
ever changed, and the fallback exists only so a deployment without `tzdata`
installed still schedules correctly instead of refusing to start.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone

try:  # pragma: no cover - exercised by whichever branch the platform provides
    from zoneinfo import ZoneInfo

    VN_TZ: timezone | ZoneInfo = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception:  # pragma: no cover - no tz database on this platform
    VN_TZ = timezone(timedelta(hours=7), name="ICT")

SHIFT_START = time(8, 0)
SHIFT_END = time(18, 0)
#: Ten hours. Derived, so moving either boundary above cannot leave it stale.
SHIFT_LENGTH = timedelta(
    hours=SHIFT_END.hour - SHIFT_START.hour,
    minutes=SHIFT_END.minute - SHIFT_START.minute,
)


def as_utc(value: datetime | None) -> datetime | None:
    """Normalize a stored timestamp before any Python-side comparison.

    PostgreSQL hands back aware values and SQLite naive ones, and a schedule
    that mixes a freshly written aware value with a reloaded naive one raises
    on the first comparison rather than at the point the mistake was made.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def to_local(moment: datetime) -> datetime:
    return as_utc(moment).astimezone(VN_TZ)


def shift_bounds(local_day: date) -> tuple[datetime, datetime]:
    """The UTC open/close instants of one local working day."""
    opens = datetime.combine(local_day, SHIFT_START, tzinfo=VN_TZ)
    closes = datetime.combine(local_day, SHIFT_END, tzinfo=VN_TZ)
    return opens.astimezone(UTC), closes.astimezone(UTC)


def is_within_shift(moment: datetime) -> bool:
    """§3: is this instant inside the working window?

    The close boundary is exclusive. 18:00 sharp is the end of the day, not a
    moment at which new work may still be handed out.
    """
    local = to_local(moment)
    return SHIFT_START <= local.timetz().replace(tzinfo=None) < SHIFT_END


def next_shift_open(moment: datetime) -> datetime:
    """The first working instant at or after `moment`.

    Inside the window this is `moment` itself. Before 08:00 it is today's
    opening; at or after 18:00 it is tomorrow's, because the window runs every
    day and there is never a longer gap than one night.
    """
    moment = as_utc(moment)
    local = to_local(moment)
    local_time = local.timetz().replace(tzinfo=None)
    if local_time < SHIFT_START:
        opens, _ = shift_bounds(local.date())
        return opens
    if local_time >= SHIFT_END:
        opens, _ = shift_bounds(local.date() + timedelta(days=1))
        return opens
    return moment


def advance(start: datetime, duration: timedelta) -> datetime:
    """Where `duration` of *working* time lands, starting from `start`.

    Work spills across the overnight gap rather than compressing into it: six
    hours begun at 15:00 finishes at 11:00 the next morning, not at 21:00 the
    same evening. That is the whole reason this cannot be plain addition.

    A non-positive duration returns the next working instant, so a zero-length
    unit is still placed inside the window rather than at an arbitrary clock
    time outside it.
    """
    cursor = next_shift_open(start)
    remaining = duration
    if remaining <= timedelta(0):
        return cursor
    # Bounded by construction: each iteration consumes a whole working day
    # except possibly the first and last, so the loop count is the duration in
    # days plus two.
    while True:
        _, closes = shift_bounds(to_local(cursor).date())
        available = closes - cursor
        if remaining <= available:
            return cursor + remaining
        remaining -= available
        cursor = next_shift_open(closes)


def working_seconds_between(start: datetime, end: datetime) -> int:
    """Working seconds from `start` to `end`, negative when `end` precedes it.

    This is the unit slack is measured in. Wall-clock seconds would count the
    fourteen hours a technician is off shift as capacity, which is exactly the
    error that makes an overnight schedule look comfortable.
    """
    start, end = as_utc(start), as_utc(end)
    if end < start:
        return -working_seconds_between(end, start)

    total = 0
    cursor = next_shift_open(start)
    if cursor > end:
        return 0
    while True:
        _, closes = shift_bounds(to_local(cursor).date())
        segment_end = min(closes, end)
        if segment_end > cursor:
            total += int((segment_end - cursor).total_seconds())
        if closes >= end:
            return total
        cursor = next_shift_open(closes)


__all__ = [
    "SHIFT_END",
    "SHIFT_LENGTH",
    "SHIFT_START",
    "VN_TZ",
    "advance",
    "as_utc",
    "is_within_shift",
    "next_shift_open",
    "shift_bounds",
    "to_local",
    "working_seconds_between",
]
