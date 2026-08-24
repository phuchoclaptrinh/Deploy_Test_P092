"""Coordinator-managed account provisioning."""

from __future__ import annotations

import re
import secrets
import string
import unicodedata
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from src.database.models.building import Building
from src.database.models.category import CategoryCatalog
from src.database.models.resident_profile import ResidentProfile
from src.database.models.technician import TechnicianProfile, TechnicianSkill
from src.database.models.ticket_assignment import TicketAssignment
from src.database.models.unit import Unit
from src.database.models.user_profile import UserProfile
from src.models.api.coordinator import (
    CoordinatorResidentSummaryResponse,
    ManagerAccountResponse,
    ManagerCreateResidentRequest,
    ManagerCreateTechnicianRequest,
)
from src.models.api.errors import UNIT_NOT_FOUND, VALIDATION_ERROR, DomainError
from src.models.enums import UserRole
from src.services.phone import normalize_e164_phone
from src.services.supabase_admin_auth_service import SupabaseAdminAuthService
from src.services.technician_report_service import record_availability

ACCOUNT_EMAIL_DOMAIN = "fixit.vn"
MAX_EMAIL_ATTEMPTS = 20
TEMP_PASSWORD_LENGTH = 6
TEMP_PASSWORD_ALPHABET = string.ascii_letters + string.digits


class AccountAuthProvider(Protocol):
    def create_resident_user(self, *, phone: str, full_name: str | None = None) -> UUID: ...

    def create_email_user(self, *, email: str, password: str, full_name: str | None = None) -> UUID: ...

    def update_user(self, user_id: UUID, payload: dict) -> None: ...

    def delete_user(self, user_id: UUID) -> None: ...


