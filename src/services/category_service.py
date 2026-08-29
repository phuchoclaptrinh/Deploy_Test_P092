"""Coordinator-owned Category catalog mutations."""

from uuid import UUID

from sqlalchemy.orm import Session

from src.database.models.audit_log import AuditLog
from src.database.models.category import CategoryCatalog
from src.models.api.coordinator import CategoryCreateRequest, CategoryUpdateRequest
from src.models.api.errors import CATEGORY_REQUIRED, DomainError
from src.repositories.catalog_repository import CatalogRepository
from src.request_context import request_id_context


class CategoryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.catalog = CatalogRepository(db)

    def create(self, actor_user_id: UUID, body: CategoryCreateRequest) -> CategoryCatalog:
        try:
            row = self.catalog.create_category(
                body.code,
                body.display_name,
                body.base_score,
                body.priority_ceiling,
            )
            self._audit(actor_user_id, "CREATE_CATEGORY", row, None, self._snapshot(row))
            self.db.commit()
            self.db.refresh(row)
            return row
        except Exception:
            self.db.rollback()
            raise

    def update(self, actor_user_id: UUID, category_id: UUID, body: CategoryUpdateRequest) -> CategoryCatalog:
        try:
            row = self._get(category_id)
            before = self._snapshot(row)
            if body.display_name is not None:
                row.display_name = body.display_name.strip()
            if body.base_score is not None:
                row.base_score = body.base_score
            if body.priority_ceiling is not None:
                row.priority_ceiling = body.priority_ceiling
            if body.is_active is not None:
                row.is_active = body.is_active
            if row.is_active and row.base_score is None:
                raise DomainError(CATEGORY_REQUIRED, "Active Category requires base_score.", 409)
            self._audit(actor_user_id, "UPDATE_CATEGORY", row, before, self._snapshot(row))
            self.db.commit()
            self.db.refresh(row)
            return row
        except Exception:
            self.db.rollback()
            raise

    def deactivate(self, actor_user_id: UUID, category_id: UUID) -> CategoryCatalog:
        try:
            row = self._get(category_id)
            before = self._snapshot(row)
            row.is_active = False
            self._audit(actor_user_id, "DEACTIVATE_CATEGORY", row, before, self._snapshot(row))
            self.db.commit()
            self.db.refresh(row)
            return row
        except Exception:
            self.db.rollback()
            raise

    def _get(self, category_id: UUID) -> CategoryCatalog:
        row = self.catalog.get_category_any_status(category_id)
        if row is None:
            raise DomainError(CATEGORY_REQUIRED, "Category not found.", 404)
        return row

    def _audit(
        self,
        actor_user_id: UUID,
        action: str,
        row: CategoryCatalog,
        before_data: dict[str, object] | None,
        after_data: dict[str, object],
    ) -> None:
        self.db.add(
            AuditLog(
                actor_user_id=actor_user_id,
                actor_role="COORDINATOR",
                action=action,
                entity_type="CATEGORY",
                entity_id=row.id,
                before_data=before_data,
                after_data=after_data,
                request_id=UUID(request_id) if (request_id := request_id_context.get()) else None,
            )
        )

    @staticmethod
    def _snapshot(row: CategoryCatalog) -> dict[str, object]:
        return {
            "id": str(row.id),
            "code": row.code,
            "display_name": row.display_name,
            "base_score": row.base_score,
            "priority_ceiling": row.priority_ceiling.value if row.priority_ceiling else None,
            "is_active": row.is_active,
        }
