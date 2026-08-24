"""Profile/binding operations for Self Dev v3 authentication flows."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor
from src.models.api.auth import CurrentUserResponse, UnitSummary
from src.models.api.errors import ACCOUNT_ALREADY_BOUND, FORBIDDEN, UNIT_NOT_FOUND, DomainError
from src.models.enums import UserRole
from src.repositories.unit_repository import UnitRepository


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.units = UnitRepository(db)

    def current_user_response(self, actor: CurrentActor) -> CurrentUserResponse:
        unit_summary = None
        resident_profile = actor.resident_profile
        if resident_profile is not None:
            unit = resident_profile.unit
            # Make sure catalog relationships are available even if the profile
            # was materialized before the binding transaction committed.
            if not getattr(unit, "building", None) or not getattr(unit, "floor", None):
                unit = self.units.get_resident_unit(actor.user.user_id) or unit
            unit_summary = UnitSummary(
                id=unit.id,
                unit_code=unit.unit_code,
                building_code=unit.building.code,
                floor_code=unit.floor.floor_code,
                is_primary=resident_profile.is_primary,
            )
        return CurrentUserResponse(
            user_id=actor.user.user_id,
            role=actor.user.role,
            actor_type=actor.actor_type,
            full_name=actor.user.full_name,
            phone_e164=actor.user.phone_e164,
            is_active=actor.user.is_active,
            unit=unit_summary,
        )

    def bind_unit(self, actor: CurrentActor, unit_code: str) -> CurrentUserResponse:
        if actor.user.role != UserRole.RESIDENT:
            raise DomainError(FORBIDDEN, "Chỉ Cư dân được liên kết căn hộ.", 403)
        existing = self.units.get_resident_profile(actor.user.user_id)
        if existing is not None:
            raise DomainError(ACCOUNT_ALREADY_BOUND, "Tài khoản đã liên kết với một căn hộ.", 409)
        unit = self.units.get_by_code(unit_code)
        if unit is None:
            raise DomainError(UNIT_NOT_FOUND, "Không tìm thấy mã căn hộ.", 404)
        is_primary = not self.units.unit_has_primary_resident(unit.id)
        try:
            profile = self.units.bind_resident(actor.user.user_id, unit, is_primary=is_primary)
            self.db.commit()
            self.db.refresh(profile)
        except IntegrityError as exc:
            self.db.rollback()
            if self.units.get_resident_profile(actor.user.user_id) is not None:
                raise DomainError(ACCOUNT_ALREADY_BOUND, "Tài khoản đã liên kết với một căn hộ.", 409)
            raise DomainError(ACCOUNT_ALREADY_BOUND, "Tài khoản đã liên kết với một căn hộ.", 409) from exc
        except Exception:
            self.db.rollback()
            raise
        refreshed_actor = CurrentActor("resident", actor.user, actor.principal, self.units.get_resident_profile(actor.user.user_id))
        return self.current_user_response(refreshed_actor)
