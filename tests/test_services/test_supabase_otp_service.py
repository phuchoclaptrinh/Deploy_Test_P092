import httpx
import pytest

from src.config import Settings
from src.models.api.errors import AUTH_SERVICE_UNAVAILABLE, AUTH_TOKEN_INVALID, VALIDATION_ERROR, DomainError
from src.services.supabase_otp_service import SupabaseOtpService


class FakeClient:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.calls = []

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _response(status: int, payload: dict):
    return httpx.Response(status, json=payload, request=httpx.Request("POST", "https://example.test"))


def _settings():
    return Settings(
        app_env="test",
        supabase_url="https://project.supabase.co",
        supabase_publishable_key="public-key",
    )


def test_request_otp_uses_public_supabase_auth_endpoint_without_persisting_otp():
    client = FakeClient(_response(200, {}))
    SupabaseOtpService(_settings(), client).request_otp("+84901234567")
    url, kwargs = client.calls[0]
    assert url.endswith("/auth/v1/otp")
    assert kwargs["json"] == {"phone": "+84901234567", "create_user": True}
    assert kwargs["headers"]["apikey"] == "public-key"


def test_verify_otp_returns_supabase_session_payload():
    client = FakeClient(
        _response(
            200,
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "token_type": "bearer",
                "expires_in": 3600,
            },
        )
    )
    payload = SupabaseOtpService(_settings(), client).verify_otp("+84901234567", "123456")
    assert payload["access_token"] == "access"
    assert client.calls[0][1]["json"]["type"] == "sms"


def test_a_vietnamese_local_number_is_normalized_rather_than_rejected():
    """`normalize_e164_phone` maps 0XXXXXXXXX onto +84XXXXXXXXX.

    Residents type the local form, so rejecting it would make the OTP flow
    unusable for the people it exists for.
    """
    client = FakeClient(_response(200, {}))
    SupabaseOtpService(_settings(), client).request_otp("0901234567")
    assert client.calls[0][1]["json"]["phone"] == "+84901234567"


def test_invalid_phone_is_validation_error():
    with pytest.raises(DomainError) as exc_info:
        SupabaseOtpService(_settings(), FakeClient(_response(200, {}))).request_otp("khong-phai-so")
    assert exc_info.value.code == VALIDATION_ERROR
    assert exc_info.value.status_code == 422


def test_invalid_otp_maps_to_stable_auth_error():
    with pytest.raises(DomainError) as exc_info:
        SupabaseOtpService(_settings(), FakeClient(_response(400, {"msg": "bad token"}))).verify_otp(
            "+84901234567", "000000"
        )
    assert exc_info.value.code == AUTH_TOKEN_INVALID


def test_missing_supabase_config_fails_closed():
    settings = Settings(app_env="test", supabase_url="", supabase_publishable_key="")
    with pytest.raises(DomainError) as exc_info:
        SupabaseOtpService(settings, FakeClient(_response(200, {}))).request_otp("+84901234567")
    assert exc_info.value.code == AUTH_SERVICE_UNAVAILABLE
