"""Backend-only private Supabase Storage bucket setup helper."""

from __future__ import annotations

import argparse
from typing import Any

import httpx

from src.config import get_settings
from src.security.supabase_admin import build_supabase_admin_headers


def parser() -> argparse.ArgumentParser:
    """Build command-line arguments."""
    arg_parser = argparse.ArgumentParser(
        description="Create or verify the private FixIt attachment bucket."
    )
    arg_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned bucket changes without applying them.",
    )
    return arg_parser


def main() -> int:
    """Create, update, or verify the configured private Storage bucket."""
    args = parser().parse_args()
    settings = get_settings()

    if not settings.supabase_url:
        raise SystemExit("SUPABASE_URL is required.")

    if not settings.supabase_secret_key:
        raise SystemExit("SUPABASE_SECRET_KEY is required.")

    bucket_name = settings.supabase_storage_bucket

    if not bucket_name:
        raise SystemExit("SUPABASE_STORAGE_BUCKET is required.")

    headers = build_supabase_admin_headers(settings.supabase_secret_key)
    base_url = f"{settings.supabase_url.rstrip('/')}/storage/v1"

    desired = {
        "public": False,
        "file_size_limit": settings.max_ticket_image_bytes,
        "allowed_mime_types": sorted(
            settings.parsed_allowed_ticket_image_mime_types
        ),
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                f"{base_url}/bucket/{bucket_name}",
                headers=headers,
            )

            if _is_bucket_not_found(response):
                return _create_bucket(
                    client=client,
                    base_url=base_url,
                    bucket_name=bucket_name,
                    headers=headers,
                    desired=desired,
                    dry_run=args.dry_run,
                )

            if response.status_code >= 400:
                raise SystemExit(
                    "Unable to inspect storage bucket. "
                    f"HTTP {response.status_code}: "
                    f"{_safe_response_message(response)}"
                )

            current = _json(response)
            patch = _required_patch(current, desired)

            if not patch:
                print(
                    f"Bucket {bucket_name} is already private "
                    "and constrained."
                )
                return 0

            if args.dry_run:
                changed_fields = ", ".join(sorted(patch))
                print(
                    f"DRY RUN: would update private bucket "
                    f"{bucket_name} settings: {changed_fields}."
                )
                return 0

            update_response = client.put(
                f"{base_url}/bucket/{bucket_name}",
                headers=headers,
                json=patch,
            )

            if update_response.status_code >= 400:
                raise SystemExit(
                    "Unable to update storage bucket safely. "
                    f"HTTP {update_response.status_code}: "
                    f"{_safe_response_message(update_response)}"
                )

    except httpx.RequestError as exc:
        raise SystemExit(
            "Unable to connect to Supabase Storage. "
            "Check SUPABASE_URL and your network connection."
        ) from exc

    changed_fields = ", ".join(sorted(patch))
    print(
        f"Updated private bucket {bucket_name} settings: "
        f"{changed_fields}."
    )
    return 0


def _create_bucket(
    *,
    client: httpx.Client,
    base_url: str,
    bucket_name: str,
    headers: dict[str, str],
    desired: dict[str, Any],
    dry_run: bool,
) -> int:
    """Create the configured private bucket."""
    payload = {
        "id": bucket_name,
        "name": bucket_name,
        **desired,
    }

    if dry_run:
        print(f"DRY RUN: would create private bucket {bucket_name}.")
        return 0

    create_response = client.post(
        f"{base_url}/bucket",
        headers=headers,
        json=payload,
    )

    if create_response.status_code >= 400:
        raise SystemExit(
            "Unable to create private storage bucket. "
            f"HTTP {create_response.status_code}: "
            f"{_safe_response_message(create_response)}"
        )

    print(f"Created private bucket {bucket_name}.")
    return 0


def _is_bucket_not_found(response: httpx.Response) -> bool:
    """
    Detect Supabase's bucket-not-found response.

    Depending on the Storage API version, Supabase may return:
    - HTTP 404; or
    - HTTP 400 with JSON containing statusCode=404 or code=NoSuchBucket.
    """
    if response.status_code == 404:
        return True

    if response.status_code != 400:
        return False

    try:
        data = response.json()
    except ValueError:
        return False

    if not isinstance(data, dict):
        return False

    return (
        str(data.get("statusCode")) == "404"
        or data.get("code") == "NoSuchBucket"
        or data.get("error") == "Bucket not found"
    )


def _json(response: httpx.Response) -> dict[str, Any]:
    """Parse a successful JSON object response."""
    try:
        data = response.json()
    except ValueError as exc:
        raise SystemExit(
            "Storage bucket response was not valid JSON."
        ) from exc

    if not isinstance(data, dict):
        raise SystemExit(
            "Storage bucket response was not a JSON object."
        )

    return data


def _safe_response_message(response: httpx.Response) -> str:
    """Return a short error message without exposing request credentials."""
    try:
        data = response.json()
    except ValueError:
        return response.text[:300] or "No response body."

    if not isinstance(data, dict):
        return str(data)[:300]

    message = (
        data.get("message")
        or data.get("error")
        or data.get("code")
        or "Unknown Storage API error."
    )

    return str(message)[:300]


def _required_patch(
    current: dict[str, Any],
    desired: dict[str, Any],
) -> dict[str, Any]:
    """Return only bucket settings that differ from the desired state."""
    patch: dict[str, Any] = {}

    if current.get("public") is not False:
        patch["public"] = False

    if current.get("file_size_limit") != desired["file_size_limit"]:
        patch["file_size_limit"] = desired["file_size_limit"]

    current_mime_types = current.get("allowed_mime_types") or []

    if sorted(current_mime_types) != desired["allowed_mime_types"]:
        patch["allowed_mime_types"] = desired["allowed_mime_types"]

    return patch


if __name__ == "__main__":
    raise SystemExit(main())
