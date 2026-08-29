"""Unit catalog and resident binding queries."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from src.database.models.resident_profile import ResidentProfile
from src.database.models.unit import Unit


class UnitRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_code(self, unit_code: str) -> Unit | None:
        return self.db.scalar(
            select(Unit)
            .where(func.lower(Unit.unit_code) == unit_code.strip().lower(), Unit.status == "ACTIVE")
            .options(joinedload(Unit.floor))
        )

    def get_resident_unit(self, user_id: UUID) -> Unit | None:
        return self.db.scalar(
            select(Unit)
            .join(ResidentProfile, ResidentProfile.unit_id == Unit.id)
            .where(ResidentProfile.user_id == user_id, Unit.status == "ACTIVE")
            .options(joinedload(Unit.floor))
        )

    def get_resident_profile(self, user_id: UUID) -> ResidentProfile | None:
        return self.db.scalar(
            select(ResidentProfile)
            .where(ResidentProfile.user_id == user_id)
            .options(joinedload(ResidentProfile.unit).joinedload(Unit.floor))
        )

    def unit_has_resident(self, unit_id: UUID) -> bool:
        count = self.db.scalar(select(func.count()).select_from(ResidentProfile).where(ResidentProfile.unit_id == unit_id)) or 0
        return bool(count)

    def unit_has_primary_resident(self, unit_id: UUID) -> bool:
        count = self.db.scalar(
            select(func.count())
            .select_from(ResidentProfile)
            .where(ResidentProfile.unit_id == unit_id, ResidentProfile.is_primary.is_(True))
        ) or 0
        return bool(count)

    def bind_resident(self, user_id: UUID, unit: Unit, *, is_primary: bool) -> ResidentProfile:
        profile = ResidentProfile(user_id=user_id, unit_id=unit.id, is_primary=is_primary)
        self.db.add(profile)
        self.db.flush()
        return profile
