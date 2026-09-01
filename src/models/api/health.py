"""Health and readiness API schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "application": "FixIt Agent API",
                "environment": "development",
                "status": "ok",
            }
        },
    )

    application: str = Field(description="Application name configured for this API process.")
    environment: str = Field(description="Runtime environment name, such as development, test, or production.")
    status: Literal["ok"] = Field(description="Liveness status for the API process.")


class ReadinessChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database: Literal["ok", "error"] = Field(description="Database connectivity check result.")
    migration: Literal["ok", "missing", "pending", "unknown"] = Field(
        description="Alembic migration status check result."
    )
    supabase_auth: Literal["configured", "missing"] = Field(
        description="Whether the Supabase Auth configuration required for token validation is present."
    )
    supabase_storage: Literal["configured", "missing"] = Field(
        description="Whether the Supabase Storage configuration required for signed URLs is present."
    )


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "status": "ready",
                    "checks": {
                        "database": "ok",
                        "migration": "ok",
                        "supabase_auth": "configured",
                        "supabase_storage": "configured",
                    },
                },
                {
                    "status": "not_ready",
                    "checks": {
                        "database": "error",
                        "migration": "unknown",
                        "supabase_auth": "missing",
                        "supabase_storage": "missing",
                    },
                },
            ]
        },
    )

    status: Literal["ready", "not_ready"] = Field(description="Readiness result for serving API traffic.")
    checks: ReadinessChecks = Field(description="Safe dependency check statuses without hostnames, URLs, or secrets.")
