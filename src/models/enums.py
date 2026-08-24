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
    """Canonical issue categories from Self_Dev_Docs v2."""

    WATER_LEAK = "WATER_LEAK"
    ELECTRICAL_SHORT = "ELECTRICAL_SHORT"
    ELEVATOR = "ELEVATOR"
    SERIOUS_SECURITY_DISORDER = "SERIOUS_SECURITY_DISORDER"
    LOCK_DOOR = "LOCK_DOOR"
    HVAC = "HVAC"
    LOCAL_POWER_OUTAGE = "LOCAL_POWER_OUTAGE"
    STRUCTURAL_ISSUE = "STRUCTURAL_ISSUE"
    COMMON_LIGHT = "COMMON_LIGHT"
    ODOR_HYGIENE = "ODOR_HYGIENE"
    NOISE_NEIGHBOR = "NOISE_NEIGHBOR"


class Severity(str, Enum):  # noqa: UP042
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Priority(str, Enum):  # noqa: UP042
    """Only real priorities. P0 is classification_status=MANUAL_REVIEW."""

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class AssignmentStatus(str, Enum):  # noqa: UP042
    """Technician assignment lifecycle, separate from ticket status."""

    ASSIGNED = "ASSIGNED"
    ACCEPTED = "ACCEPTED"
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


class SeveritySource(str, Enum):  # noqa: UP042
    VISION = "VISION"
    TEXT_FALLBACK = "TEXT_FALLBACK"
    # §8.3: the Coordinator picked the severity by hand during manual review,
    # because the analysis never produced one. Never written by an Agent run.
    COORDINATOR_MANUAL = "COORDINATOR_MANUAL"


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
    """Who produced an assignment (contract §7.3).

    `COORDINATOR_MANUAL` replaces the pre-v4 literal `MANUAL`; the migration
    rewrites existing rows so there is one spelling in the column.
    """

    COORDINATOR_MANUAL = "COORDINATOR_MANUAL"
    AI_AUTO = "AI_AUTO"
    AI_PROPOSAL_CONFIRMED = "AI_PROPOSAL_CONFIRMED"


class AssignmentEndReason(str, Enum):  # noqa: UP042
    """Why an assignment stopped being active (contract §6, §7.3).

    `TECHNICIAN_REJECTED` and `ACCEPTANCE_TIMEOUT` are what put a technician on
    a work item's exclusion list (§4.3 rule 1), which is why "rejected" and
    "could not handle" must never share a value.
    """

    TECHNICIAN_REJECTED = "TECHNICIAN_REJECTED"
    ACCEPTANCE_TIMEOUT = "ACCEPTANCE_TIMEOUT"
    COORDINATOR_REASSIGNED = "COORDINATOR_REASSIGNED"
    COMPLETED = "COMPLETED"
    UNABLE_TO_HANDLE = "UNABLE_TO_HANDLE"


EXCLUDING_END_REASONS = frozenset(
    {AssignmentEndReason.TECHNICIAN_REJECTED, AssignmentEndReason.ACCEPTANCE_TIMEOUT}
)


class AssignmentJobMode(str, Enum):  # noqa: UP042
    DIRECT = "DIRECT"
    PROPOSAL = "PROPOSAL"


class AssignmentJobTrigger(str, Enum):  # noqa: UP042
    """§4.2. `COORDINATOR_PROPOSAL` is the one non-DIRECT trigger: it exists so a
    PROPOSAL job records why it was built, not to make PROPOSAL a reassignment
    path."""

    INITIAL_AUTO = "INITIAL_AUTO"
    REASSIGN_REJECTED = "REASSIGN_REJECTED"
    REASSIGN_SILENT = "REASSIGN_SILENT"
    COORDINATOR_PROPOSAL = "COORDINATOR_PROPOSAL"


class AssignmentJobStatus(str, Enum):  # noqa: UP042
    """§5.1.

    `COMPLETED` means the model returned a valid *business* answer, including
    `NO_SUITABLE_CANDIDATE`. `FAILED` is a technical dead end after the fallback;
    DIRECT then moves on to `MANUAL_REQUIRED` and PROPOSAL marks its item EMPTY.
    """

    SCHEDULED_GRACE = "SCHEDULED_GRACE"
    PRIMARY_RUNNING = "PRIMARY_RUNNING"
    FALLBACK_RUNNING = "FALLBACK_RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED_BY_COORDINATOR = "CANCELLED_BY_COORDINATOR"
    CANCELLED_MANUAL_WON = "CANCELLED_MANUAL_WON"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


TERMINAL_JOB_STATUSES = frozenset(
    {
        AssignmentJobStatus.COMPLETED,
        AssignmentJobStatus.FAILED,
        AssignmentJobStatus.CANCELLED_BY_COORDINATOR,
        AssignmentJobStatus.CANCELLED_MANUAL_WON,
        AssignmentJobStatus.MANUAL_REQUIRED,
    }
)


class ProposalBatchStatus(str, Enum):  # noqa: UP042
    """§4.6. `BUILDING` exists because the switch must stay OFF while the model
    is still working; only a successful confirm can turn it on."""

    BUILDING = "BUILDING"
    READY = "READY"
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ProposalItemStatus(str, Enum):  # noqa: UP042
    """§7.5. `EMPTY` covers both "no candidate" and "the model could not answer
    this row" — neither blocks the rest of the batch (§5.2 items 1, 5, 7)."""

    PENDING = "PENDING"
    PROPOSED = "PROPOSED"
    EMPTY = "EMPTY"
    DESELECTED = "DESELECTED"
    ASSIGNED = "ASSIGNED"
    SKIPPED_MANUAL_WON = "SKIPPED_MANUAL_WON"


class ActivationDelay(str, Enum):  # noqa: UP042
    """§4.2 / §7.6. The only delays a coordinator may pick."""

    IMMEDIATE = "IMMEDIATE"
    HOURS_2 = "2_HOURS"
    HOURS_5 = "5_HOURS"
    DAY_1 = "1_DAY"
    DAYS_3 = "3_DAYS"


class ProposalBatchCreatedBy(str, Enum):  # noqa: UP042
    """Who opened a proposal batch.

    A batch the recurring schedule opened has no coordinator behind it, and
    §8.1 separates SYSTEM from a named actor. Borrowing whoever last configured
    the schedule would put their name on a decision they were not present for.
    """

    COORDINATOR = "COORDINATOR"
    SYSTEM = "SYSTEM"


class ProposalScheduleInterval(str, Enum):  # noqa: UP042
    """How often the recurring schedule opens a **draft** proposal for review.

    Deliberately a separate enum from :class:`ActivationDelay`, which answers a
    different question: that one is how long a single approved ticket waits
    before DIRECT auto-assignment fires, this one is how often the system builds
    a new table for a human to confirm. Sharing the type would invite sharing
    the semantics, and the two must never be conflated.
    """

    HOURS_2 = "2_HOURS"
    DAY_1 = "1_DAY"
    DAYS_3 = "3_DAYS"


class TicketRelationType(str, Enum):  # noqa: UP042
    """§7.7. A relation, never a duplicate link: the source ticket stays in the
    normal queue and keeps its own P3 handling."""

    RED_FLAG_EVIDENCE = "RED_FLAG_EVIDENCE"


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
