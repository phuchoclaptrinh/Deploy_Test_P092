from src.models.enums import (
    AUTOMATIC_ASSIGNMENT_SOURCES,
    EXCLUDING_END_REASONS,
    AssignmentEndReason,
    AssignmentSource,
    AssignmentStatus,
    Category,
    ClassificationStatus,
    DispatchDecisionSource,
    DispatchEventStatus,
    DispatchRiskState,
    InvalidReason,
    Priority,
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
    # system) are separate from UNABLE_TO_HANDLE (started, then found
    # impossible). Only REJECTED puts a technician on an exclusion list.
    assert [x.value for x in AssignmentStatus] == [
        "ASSIGNED", "IN_PROGRESS", "COMPLETED",
        "REJECTED", "REASSIGNED", "UNABLE_TO_HANDLE",
    ]
    assert "ASSIGNED" not in {x.value for x in TicketStatus}


def test_there_is_no_acceptance_state_to_reach():
    """The acknowledgement step is gone from the type, not merely unused.

    A member that still existed would be assignable -- by a stale client, by an
    older migration, by a test -- and "there is no active ACCEPTED state" would
    be a convention rather than a fact.
    """
    assert "ACCEPTED" not in {member.value for member in AssignmentStatus}
    assert not hasattr(AssignmentStatus, "ACCEPTED")
    # And nothing can *end* an assignment for failing to acknowledge it either.
    assert "ACCEPTANCE_TIMEOUT" not in {member.value for member in AssignmentEndReason}


def test_v4_operational_enums_match_the_contract():
    # §2/§7: the two human sources name a person, the three automatic ones do
    # not. AUTO_AGENT and AUTO_FALLBACK stay apart so an auditor can find the
    # decisions no model actually reasoned about.
    assert [x.value for x in AssignmentSource] == [
        "COORDINATOR_MANUAL", "COORDINATOR_VISUAL",
        "AUTO_SCHEDULER", "AUTO_AGENT", "AUTO_FALLBACK",
    ]
    assert AUTOMATIC_ASSIGNMENT_SOURCES == {
        AssignmentSource.AUTO_SCHEDULER,
        AssignmentSource.AUTO_AGENT,
        AssignmentSource.AUTO_FALLBACK,
    }
    assert [x.value for x in DispatchEventStatus] == [
        "PENDING", "CLAIMED", "ASSIGNED", "ESCALATED", "SUPERSEDED", "FAILED",
    ]
    assert [x.value for x in DispatchRiskState] == ["SAFE", "AT_RISK"]
    assert [x.value for x in DispatchDecisionSource] == [
        "SCHEDULER", "AGENT", "SCHEDULER_FALLBACK",
    ]
    # §8.2: only CONTENT_INSUFFICIENT counts toward the AI-rejection limit.
    # COORDINATOR_REJECTED records a Building Management rejection at manual
    # review, which ends the report without counting against that limit.
    assert {x.value for x in InvalidReason} == {
        "CONTENT_INSUFFICIENT",
        "RESIDENT_RESPONSE_TIMEOUT",
        "COORDINATOR_REJECTED",
    }
    # Only a refusal excludes now. The acceptance timeout that used to sit
    # beside it is gone with the step it enforced, and no start deadline has
    # been approved to replace it.
    assert EXCLUDING_END_REASONS == {AssignmentEndReason.TECHNICIAN_REJECTED}


def test_canonical_category_and_severity_taxonomy():
    assert {x.value for x in Category} == {
        "WATER", "WALL_DAMP", "ELEVATOR", "POWER_OUTAGE", "SECURITY_SAFETY",
        "NOISE", "LOCK_DOOR", "HVAC", "ODOR_HYGIENE", "INTERNET_TV",
        "COMMON_AREA_DAMAGE",
    }
    assert [x.value for x in Severity] == ["LOW", "MEDIUM", "HIGH"]
