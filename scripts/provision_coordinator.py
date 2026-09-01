"""Trusted provisioning for Self Dev v2 Coordinator accounts.

Supabase Auth stores the email/password identity. This script only creates or
synchronizes the application authorization record in ``user_profiles`` with
role=COORDINATOR. No password is persisted in PostgreSQL.
"""

from __future__ import annotations

import argparse
import getpass
import os
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import create_engine, text

from src.config import Settings, get_settings
from src.security.supabase_admin import build_supabase_admin_headers


@dataclass(frozen=True)
class SupabaseAuthUser:
    id: UUID
    email: str


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Provision/sync/deactivate FixIt Coordinator profiles.")
    p.add_argument("action", choices=["provision", "sync", "deactivate"])
    p.add_argument("--email")
    p.add_argument("--auth-user-id")
    p.add_argument("--full-name")
    p.add_argument("--dry-run", action="store_true")
    return p


def main() -> int:
    args = parser().parse_args()
    if args.action == "deactivate":
        if not args.auth_user_id:
            raise SystemExit("--auth-user-id is required for deactivate.")
        return deactivate_profile(UUID(args.auth_user_id), args.dry_run)

    email = _normalize_email(args.email)
    if args.action == "provision":
        if args.dry_run:
            print(f"DRY RUN: would create Supabase Auth email user {email} and COORDINATOR profile.")
            return 0
        auth_user = create_auth_user(email)
    else:
        if not args.auth_user_id:
            raise SystemExit("--auth-user-id is required for sync.")
        if args.dry_run:
            print("DRY RUN: would validate Auth identity and upsert COORDINATOR user_profile.")
            return 0
        auth_user = fetch_auth_user(UUID(args.auth_user_id))
        if auth_user.email != email:
            raise SystemExit("Supabase Auth email does not match requested Coordinator email.")

    upsert_coordinator(auth_user.id, args.full_name)
    print(f"Provisioned Coordinator profile {auth_user.id}.")
    return 0


def create_auth_user(email: str) -> SupabaseAuthUser:
    settings = _require_admin_settings()
    password = os.getenv("SUPABASE_USER_PASSWORD") or getpass.getpass("Temporary password: ")
    if not password:
        raise SystemExit("A non-empty temporary password is required.")
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users",
                headers=build_supabase_admin_headers(settings.supabase_secret_key),
                json={"email": email, "password": password, "email_confirm": True},
            )
    except httpx.RequestError as exc:
        raise SystemExit("Unable to connect to Supabase Auth.") from exc
    if response.status_code >= 400:
        raise SystemExit(f"Unable to create Supabase Auth user. HTTP {response.status_code}.")
    return _validated_auth_user_payload(response.json(), expected_email=email)


def fetch_auth_user(auth_user_id: UUID) -> SupabaseAuthUser:
    settings = _require_admin_settings()
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{auth_user_id}",
                headers=build_supabase_admin_headers(settings.supabase_secret_key),
            )
    except httpx.RequestError as exc:
        raise SystemExit("Unable to connect to Supabase Auth.") from exc
    if response.status_code == 404:
        raise SystemExit("Supabase Auth user was not found.")
    if response.status_code >= 400:
        raise SystemExit(f"Unable to inspect Supabase Auth user. HTTP {response.status_code}.")
    user = _validated_auth_user_payload(response.json())
    if user.id != auth_user_id:
        raise SystemExit("Supabase Auth response returned an unexpected user ID.")
    return user


def upsert_coordinator(auth_user_id: UUID, full_name: str | None) -> None:
    engine = create_engine(_database_url())
    with engine.begin() as connection:
        existing = connection.execute(
            text("SELECT role FROM user_profiles WHERE user_id = :id"), {"id": auth_user_id}
        ).scalar_one_or_none()
        if existing is not None and str(existing) not in {"COORDINATOR", "UserRole.COORDINATOR"}:
            raise SystemExit("Refusing to replace an existing non-Coordinator application profile.")
        connection.execute(
            text(
                """
                INSERT INTO user_profiles (user_id, phone_e164, full_name, role, is_active)
                VALUES (:id, NULL, :full_name, 'COORDINATOR', true)
                ON CONFLICT (user_id) DO UPDATE
                SET full_name = COALESCE(EXCLUDED.full_name, user_profiles.full_name),
                    role = 'COORDINATOR',
                    is_active = true,
                    updated_at = now()
                """
            ),
            {"id": auth_user_id, "full_name": full_name},
        )


def deactivate_profile(auth_user_id: UUID, dry_run: bool) -> int:
    if dry_run:
        print(f"DRY RUN: would deactivate Coordinator {auth_user_id}.")
        return 0
    confirmation = input(f"Type {auth_user_id} to deactivate this Coordinator: ")
    if confirmation != str(auth_user_id):
        raise SystemExit("Confirmation did not match; aborting.")
    engine = create_engine(_database_url())
    with engine.begin() as connection:
        result = connection.execute(
            text(
                "UPDATE user_profiles SET is_active=false, updated_at=now() "
                "WHERE user_id=:id AND role='COORDINATOR'"
            ),
            {"id": auth_user_id},
        )
        if result.rowcount == 0:
            raise SystemExit("Coordinator profile was not found.")
    print(f"Deactivated Coordinator {auth_user_id}.")
    return 0


def _validated_auth_user_payload(payload: Any, *, expected_email: str | None = None) -> SupabaseAuthUser:
    if not isinstance(payload, dict) or not payload.get("id") or not payload.get("email"):
        raise SystemExit("Supabase Auth response is missing id/email.")
    try:
        user_id = UUID(str(payload["id"]))
    except ValueError as exc:
        raise SystemExit("Supabase Auth returned an invalid user ID.") from exc
    email = _normalize_email(str(payload["email"]))
    if expected_email is not None and email != _normalize_email(expected_email):
        raise SystemExit("Supabase Auth response email does not match request.")
    return SupabaseAuthUser(user_id, email)


def _normalize_email(value: str | None) -> str:
    if value is None:
        raise SystemExit("--email is required.")
    email = value.strip().casefold()
    if not email or "@" not in email or email.startswith("@") or email.endswith("@"):
        raise SystemExit("A valid --email value is required.")
    return email


def _require_admin_settings() -> Settings:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SECRET_KEY are required.")
    return settings


def _database_url() -> str:
    url = get_settings().require_database_url()
    return url.replace("postgresql://", "postgresql+psycopg://", 1) if url.startswith("postgresql://") else url


if __name__ == "__main__":
    raise SystemExit(main())
