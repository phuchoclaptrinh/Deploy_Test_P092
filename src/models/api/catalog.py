"""Catalog response schemas for location and categories."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class LocationCatalogItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    floor_code: str
    floor_display_name: str
    location_type_code: str
    location_type_name: str
    unit_code: str | None
    label: str


class CategoryCatalogItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    code: str
    display_name: str
