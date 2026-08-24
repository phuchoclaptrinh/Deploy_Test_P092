"""Supabase Auth admin operations used by Coordinator account management."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

import httpx

from src.config import Settings, get_settings
from src.models.api.errors import AUTH_SERVICE_UNAVAILABLE, VALIDATION_ERROR, DomainError
from src.security.supabase_admin import build_supabase_admin_headers


class AdminHTTPClient(Protocol):
    def post(self, url: str, **kwargs) -> httpx.Response: ...

    def put(self, url: str, **kwargs) -> httpx.Response: ...

    def delete(self, url: str, **kwargs) -> httpx.Response: ...


class SupabaseAdminAuthService:
    def __init__(self, settings: Settings | None = None, http_client: AdminHTTPClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.http = http_client or httpx.Client(timeout=10.0)

    def create_resident_user(self, *, phone: str, full_name: str | None = None) -> UUID:
        payload: dict[str, Any] = {
            "phone": phone,
            "phone_confirm": True,
            "user_metadata": {"full_name": full_name} if full_name else {},
        }
        return self._create_user(payload)

    def create_email_user(self, *, email: str, password: str, full_name: str | None = None) -> UUID:
        payload: dict[str, Any] = {
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {"full_name": full_name} if full_name else {},
        }
        return self._create_user(payload)

    def delete_user(self, user_id: UUID) -> None:
        if not self.settings.supabase_url or not self.settings.supabase_secret_key:
            return
        try:
            self.http.delete(
                f"{self.settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}",
                headers=build_supabase_admin_headers(
                    self.settings.supabase_secret_key,
                    include_json_content_type=False,
                ),
            )
        except httpx.HTTPError:
            return

    def update_user(self, user_id: UUID, payload: dict[str, Any]) -> None:
        if not self.settings.supabase_url or not self.settings.supabase_secret_key:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Supabase Auth admin chưa được cấu hình.", 503)
        try:
            response = self.http.put(
                f"{self.settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}",
                headers=build_supabase_admin_headers(self.settings.supabase_secret_key),
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Không thể kết nối Supabase Auth admin.", 503) from exc

        if response.status_code >= 400:
            message = self._extract_error_message(response) or "Không thể cập nhật tài khoản Supabase."
            raise DomainError(VALIDATION_ERROR, message, 400)

    def _create_user(self, payload: dict[str, Any]) -> UUID:
        if not self.settings.supabase_url or not self.settings.supabase_secret_key:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Supabase Auth admin chưa được cấu hình.", 503)
        try:
            response = self.http.post(
                f"{self.settings.supabase_url.rstrip('/')}/auth/v1/admin/users",
                headers=build_supabase_admin_headers(self.settings.supabase_secret_key),
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Không thể kết nối Supabase Auth admin.", 503) from exc

        if response.status_code >= 400:
            message = self._extract_error_message(response) or "Không thể tạo tài khoản Supabase."
            status_code = 409 if response.status_code == 409 else 400
            raise DomainError(VALIDATION_ERROR, message, status_code)

        try:
            data = response.json()
        except ValueError as exc:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Supabase Auth admin trả dữ liệu không hợp lệ.", 503) from exc

        user_id = data.get("id")
        if not user_id and isinstance(data.get("user"), dict):
            user_id = data["user"].get("id")
        if not user_id:
            raise DomainError(AUTH_SERVICE_UNAVAILABLE, "Supabase Auth admin không trả user_id.", 503)
        return UUID(str(user_id))

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str | None:
        try:
            data = response.json()
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        value = data.get("msg") or data.get("message") or data.get("error_description") or data.get("error")
        return str(value) if value else None
