"""Coordinator Category catalog administration routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor, require_coordinator
from src.api.dependencies.database import get_db
from src.api.routes.coordinator._common import ok
from src.models.api.common import ApiResponse
from src.models.api.coordinator import CategoryCreateRequest, CategoryUpdateRequest, CoordinatorCategoryResponse
from src.repositories.catalog_repository import CatalogRepository
from src.services.category_service import CategoryService

router = APIRouter()


def _category_response(row) -> CoordinatorCategoryResponse:
    return CoordinatorCategoryResponse(
        id=row.id,
        code=row.code,
        display_name=row.display_name,
        is_active=row.is_active,
    )


@router.get("/categories", response_model=ApiResponse[list[CoordinatorCategoryResponse]])
def list_categories(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, [_category_response(row) for row in CatalogRepository(db).list_all_categories()])


@router.post("/categories", response_model=ApiResponse[CoordinatorCategoryResponse])
def create_category(
    request: Request,
    body: CategoryCreateRequest,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, _category_response(CategoryService(db).create(actor.user.user_id, body)))


@router.patch("/categories/{category_id}", response_model=ApiResponse[CoordinatorCategoryResponse])
def update_category(
    request: Request,
    category_id: UUID,
    body: CategoryUpdateRequest,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, _category_response(CategoryService(db).update(actor.user.user_id, category_id, body)))


@router.delete("/categories/{category_id}", response_model=ApiResponse[CoordinatorCategoryResponse])
def deactivate_category(
    request: Request,
    category_id: UUID,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, _category_response(CategoryService(db).deactivate(actor.user.user_id, category_id)))