class ManagerAccountService:
    def __init__(self, db: Session, auth_provider: AccountAuthProvider | None = None) -> None:
        self.db = db
        self.auth = auth_provider or SupabaseAdminAuthService()

    def list_residents(self) -> list[CoordinatorResidentSummaryResponse]:
        rows = self.db.scalars(
            select(UserProfile)
            .where(UserProfile.role == UserRole.RESIDENT)
            .options(
                joinedload(UserProfile.resident_profile)
                .joinedload(ResidentProfile.unit)
                .joinedload(Unit.building),
                joinedload(UserProfile.resident_profile)
                .joinedload(ResidentProfile.unit)
                .joinedload(Unit.floor),
            )
            .order_by(UserProfile.created_at.desc())
        ).all()
        data: list[CoordinatorResidentSummaryResponse] = []
        for user in rows:
            profile = user.resident_profile
            unit = profile.unit if profile else None
            data.append(
                CoordinatorResidentSummaryResponse(
                    user_id=user.user_id,
                    full_name=user.full_name,
                    phone_e164=user.phone_e164,
                    is_active=user.is_active,
                    unit_id=unit.id if unit else None,
                    unit_code=unit.unit_code if unit else None,
                    building_code=unit.building.code if unit and unit.building else None,
                    floor_code=unit.floor.floor_code if unit and unit.floor else None,
                    is_primary=profile.is_primary if profile else None,
                )
            )
        return data

    def create_resident(self, payload: ManagerCreateResidentRequest) -> ManagerAccountResponse:
        email = self._account_email_base(payload.email, payload.full_name)
        password = payload.password or self._generate_temporary_password()
        phone = self._normalize_phone(payload.phone)
        if phone is not None:
            self._ensure_phone_available(phone)
        unit = self._find_unit(payload)
        is_primary = bool(payload.is_primary and not self._unit_has_primary_resident(unit.id))

        auth_user_id: UUID | None = None
        try:
            auth_user_id, email = self._create_unique_email_user(
                email_base=email,
                password=password,
                full_name=payload.full_name,
            )
            user = UserProfile(
                user_id=auth_user_id,
                phone_e164=phone,
                full_name=payload.full_name,
                role=UserRole.RESIDENT,
                is_active=True,
            )
            self.db.add(user)
            self.db.add(ResidentProfile(user_id=auth_user_id, unit_id=unit.id, is_primary=is_primary))
            self.db.commit()
            return ManagerAccountResponse(
                user_id=auth_user_id,
                role=UserRole.RESIDENT,
                full_name=payload.full_name,
                phone_e164=phone,
                email=email,
                temporary_password=password,
                unit_id=unit.id,
                unit_code=unit.unit_code,
                is_active=True,
            )
        except IntegrityError as exc:
            self.db.rollback()
            self._cleanup_auth_user(auth_user_id)
            raise DomainError(VALIDATION_ERROR, "Không thể tạo tài khoản cư dân do dữ liệu bị trùng.", 409) from exc
        except DomainError:
            self.db.rollback()
            raise
        except SQLAlchemyError as exc:
            self.db.rollback()
            self._cleanup_auth_user(auth_user_id)
            raise DomainError(VALIDATION_ERROR, "Không thể tạo tài khoản cư dân.", 400) from exc

    def create_technician(self, payload: ManagerCreateTechnicianRequest) -> ManagerAccountResponse:
        email = self._account_email_base(payload.email, payload.full_name)
        password = payload.password or self._generate_temporary_password()
        skill_category_ids = self._validate_skill_categories(payload.skill_category_ids)
        phone = self._normalize_phone(payload.phone_number)
        if phone is not None:
            self._ensure_phone_available(phone)

        auth_user_id: UUID | None = None
        try:
            auth_user_id, email = self._create_unique_email_user(
                email_base=email,
                password=password,
                full_name=payload.full_name,
            )
            user = UserProfile(
                user_id=auth_user_id,
                phone_e164=phone,
                full_name=payload.full_name,
                role=UserRole.TECHNICIAN,
                is_active=True,
            )
            technician = TechnicianProfile(
                user_id=auth_user_id,
                # The profile column keeps what the Coordinator typed; the
                # identity phone above is the normalized E.164 one.
                phone_number=payload.phone_number,
                is_active=True,
                is_available=payload.is_available,
            )
            self.db.add_all([user, technician])
            self.db.add_all(
                [TechnicianSkill(technician_id=auth_user_id, category_id=category_id) for category_id in skill_category_ids]
            )
            # §2.13 counts active days from readiness history, so the opening
            # state has to be written down when the account is created.
            record_availability(
                self.db,
                auth_user_id,
                is_available=payload.is_available,
                source="ACCOUNT_CREATED",
            )
            self.db.commit()
            return ManagerAccountResponse(
                user_id=auth_user_id,
                role=UserRole.TECHNICIAN,
                full_name=payload.full_name,
                phone_e164=phone,
                email=email,
                temporary_password=password,
                is_active=True,
                is_available=payload.is_available,
                skill_category_ids=skill_category_ids,
            )
        except IntegrityError as exc:
            self.db.rollback()
            self._cleanup_auth_user(auth_user_id)
            raise DomainError(VALIDATION_ERROR, "Không thể tạo tài khoản KTV do dữ liệu bị trùng.", 409) from exc
        except DomainError:
            self.db.rollback()
            raise
        except SQLAlchemyError as exc:
            self.db.rollback()
            self._cleanup_auth_user(auth_user_id)
            raise DomainError(VALIDATION_ERROR, "Không thể tạo tài khoản KTV.", 400) from exc

    def reset_resident_password(self, user_id: UUID) -> ManagerAccountResponse:
        user = self._get_user(user_id, UserRole.RESIDENT)
        password = self._generate_temporary_password()
        self.auth.update_user(user_id, {"password": password})
        return self._resident_response(user, temporary_password=password)

    def reset_technician_password(self, user_id: UUID) -> ManagerAccountResponse:
        user = self._get_user(user_id, UserRole.TECHNICIAN)
        password = self._generate_temporary_password()
        self.auth.update_user(user_id, {"password": password})
        return self._technician_response(user, temporary_password=password)

    def delete_technician(self, user_id: UUID) -> ManagerAccountResponse:
        """Remove an unused technician account without erasing assignment history.

        A technician referenced by an assignment is intentionally retained: the
        historical assignment is an audit record and its foreign key is
        restrictive. The coordinator receives a clear conflict rather than a
        partial delete.
        """
        user = self._get_user(user_id, UserRole.TECHNICIAN)
        assigned = self.db.scalar(
            select(TicketAssignment.id).where(TicketAssignment.technician_id == user_id).limit(1)
        )
        if assigned is not None:
            raise DomainError(
                VALIDATION_ERROR,
                "Không thể xóa kỹ thuật viên đã có lịch sử phân công. Hãy tạm khóa tài khoản để giữ lịch sử.",
                409,
            )
        response = self._technician_response(user)
        self.db.delete(user)
        self.db.commit()
        self.auth.delete_user(user_id)
        return response

    def set_resident_active(self, user_id: UUID, is_active: bool) -> ManagerAccountResponse:
        user = self._get_user(user_id, UserRole.RESIDENT)
        user.is_active = is_active
        self.db.commit()
        self.db.refresh(user)
        return self._resident_response(user)

    def _get_user(self, user_id: UUID, role: UserRole) -> UserProfile:
        user = self.db.scalar(
            select(UserProfile)
            .where(UserProfile.user_id == user_id, UserProfile.role == role)
            .options(
                joinedload(UserProfile.resident_profile)
                .joinedload(ResidentProfile.unit)
                .joinedload(Unit.building),
                joinedload(UserProfile.resident_profile)
                .joinedload(ResidentProfile.unit)
                .joinedload(Unit.floor),
                joinedload(UserProfile.technician_profile).joinedload(TechnicianProfile.skills),
            )
        )
        if user is None:
            raise DomainError(VALIDATION_ERROR, "Không tìm thấy tài khoản.", 404)
        return user

    def _resident_response(self, user: UserProfile, *, temporary_password: str | None = None) -> ManagerAccountResponse:
        profile = user.resident_profile
        unit = profile.unit if profile else None
        return ManagerAccountResponse(
            user_id=user.user_id,
            role=UserRole.RESIDENT,
            full_name=user.full_name,
            phone_e164=user.phone_e164,
            temporary_password=temporary_password,
            unit_id=unit.id if unit else None,
            unit_code=unit.unit_code if unit else None,
            is_active=user.is_active,
        )

    def _technician_response(self, user: UserProfile, *, temporary_password: str | None = None) -> ManagerAccountResponse:
        technician = user.technician_profile
        return ManagerAccountResponse(
            user_id=user.user_id,
            role=UserRole.TECHNICIAN,
            full_name=user.full_name,
            phone_e164=user.phone_e164,
            temporary_password=temporary_password,
            is_active=user.is_active and bool(technician and technician.is_active),
            is_available=technician.is_available if technician else None,
            skill_category_ids=[skill.category_id for skill in technician.skills] if technician else [],
        )

    def _normalize_phone(self, raw_phone: str | None) -> str | None:
        if raw_phone is None:
            return None
        try:
            phone = normalize_e164_phone(raw_phone)
        except DomainError as exc:
            raise DomainError(VALIDATION_ERROR, "Số điện thoại phải ở định dạng E.164.", 422) from exc
        return phone

    def _ensure_phone_available(self, phone: str) -> None:
        existing = self.db.scalar(select(UserProfile.user_id).where(UserProfile.phone_e164 == phone))
        if existing is not None:
            raise DomainError(VALIDATION_ERROR, "Số điện thoại đã thuộc một tài khoản khác.", 409)

    def _generate_temporary_password(self) -> str:
        return "".join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(TEMP_PASSWORD_LENGTH))

    def _account_email_base(self, requested_email: str | None, full_name: str | None) -> str:
        if requested_email:
            return requested_email.strip().lower()
        local_part = self._email_local_part_from_name(full_name)
        if not local_part:
            raise DomainError(VALIDATION_ERROR, "Họ tên là bắt buộc để tự sinh email đăng nhập.", 422)
        return f"{local_part}@{ACCOUNT_EMAIL_DOMAIN}"

    def _create_unique_email_user(self, *, email_base: str, password: str, full_name: str | None) -> tuple[UUID, str]:
        last_error: DomainError | None = None
        for email in self._email_candidates(email_base):
            try:
                return self.auth.create_email_user(email=email, password=password, full_name=full_name), email
            except DomainError as exc:
                if exc.status_code != 409:
                    raise
                last_error = exc
        raise DomainError(
            VALIDATION_ERROR,
            "Không thể tự sinh email chưa trùng, vui lòng đổi họ tên hoặc nhập email khác.",
            409,
        ) from last_error

    def _email_candidates(self, email_base: str):
        local_part, _, domain = email_base.partition("@")
        domain = domain or ACCOUNT_EMAIL_DOMAIN
        local_part = re.sub(r"[^a-z0-9._-]+", ".", local_part.lower()).strip(".") or "user"
        yield f"{local_part}@{domain}"
        for suffix in range(2, MAX_EMAIL_ATTEMPTS + 1):
            yield f"{local_part}{suffix}@{domain}"

    def _email_local_part_from_name(self, full_name: str | None) -> str:
        if not full_name:
            return ""
        normalized = unicodedata.normalize("NFD", full_name.strip())
        ascii_name = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        ascii_name = ascii_name.replace("đ", "d").replace("Đ", "d").lower()
        parts = re.sub(r"[^a-z0-9]+", " ", ascii_name).split()
        if len(parts) >= 2:
            return f"{parts[-1]}.{parts[0]}"
        return parts[0] if parts else ""

    def _find_unit(self, payload: ManagerCreateResidentRequest) -> Unit:
        if payload.unit_id is not None:
            unit = self.db.get(Unit, payload.unit_id)
            if unit is None or not unit.is_active:
                raise DomainError(UNIT_NOT_FOUND, "Không tìm thấy căn hộ đang hoạt động.", 404)
            return unit

        query = select(Unit).where(Unit.unit_code == payload.unit_code, Unit.status == "ACTIVE")
        if payload.building_code:
            query = query.join(Unit.building).where(Building.code == payload.building_code)
        units = list(self.db.scalars(query).all())
        if not units:
            raise DomainError(UNIT_NOT_FOUND, "Không tìm thấy căn hộ đang hoạt động.", 404)
        if len(units) > 1:
            raise DomainError(VALIDATION_ERROR, "Mã căn hộ bị trùng giữa nhiều tòa, vui lòng truyền unit_id.", 422)
        return units[0]

    def _unit_has_primary_resident(self, unit_id: UUID) -> bool:
        existing = self.db.scalar(
            select(ResidentProfile.user_id).where(
                ResidentProfile.unit_id == unit_id,
                ResidentProfile.is_primary.is_(True),
            )
        )
        return existing is not None

    def _validate_skill_categories(self, category_ids: list[UUID]) -> list[UUID]:
        if not category_ids:
            return []
        rows = self.db.scalars(
            select(CategoryCatalog.id).where(CategoryCatalog.id.in_(category_ids), CategoryCatalog.is_active.is_(True))
        ).all()
        found = set(rows)
        missing = [category_id for category_id in category_ids if category_id not in found]
        if missing:
            raise DomainError(VALIDATION_ERROR, "Danh mục kỹ năng không tồn tại hoặc đã tắt.", 422, {"category_ids": missing})
        return category_ids

    def _cleanup_auth_user(self, user_id: UUID | None) -> None:
        if user_id is None:
            return
        self.auth.delete_user(user_id)
