"""Unit response schemas."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UnitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: UUID
    building_code: str
    floor_code: str
    unit_code: str
    is_active: bool
