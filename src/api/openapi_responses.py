"""Reusable OpenAPI error metadata using the Self Dev v2 envelope."""

from typing import Any

from src.models.api.common import ApiErrorResponse
from src.models.api.errors import (
    ATTACHMENT_NOT_FOUND,
    AUTH_SERVICE_UNAVAILABLE,
    AUTH_TOKEN_INVALID,
    FORBIDDEN,
    INTERNAL_ERROR,
    INVALID_ATTACHMENT,
    STORAGE_NOT_CONFIGURED,
    TICKET_NOT_FOUND,
    UNIT_NOT_FOUND,
)

REQUEST_ID_EXAMPLE = "550e8400-e29b-41d4-a716-446655440000"


def error_response(description: str, code: str, message: str) -> dict[str, Any]:
    return {
        "model": ApiErrorResponse,
        "description": description,
        "content": {
            "application/json": {
                "example": {
                    "data": None,
                    "error": {"code": code, "message": message},
                    "request_id": REQUEST_ID_EXAMPLE,
                }
            }
        },
    }


BAD_REQUEST_RESPONSE = error_response("Invalid request.", INVALID_ATTACHMENT, "Invalid request.")
UNAUTHORIZED_RESPONSE = error_response("Authentication required.", AUTH_TOKEN_INVALID, "Invalid access token.")
FORBIDDEN_RESPONSE = error_response("Forbidden.", FORBIDDEN, "Forbidden.")
GENERIC_FORBIDDEN_RESPONSE = FORBIDDEN_RESPONSE
UNIT_NOT_FOUND_RESPONSE = error_response("Unit not found.", UNIT_NOT_FOUND, "Unit not found.")
TICKET_NOT_FOUND_RESPONSE = error_response("Ticket not found.", TICKET_NOT_FOUND, "Ticket not found.")
ATTACHMENT_NOT_FOUND_RESPONSE = error_response("Attachment not found.", ATTACHMENT_NOT_FOUND, "Attachment not found.")
AUTH_SERVICE_UNAVAILABLE_RESPONSE = error_response(
    "Authentication service unavailable.", AUTH_SERVICE_UNAVAILABLE, "Authentication service is unavailable."
)
STORAGE_UNAVAILABLE_RESPONSE = error_response(
    "Storage unavailable.", STORAGE_NOT_CONFIGURED, "Supabase Storage is not configured."
)
INTERNAL_SERVER_ERROR_RESPONSE = error_response("Internal server error.", INTERNAL_ERROR, "Internal server error.")

AUTHENTICATED_RESPONSES = {
    401: UNAUTHORIZED_RESPONSE,
    403: FORBIDDEN_RESPONSE,
    503: AUTH_SERVICE_UNAVAILABLE_RESPONSE,
    500: INTERNAL_SERVER_ERROR_RESPONSE,
}
