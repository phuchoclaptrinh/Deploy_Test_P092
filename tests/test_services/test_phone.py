import pytest

from src.models.api.errors import AUTH_PROFILE_INVALID, DomainError
from src.services.phone import normalize_e164_phone


def test_accepts_e164_phone():
    assert normalize_e164_phone("+84920000001") == "+84920000001"


def test_normalizes_supabase_phone_without_plus():
    assert normalize_e164_phone("84920000001") == "+84920000001"


def test_treats_empty_supabase_phone_as_missing():
    assert normalize_e164_phone("") is None


def test_rejects_non_phone_value():
    with pytest.raises(DomainError) as exc:
        normalize_e164_phone("not-a-phone")
    assert exc.value.code == AUTH_PROFILE_INVALID
