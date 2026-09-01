"""One question, asked from every path that could put work on a technician.

    may this ticket be assigned to anybody, by anybody, right now?

`docs/risk_scoring_v2.md` §8 makes P5 manual-only, and "manual-only" is worth
being precise about: it does not mean "the automatic path skips it". It means
**no path assigns it** -- not auto-dispatch, not the dispatch worker, not the
backlog sweep, not a coordinator clicking assign on a ticket screen, not a case
assignment, not a Visual Assignment drop, and not a reassignment of work that
was already placed. Building Management handles a P5 by walking there.

That is ten call sites, and ten independent checks is nine chances to write one
of them slightly differently. So there is one function, it takes a ticket, and
every path calls it.

**Why a domain module.** The callers span dispatch, the assignment service, the
visual board, the coordinator API and the worker -- five packages with no common
base class, and two of them (the enqueue predicate and the scheduler) must not
import a service layer. This takes a `Ticket` and returns a verdict; it opens no
session and reads no configuration, so anybody can call it.

**What it deliberately does not decide.** Whether a *technician* is eligible,
whether a shift is open, whether the ticket is approved, whether somebody else
already took it: those are the assignment rules and they live where they always
did. This answers one question, and a caller that passes it still has every
other check to satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.risk_scoring import EMERGENCY_PRIORITY, is_emergency
from src.models.enums import Priority

#: What a refused assignment says. Written once so the board, the API and the
#: audit trail all give the coordinator the same sentence.
EMERGENCY_MANUAL_ONLY_MESSAGE = (
    "Phản ánh ở mức khẩn cấp P5 do Ban quản lý xử lý thủ công, "
    "không phân việc cho kỹ thuật viên."
)


@dataclass(frozen=True)
class AssignmentVerdict:
    """Allowed, or refused with a reason somebody can act on."""

    allowed: bool
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.allowed


ALLOWED = AssignmentVerdict(True)


class EmergencyManualOnlyError(Exception):
    """A P5 ticket reached an assignment path. Never a normal outcome.

    Carries no HTTP status of its own: the API layer maps it, and the worker
    and the scheduler catch it as the refusal it is.
    """

    def __init__(self, message: str = EMERGENCY_MANUAL_ONLY_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


def ticket_assignment_verdict(priority: Priority | None) -> AssignmentVerdict:
    """The rule itself, over a bare priority.

    Takes a priority rather than a ticket so the scheduler -- which works on
    row tuples, not ORM objects -- asks the same question the API does.

    A ticket with no priority is *not* refused here. "Not yet classified" is a
    different fact from "classified as an emergency", and the paths that care
    about it already refuse an unscored ticket for their own reasons; refusing
    it here too would report the wrong reason to a coordinator.
    """
    if is_emergency(priority):
        return AssignmentVerdict(False, EMERGENCY_MANUAL_ONLY_MESSAGE)
    return ALLOWED


def ticket_assignment_allowed(ticket) -> bool:
    """True when this ticket may be assigned. Never raises."""
    return bool(ticket_assignment_verdict(getattr(ticket, "priority", None)))


def assert_ticket_assignment_allowed(ticket) -> None:
    """Refuse a P5 ticket. The form every mutating path uses.

    Raises `EmergencyManualOnlyError`, which each layer maps to its own
    refusal: a 409 from the API, an escalation from the dispatch worker.
    """
    verdict = ticket_assignment_verdict(getattr(ticket, "priority", None))
    if not verdict:
        raise EmergencyManualOnlyError(verdict.reason or EMERGENCY_MANUAL_ONLY_MESSAGE)


__all__ = [
    "ALLOWED",
    "EMERGENCY_MANUAL_ONLY_MESSAGE",
    "EMERGENCY_PRIORITY",
    "AssignmentVerdict",
    "EmergencyManualOnlyError",
    "assert_ticket_assignment_allowed",
    "ticket_assignment_allowed",
    "ticket_assignment_verdict",
]
