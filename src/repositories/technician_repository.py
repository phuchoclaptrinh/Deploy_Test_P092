"""Technician roster persistence."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from src.database.models.technician import TechnicianProfile, TechnicianSkill


class TechnicianRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_technicians(self) -> list[TechnicianProfile]:
        return list(
            self.db.scalars(
                select(TechnicianProfile)
                .options(joinedload(TechnicianProfile.user), selectinload(TechnicianProfile.skills))
                .order_by(TechnicianProfile.user_id)
            )
        )

    def get_technician(self, technician_id: UUID, *, lock: bool = False) -> TechnicianProfile | None:
        query = (
            select(TechnicianProfile)
            .where(TechnicianProfile.user_id == technician_id)
            .options(joinedload(TechnicianProfile.user), selectinload(TechnicianProfile.skills))
        )
        if lock:
            query = query.with_for_update(of=TechnicianProfile)
        return self.db.scalar(query)

    def technician_has_skill(self, technician_id: UUID, category_id: UUID) -> bool:
        return (
            self.db.scalar(
                select(TechnicianSkill.id).where(
                    TechnicianSkill.technician_id == technician_id,
                    TechnicianSkill.category_id == category_id,
                )
            )
            is not None
        )
