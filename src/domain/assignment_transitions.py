"""Technician assignment transition rules."""

from src.models.api.errors import INVALID_STATUS_TRANSITION, DomainError
from src.models.enums import AssignmentStatus

ALLOWED_ASSIGNMENT_TRANSITIONS = {
    AssignmentStatus.ASSIGNED: {AssignmentStatus.ACCEPTED, AssignmentStatus.REJECTED, AssignmentStatus.REASSIGNED, AssignmentStatus.UNABLE_TO_HANDLE},
    AssignmentStatus.ACCEPTED: {AssignmentStatus.IN_PROGRESS, AssignmentStatus.REJECTED, AssignmentStatus.UNABLE_TO_HANDLE},
    AssignmentStatus.IN_PROGRESS: {AssignmentStatus.COMPLETED, AssignmentStatus.UNABLE_TO_HANDLE},
}


def require_assignment_transition(from_status: AssignmentStatus, to_status: AssignmentStatus) -> None:
    if to_status not in ALLOWED_ASSIGNMENT_TRANSITIONS.get(from_status, set()):
        raise DomainError(INVALID_STATUS_TRANSITION, "Assignment transition không hợp lệ.", 409)
