"""Supabase access-token verification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import httpx
import jwt
from jwt import PyJWK
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    InvalidTokenError,
    MissingRequiredClaimError,
)

from src.config import Settings
from src.models.api.errors import (
    AUTH_SERVICE_UNAVAILABLE,
    AUTH_TOKEN_EXPIRED,
    AUTH_TOKEN_INVALID,
    DomainError,
)

ASYMMETRIC_ALGORITHMS = ("RS256", "ES256")
AUTH_SERVER_ALGORITHMS = ("HS256", "RS256", "ES256")
JWT_CLOCK_SKEW_SECONDS = 5


class HTTPClient(Protocol):
    def get(self, url: str, **kwargs) -> httpx.Response: ...


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    auth_user_id: UUID
    email: str | None
    phone: str | None
    issuer: str
    audience: str | list[str]
    expires_at: datetime


class SupabaseJWTVerifier:
    """Verifies Supabase JWTs without trusting editable metadata."""

    def __init__(self, settings: Settings, http_client: HTTPClient | None = None) -> None:
        self.settings = settings
        self._http_client = http_client or httpx.Client(timeout=10.0)
        self._jwks_cache: dict[str, dict[str, Any]] | None = None

    @property
    def issuer(self) -> str:
        return f"{self.settings.supabase_url.rstrip('/')}/auth/v1"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

    def refresh_jwks(self) -> None:
        self._jwks_cache = None

    def verify(self, token: str) -> AuthenticatedPrincipal:
        header = self._safe_header(token)
        algorithm = header["alg"]
        mode = self.settings.supabase_jwt_verification_mode
        if mode == "jwks":
            if algorithm not in ASYMMETRIC_ALGORITHMS:
                raise DomainError(AUTH_TOKEN_INVALID, "Unsupported token algorithm.", 401)
            return self._verify_jwks(token, header)
        if mode == "auth_server":
            if algorithm not in AUTH_SERVER_ALGORITHMS:
                raise DomainError(AUTH_TOKEN_INVALID, "Unsupported token algorithm.", 401)
            return self._verify_auth_server(token)
        if algorithm == "HS256":
            return self._verify_auth_server(token)
        if algorithm in ASYMMETRIC_ALGORITHMS:
            return self._verify_jwks(token, header)
        raise DomainError(AUTH_TOKEN_INVALID, "Unsupported token algorithm.", 401)

    def _safe_header(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except (DecodeError, InvalidTokenError) as exc:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401) from exc
        algorithm = header.get("alg")
        if not algorithm or algorithm == "none":
            raise DomainError(AUTH_TOKEN_INVALID, "Unsupported token algorithm.", 401)
        return header

    def _verify_jwks(self, token: str, header: dict[str, Any]) -> AuthenticatedPrincipal:
        if not self.settings.supabase_url:
            raise DomainError(AUTH_TOKEN_INVALID, "JWT verification is not configured.", 401)
        algorithm = header.get("alg")
        if algorithm not in ASYMMETRIC_ALGORITHMS:
            raise DomainError(AUTH_TOKEN_INVALID, "Unsupported token algorithm.", 401)
        kid = header.get("kid")
        if not kid:
            raise DomainError(AUTH_TOKEN_INVALID, "Missing token key identifier.", 401)
        key_data = self._get_jwk(kid, refresh_on_miss=True)
        if key_data is None:
            raise DomainError(AUTH_TOKEN_INVALID, "Unknown token signing key.", 401)
        try:
            public_key = PyJWK.from_dict(key_data, algorithm=algorithm).key
            payload = jwt.decode(
                token,
                public_key,
                algorithms=[algorithm],
                audience=self.settings.supabase_jwt_audience,
                issuer=self.issuer,
                options={"require": ["exp", "sub"], "verify_iat": False},
            )
        except ExpiredSignatureError as exc:
            raise DomainError(AUTH_TOKEN_EXPIRED, "Access token has expired.", 401) from exc
        except (InvalidIssuerError, InvalidAudienceError, MissingRequiredClaimError, InvalidSignatureError) as exc:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401) from exc
        except InvalidTokenError as exc:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401) from exc
        return self._principal_from_payload(payload)

    def _get_jwk(self, kid: str, refresh_on_miss: bool) -> dict[str, Any] | None:
        jwks = self._fetch_jwks(force=False)
        key = jwks.get(kid)
        if key is None and refresh_on_miss:
            jwks = self._fetch_jwks(force=True)
            key = jwks.get(kid)
        return key

    def _fetch_jwks(self, force: bool) -> dict[str, dict[str, Any]]:
        if self._jwks_cache is not None and not force:
            return self._jwks_cache
        try:
            response = self._http_client.get(self.jwks_url)
        except httpx.HTTPError as exc:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Authentication service is unavailable.", 503) from exc
        if response.status_code == 404:
            self._jwks_cache = {}
            return self._jwks_cache
        if response.status_code in {401, 403}:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401)
        if response.status_code == 429 or response.status_code >= 500:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Authentication service is unavailable.", 503)
        if response.status_code >= 400:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401)
        try:
            payload = response.json()
        except ValueError as exc:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Authentication service is unavailable.", 503) from exc
        keys = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(keys, list):
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Authentication service is unavailable.", 503)
        self._jwks_cache = {
            str(key["kid"]): key
            for key in keys
            if isinstance(key, dict) and key.get("kid") and key.get("alg") in ASYMMETRIC_ALGORITHMS
        }
        return self._jwks_cache

    def _verify_auth_server(self, token: str) -> AuthenticatedPrincipal:
        if not self.settings.supabase_url or not self.settings.supabase_publishable_key:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Authentication service is unavailable.", 503)
        try:
            response = self._http_client.get(
                f"{self.settings.supabase_url.rstrip('/')}/auth/v1/user",
                headers={
                    "apikey": self.settings.supabase_publishable_key,
                    "Authorization": f"Bearer {token}",
                },
            )
        except httpx.HTTPError as exc:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Authentication service is unavailable.", 503) from exc
        if response.status_code in {401, 403}:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401)
        if response.status_code == 429 or response.status_code >= 500:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Authentication service is unavailable.", 503)
        if response.status_code != 200:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401)
        try:
            user_data = response.json()
        except ValueError as exc:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Authentication service is unavailable.", 503) from exc
        claims = self._unverified_claims(token)
        user_id = user_data.get("id") if isinstance(user_data, dict) else None
        if not user_id or str(claims.get("sub")) != str(user_id):
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401)
        principal = self._principal_from_payload(
            {
                **claims,
                "email": user_data.get("email"),
                "phone": user_data.get("phone"),
            }
        )
        return principal

    def _unverified_claims(self, token: str) -> dict[str, Any]:
        try:
            claims = jwt.decode(
                token,
                options={
                    "verify_signature": False,
                    "verify_exp": False,
                    "verify_aud": False,
                    "verify_iss": False,
                    "require": ["exp", "sub"],
                },
            )
        except MissingRequiredClaimError as exc:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401) from exc
        except InvalidTokenError as exc:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401) from exc
        self._validate_registered_claims(claims)
        return claims

    def _validate_registered_claims(self, payload: dict[str, Any]) -> None:
        if payload.get("iss") != self.issuer:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401)
        audience = payload.get("aud")
        expected = self.settings.supabase_jwt_audience
        if isinstance(audience, list):
            if expected not in audience:
                raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401)
        elif audience != expected:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401)
        exp = payload.get("exp")
        if exp is None:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401)
        try:
            expires_at = datetime.fromtimestamp(int(exp), tz=UTC)
        except (TypeError, ValueError, OSError) as exc:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401) from exc
        if expires_at <= datetime.now(UTC):
            raise DomainError(AUTH_TOKEN_EXPIRED, "Access token has expired.", 401)
        issued_at = payload.get("iat")
        if issued_at is not None:
            try:
                issued_at_value = datetime.fromtimestamp(int(issued_at), tz=UTC)
            except (TypeError, ValueError, OSError) as exc:
                raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401) from exc
            if issued_at_value > datetime.now(UTC) + timedelta(seconds=JWT_CLOCK_SKEW_SECONDS):
                raise DomainError(AUTH_TOKEN_INVALID, "Invalid access token.", 401)

    def _principal_from_payload(self, payload: dict[str, Any]) -> AuthenticatedPrincipal:
        self._validate_registered_claims(payload)
        subject = payload.get("sub")
        try:
            auth_user_id = UUID(str(subject))
        except (TypeError, ValueError) as exc:
            raise DomainError(AUTH_TOKEN_INVALID, "Invalid token subject.", 401) from exc
        if not payload.get("email") and not payload.get("phone"):
            raise DomainError(AUTH_TOKEN_INVALID, "Token must contain validated email or phone.", 401)
        return AuthenticatedPrincipal(
            auth_user_id=auth_user_id,
            email=payload.get("email"),
            phone=payload.get("phone"),
            issuer=str(payload["iss"]),
            audience=payload["aud"],
            expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
        )
