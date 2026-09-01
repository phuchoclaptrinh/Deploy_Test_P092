"""The SLA clock: one place that decides when a promise falls due.

Three policies, and the difference between the first two is *when the clock
runs*, never how long the promise is. The third changes both, because the
priority scale underneath it changed:

* ``WALL_CLOCK_V1`` -- today's production behaviour. The clock runs continuously.
  A P2 reported at 17:00 with a three-hour SLA is due at 20:00, two hours after
  the last technician went home.
* ``SERVICE_HOURS_DRAFT_V1`` -- the proposal. For P1 and P2 the clock only runs
  inside the 08:00-18:00 service window, so that same P2 consumes one hour
  before 18:00, pauses overnight, and falls due at 10:00 the next morning. P3
  keeps running 24/7 because it is the emergency priority: a five-minute
  emergency SLA that pauses overnight would not be an emergency SLA.
* ``SERVICE_HOURS_RISK_V2`` -- production, since risk scoring v2. Five bands
  instead of three, and the emergency moved from P3 to P5, so the band that
  runs 24/7 moved with it. P1-P4 pause outside the window.

`_DRAFT_` is in the second name deliberately, and it stays there: nothing calls
`SERVICE_HOURS_DRAFT_V1` in production. It exists so the simulator can run a v1
dataset under both v1 policies and show what adopting the second would have
done.

`SERVICE_HOURS_RISK_V2` is the one production uses. That is the day the module
docstring used to talk about in the future tense: `RiskAssessmentService`
computes every deadline through `add_sla_duration` here, so a report and the
screen a coordinator was looking at cannot disagree about when something was
due.

Three rules this module deliberately does **not** implement:

* **No lunch break.** The service window is one unbroken 08:00-18:00 block.
* **No weekend or holiday calendar.** `shift.py` says every day of the week, and
  this does not quietly add exceptions to that.
* **Technician availability is not a service-hours input.** `is_available` is a
  staffing fact; the SLA is a promise to a resident. Nobody's day off shortens
  what was promised, so no function here takes a technician.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from src.dispatch.shift import advance, as_utc, working_seconds_between
from src.models.enums import Priority


class SlaPolicy(str, Enum):  # noqa: UP042
    """Which clock a ticket's deadline was computed under.

    Stored per ticket rather than read from configuration, so that changing the
    policy never silently moves a deadline that has already been published. A
    ticket keeps the policy it was created under until somebody deliberately
    migrates it.
    """

    WALL_CLOCK_V1 = "WALL_CLOCK_V1"
    SERVICE_HOURS_DRAFT_V1 = "SERVICE_HOURS_DRAFT_V1"
    #: Production, since risk scoring v2. Service hours for P1-P4; P5 runs
    #: continuously because a five-minute emergency promise that paused
    #: overnight would not be an emergency promise.
    SERVICE_HOURS_RISK_V2 = "SERVICE_HOURS_RISK_V2"


#: The canonical duration for each priority under each policy, in minutes.
#:
#: P1 differs between the two and that is the whole point of publishing this
#: table. 4320 wall-clock minutes is 72 hours, which was written meaning "three
#: days". Run the same 4320 through a ten-hour service window and it becomes 7.2
#: working days -- a materially weaker promise arrived at by accident. So the
#: service-hours policy restates P1 as 1800 service minutes, which *is* three
#: working days. P2 is unchanged because three hours rarely crosses a boundary,
#: and P3 is unchanged because it never pauses at all.
#:
#: The v2 row is `docs/risk_scoring_v2.md` §6.1. The two v1 rows read the *old*
#: scale, where P3 was the five-minute emergency; they are kept verbatim so a v1
#: simulator run still reproduces its recorded output, and nothing may look up a
#: v1 policy with a v2 priority.
POLICY_SLA_MINUTES: dict[SlaPolicy, dict[Priority, int]] = {
    SlaPolicy.WALL_CLOCK_V1: {Priority.P1: 4320, Priority.P2: 180, Priority.P3: 5},
    SlaPolicy.SERVICE_HOURS_DRAFT_V1: {Priority.P1: 1800, Priority.P2: 180, Priority.P3: 5},
    SlaPolicy.SERVICE_HOURS_RISK_V2: {
        Priority.P1: 1800,
        Priority.P2: 1200,
        Priority.P3: 600,
        Priority.P4: 180,
        # Not a technician's deadline at all: five wall-clock minutes for
        # Building Management to answer an emergency they are handling by hand.
        Priority.P5: 5,
    },
}

#: The bands a technician's SLA compliance is measured over. P5 is excluded
#: from the denominator, not scored as a pass -- an emergency nobody was
#: dispatched to is not a technician's success or failure.
#: `docs/risk_scoring_v2.md` §6.2.
COMPLIANCE_PRIORITIES: frozenset[Priority] = frozenset(
    {Priority.P1, Priority.P2, Priority.P3, Priority.P4}
)

#: The policy production writes new deadlines under.
CURRENT_POLICY = SlaPolicy.SERVICE_HOURS_RISK_V2


#: Which priority each service-hours policy treats as the 24/7 emergency. The
#: scale inverted between them, so the exemption moved with it.
_ALWAYS_RUNNING: dict[SlaPolicy, Priority] = {
    SlaPolicy.SERVICE_HOURS_DRAFT_V1: Priority.P3,
    SlaPolicy.SERVICE_HOURS_RISK_V2: Priority.P5,
}


def runs_on_service_hours(priority: Priority, policy: SlaPolicy) -> bool:
    """Does this ticket's clock pause outside 08:00-18:00?

    The single predicate the other three functions branch on, so "the emergency
    band is always 24/7" is stated once instead of three times.

    Which band that is depends on the policy, and getting it from a table rather
    than from a literal is the whole point: under v1 it was P3 and under v2 it is
    P5, so a hard-coded `is not Priority.P3` would have quietly put v2's
    five-minute emergency promise on a clock that pauses overnight.
    """
    exempt = _ALWAYS_RUNNING.get(policy)
    return exempt is not None and priority is not exempt


def counts_toward_compliance(priority: Priority) -> bool:
    """Whether a technician's SLA is measured on this band at all."""
    return priority in COMPLIANCE_PRIORITIES


