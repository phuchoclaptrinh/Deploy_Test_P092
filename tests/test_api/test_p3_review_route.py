"""The P3 review endpoint: who may call it, and what a valid payload looks like.

The behaviour behind the endpoint is covered against a real database in
`tests/test_agents/test_p3_review_gate.py`. What is checked here is the part
only the HTTP layer can enforce: the route is coordinator-only, and the request
schema refuses a decision that contradicts itself before any of that behaviour
is reached.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from src.main import app
from src.models.agent_schemas import P3Decision
from src.models.api.coordinator import P3ReviewRequest
from src.models.enums import Priority

P3_PATH = "/api/v1/coordinator/tickets/{ticket_id}/p3-review"


def test_the_route_is_registered_as_a_post():
    # Read through the OpenAPI schema rather than `app.routes`: the sub-routers
    # are included lazily, so the route objects do not exist until something
    # forces them to resolve.
    assert "post" in app.openapi()["paths"][P3_PATH]


def test_the_route_is_coordinator_only():
    """Read off the handler itself. The sub-routers are included lazily, so
    there is no resolved route object to inspect until the app is served."""
    from src.api.routes import coordinator_tickets

    guards = {
        getattr(parameter.default, "dependency", None).__name__
        for parameter in inspect.signature(coordinator_tickets.review_p3).parameters.values()
        if getattr(parameter.default, "dependency", None) is not None
    }
    assert "require_coordinator" in guards


@pytest.mark.asyncio
async def test_an_anonymous_caller_is_rejected(client):
    from uuid import uuid4

    response = await client.post(
        f"/api/v1/coordinator/tickets/{uuid4()}/p3-review",
        json={"decision": "CONFIRM_P3"},
    )
    assert response.status_code in {401, 403}


def test_confirming_p3_takes_no_priority():
    """Confirming keeps P3 by definition. A priority alongside it would be a
    caller expressing two different intentions at once."""
    with pytest.raises(ValidationError):
        P3ReviewRequest(decision=P3Decision.CONFIRM_P3, priority=Priority.P2)

    assert P3ReviewRequest(decision=P3Decision.CONFIRM_P3).priority is None


def test_a_downgrade_requires_a_target_below_p3():
    with pytest.raises(ValidationError):
        P3ReviewRequest(decision=P3Decision.DOWNGRADE_SEVERITY, reason="Không nguy hiểm.")
    with pytest.raises(ValidationError):
        P3ReviewRequest(
            decision=P3Decision.DOWNGRADE_SEVERITY, priority=Priority.P3, reason="Vẫn khẩn cấp."
        )

    accepted = P3ReviewRequest(
        decision=P3Decision.DOWNGRADE_SEVERITY, priority=Priority.P1, reason="Không nguy hiểm."
    )
    assert accepted.priority is Priority.P1


def test_a_downgrade_requires_a_written_reason():
    """Overruling the model is a decision somebody has to own."""
    with pytest.raises(ValidationError):
        P3ReviewRequest(decision=P3Decision.DOWNGRADE_SEVERITY, priority=Priority.P2, reason="   ")


def test_the_payload_rejects_fields_it_does_not_name():
    with pytest.raises(ValidationError):
        P3ReviewRequest(decision=P3Decision.CONFIRM_P3, severity="HIGH")
