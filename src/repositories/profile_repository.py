"""Application profile persistence for trusted human actor roles."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from src.database.models.resident_profile import ResidentProfile
from src.database.models.technician import TechnicianProfile
from src.database.models.user_profile import UserProfile
from src.models.enums import UserRole


class ProfileRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_user_profile(self, auth_user_id: UUID) -> UserProfile | None:
        return self.db.scalar(
            select(UserProfile)
            .where(UserProfile.user_id == auth_user_id)
            .options(
                joinedload(UserProfile.resident_profile).joinedload(ResidentProfile.unit),
                joinedload(UserProfile.technician_profile),
            )
        )

    def get_resident_profile(self, auth_user_id: UUID) -> ResidentProfile | None:
        return self.db.scalar(
            select(ResidentProfile)
            .where(ResidentProfile.user_id == auth_user_id)
            .options(joinedload(ResidentProfile.unit))
        )

    def get_technician_profile(self, auth_user_id: UUID) -> TechnicianProfile | None:
        return self.db.scalar(select(TechnicianProfile).where(TechnicianProfile.user_id == auth_user_id))

    def create_resident_user(self, auth_user_id: UUID, phone_e164: str) -> UserProfile:
        profile = UserProfile(
            user_id=auth_user_id,
            phone_e164=phone_e164,
            role=UserRole.RESIDENT,
            is_active=True,
        )
        self.db.add(profile)
        self.db.flush()
        return profile

    def get_phone_owner(self, phone_e164: str) -> UserProfile | None:
        return self.db.scalar(select(UserProfile).where(UserProfile.phone_e164 == phone_e164))
