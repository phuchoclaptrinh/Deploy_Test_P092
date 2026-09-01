"""Read-only product catalogs."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from src.database.models.category import CategoryCatalog
from src.database.models.floor import Floor
from src.database.models.location import Location
from src.database.models.location_type import LocationType
from src.models.api.errors import CATEGORY_REQUIRED, DomainError


class CatalogRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_locations(self) -> list[Location]:
        query = (
            select(Location)
            .join(Location.floor)
            .join(Location.location_type)
            .where(Location.is_active.is_(True))
        )
        return list(
            self.db.scalars(
                query
                .options(
                    joinedload(Location.floor),
                    joinedload(Location.location_type),
                    joinedload(Location.unit),
                )
                .order_by(Floor.adjacency_index, LocationType.display_name, Location.label)
            )
        )

    def get_location(self, location_id: UUID) -> Location | None:
        return self.db.scalar(
            select(Location)
            .where(Location.id == location_id, Location.is_active.is_(True))
            .options(joinedload(Location.floor), joinedload(Location.location_type), joinedload(Location.unit))
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

    def create_category(self, code: str, display_name: str) -> CategoryCatalog:
        """A code and a name. There is nothing else to configure.

        `base_score` and `priority_ceiling` used to be required here, and the
        "an active Category requires a base_score" rule guarded a real hazard:
        an unscored category silently made a ticket unscoreable. Under the v2
        rubric a category is not an input to a score at all, so both the fields
        and the rule that protected them are gone.
        """
        normalized = self.normalize_category_code(code)
        if self.get_category_by_code_any_status(normalized) is not None:
            raise DomainError(CATEGORY_REQUIRED, "Category code already exists.", 409)
        row = CategoryCatalog(
            code=normalized,
            display_name=display_name.strip(),
            is_active=True,
        )
        self.db.add(row)
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise DomainError(CATEGORY_REQUIRED, "Category code already exists.", 409) from exc
        return row
