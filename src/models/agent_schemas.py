"""Backend-side AI Agent v3 contract.

These schemas define the trusted boundary between a future Agent runtime and
the backend. They do not implement LangGraph, prompts, tools, or LLM calls.
"""

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.enums import Severity


class AgentExitReason(str, Enum):  # noqa: UP042
    RED_FLAG = "RED_FLAG"
    CONFIDENT_MATCH = "CONFIDENT_MATCH"
    CATEGORY_MISMATCH = "CATEGORY_MISMATCH"
    LIMIT_REACHED = "LIMIT_REACHED"
    INSUFFICIENT_INPUT = "INSUFFICIENT_INPUT"


class AgentSeveritySource(str, Enum):  # noqa: UP042
    IMAGE = "IMAGE"
    TEXT = "TEXT"


class AgentGroupingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    grouped: bool
    density: int = Field(ge=1)
    related_ticket_ids: list[UUID] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=1, max_length=300)


class AgentToolUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_tool_calls: int = Field(ge=0, le=5)
    ask_resident_rounds: int = Field(ge=0, le=3)
    ask_resident_elapsed_seconds: int = Field(ge=0, le=300)
    search_related_tickets_called: bool = False
    propose_case_grouping_called: bool = False


class AgentAnalysisResultV3(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ticket_id: UUID
    analysis_session_id: UUID
    exit_reason: AgentExitReason
    text_categories: list[UUID] | None = Field(default_factory=list, max_length=20)
    red_flag_text: bool = False
    image_categories: list[UUID] | None = Field(default=None, max_length=20)
    red_flag_signal: bool | None = None
    is_relevant: bool | None = None
    severity: Severity | None = None
    severity_source: AgentSeveritySource | None = None
    is_confident: bool
    confidence_notes: str | None = Field(default=None, max_length=500)
    grouping: AgentGroupingResult | None = None
    tool_usage: AgentToolUsage
    category_catalog_version: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=100)
    analyzed_at: datetime

    @model_validator(mode="after")
    def validate_v3_invariants(self):
        if self.exit_reason == AgentExitReason.INSUFFICIENT_INPUT:
            return self

        if self.text_categories is None:
            raise ValueError("text_categories is required unless exit_reason is INSUFFICIENT_INPUT.")
        if self.severity is None or self.severity_source is None:
            raise ValueError("severity and severity_source are required unless exit_reason is INSUFFICIENT_INPUT.")

        if self.image_categories is None:
            if self.red_flag_signal is not None or self.is_relevant is not None:
                raise ValueError("image fields must all be null when ticket has no image.")
        elif self.red_flag_signal is None or self.is_relevant is None:
            raise ValueError("image result fields are required when image_categories is present.")

        if self.exit_reason == AgentExitReason.LIMIT_REACHED:
            if self.is_confident:
                raise ValueError("LIMIT_REACHED requires is_confident=false.")
            if self.tool_usage.total_tool_calls != 5 and self.tool_usage.ask_resident_rounds != 3:
                raise ValueError("LIMIT_REACHED requires tool or ask-resident limit reached.")
        return self


class CategoryCatalogToolItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: UUID
    display_name: str
    priority_ceiling: Literal["P1", "P2", "P3", "UNLIMITED"]
    base_score: int


class CategoryCatalogToolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_version: str
    categories: list[CategoryCatalogToolItem]


# Backward-compatible export name used by older tests/imports. V3 finalization
# code should use AgentAnalysisResultV3 explicitly.
AgentResult = AgentAnalysisResultV3
