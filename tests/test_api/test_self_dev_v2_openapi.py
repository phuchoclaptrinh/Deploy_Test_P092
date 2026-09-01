from src.main import app

REQUIRED_PATHS = {
    "/api/v1/auth/otp/request",
    "/api/v1/auth/otp/verify",
    "/api/v1/me",
    "/api/v1/me/bind-unit",
    "/api/v1/catalog/locations",
    "/api/v1/catalog/categories",
    "/api/v1/tickets",
    "/api/v1/tickets/{ticket_id}",
    "/api/v1/tickets/{ticket_id}/cancel",
    "/api/v1/tickets/{ticket_id}/supplements",
    "/api/v1/tickets/{ticket_id}/agent-question",
    "/api/v1/tickets/{ticket_id}/agent-question/{question_id}/answer",
    "/api/v1/notifications",
    "/api/v1/notifications/{notification_id}/read",
    "/api/v1/coordinator/tickets",
    "/api/v1/coordinator/tickets/{ticket_id}",
    "/api/v1/coordinator/tickets/{ticket_id}/manual-review/resolve",
    "/api/v1/coordinator/tickets/{ticket_id}/manual-review/reject",
    "/api/v1/coordinator/tickets/{ticket_id}/request-information",
    "/api/v1/coordinator/tickets/{ticket_id}/approve",
    "/api/v1/coordinator/tickets/{ticket_id}/classification",
    "/api/v1/coordinator/tickets/{ticket_id}/assign",
    "/api/v1/coordinator/clusters",
    "/api/v1/coordinator/clusters/{case_id}/approve",
    "/api/v1/coordinator/clusters/{case_id}/assign",
    "/api/v1/coordinator/clusters/{case_id}/tickets/{ticket_id}",
    "/api/v1/coordinator/accounts/residents",
    "/api/v1/coordinator/accounts/technicians",
    "/api/v1/coordinator/technicians",
    "/api/v1/coordinator/technicians/{technician_id}",
    "/api/v1/coordinator/categories",
    "/api/v1/coordinator/categories/{category_id}",
    "/api/v1/coordinator/audit-logs",
    "/api/v1/coordinator/reports/tickets-summary",
    "/api/v1/coordinator/reports/sla-performance",
    "/api/v1/coordinator/reports/technician-productivity",
    "/api/v1/coordinator/reports/export",
    "/api/v1/technician/assignments",
    "/api/v1/technician/assignments/{assignment_id}",
    "/api/v1/technician/assignments/{assignment_id}/start",
    "/api/v1/technician/assignments/{assignment_id}/unable-to-handle",
    "/api/v1/technician/assignments/{assignment_id}/complete",
}


def test_openapi_contains_self_dev_v3_paths():
    schema = app.openapi()
    paths = set(schema["paths"])
    assert REQUIRED_PATHS <= paths
    assert "/api/v1/coordinator/tickets/{ticket_id}/start" not in paths
    assert "/api/v1/coordinator/tickets/{ticket_id}/complete" not in paths
    assert "/api/v1/coordinator/tickets/{ticket_id}/unresolvable" not in paths
    assert not any(path.startswith("/api/v1/bql") for path in paths)
    # There is no acknowledgement endpoint. A client that still calls it gets a
    # 404 rather than silently doing nothing, which is the point of removing
    # the route rather than making it a no-op.
    assert "/api/v1/technician/assignments/{assignment_id}/accept" not in paths


def test_resident_response_schema_does_not_expose_raw_priority_or_score():
    schema = app.openapi()["components"]["schemas"]["ResidentTicketResponse"]["properties"]
    assert "priority" not in schema
    assert "score_total" not in schema
    assert "sla_due_at" not in schema
    assert "status" not in schema
    assert "classification_status" not in schema
    assert "priority_description" in schema
    # §4 replaced the completion promise with a description of the current
    # state and an expected *start*. Both old keys must be gone, not renamed.
    assert "estimated_resolution_text" not in schema
    assert "expected_resolution_at" not in schema
    assert "planned_finish_at" not in schema
    assert "progress_text" in schema
    assert "expected_start_at" in schema
    # The acceptance SLA went with the acceptance step. A resident is never
    # told to wait for a technician to confirm, so there is no key to tell
    # them with.
    assert "acceptance_due_at" not in schema


def test_ticket_filters_use_dynamic_category_id():
    schema = app.openapi()
    resident_params = schema["paths"]["/api/v1/tickets"]["get"]["parameters"]
    coordinator_params = schema["paths"]["/api/v1/coordinator/tickets"]["get"]["parameters"]

    assert any(param["name"] == "category_id" for param in resident_params)
    assert not any(param["name"] == "category" for param in resident_params)
    assert any(param["name"] == "category_id" for param in coordinator_params)
    assert not any(param["name"] == "category" for param in coordinator_params)
