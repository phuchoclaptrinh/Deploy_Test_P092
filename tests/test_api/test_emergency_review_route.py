"""The P3 review endpoint: who may call it, and what a valid payload looks like.

The behaviour behind the endpoint is covered against a real database in
`tests/test_agents/test_emergency_review_gate.py`. What is checked here is the part
only the HTTP layer can enforce: the route is coordinator-only, and the request
schema refuses a decision that contradicts itself before any of that behaviour
is reached.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from src.main import app
from src.models.api.coordinator import EmergencyReviewRequest
from src.models.enums import EmergencyDecision, Priority

EMERGENCY_PATH = "/api/v1/coordinator/tickets/{ticket_id}/emergency-review"


def test_the_route_is_registered_as_a_post():
    # Read through the OpenAPI schema rather than `app.routes`: the sub-routers
    # are included lazily, so the route objects do not exist until something
    # forces them to resolve.
    assert "post" in app.openapi()["paths"][EMERGENCY_PATH]


def test_the_route_is_coordinator_only():
    """Read off the handler itself. The sub-routers are included lazily, so
    there is no resolved route object to inspect until the app is served."""
    from src.api.routes import coordinator_tickets

    guards = {
        getattr(parameter.default, "dependency", None).__name__
        for parameter in inspect.signature(coordinator_tickets.review_emergency).parameters.values()
        if getattr(parameter.default, "dependency", None) is not None
    }
    assert "require_coordinator" in guards


@pytest.mark.asyncio
async def test_an_anonymous_caller_is_rejected(client):
    from uuid import uuid4

    response = await client.post(
        f"/api/v1/coordinator/tickets/{uuid4()}/emergency-review",
        json={"decision": "CONFIRM_P5"},
    )
    assert response.status_code in {401, 403}


def test_confirming_the_emergency_takes_no_priority():
    """Confirming keeps P5 by definition. A priority alongside it would be a
    caller expressing two different intentions at once."""
    with pytest.raises(ValidationError):
        EmergencyReviewRequest(decision=EmergencyDecision.CONFIRM_P5, priority=Priority.P2)

    assert EmergencyReviewRequest(decision=EmergencyDecision.CONFIRM_P5).priority is None


def test_a_downgrade_requires_a_target_below_p5():
    with pytest.raises(ValidationError):
        EmergencyReviewRequest(decision=EmergencyDecision.DOWNGRADE_PRIORITY, reason="Không nguy hiểm.")
    # P5 is not a downgrade target: confirming is the action for staying there.
    with pytest.raises(ValidationError):
        EmergencyReviewRequest(
            decision=EmergencyDecision.DOWNGRADE_PRIORITY, priority=Priority.P5, reason="Vẫn khẩn cấp."
        )
    # Every band below it is, including P4 -- "urgent but not an emergency" is
    # exactly the judgement this action exists to record.
    assert (
        EmergencyReviewRequest(
            decision=EmergencyDecision.DOWNGRADE_PRIORITY, priority=Priority.P4, reason="Khẩn nhưng không nguy hiểm."
        ).priority
        is Priority.P4
    )

    accepted = EmergencyReviewRequest(
        decision=EmergencyDecision.DOWNGRADE_PRIORITY, priority=Priority.P1, reason="Không nguy hiểm."
    )
    assert accepted.priority is Priority.P1


def test_a_downgrade_requires_a_written_reason():
    """Overruling the model is a decision somebody has to own."""
    with pytest.raises(ValidationError):
        EmergencyReviewRequest(decision=EmergencyDecision.DOWNGRADE_PRIORITY, priority=Priority.P2, reason="   ")


def test_the_payload_rejects_fields_it_does_not_name():
    with pytest.raises(ValidationError):
        EmergencyReviewRequest(decision=EmergencyDecision.CONFIRM_P5, severity="HIGH")


def test_risk_assessment_response_preserves_blocker_evidence_mapping():
    from src.api.routes.coordinator_tickets import _risk_evidence_response

    payload = _risk_evidence_response(
        {
            "human_safety": ["Có khói tại hành lang."],
            "blockers": {
                "FIRE_OR_SMOKE": ["Ảnh hiện trường có khói đen."],
                "SOLE_ESCAPE_ROUTE_BLOCKED": ["Lối thoát duy nhất bị khóa."],
            },
        }
    )

    assert payload["human_safety"] == ["Có khói tại hành lang."]
    assert payload["blockers"] == {
        "FIRE_OR_SMOKE": ["Ảnh hiện trường có khói đen."],
        "SOLE_ESCAPE_ROUTE_BLOCKED": ["Lối thoát duy nhất bị khóa."],
    }
