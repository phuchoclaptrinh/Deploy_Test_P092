from src.models.enums import (
    EXCLUDING_END_REASONS,
    ActivationDelay,
    AssignmentEndReason,
    AssignmentJobStatus,
    AssignmentSource,
    AssignmentStatus,
    Category,
    ClassificationStatus,
    InvalidReason,
    Priority,
    ProposalBatchStatus,
    ProposalItemStatus,
    Severity,
    TicketStatus,
    UserRole,
)


def test_self_dev_v3_roles_are_exactly_three():
    assert [x.value for x in UserRole] == ["RESIDENT", "COORDINATOR", "TECHNICIAN"]


def test_p0_is_not_a_priority():
    assert [x.value for x in Priority] == ["P1", "P2", "P3"]
    assert ClassificationStatus.MANUAL_REVIEW.value == "MANUAL_REVIEW"


def test_business_lifecycle_is_self_dev_v4():
    # LINKED_DUPLICATE is a v4 addition (contract §7.1): a ticket that is real,
    # was recorded, and is tracked through another ticket rather than closed.
    assert {x.value for x in TicketStatus} == {
        "NEW", "WAITING_RESIDENT_INFO", "APPROVED", "IN_PROGRESS",
        "COMPLETED", "LINKED_DUPLICATE", "UNRESOLVABLE", "CANCELLED", "INVALID",
    }


def test_assignment_lifecycle_is_separate_from_ticket_status():
    # §6: REJECTED (declined before starting) and REASSIGNED (taken away by the
    # system) are separate from UNABLE_TO_HANDLE (accepted, then found
    # impossible). Only the first two put a technician on an exclusion list.
    assert [x.value for x in AssignmentStatus] == [
        "ASSIGNED", "ACCEPTED", "IN_PROGRESS", "COMPLETED",
        "REJECTED", "REASSIGNED", "UNABLE_TO_HANDLE",
    ]
    assert "ASSIGNED" not in {x.value for x in TicketStatus}


def test_v4_operational_enums_match_the_contract():
    assert [x.value for x in AssignmentSource] == [
        "COORDINATOR_MANUAL", "AI_AUTO", "AI_PROPOSAL_CONFIRMED",
    ]
    # §5.1 job states. COMPLETED covers NO_SUITABLE_CANDIDATE, which is a
    # business answer; FAILED is the technical dead end after the fallback.
    assert {x.value for x in AssignmentJobStatus} == {
        "SCHEDULED_GRACE", "PRIMARY_RUNNING", "FALLBACK_RUNNING", "COMPLETED",
        "FAILED", "CANCELLED_BY_COORDINATOR", "CANCELLED_MANUAL_WON", "MANUAL_REQUIRED",
    }
    assert {x.value for x in ProposalBatchStatus} == {
        "BUILDING", "READY", "CONFIRMED", "EXPIRED", "CANCELLED",
    }
    assert {x.value for x in ProposalItemStatus} == {
        "PENDING", "PROPOSED", "EMPTY", "DESELECTED", "ASSIGNED", "SKIPPED_MANUAL_WON",
    }
    assert [x.value for x in ActivationDelay] == ["IMMEDIATE", "2_HOURS", "5_HOURS", "1_DAY", "3_DAYS"]
    # §8.2: only CONTENT_INSUFFICIENT counts toward the AI-rejection limit.
    # COORDINATOR_REJECTED records a Building Management rejection at manual
    # review, which ends the report without counting against that limit.
    assert {x.value for x in InvalidReason} == {
        "CONTENT_INSUFFICIENT",
        "RESIDENT_RESPONSE_TIMEOUT",
        "COORDINATOR_REJECTED",
    }
    assert EXCLUDING_END_REASONS == {
        AssignmentEndReason.TECHNICIAN_REJECTED,
        AssignmentEndReason.ACCEPTANCE_TIMEOUT,
    }


def test_canonical_category_and_severity_taxonomy():
    assert {x.value for x in Category} == {
        "WATER_LEAK", "ELECTRICAL_SHORT", "ELEVATOR", "SERIOUS_SECURITY_DISORDER",
        "LOCK_DOOR", "HVAC", "LOCAL_POWER_OUTAGE", "STRUCTURAL_ISSUE",
        "COMMON_LIGHT", "ODOR_HYGIENE", "NOISE_NEIGHBOR",
    }
    assert [x.value for x in Severity] == ["LOW", "MEDIUM", "HIGH"]
