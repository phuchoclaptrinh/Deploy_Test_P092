"""Storage service tests."""

from uuid import uuid4

import httpx
import pytest

from src.config import Settings
from src.models.api.errors import INVALID_ATTACHMENT, STORAGE_NOT_CONFIGURED, DomainError
from src.models.api.storage import SignedUploadRequest
from src.services.storage_service import SIGNED_UPLOAD_EXPIRY_SECONDS, StorageService


class FakeHTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url: str, **kwargs):
        self.calls.append(("POST", url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url: str, **kwargs):
        self.calls.append(("GET", url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_path_generated_server_side_and_scoped_to_user():
    user_id = uuid4()
    service = StorageService(Settings(app_env="test"))

    path = service.generate_ticket_attachment_path(user_id, "leak.jpg", "image/jpeg")

    assert path.startswith(f"tickets/{user_id}/")
    assert path.endswith(".jpg")
    assert "leak.jpg" not in path


def test_rejects_svg_and_oversized_file():
    service = StorageService(Settings(app_env="test", max_ticket_image_bytes=10))

    with pytest.raises(DomainError) as svg_exc:
        service._validate_metadata(SignedUploadRequest(original_filename="x.svg", mime_type="image/svg+xml", file_size=1))
    assert svg_exc.value.code == INVALID_ATTACHMENT

    with pytest.raises(DomainError):
        service._validate_metadata(SignedUploadRequest(original_filename="x.jpg", mime_type="image/jpeg", file_size=11))


def test_rejects_path_traversal():
    service = StorageService(Settings(app_env="test"))

    with pytest.raises(DomainError):
        service.generate_ticket_attachment_path(uuid4(), "../x.jpg", "image/jpeg")


def test_create_signed_upload_target_accepts_supabase_response_fields():
    user_id = uuid4()
    http = FakeHTTP([_response({"signedURL": "https://upload.example/signed", "token": "upload-token"})])
    service = StorageService(_settings(), http)

    target = service.create_signed_upload_target(
        user_id,
        SignedUploadRequest(original_filename="x.jpg", mime_type="image/jpeg", file_size=10),
    )

    assert target.storage_path.startswith(f"tickets/{user_id}/")
    assert target.signed_upload_url == "https://upload.example/signed"
    assert target.signed_upload_token == "upload-token"
    assert target.expires_in == SIGNED_UPLOAD_EXPIRY_SECONDS
    assert "expiresIn" not in str(http.calls[0][2])
    assert http.calls[0][2]["json"] == {}


def test_create_signed_upload_target_accepts_current_relative_url_field():
    http = FakeHTTP([_response({"url": "/object/upload/sign/ticket-attachments/path?token=x", "token": "x"})])
    service = StorageService(_settings(), http)

    target = service.create_signed_upload_target(
        uuid4(),
        SignedUploadRequest(original_filename="x.png", mime_type="image/png", file_size=10),
    )

    assert target.signed_upload_url == (
        "https://project.supabase.co/storage/v1/object/upload/sign/ticket-attachments/path?token=x"
    )


def test_create_signed_upload_target_requires_usable_credential():
    service = StorageService(_settings(), FakeHTTP([_response({})]))

    with pytest.raises(DomainError) as exc:
        service.create_signed_upload_target(
            uuid4(),
            SignedUploadRequest(original_filename="x.jpg", mime_type="image/jpeg", file_size=10),
        )

    assert exc.value.code == STORAGE_NOT_CONFIGURED


@pytest.mark.parametrize("status_code", [401, 403, 429, 500])
def test_create_signed_upload_target_maps_storage_failures(status_code):
    service = StorageService(_settings(), FakeHTTP([_response({}, status_code=status_code)]))

    with pytest.raises(DomainError) as exc:
        service.create_signed_upload_target(
            uuid4(),
            SignedUploadRequest(original_filename="x.jpg", mime_type="image/jpeg", file_size=10),
        )

    assert exc.value.code == STORAGE_NOT_CONFIGURED


def test_verify_uploaded_object_uses_object_info_metadata():
    storage_path = f"tickets/{uuid4()}/2026/08/{uuid4()}.jpg"
    service = StorageService(
        _settings(),
        FakeHTTP([_response({"metadata": {"mimetype": "image/jpeg", "size": 10}})]),
    )

    verified = service.verify_uploaded_object(storage_path, expected_mime_type="image/jpeg", expected_file_size=10)

    assert verified.mime_type == "image/jpeg"
    assert verified.file_size == 10


def test_verify_uploaded_object_accepts_current_top_level_metadata_fields():
    storage_path = f"tickets/{uuid4()}/2026/08/{uuid4()}.png"
    service = StorageService(
        _settings(),
        FakeHTTP([_response({"content_type": "image/png", "size": 10, "metadata": {}})]),
    )

    verified = service.verify_uploaded_object(storage_path, expected_mime_type="image/png", expected_file_size=10)

    assert verified.mime_type == "image/png"


def test_verify_uploaded_object_is_strict_when_missing_or_mismatched():
    storage_path = f"tickets/{uuid4()}/2026/08/{uuid4()}.jpg"
    service = StorageService(
        _settings(),
        FakeHTTP([_response({"metadata": {"mimetype": "image/png", "size": 10}})]),
    )

    with pytest.raises(DomainError) as exc:
        service.verify_uploaded_object(storage_path, expected_mime_type="image/jpeg", expected_file_size=10)

    assert exc.value.code == INVALID_ATTACHMENT


def test_verify_uploaded_object_missing_configuration_is_not_success():
    service = StorageService(Settings(app_env="test"))

    with pytest.raises(DomainError) as exc:
        service.verify_uploaded_object(f"tickets/{uuid4()}/2026/08/{uuid4()}.jpg")

    assert exc.value.code == STORAGE_NOT_CONFIGURED


def test_create_signed_download_url_never_returns_none_string():
    service = StorageService(_settings(), FakeHTTP([_response({})]))

    with pytest.raises(DomainError) as exc:
        service.create_signed_download_url(f"tickets/{uuid4()}/2026/08/{uuid4()}.jpg")

    assert exc.value.code == STORAGE_NOT_CONFIGURED


def test_network_errors_map_to_safe_storage_failure():
    service = StorageService(_settings(), FakeHTTP([httpx.TimeoutException("timeout")]))

    with pytest.raises(DomainError) as exc:
        service.create_signed_download_url(f"tickets/{uuid4()}/2026/08/{uuid4()}.jpg")

    assert exc.value.code == STORAGE_NOT_CONFIGURED


def _settings() -> Settings:
    return Settings(
        app_env="test",
        supabase_url="https://project.supabase.co",
        supabase_secret_key="secret",
        supabase_storage_bucket="ticket-attachments",
    )


def _response(payload: dict[str, object], status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("POST", "https://project.supabase.co"))
