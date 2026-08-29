"""Technician assignment transition rules.

    ASSIGNED ──▶ IN_PROGRESS ──▶ COMPLETED
        │              │
        │              └──▶ UNABLE_TO_HANDLE
        ├──▶ REJECTED
        ├──▶ REASSIGNED
        └──▶ UNABLE_TO_HANDLE

There is no acknowledgement state between ASSIGNED and IN_PROGRESS. The first
positive action a technician can take on assigned work is "Bắt đầu xử lý", and
the map below is the single place that is decided -- an `ACCEPTED` target is
not merely rejected here, it no longer exists in `AssignmentStatus`.
"""

from src.models.api.errors import INVALID_STATUS_TRANSITION, DomainError
from src.models.enums import AssignmentStatus

ALLOWED_ASSIGNMENT_TRANSITIONS = {
    AssignmentStatus.ASSIGNED: {
        AssignmentStatus.IN_PROGRESS,
        AssignmentStatus.REJECTED,
        AssignmentStatus.REASSIGNED,
        AssignmentStatus.UNABLE_TO_HANDLE,
    },
    AssignmentStatus.IN_PROGRESS: {AssignmentStatus.COMPLETED, AssignmentStatus.UNABLE_TO_HANDLE},
}

#: The statuses that still occupy a technician's day. Terminal ones -- COMPLETED,
#: REJECTED, REASSIGNED, UNABLE_TO_HANDLE -- do not.
ACTIVE_ASSIGNMENT_STATUSES = (AssignmentStatus.ASSIGNED, AssignmentStatus.IN_PROGRESS)


def require_assignment_transition(from_status: AssignmentStatus, to_status: AssignmentStatus) -> None:
    if to_status not in ALLOWED_ASSIGNMENT_TRANSITIONS.get(from_status, set()):
        raise DomainError(INVALID_STATUS_TRANSITION, "Assignment transition không hợp lệ.", 409)
