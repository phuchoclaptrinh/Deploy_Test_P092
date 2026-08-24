"""Supabase authentication and three-actor authorization resolution.

Supabase Auth proves identity; ``user_profiles`` plus the actor-specific profile
tables determine application authorization. A verified phone identity may be
auto-provisioned as a Resident profile shell, but Coordinator and Technician
access are always provisioned by trusted backend processes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.dependencies.database import get_db
from src.config import get_settings
from src.database.models.resident_profile import ResidentProfile
from src.database.models.technician import TechnicianProfile
from src.database.models.user_profile import UserProfile
from src.models.api.errors import (
    AUTH_PROFILE_INVALID,
    AUTH_REQUIRED,
    AUTH_TOKEN_INVALID,
    FORBIDDEN,
    USER_INACTIVE,
    DomainError,
)
from src.models.enums import UserRole
from src.repositories.profile_repository import ProfileRepository
from src.security.supabase_jwt import AuthenticatedPrincipal, SupabaseJWTVerifier
from src.services.phone import normalize_e164_phone

supabase_bearer = HTTPBearer(
    scheme_name="SupabaseBearerAuth",
    bearerFormat="JWT",
    description="Supabase access token. Send `Authorization: Bearer <access_token>`.",
    auto_error=False,
)


@dataclass(frozen=True)
class CurrentActor:
    actor_type: Literal["resident", "coordinator", "technician"]
    user: UserProfile
    principal: AuthenticatedPrincipal
    resident_profile: ResidentProfile | None = None
    technician_profile: TechnicianProfile | None = None


@lru_cache
def get_supabase_jwt_verifier() -> SupabaseJWTVerifier:
    return SupabaseJWTVerifier(get_settings())


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(supabase_bearer),
    authorization: str | None = Header(default=None, include_in_schema=False),
    verifier: SupabaseJWTVerifier = Depends(get_supabase_jwt_verifier),
) -> AuthenticatedPrincipal:
    if credentials is None:
        if authorization:
            raise DomainError(AUTH_TOKEN_INVALID, "Authorization header không hợp lệ.", 401)
        raise DomainError(AUTH_REQUIRED, "Yêu cầu đăng nhập.", 401)
    if credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise DomainError(AUTH_TOKEN_INVALID, "Authorization header không hợp lệ.", 401)
    return verifier.verify(credentials.credentials)


def get_current_actor(
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> CurrentActor:
    repo = ProfileRepository(db)
    user = repo.get_user_profile(principal.auth_user_id)
    phone = normalize_e164_phone(principal.phone)

    if user is None:
        # Self Dev allows first-time Resident creation from a verified phone OTP
        # identity. Email-only identities can never self-elevate to Coordinator.
        if not phone:
            raise DomainError(AUTH_PROFILE_INVALID, "Tài khoản chưa được cấp quyền sử dụng hệ thống.", 403)
        try:
            owner = repo.get_phone_owner(phone)
            if owner is not None and owner.user_id != principal.auth_user_id:
                raise DomainError(AUTH_PROFILE_INVALID, "Số điện thoại đã thuộc một tài khoản khác.", 401)
            user = repo.create_resident_user(principal.auth_user_id, phone)
            db.commit()
            db.refresh(user)
        except DomainError:
            db.rollback()
            raise
        except IntegrityError as exc:
            db.rollback()
            raise DomainError(AUTH_PROFILE_INVALID, "Không thể tạo hồ sơ cư dân.", 401) from exc

    if not user.is_active:
        raise DomainError(USER_INACTIVE, "Tài khoản đã bị khóa.", 403)

    if user.role == UserRole.RESIDENT:
        if user.phone_e164 and phone and user.phone_e164 != phone:
            raise DomainError(AUTH_PROFILE_INVALID, "Số điện thoại xác thực không khớp hồ sơ cư dân.", 401)
        if not phone and not principal.email:
            raise DomainError(AUTH_PROFILE_INVALID, "Tài khoản cư dân phải đăng nhập bằng email hoặc số điện thoại.", 401)
        resident_profile = repo.get_resident_profile(user.user_id)
        return CurrentActor("resident", user, principal, resident_profile, None)

    if user.role == UserRole.COORDINATOR:
        if not principal.email:
            raise DomainError(AUTH_PROFILE_INVALID, "Tài khoản Điều phối viên phải đăng nhập bằng email.", 401)
        return CurrentActor("coordinator", user, principal, None, None)

    if user.role == UserRole.TECHNICIAN:
        technician_profile = repo.get_technician_profile(user.user_id)
        if technician_profile is None or not technician_profile.is_active:
            raise DomainError(AUTH_PROFILE_INVALID, "Tài khoản Kỹ thuật viên chưa được cấp quyền hợp lệ.", 403)
        return CurrentActor("technician", user, principal, None, technician_profile)

    raise DomainError(AUTH_PROFILE_INVALID, "Vai trò tài khoản không hợp lệ.", 403)


def require_resident(actor: CurrentActor = Depends(get_current_actor)) -> CurrentActor:
    if actor.actor_type != "resident":
        raise DomainError(FORBIDDEN, "Chỉ Cư dân được thực hiện thao tác này.", 403)
    return actor


def require_coordinator(actor: CurrentActor = Depends(get_current_actor)) -> CurrentActor:
    if actor.actor_type != "coordinator":
        raise DomainError(FORBIDDEN, "Chỉ Điều phối viên BQL được thực hiện thao tác này.", 403)
    return actor


def require_technician(actor: CurrentActor = Depends(get_current_actor)) -> CurrentActor:
    if actor.actor_type != "technician":
        raise DomainError(FORBIDDEN, "Chỉ Kỹ thuật viên được thực hiện thao tác này.", 403)
    return actor
