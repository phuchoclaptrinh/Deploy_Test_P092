"""Backend-owned scoring result contract from Self Dev v2."""

from pydantic import BaseModel, ConfigDict, Field

from src.models.enums import Priority


class ScoringResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_base: int = Field(ge=0)
    location_category_score: int = Field(ge=0)
    density_score: int = Field(ge=0)
    severity_score: int = Field(ge=0)
    score_total: int | None = Field(default=None, ge=0)
    priority_raw: Priority
    priority_final: Priority
    ceiling_applied: bool = False
    red_flag_override: bool = False
