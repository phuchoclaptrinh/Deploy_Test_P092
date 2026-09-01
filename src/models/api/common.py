"""Shared API response envelopes from Self_Dev_Docs v2."""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")

    data: T
    meta: dict[str, object] = Field(default_factory=dict)
    error: None = None
    request_id: str | None = None


class ApiErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ApiErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: None = None
    error: ApiErrorBody
    request_id: str | None = None
