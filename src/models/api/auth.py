"""Authentication/profile schemas for the two-actor Self Dev model."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.models.enums import UserRole


class UnitSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    unit_code: str
    floor_code: str
    is_primary: bool


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    role: UserRole
    actor_type: Literal["resident", "coordinator", "technician"]
    full_name: str | None
    phone_e164: str | None
    is_active: bool
    unit: UnitSummary | None = None


class BindUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_code: str = Field(min_length=1, max_length=80)


class OtpRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=32)


class OtpRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool = True


class OtpVerifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=32)
    otp: str = Field(min_length=4, max_length=12)


class OtpVerifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int | None = None
