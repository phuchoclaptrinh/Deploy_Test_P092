"""Thin Supabase Auth OTP proxy for the canonical Self Dev auth API.

The service never persists OTPs or tokens. Frontend may also call Supabase Auth
itself, but exposing these two endpoints keeps the backend API surface compatible
with Self_Dev_Docs/dexuatdb_API-_v2.md.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from src.config import Settings, get_settings
from src.models.api.errors import AUTH_SERVICE_UNAVAILABLE, AUTH_TOKEN_INVALID, VALIDATION_ERROR, DomainError
from src.services.phone import normalize_e164_phone


class HTTPClient(Protocol):
    def post(self, url: str, **kwargs) -> httpx.Response: ...


class SupabaseOtpService:
    def __init__(self, settings: Settings | None = None, http_client: HTTPClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.http = http_client or httpx.Client(timeout=10.0)

    def request_otp(self, raw_phone: str) -> None:
        phone = self._phone(raw_phone)
        response = self._post("/auth/v1/otp", {"phone": phone, "create_user": True})
        if response.status_code >= 400:
            self._raise_auth_error(response, invalid_message="Không thể gửi OTP tới số điện thoại này.")

    def verify_otp(self, raw_phone: str, otp: str) -> dict[str, Any]:
        phone = self._phone(raw_phone)
        response = self._post(
            "/auth/v1/verify",
            {"phone": phone, "token": otp.strip(), "type": "sms"},
        )
        if response.status_code >= 400:
            self._raise_auth_error(response, invalid_message="OTP không hợp lệ hoặc đã hết hạn.")
        try:
            data = response.json()
        except ValueError as exc:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Supabase Auth trả dữ liệu không hợp lệ.", 503) from exc
        if not isinstance(data, dict) or not data.get("access_token"):
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Supabase Auth không trả access token.", 503)
        return data

    def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        if not self.settings.supabase_url or not self.settings.supabase_publishable_key:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Supabase Auth chưa được cấu hình.", 503)
        try:
            return self.http.post(
                f"{self.settings.supabase_url.rstrip('/')}{path}",
                headers={
                    "apikey": self.settings.supabase_publishable_key,
                    "content-type": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Không thể kết nối Supabase Auth.", 503) from exc

    @staticmethod
    def _phone(raw_phone: str) -> str:
        try:
            phone = normalize_e164_phone(raw_phone)
        except DomainError as exc:
            raise DomainError(VALIDATION_ERROR, "Số điện thoại phải ở định dạng E.164.", 422) from exc
        if not phone:
            raise DomainError(VALIDATION_ERROR, "Số điện thoại phải ở định dạng E.164.", 422)
        return phone

    @staticmethod
    def _raise_auth_error(response: httpx.Response, *, invalid_message: str) -> None:
        if response.status_code in {429} or response.status_code >= 500:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Supabase Auth tạm thời không khả dụng.", 503)
        raise DomainError(AUTH_TOKEN_INVALID, invalid_message, 400)
