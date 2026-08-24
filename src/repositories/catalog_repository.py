"""Read-only product catalogs."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from src.database.models.building import Building
from src.database.models.category import CategoryCatalog
from src.database.models.floor import Floor
from src.database.models.location import Location
from src.database.models.location_type import LocationType
from src.models.api.errors import CATEGORY_REQUIRED, DomainError
from src.models.enums import Priority


class CatalogRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_locations(
        self, *, building_id: UUID | None = None, resident_unit_id: UUID | None = None
    ) -> list[Location]:
        query = (
            select(Location)
            .join(Location.building)
            .join(Location.floor)
            .join(Location.location_type)
            .where(Location.is_active.is_(True))
        )
        if building_id is not None:
            query = query.where(Location.building_id == building_id)
        if resident_unit_id is not None:
            # Resident dropdown may contain common-area locations plus locations
            # scoped to the authenticated unit, never another household's unit.
            query = query.where(or_(Location.unit_id.is_(None), Location.unit_id == resident_unit_id))
        return list(
            self.db.scalars(
                query
                .options(
                    joinedload(Location.building),
                    joinedload(Location.floor),
                    joinedload(Location.location_type),
                    joinedload(Location.unit),
                )
                .order_by(Building.code, Floor.adjacency_index, LocationType.display_name, Location.label)
            )
        )

    def get_location(self, location_id: UUID) -> Location | None:
        return self.db.scalar(
            select(Location)
            .where(Location.id == location_id, Location.is_active.is_(True))
            .options(joinedload(Location.building), joinedload(Location.floor), joinedload(Location.location_type), joinedload(Location.unit))
        )

    def list_categories(self) -> list[CategoryCatalog]:
        return list(self.db.scalars(select(CategoryCatalog).where(CategoryCatalog.is_active.is_(True)).order_by(CategoryCatalog.display_name)))

    def get_category(self, category_id: UUID) -> CategoryCatalog | None:
        return self.db.scalar(select(CategoryCatalog).where(CategoryCatalog.id == category_id, CategoryCatalog.is_active.is_(True)))

    def list_all_categories(self) -> list[CategoryCatalog]:
        return list(self.db.scalars(select(CategoryCatalog).order_by(CategoryCatalog.display_name)))

    def get_category_any_status(self, category_id: UUID) -> CategoryCatalog | None:
        return self.db.scalar(select(CategoryCatalog).where(CategoryCatalog.id == category_id))

    @staticmethod
    def normalize_category_code(code: str) -> str:
        return code.strip().upper()

    def get_category_by_code_any_status(self, code: str) -> CategoryCatalog | None:
        return self.db.scalar(
            select(CategoryCatalog).where(CategoryCatalog.code == self.normalize_category_code(code))
        )

    def create_category(
        self,
        code: str,
        display_name: str,
        base_score: int | None,
        priority_ceiling: Priority | None,
    ) -> CategoryCatalog:
        normalized = self.normalize_category_code(code)
        if self.get_category_by_code_any_status(normalized) is not None:
            raise DomainError(CATEGORY_REQUIRED, "Category code already exists.", 409)
        if base_score is None:
            raise DomainError(CATEGORY_REQUIRED, "Active Category requires base_score.", 409)
        row = CategoryCatalog(
            code=normalized,
            display_name=display_name.strip(),
            base_score=base_score,
            priority_ceiling=priority_ceiling,
            is_active=True,
        )
        self.db.add(row)
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise DomainError(CATEGORY_REQUIRED, "Category code already exists.", 409) from exc
        return row
