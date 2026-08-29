"""Supabase JWT verifier tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from src.api.dependencies.auth import get_supabase_jwt_verifier
from src.config import Settings
from src.models.api.errors import AUTH_SERVICE_UNAVAILABLE, AUTH_TOKEN_EXPIRED, AUTH_TOKEN_INVALID, DomainError
from src.security.supabase_jwt import SupabaseJWTVerifier


class FakeHTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[str] = []
        self.headers: list[dict[str, str]] = []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        self.headers.append(kwargs.get("headers") or {})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_valid_rs256_token():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _token(key, "RS256", kid="rsa-key")
    verifier = SupabaseJWTVerifier(_settings(mode="jwks"), FakeHTTP([_response({"keys": [_jwk(key, "RS256", "rsa-key")]})]))

    principal = verifier.verify(token)

    assert principal.email == "resident@example.com"


def test_valid_es256_token_when_supported():
    key = ec.generate_private_key(ec.SECP256R1())
    token = _token(key, "ES256", kid="ec-key")
    verifier = SupabaseJWTVerifier(_settings(mode="jwks"), FakeHTTP([_response({"keys": [_jwk(key, "ES256", "ec-key")]})]))

    principal = verifier.verify(token)

    assert principal.auth_user_id


def test_accepts_small_issued_at_clock_skew():
    key = ec.generate_private_key(ec.SECP256R1())
    token = _token(key, "ES256", kid="ec-key", iat=datetime.now(UTC) + timedelta(seconds=3))
    verifier = SupabaseJWTVerifier(_settings(mode="jwks"), FakeHTTP([_response({"keys": [_jwk(key, "ES256", "ec-key")]})]))

    assert verifier.verify(token).auth_user_id


def test_rejects_large_issued_at_clock_skew():
    key = ec.generate_private_key(ec.SECP256R1())
    token = _token(key, "ES256", kid="ec-key", iat=datetime.now(UTC) + timedelta(seconds=30))
    verifier = SupabaseJWTVerifier(_settings(mode="jwks"), FakeHTTP([_response({"keys": [_jwk(key, "ES256", "ec-key")]})]))

    with pytest.raises(DomainError) as exc:
        verifier.verify(token)

    assert exc.value.code == AUTH_TOKEN_INVALID


def test_rejects_alg_none_token():
    token = jwt.encode(_claims(), key="", algorithm="none")

    with pytest.raises(DomainError) as exc:
        SupabaseJWTVerifier(_settings(mode="jwks")).verify(token)

    assert exc.value.code == AUTH_TOKEN_INVALID


def test_hs256_auto_mode_calls_auth_server():
    user_id = uuid4()
    token = _hs_token(user_id=user_id)
    http = FakeHTTP([_response({"id": str(user_id), "email": "resident@example.com", "phone": None})])
    verifier = SupabaseJWTVerifier(_settings(mode="auto"), http)

    principal = verifier.verify(token)

    assert principal.auth_user_id == user_id
    assert http.calls == ["https://project.supabase.co/auth/v1/user"]
    assert http.headers[0]["apikey"] == "publishable"
    assert "secret" not in str(http.headers[0])


def test_hs256_jwks_mode_rejected():
    token = _hs_token()

    with pytest.raises(DomainError) as exc:
        SupabaseJWTVerifier(_settings(mode="jwks")).verify(token)

    assert exc.value.code == AUTH_TOKEN_INVALID


def test_expired_token_rejected():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _token(key, "RS256", kid="rsa-key", exp=datetime.now(UTC) - timedelta(seconds=1))
    verifier = SupabaseJWTVerifier(_settings(mode="jwks"), FakeHTTP([_response({"keys": [_jwk(key, "RS256", "rsa-key")]})]))

    with pytest.raises(DomainError) as exc:
        verifier.verify(token)

    assert exc.value.code == AUTH_TOKEN_EXPIRED


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("iss", "https://wrong.example/auth/v1"),
        ("aud", "wrong"),
        ("sub", None),
        ("sub", "not-a-uuid"),
        ("exp", None),
    ],
)
def test_invalid_required_claims_rejected(claim, value):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = _claims()
    if value is None:
        claims.pop(claim, None)
    else:
        claims[claim] = value
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "rsa-key"})
    verifier = SupabaseJWTVerifier(_settings(mode="jwks"), FakeHTTP([_response({"keys": [_jwk(key, "RS256", "rsa-key")]})]))

    with pytest.raises(DomainError) as exc:
        verifier.verify(token)

    assert exc.value.code == AUTH_TOKEN_INVALID


def test_missing_kid_rejected_for_asymmetric_token():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(_claims(), key, algorithm="RS256")

    with pytest.raises(DomainError) as exc:
        SupabaseJWTVerifier(_settings(mode="jwks")).verify(token)

    assert exc.value.code == AUTH_TOKEN_INVALID


def test_unknown_kid_refreshes_once_then_succeeds():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _token(key, "RS256", kid="new-key")
    http = FakeHTTP([_response({"keys": []}), _response({"keys": [_jwk(key, "RS256", "new-key")]})])
    verifier = SupabaseJWTVerifier(_settings(mode="auto"), http)

    principal = verifier.verify(token)

    assert principal.email == "resident@example.com"
    assert len(http.calls) == 2


def test_invalid_signature_does_not_fallback_to_auth_server():
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _token(signing_key, "RS256", kid="rsa-key")
    http = FakeHTTP(
        [
            _response({"keys": [_jwk(jwks_key, "RS256", "rsa-key")]}),
            _response({"id": str(uuid4()), "email": "resident@example.com"}),
        ]
    )
    verifier = SupabaseJWTVerifier(_settings(mode="auto"), http)

    with pytest.raises(DomainError) as exc:
        verifier.verify(token)

    assert exc.value.code == AUTH_TOKEN_INVALID
    assert len(http.calls) == 1


def test_empty_jwks_rejects_asymmetric_token_without_auth_fallback():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _token(key, "RS256", kid="rsa-key")
    http = FakeHTTP([_response({"keys": []}), _response({"keys": []})])
    verifier = SupabaseJWTVerifier(_settings(mode="auto"), http)

    with pytest.raises(DomainError) as exc:
        verifier.verify(token)

    assert exc.value.code == AUTH_TOKEN_INVALID
    assert len(http.calls) == 2


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_server_unauthorized_statuses(status_code):
    token = _hs_token()
    verifier = SupabaseJWTVerifier(_settings(mode="auth_server"), FakeHTTP([_response({}, status_code=status_code)]))

    with pytest.raises(DomainError) as exc:
        verifier.verify(token)

    assert exc.value.code == AUTH_TOKEN_INVALID


@pytest.mark.parametrize("status_code", [429, 500])
def test_auth_server_unavailable_statuses(status_code):
    token = _hs_token()
    verifier = SupabaseJWTVerifier(_settings(mode="auth_server"), FakeHTTP([_response({}, status_code=status_code)]))

    with pytest.raises(DomainError) as exc:
        verifier.verify(token)

    assert exc.value.code == AUTH_SERVICE_UNAVAILABLE


def test_auth_server_timeout():
    token = _hs_token()
    verifier = SupabaseJWTVerifier(_settings(mode="auth_server"), FakeHTTP([httpx.TimeoutException("timeout")]))

    with pytest.raises(DomainError) as exc:
        verifier.verify(token)

    assert exc.value.code == AUTH_SERVICE_UNAVAILABLE


def test_auth_server_invalid_json():
    token = _hs_token()
    response = httpx.Response(200, content=b"not-json", request=httpx.Request("GET", "https://project.supabase.co/auth/v1/user"))
    verifier = SupabaseJWTVerifier(_settings(mode="auth_server"), FakeHTTP([response]))

    with pytest.raises(DomainError) as exc:
        verifier.verify(token)

    assert exc.value.code == AUTH_SERVICE_UNAVAILABLE


def test_auth_server_sub_mismatch():
    token = _hs_token(user_id=uuid4())
    verifier = SupabaseJWTVerifier(
        _settings(mode="auth_server"),
        FakeHTTP([_response({"id": str(uuid4()), "email": "resident@example.com"})]),
    )

    with pytest.raises(DomainError) as exc:
        verifier.verify(token)

    assert exc.value.code == AUTH_TOKEN_INVALID


def test_cached_verifier_dependency_reused():
    get_supabase_jwt_verifier.cache_clear()

    first = get_supabase_jwt_verifier()
    second = get_supabase_jwt_verifier()

    assert first is second


def _settings(mode: str = "auto") -> Settings:
    return Settings(
        app_env="test",
        supabase_url="https://project.supabase.co",
        supabase_publishable_key="publishable",
        supabase_jwt_audience="authenticated",
        supabase_jwt_verification_mode=mode,
    )


def _claims(user_id=None, exp=None) -> dict[str, object]:
    return {
        "sub": str(user_id or uuid4()),
        "email": "resident@example.com",
        "iss": "https://project.supabase.co/auth/v1",
        "aud": "authenticated",
        "exp": exp or datetime.now(UTC) + timedelta(minutes=5),
    }


def _token(key, algorithm: str, kid: str, **claim_overrides) -> str:
    claims = _claims()
    claims.update(claim_overrides)
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})


def _hs_token(user_id=None) -> str:
    return jwt.encode(_claims(user_id=user_id), "shared-secret-value-with-at-least-32-bytes", algorithm="HS256")


def _jwk(key, algorithm: str, kid: str) -> dict[str, object]:
    public_key = key.public_key()
    if algorithm == "RS256":
        data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))
    else:
        data = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(public_key))
    data["kid"] = kid
    data["alg"] = algorithm
    data["use"] = "sig"
    return data


def _response(payload: dict[str, object], status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "https://project.supabase.co/auth/v1/.well-known/jwks.json"),
    )
