"""Read-only location/category catalogs from Self Dev v3."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor, get_current_actor
from src.api.dependencies.database import get_db
from src.models.api.catalog import CategoryCatalogItem, LocationCatalogItem
from src.models.api.common import ApiResponse
from src.repositories.catalog_repository import CatalogRepository

router = APIRouter()


def _ok(request: Request, data):
    return {"data": data, "meta": {}, "error": None, "request_id": request.state.request_id}


@router.get("/locations", response_model=ApiResponse[list[LocationCatalogItem]], summary="Danh mục vị trí")
def list_locations(
    request: Request,
    _actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    rows = CatalogRepository(db).list_locations()
    data = [
        LocationCatalogItem(
            id=row.id,
            floor_code=row.floor.floor_code,
            floor_display_name=row.floor.display_name,
            location_type_code=row.location_type.code,
            location_type_name=row.location_type.display_name,
            unit_code=row.unit.unit_code if row.unit else None,
            label=row.label,
        )
        for row in rows
    ]
    return _ok(request, data)


@router.get("/categories", response_model=ApiResponse[list[CategoryCatalogItem]], summary="Danh mục Category")
def list_categories(
    request: Request,
    _actor=Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    rows = CatalogRepository(db).list_categories()
    return _ok(
        request,
        [
            CategoryCatalogItem(
                id=row.id,
                code=row.code,
                display_name=row.display_name,
            )
            for row in rows
        ],
    )