def sla_duration(priority: Priority, policy: SlaPolicy) -> timedelta:
    """The canonical promise for one priority under one policy."""
    return timedelta(minutes=POLICY_SLA_MINUTES[policy][priority])


def add_sla_duration(
    started_at: datetime,
    duration: timedelta,
    priority: Priority,
    policy: SlaPolicy,
) -> datetime:
    """When the promise falls due.

    On service hours this is `shift.advance`, the same function production's
    scheduler uses to lay work across the overnight gap -- so a deadline and a
    schedule can never disagree about where 18:00 is.

    A report arriving outside the window starts its clock at the next opening
    rather than burning SLA overnight: `advance` applies `next_shift_open`
    first, so 18:00 + three hours is 11:00 tomorrow, not 21:00 tonight.
    """
    if runs_on_service_hours(priority, policy):
        return advance(started_at, duration)
    return as_utc(started_at) + duration


def sla_seconds_between(
    started_at: datetime,
    ended_at: datetime,
    priority: Priority,
    policy: SlaPolicy,
) -> int:
    """How much SLA one interval consumed. Negative when `ended_at` is earlier."""
    if runs_on_service_hours(priority, policy):
        return working_seconds_between(started_at, ended_at)
    return int((as_utc(ended_at) - as_utc(started_at)).total_seconds())


def sla_late_seconds(
    due_at: datetime,
    completed_at: datetime,
    priority: Priority,
    policy: SlaPolicy,
) -> int:
    """How late, floored at zero, in the units the promise was made in.

    On service hours this counts *service* seconds. A job due 17:50 and finished
    08:10 the next morning is twenty minutes late against the promise -- not
    fourteen hours. The fourteen hours are real and a resident felt every one of
    them, which is why the simulator reports that separately as
    `resident_wall_late_minutes` and never adds the two together.
    """
    return max(0, sla_seconds_between(due_at, completed_at, priority, policy))


def wall_late_seconds(due_at: datetime, completed_at: datetime) -> int:
    """How late in wall-clock seconds, whatever the policy says.

    Always measured continuously, because this is the number that answers "how
    long past the promised time did the resident actually wait", and no clock
    policy changes that.
    """
    return max(0, int((as_utc(completed_at) - as_utc(due_at)).total_seconds()))


__all__ = [
    "COMPLIANCE_PRIORITIES",
    "CURRENT_POLICY",
    "POLICY_SLA_MINUTES",
    "SlaPolicy",
    "add_sla_duration",
    "counts_toward_compliance",
    "runs_on_service_hours",
    "sla_duration",
    "sla_late_seconds",
    "sla_seconds_between",
    "wall_late_seconds",
]
