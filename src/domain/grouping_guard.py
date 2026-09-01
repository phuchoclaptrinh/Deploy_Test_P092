"""One question, asked from every path that could put a ticket into a case.

    may this ticket be counted as part of an incident case?

The answer is no for an emergency, and the reason is arithmetic rather than
policy. An `IncidentCase` exists to establish one number -- how many apartments
a problem has actually reached -- and that number is every member's
`affected_scope`. So a member is not a passenger: it is a term in four other
tickets' scores.

A P5 in a case therefore does damage in two directions at once.

Outward: it raises the confirmed count for everybody else. A P4 sitting at 77.50
with four other apartments confirmed goes to 81.25 with five, which is P5. The
emergency that was never going to be grouped anyway has just made a second
emergency out of a ticket that was not one.

Inward: `docs/risk_scoring_v2.md` §8 makes P5 manual-only, and case membership
is how work gets planned in bulk -- the visual board treats a case as a single
unit and offers it to a technician. A P5 in a case is a P5 on a board.

And the outward damage does not clean itself up. §7.3 detaches a member that
*re-scores* to P5, which is a different event: it fires when the rescore pass
runs, and the survivors are re-scored once after the detach. A P5 that was
already an emergency before it joined never triggers that pass, so it sits in
the case inflating everyone's density until something else disturbs it. By then
the P4 it pushed over the line has been published, alerted on, and read by a
coordinator as an emergency.

**Three call sites, deliberately.** The candidate search
(`AgentToolService._grouping_candidates`) keeps it out of the model's sight; the
proposal re-check (`AgentToolService._valid_grouping_related`) refuses it even
if the model names it anyway or a stale proposal is replayed; and the membership
write (`AgentResultService._can_join_case`) refuses it at the last moment before
the row exists. The three are not redundant: minutes pass between them, and a
ticket that was P4 during the search can be P5 by the time the proposal lands --
a duplicate report escalating its master does exactly that.

Like `assignment_guard`, this takes a priority and opens no session, so the
tool layer and the result layer can both call it without importing each other.
"""

from __future__ import annotations

from src.domain.risk_scoring import is_emergency
from src.models.enums import Priority

#: Why a ticket was left out of a case. One sentence, so the audit trail and the
#: sanitized tool response say the same thing.
EMERGENCY_NOT_GROUPABLE_REASON = (
    "Phản ánh ở mức khẩn cấp P5 không tham gia cụm sự cố: "
    "Ban quản lý xử lý thủ công và số căn hộ của cụm không tính phản ánh này."
)


def priority_may_join_case(priority: Priority | None) -> bool:
    """False only for the emergency band.

    An unscored ticket is allowed through, for the same reason
    `assignment_guard` allows it: "not yet classified" is a different fact from
    "classified as an emergency", and the grouping paths reject an unscored
    ticket on their own terms -- it has no category to match on.
    """
    return not is_emergency(priority)


def ticket_may_join_case(ticket) -> bool:
    """True when this ticket may become a case member. Never raises."""
    return priority_may_join_case(getattr(ticket, "priority", None))


__all__ = [
    "EMERGENCY_NOT_GROUPABLE_REASON",
    "priority_may_join_case",
    "ticket_may_join_case",
]
