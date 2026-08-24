from uuid import uuid4

from src.models.api.auth import CurrentUserResponse
from src.models.enums import UserRole


def test_current_user_response_accepts_technician_actor():
    response = CurrentUserResponse(
        user_id=uuid4(),
        role=UserRole.TECHNICIAN,
        actor_type="technician",
        full_name="Technician",
        phone_e164=None,
        is_active=True,
    )
    assert response.actor_type == "technician"
