"""Supabase admin HTTP header helpers."""

from __future__ import annotations


def build_supabase_admin_headers(
    secret_key: str,
    *,
    include_json_content_type: bool = True,
) -> dict[str, str]:
    """Return backend-only Supabase admin headers without leaking secrets."""
    if not secret_key or not secret_key.strip():
        raise RuntimeError("SUPABASE_SECRET_KEY is required.")

    key = secret_key.strip()
    headers = {"apikey": key}
    if include_json_content_type:
        headers["Content-Type"] = "application/json"
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    return headers
