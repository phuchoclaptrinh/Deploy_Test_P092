"""Supabase Storage signed URL and object-verification operations."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx

from src.config import Settings, get_settings
from src.models.api.errors import INVALID_ATTACHMENT, STORAGE_NOT_CONFIGURED, DomainError
from src.models.api.storage import SignedUploadRequest
from src.security.supabase_admin import build_supabase_admin_headers

SIGNED_UPLOAD_EXPIRY_SECONDS = 7200
SAFE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class HTTPClient(Protocol):
    def get(self, url: str, **kwargs) -> httpx.Response: ...

    def post(self, url: str, **kwargs) -> httpx.Response: ...


@dataclass(frozen=True)
class SignedUploadTarget:
    storage_path: str
    signed_upload_url: str | None
    signed_upload_token: str | None
    expires_in: int
    required_headers: dict[str, str]


@dataclass(frozen=True)
class VerifiedStorageObject:
    mime_type: str
    file_size: int
    verified_at: datetime


class StorageService:
    def __init__(self, settings: Settings | None = None, http_client: HTTPClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._http_client = http_client or httpx.Client(timeout=10.0)

    def create_signed_upload_target(self, user_id: UUID, request: SignedUploadRequest) -> SignedUploadTarget:
        self._validate_metadata(request)
        storage_path = self.generate_ticket_attachment_path(user_id, request.original_filename, request.mime_type)
        return self._create_signed_upload_target_for_path(storage_path, request)

    def create_completion_evidence_upload_target(self, technician_id: UUID, request: SignedUploadRequest) -> SignedUploadTarget:
        self._validate_metadata(request)
        storage_path = self.generate_completion_evidence_path(technician_id, request.original_filename, request.mime_type)
        return self._create_signed_upload_target_for_path(storage_path, request)

    def _create_signed_upload_target_for_path(self, storage_path: str, request: SignedUploadRequest) -> SignedUploadTarget:
        if not self._is_configured():
            raise DomainError(STORAGE_NOT_CONFIGURED, "Supabase Storage is not configured.", 503)
        try:
            response = self._http_client.post(
                f"{self._base_storage_url()}/object/upload/sign/"
                f"{self.settings.supabase_storage_bucket}/{storage_path}",
                headers=self._service_headers(),
                json={},
            )
        except httpx.HTTPError as exc:
            raise DomainError(STORAGE_NOT_CONFIGURED, "Unable to create signed upload URL.", 503) from exc
        self._raise_for_storage_response(response, "Unable to create signed upload URL.")
        data = self._json(response, "Unable to create signed upload URL.")
        signed_url = data.get("signedURL") or data.get("signedUrl") or data.get("signed_url") or data.get("url")
        token = data.get("token")
        if not signed_url and not token:
            raise DomainError(STORAGE_NOT_CONFIGURED, "Unable to create signed upload URL.", 503)
        return SignedUploadTarget(
            storage_path=storage_path,
            signed_upload_url=self._absolute_storage_url(str(signed_url)) if signed_url else None,
            signed_upload_token=str(token) if token else None,
            expires_in=SIGNED_UPLOAD_EXPIRY_SECONDS,
            required_headers={"content-type": request.mime_type},
        )

    def create_signed_download_url(self, storage_path: str) -> str:
        self._reject_path_traversal(storage_path)
        if not self._is_configured():
            raise DomainError(STORAGE_NOT_CONFIGURED, "Supabase Storage is not configured.", 503)
        try:
            response = self._http_client.post(
                f"{self._base_storage_url()}/object/sign/{self.settings.supabase_storage_bucket}/{storage_path}",
                headers=self._service_headers(),
                json={"expiresIn": self.settings.supabase_signed_download_ttl_seconds},
            )
        except httpx.HTTPError as exc:
            raise DomainError(STORAGE_NOT_CONFIGURED, "Unable to create signed download URL.", 503) from exc
        self._raise_for_storage_response(response, "Unable to create signed download URL.")
        data = self._json(response, "Unable to create signed download URL.")
        signed_url = data.get("signedURL") or data.get("signedUrl") or data.get("signed_url")
        if not signed_url:
            raise DomainError(STORAGE_NOT_CONFIGURED, "Unable to create signed download URL.", 503)
        return self._absolute_storage_url(str(signed_url))

    def generate_ticket_attachment_path(self, user_id: UUID, original_filename: str, mime_type: str) -> str:
        self._reject_path_traversal(original_filename)
        now = datetime.now(UTC)
        extension = SAFE_EXTENSIONS.get(mime_type) or mimetypes.guess_extension(mime_type)
        if extension not in SAFE_EXTENSIONS.values():
            raise DomainError(INVALID_ATTACHMENT, "Unsupported image type.", 400)
        return f"tickets/{user_id}/{now:%Y}/{now:%m}/{uuid4()}{extension}"

    def generate_completion_evidence_path(self, technician_id: UUID, original_filename: str, mime_type: str) -> str:
        self._reject_path_traversal(original_filename)
        now = datetime.now(UTC)
        extension = SAFE_EXTENSIONS.get(mime_type) or mimetypes.guess_extension(mime_type)
        if extension not in SAFE_EXTENSIONS.values():
            raise DomainError(INVALID_ATTACHMENT, "Unsupported image type.", 400)
        return f"completion-evidence/{technician_id}/{now:%Y}/{now:%m}/{uuid4()}{extension}"

    def is_owned_ticket_attachment_path(self, storage_path: str, user_id: UUID) -> bool:
        self._reject_path_traversal(storage_path)
        return storage_path.startswith(f"tickets/{user_id}/")

    def is_owned_completion_evidence_path(self, storage_path: str, technician_id: UUID) -> bool:
        self._reject_path_traversal(storage_path)
        return storage_path.startswith(f"completion-evidence/{technician_id}/")

    def verify_uploaded_object(
        self,
        storage_path: str,
        expected_mime_type: str | None = None,
        expected_file_size: int | None = None,
    ) -> VerifiedStorageObject:
        self._reject_path_traversal(storage_path)
        if not self._is_configured():
            raise DomainError(STORAGE_NOT_CONFIGURED, "Supabase Storage is not configured.", 503)
        try:
            response = self._http_client.get(
                f"{self._base_storage_url()}/object/info/"
                f"{self.settings.supabase_storage_bucket}/{storage_path}",
                headers=self._service_headers(),
            )
        except httpx.HTTPError as exc:
            raise DomainError(STORAGE_NOT_CONFIGURED, "Unable to verify uploaded object.", 503) from exc
        if response.status_code == 404:
            raise DomainError(INVALID_ATTACHMENT, "Uploaded attachment object was not found.", 400)
        self._raise_for_storage_response(response, "Unable to verify uploaded object.")
        data = self._json(response, "Unable to verify uploaded object.")
        mime_type = self._metadata_value(data, "mimetype", "mime_type", "content_type", "contentType", "content-type")
        file_size_raw = self._metadata_value(data, "size", "contentLength", "content-length")
        try:
            file_size = int(file_size_raw)
        except (TypeError, ValueError) as exc:
            raise DomainError(INVALID_ATTACHMENT, "Uploaded attachment metadata is invalid.", 400) from exc
        if not isinstance(mime_type, str) or mime_type not in self.settings.parsed_allowed_ticket_image_mime_types:
            raise DomainError(INVALID_ATTACHMENT, "Uploaded attachment MIME type is invalid.", 400)
        if expected_mime_type is not None and mime_type != expected_mime_type:
            raise DomainError(INVALID_ATTACHMENT, "Uploaded attachment MIME type is invalid.", 400)
        if expected_file_size is not None and file_size != expected_file_size:
            raise DomainError(INVALID_ATTACHMENT, "Uploaded attachment size is invalid.", 400)
        if file_size <= 0 or file_size > self.settings.max_ticket_image_bytes:
            raise DomainError(INVALID_ATTACHMENT, "Uploaded attachment size is invalid.", 400)
        return VerifiedStorageObject(mime_type=mime_type, file_size=file_size, verified_at=datetime.now(UTC))

    def _validate_metadata(self, request: SignedUploadRequest) -> None:
        if request.mime_type not in self.settings.parsed_allowed_ticket_image_mime_types:
            raise DomainError(INVALID_ATTACHMENT, "Unsupported image type.", 400)
        if request.mime_type == "image/svg+xml":
            raise DomainError(INVALID_ATTACHMENT, "SVG images are not allowed.", 400)
        if request.file_size > self.settings.max_ticket_image_bytes:
            raise DomainError(INVALID_ATTACHMENT, "Image is too large.", 400)
        self._reject_path_traversal(request.original_filename)

    def _reject_path_traversal(self, value: str) -> None:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise DomainError(INVALID_ATTACHMENT, "Invalid attachment path.", 400)

    def _raise_for_storage_response(self, response: httpx.Response, message: str) -> None:
        if response.status_code in {401, 403, 429} or response.status_code >= 500:
            raise DomainError(STORAGE_NOT_CONFIGURED, message, 503)
        if response.status_code == 409:
            raise DomainError(INVALID_ATTACHMENT, "Attachment object already exists.", 400)
        if response.status_code >= 400:
            raise DomainError(STORAGE_NOT_CONFIGURED, message, 503)

    def _json(self, response: httpx.Response, message: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise DomainError(STORAGE_NOT_CONFIGURED, message, 503) from exc
        if not isinstance(data, dict):
            raise DomainError(STORAGE_NOT_CONFIGURED, message, 503)
        return data

    def _metadata_value(self, data: dict[str, Any], *keys: str) -> Any:
        metadata = data.get("metadata")
        candidates: list[dict[str, Any]] = [data]
        if isinstance(metadata, dict):
            candidates.append(metadata)
        for candidate in candidates:
            for key in keys:
                if key in candidate:
                    return candidate[key]
        return None

    def _is_configured(self) -> bool:
        return bool(self.settings.supabase_url and self.settings.supabase_secret_key and self.settings.supabase_storage_bucket)

    def _base_storage_url(self) -> str:
        return f"{self.settings.supabase_url.rstrip('/')}/storage/v1"

    def _absolute_storage_url(self, value: str) -> str:
        if value.startswith("/"):
            return f"{self._base_storage_url()}{value}"
        return value

    def _service_headers(self) -> dict[str, str]:
        return build_supabase_admin_headers(self.settings.supabase_secret_key)
