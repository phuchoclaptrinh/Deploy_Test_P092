"""Stable error codes shared with FE according to Self_Dev_Docs v2."""

from typing import Any


class DomainError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


AUTH_REQUIRED = "AUTH_REQUIRED"
AUTH_TOKEN_MISSING = AUTH_REQUIRED
AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
AUTH_SERVICE_UNAVAILABLE = "AUTH_SERVICE_UNAVAILABLE"
AUTH_PROFILE_INVALID = "AUTH_PROFILE_INVALID"
USER_INACTIVE = "USER_INACTIVE"
FORBIDDEN = "FORBIDDEN"
UNIT_NOT_FOUND = "UNIT_NOT_FOUND"
ACCOUNT_ALREADY_BOUND = "ACCOUNT_ALREADY_BOUND"
UNIT_ALREADY_BOUND = "UNIT_ALREADY_BOUND"
NO_ACTIVE_UNIT = "NO_ACTIVE_UNIT"
TEXT_OR_IMAGE_REQUIRED = "TEXT_OR_IMAGE_REQUIRED"
DESCRIPTION_REQUIRED = "DESCRIPTION_REQUIRED"
IMAGE_UNREADABLE = "IMAGE_UNREADABLE"
INVALID_LOCATION = "INVALID_LOCATION"
TICKET_NOT_FOUND = "TICKET_NOT_FOUND"
TICKET_NOT_OWNED = "TICKET_NOT_OWNED"
TICKET_CREATE_RATE_LIMITED = "TICKET_CREATE_RATE_LIMITED"
INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
AGENT_BUDGET_EXHAUSTED = "AGENT_BUDGET_EXHAUSTED"
P0_REVIEW_REQUIRED = "P0_REVIEW_REQUIRED"
#: The mandatory human gate in front of the emergency Priority. Distinct
#: from P0_REVIEW_REQUIRED and from INVALID_STATUS_TRANSITION: it does not
#: mean "wrong state, try later", it means one specific action applies.
P3_REVIEW_REQUIRED = "P3_REVIEW_REQUIRED"
CATEGORY_REQUIRED = "CATEGORY_REQUIRED"
SEVERITY_REQUIRED = "SEVERITY_REQUIRED"
OVERRIDE_REASON_REQUIRED = "OVERRIDE_REASON_REQUIRED"
CONFLICT_VERSION = "CONFLICT_VERSION"
ATTACHMENT_NOT_FOUND = "ATTACHMENT_NOT_FOUND"
INVALID_ATTACHMENT = "INVALID_ATTACHMENT"
ASSIGNMENT_NOT_FOUND = "ASSIGNMENT_NOT_FOUND"
TECHNICIAN_NOT_FOUND = "TECHNICIAN_NOT_FOUND"
TECHNICIAN_NOT_ELIGIBLE = "TECHNICIAN_NOT_ELIGIBLE"
ACTIVE_ASSIGNMENT_EXISTS = "ACTIVE_ASSIGNMENT_EXISTS"
#: The technician tried to start work that is not "Làm ngay". The scheduler's
#: `planned_order` decides the head of a queue, and only Building Management
#: can change it -- through an action that leaves an audit record.
ASSIGNMENT_NOT_AT_QUEUE_HEAD = "ASSIGNMENT_NOT_AT_QUEUE_HEAD"
#: A Visual Assignment board unit that is no longer on the board -- assigned by
#: someone else, linked as a duplicate, or otherwise no longer eligible -- named
#: in a bulk confirm. Distinct from TICKET_NOT_FOUND: the ticket exists, it is
#: just not this board's to place any more.
VISUAL_UNIT_NOT_PLACEABLE = "VISUAL_UNIT_NOT_PLACEABLE"
#: One or more placements in a bulk confirm broke a hard constraint. Carries the
#: offending placements in `details` so the board can mark them rather than
#: making Building Management hunt for the one that failed.
VISUAL_PLACEMENT_INVALID = "VISUAL_PLACEMENT_INVALID"
#: A dispatch event named by an operational endpoint does not exist.
DISPATCH_EVENT_NOT_FOUND = "DISPATCH_EVENT_NOT_FOUND"
COMPLETION_EVIDENCE_REQUIRED = "COMPLETION_EVIDENCE_REQUIRED"
INFORMATION_REQUEST_NOT_FOUND = "INFORMATION_REQUEST_NOT_FOUND"
STORAGE_NOT_CONFIGURED = "STORAGE_NOT_CONFIGURED"
DATABASE_NOT_CONFIGURED = "DATABASE_NOT_CONFIGURED"
VALIDATION_ERROR = "VALIDATION_ERROR"
INTERNAL_ERROR = "INTERNAL_ERROR"
