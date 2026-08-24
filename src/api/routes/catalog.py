"""Read-only location/category catalogs from Self Dev v3."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor, get_current_actor
from src.api.dependencies.database import get_db
from src.models.api.catalog import CategoryCatalogItem, LocationCatalogItem
from src.models.api.common import ApiResponse
from src.models.api.errors import NO_ACTIVE_UNIT, DomainError
from src.repositories.catalog_repository import CatalogRepository

router = APIRouter()


def _ok(request: Request, data):
    return {"data": data, "meta": {}, "error": None, "request_id": request.state.request_id}


@router.get("/locations", response_model=ApiResponse[list[LocationCatalogItem]], summary="Danh mục vị trí")
def list_locations(
    request: Request,
    actor: CurrentActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    building_id = None
    resident_unit_id = None
    if actor.actor_type == "resident":
        if actor.resident_profile is None:
            raise DomainError(NO_ACTIVE_UNIT, "Tài khoản chưa liên kết căn hộ.", 400)
        building_id = actor.resident_profile.unit.building_id
        resident_unit_id = actor.resident_profile.unit_id
    rows = CatalogRepository(db).list_locations(
        building_id=building_id, resident_unit_id=resident_unit_id
    )
    data = [
        LocationCatalogItem(
            id=row.id,
            building_code=row.building.code,
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
