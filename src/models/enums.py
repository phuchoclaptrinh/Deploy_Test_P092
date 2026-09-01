from enum import Enum


class UserRole(str, Enum):  # noqa: UP042
    """Application roles defined by the Self Dev v3 human workflow."""

    RESIDENT = "RESIDENT"
    COORDINATOR = "COORDINATOR"
    TECHNICIAN = "TECHNICIAN"


class TicketStatus(str, Enum):  # noqa: UP042
    """Business lifecycle states. P0 is deliberately not represented here."""

    NEW = "NEW"
    WAITING_RESIDENT_INFO = "WAITING_RESIDENT_INFO"
    APPROVED = "APPROVED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    LINKED_DUPLICATE = "LINKED_DUPLICATE"
    UNRESOLVABLE = "UNRESOLVABLE"
    CANCELLED = "CANCELLED"
    INVALID = "INVALID"


class ClassificationStatus(str, Enum):  # noqa: UP042
    """AI/classification state, independent from the business lifecycle."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    RESOLVED = "RESOLVED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    FAILED = "FAILED"


class Category(str, Enum):  # noqa: UP042
    """Fixed resident issue catalog for the single-building deployment."""

    WATER = "WATER"
    WALL_DAMP = "WALL_DAMP"
    ELEVATOR = "ELEVATOR"
    POWER_OUTAGE = "POWER_OUTAGE"
    SECURITY_SAFETY = "SECURITY_SAFETY"
    NOISE = "NOISE"
    LOCK_DOOR = "LOCK_DOOR"
    HVAC = "HVAC"
    ODOR_HYGIENE = "ODOR_HYGIENE"
    INTERNET_TV = "INTERNET_TV"
    COMMON_AREA_DAMAGE = "COMMON_AREA_DAMAGE"


class Priority(str, Enum):  # noqa: UP042
    """The five risk bands of `docs/risk_scoring_v2.md`, lowest urgency first.

    **The direction flipped in v2.** P3 used to be the five-minute emergency and
    P1 the routine multi-day promise. Now P1 is the routine band and **P5 is the
    emergency**, handled manually and never placed by any automatic path. Any
    comparison written before v2 means the opposite of what it says.

    P0 is still not a member: "nobody could classify this" is
    `classification_status=MANUAL_REVIEW`, not a priority.
    """

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"
    P5 = "P5"


class AssignmentStatus(str, Enum):  # noqa: UP042
    """Technician assignment lifecycle, separate from ticket status.

    There is no separate acknowledgement step. A technician does not "accept"
    work; the first positive action they can take is to *start* it, so ASSIGNED
    goes straight to IN_PROGRESS. The old ACCEPTED value is gone from the
    Python enum and from the PostgreSQL type (`9f0a1b2c3d4e`), which is what
    makes "there is no active accepted state" a fact rather than a convention.
    """

    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    REASSIGNED = "REASSIGNED"
    UNABLE_TO_HANDLE = "UNABLE_TO_HANDLE"


class ResolutionSource(str, Enum):  # noqa: UP042
    IMAGE = "IMAGE"
    TEXT = "TEXT"
    OTHER = "OTHER"


class AttachmentType(str, Enum):  # noqa: UP042
    ISSUE_ORIGINAL = "ISSUE_ORIGINAL"
    RESIDENT_SUPPLEMENT = "RESIDENT_SUPPLEMENT"
    TECHNICIAN_COMPLETION = "TECHNICIAN_COMPLETION"


class ImageQualityStatus(str, Enum):  # noqa: UP042
    PENDING = "PENDING"
    READABLE = "READABLE"
    UNREADABLE = "UNREADABLE"


class AnalysisRunStatus(str, Enum):  # noqa: UP042
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class RiskAssessmentSource(str, Enum):  # noqa: UP042
    """What produced one `ticket_risk_assessments` revision.

    The table is append-only, so every row needs to say why it exists. These
    four are the only things that may write one, and they are kept apart
    because "the case grew" and "a human overruled it" are different stories to
    a reviewer even when they land on the same priority.
    """

    #: The Agent scored the ticket and the backend turned that into a priority.
    AI_ANALYSIS = "AI_ANALYSIS"
    #: A case gained or lost a member, so every member's confirmed scope moved.
    GROUPING_RESCORE = "GROUPING_RESCORE"
    #: A coordinator confirmed or downgraded at the emergency gate.
    HUMAN_REVIEW = "HUMAN_REVIEW"
    #: A confident duplicate of an emergency pulled its master up to P5.
    DUPLICATE_ESCALATION = "DUPLICATE_ESCALATION"


class EmergencyReviewStatus(str, Enum):  # noqa: UP042
    """The mandatory human gate in front of P5.

    P5 means "somebody is dealing with this by hand, right now". Nothing
    automatic decides that on its own and nothing automatic runs behind it, so a
    P5 classification parks here until a coordinator either confirms the
    emergency or downgrades it.

    Replaces `p3_review_status`: the gate did not change, only which band sits
    behind it.
    """

    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    DOWNGRADED = "DOWNGRADED"


class EmergencyDecision(str, Enum):  # noqa: UP042
    """The only two things a coordinator may do at the emergency gate.

    Confirming ends the automation deliberately -- correlating an emergency with
    other tickets is not worth the minutes it costs, and confirming does *not*
    unlock assignment: a confirmed P5 is still handled outside the system.
    Downgrading is the only way back into the pipeline, and it cannot land on P5
    again -- confirming is the action for that.
    """

    CONFIRM_P5 = "CONFIRM_P5"
    DOWNGRADE_PRIORITY = "DOWNGRADE_PRIORITY"


class InformationRequestStatus(str, Enum):  # noqa: UP042
    OPEN = "OPEN"
    RESPONDED = "RESPONDED"
    CLOSED = "CLOSED"


class NotificationChannel(str, Enum):  # noqa: UP042
    PUSH = "PUSH"
    SMS = "SMS"
    IN_APP = "IN_APP"


class NotificationStatus(str, Enum):  # noqa: UP042
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    READ = "READ"


class AssignmentSource(str, Enum):  # noqa: UP042
    """Who produced an assignment.

    Four sources, and the distinction between the last three is the audit
    question "who decided?", which the resident-visible outcome never answers:

    * ``COORDINATOR_MANUAL`` -- a single assign action from a ticket screen.
    * ``COORDINATOR_VISUAL`` -- one placement inside a confirmed Visual
      Assignment board. Same authority, different act: it was decided against a
      board showing every technician's workload rather than one ticket.
    * ``AUTO_SCHEDULER`` -- the ticket was SAFE, so the scheduler placed it and
      no model was called at all.
    * ``AUTO_AGENT`` -- the ticket was AT_RISK and the at-risk agent chose from
      the eligible set the backend handed it.
    * ``AUTO_FALLBACK`` -- the ticket was AT_RISK but the agent timed out or
      failed, so the scheduler's least-negative-slack candidate was taken
      instead. Kept apart from ``AUTO_AGENT`` because "nobody reasoned about
      this one" is exactly the fact an auditor is looking for.
    """

    COORDINATOR_MANUAL = "COORDINATOR_MANUAL"
    COORDINATOR_VISUAL = "COORDINATOR_VISUAL"
    AUTO_SCHEDULER = "AUTO_SCHEDULER"
    AUTO_AGENT = "AUTO_AGENT"
    AUTO_FALLBACK = "AUTO_FALLBACK"


#: The sources that carry no human actor, so `assigned_by_user_id` is null and
#: the audit actor is SYSTEM. The database check constraint mirrors this set.
AUTOMATIC_ASSIGNMENT_SOURCES = frozenset(
    {AssignmentSource.AUTO_SCHEDULER, AssignmentSource.AUTO_AGENT, AssignmentSource.AUTO_FALLBACK}
)


class AssignmentEndReason(str, Enum):  # noqa: UP042
    """Why an assignment stopped being active (contract §6, §7.3).

    `TECHNICIAN_REJECTED` is what puts a technician on a work item's exclusion
    list (§4.3 rule 1), which is why "rejected" and "could not handle" must
    never share a value.

    `ACCEPTANCE_TIMEOUT` is gone with the acceptance step. Nothing releases an
    assignment on a clock any more: no start deadline has been approved, so
    inventing one here -- and the automatic reassignment that would follow from
    it -- is exactly the policy decision this change is not allowed to make.
    A `START_TIMEOUT` value belongs here the day that rule is decided.
    """

    TECHNICIAN_REJECTED = "TECHNICIAN_REJECTED"
    COORDINATOR_REASSIGNED = "COORDINATOR_REASSIGNED"
    COMPLETED = "COMPLETED"
    UNABLE_TO_HANDLE = "UNABLE_TO_HANDLE"
    #: The ticket was re-scored to P5 while a technician held it. The work does
    #: not move to another technician -- it leaves the dispatch system entirely
    #: -- so this is neither a rejection nor a reassignment, and it must not put
    #: anyone on an exclusion list for a decision they had no part in.
    EMERGENCY_MANUAL_ESCALATION = "EMERGENCY_MANUAL_ESCALATION"


EXCLUDING_END_REASONS = frozenset({AssignmentEndReason.TECHNICIAN_REJECTED})


class TicketRelationType(str, Enum):  # noqa: UP042
    """§7.7. A relation, never a duplicate link: the source ticket stays in the
    normal queue and keeps its own emergency handling."""

    EMERGENCY_EVIDENCE = "EMERGENCY_EVIDENCE"


class InvalidReason(str, Enum):  # noqa: UP042
    """§7.1. Only `CONTENT_INSUFFICIENT` counts toward the 3-AI-rejections/day
    limit; a resident timeout does not (§8.2)."""

    CONTENT_INSUFFICIENT = "CONTENT_INSUFFICIENT"
    RESIDENT_RESPONSE_TIMEOUT = "RESIDENT_RESPONSE_TIMEOUT"
    #: Building Management rejected the report during manual review. The report
    #: ends here; the resident creates a new one instead of supplementing this.
    COORDINATOR_REJECTED = "COORDINATOR_REJECTED"


class TicketLifecycleGroup(str, Enum):  # noqa: UP042
    """Resident-facing grouping behind the "Đang theo dõi"/"Đã kết thúc" tabs."""

    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"


class DispatchEventStatus(str, Enum):  # noqa: UP042
    """Lifecycle of one durable automatic-dispatch event.

    The event is the unit of durability: a ticket becoming eligible writes one
    row, and nothing about the automatic path is held in memory between passes.
    A worker restarting mid-batch leaves `CLAIMED` rows behind, which the claim
    timeout returns to `PENDING` -- the ticket is never silently dropped.

    `ESCALATED` and `FAILED` are different facts. `ESCALATED` is a *decision*:
    the ticket is legitimately Building Management's (no eligible technician,
    the toggle went off, it stopped being eligible). `FAILED` is a technical
    dead end that a human should look at.
    """

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    ASSIGNED = "ASSIGNED"
    ESCALATED = "ESCALATED"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"


#: An event in one of these states is finished; no worker may claim it again.
TERMINAL_DISPATCH_STATUSES = frozenset(
    {
        DispatchEventStatus.ASSIGNED,
        DispatchEventStatus.ESCALATED,
        DispatchEventStatus.SUPERSEDED,
        DispatchEventStatus.FAILED,
    }
)


class DispatchRiskState(str, Enum):  # noqa: UP042
    """What the in-memory scheduler concluded about one ticket.

    `SAFE` means at least one valid placement leaves every already-scheduled
    ticket with non-negative slack. `AT_RISK` means every valid placement
    pushes something into negative slack -- the ticket is still assignable, but
    a trade-off is being made, and that is the only case an agent is called for.
    """

    SAFE = "SAFE"
    AT_RISK = "AT_RISK"


class DispatchDecisionSource(str, Enum):  # noqa: UP042
    """Which component picked the technician for a dispatch event."""

    SCHEDULER = "SCHEDULER"
    AGENT = "AGENT"
    SCHEDULER_FALLBACK = "SCHEDULER_FALLBACK"


class DispatchEscalationReason(str, Enum):  # noqa: UP042
    """Why an event went to Building Management instead of being assigned.

    Every one of these is a normal outcome rather than an error. `P5_EMERGENCY`
    should never actually be written -- a P5 ticket is refused at enqueue time
    -- but it exists so that a P5 arriving here through some future path is
    recorded as escalated rather than quietly assigned.
    """

    NO_ELIGIBLE_TECHNICIAN = "NO_ELIGIBLE_TECHNICIAN"
    NO_FEASIBLE_PLACEMENT = "NO_FEASIBLE_PLACEMENT"
    AUTO_ASSIGNMENT_DISABLED = "AUTO_ASSIGNMENT_DISABLED"
    TICKET_NOT_ELIGIBLE = "TICKET_NOT_ELIGIBLE"
    P5_EMERGENCY = "P5_EMERGENCY"


class PlacementWarningCode(str, Enum):  # noqa: UP042
    """What the Visual Assignment board flags about one proposed placement.

    All five are hard-enforced on confirm: the board refuses the drop rather
    than letting Building Management confirm a placement the backend will
    reject. They are warnings only in the sense that the board shows *why*
    before the drop is attempted.
    """

    MISSING_SKILL = "MISSING_SKILL"
    OUT_OF_SHIFT = "OUT_OF_SHIFT"
    OVERLOADED = "OVERLOADED"
    SCHEDULE_RISK = "SCHEDULE_RISK"
    TECHNICIAN_UNAVAILABLE = "TECHNICIAN_UNAVAILABLE"
