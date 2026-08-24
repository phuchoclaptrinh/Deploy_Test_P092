"""Supabase admin header construction tests."""

from pathlib import Path

import pytest

from src.security.supabase_admin import build_supabase_admin_headers

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_sb_secret_key_uses_apikey_without_bearer_authorization():
    headers = build_supabase_admin_headers("sb_secret_" + ("a" * 24))

    assert headers["apikey"].startswith("sb_secret_")
    assert headers["Content-Type"] == "application/json"
    assert "Authorization" not in headers


def test_legacy_service_role_jwt_keeps_bearer_authorization():
    key = "eyJ" + ("a" * 24) + "." + ("b" * 24) + "." + ("c" * 24)

    headers = build_supabase_admin_headers(key)

    assert headers["apikey"] == key
    assert headers["Authorization"] == f"Bearer {key}"


def test_json_content_type_can_be_omitted():
    headers = build_supabase_admin_headers("sb_secret_" + ("a" * 24), include_json_content_type=False)

    assert "Content-Type" not in headers


@pytest.mark.parametrize("secret_key", ["", "   "])
def test_blank_key_raises_safe_configuration_error(secret_key):
    with pytest.raises(RuntimeError) as exc:
        build_supabase_admin_headers(secret_key)

    assert str(exc.value) == "SUPABASE_SECRET_KEY is required."
    if secret_key.strip():
        assert secret_key not in str(exc.value)


def test_admin_http_clients_use_shared_helper():
    storage_text = (PROJECT_ROOT / "src" / "services" / "storage_service.py").read_text(encoding="utf-8")
    setup_text = (PROJECT_ROOT / "scripts" / "setup_supabase_storage.py").read_text(encoding="utf-8")
    provision_text = (PROJECT_ROOT / "scripts" / "provision_coordinator.py").read_text(encoding="utf-8")

    for text in (storage_text, setup_text, provision_text):
        assert "build_supabase_admin_headers" in text
        assert "Authorization\": f\"Bearer {settings.supabase_secret_key}" not in text
        assert "Authorization\": f\"Bearer {secret}" not in text
