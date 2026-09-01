"""Phone-number normalization helpers."""

from __future__ import annotations

import re

from src.models.api.errors import AUTH_PROFILE_INVALID, DomainError

E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
SUPABASE_PHONE_RE = re.compile(r"^[1-9]\d{6,14}$")
VIETNAM_LOCAL_PHONE_RE = re.compile(r"^0\d{8,10}$")


def normalize_e164_phone(phone_number: str | None) -> str | None:
    """Return a strict E.164 phone number or raise a stable auth error."""
    if phone_number is None or phone_number == "":
        return None
    phone_number = re.sub(r"[\s.-]+", "", phone_number.strip())
    if VIETNAM_LOCAL_PHONE_RE.fullmatch(phone_number):
        phone_number = f"+84{phone_number[1:]}"
    # Supabase Auth returns its canonical phone field without the leading plus.
    # Normalize that representation before enforcing the application's E.164 contract.
    if SUPABASE_PHONE_RE.fullmatch(phone_number):
        phone_number = f"+{phone_number}"
    if not E164_RE.fullmatch(phone_number):
        raise DomainError(AUTH_PROFILE_INVALID, "Authenticated phone is not normalized E.164.", 401)
    return phone_number
