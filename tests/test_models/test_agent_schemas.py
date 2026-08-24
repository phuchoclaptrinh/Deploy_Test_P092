from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.models.agent_schemas import AgentAnalysisResultV3, AgentExitReason, AgentSeveritySource, AgentToolUsage


def _base_result(**overrides):
    payload = {
        "ticket_id": uuid4(),
        "analysis_session_id": uuid4(),
        "exit_reason": AgentExitReason.CONFIDENT_MATCH,
        "text_categories": [uuid4()],
        "red_flag_text": False,
        "image_categories": None,
        "red_flag_signal": None,
        "is_relevant": None,
        "severity": "MEDIUM",
        "severity_source": AgentSeveritySource.TEXT,
        "is_confident": True,
        "tool_usage": AgentToolUsage(total_tool_calls=0, ask_resident_rounds=0, ask_resident_elapsed_seconds=0),
        "category_catalog_version": "catalog-v1",
        "model_version": "agent-v3",
        "analyzed_at": datetime.now(UTC),
    }
    payload.update(overrides)
    return payload


def test_agent_contract_contains_only_ai_outputs():
    result = AgentAnalysisResultV3(**_base_result())

    assert result.severity.value == "MEDIUM"
    dumped = result.model_dump()
    for backend_owned in ("priority", "score_total", "category_match", "ceiling_applied"):
        assert backend_owned not in dumped


def test_agent_contract_forbids_backend_owned_fields():
    with pytest.raises(ValidationError):
        AgentAnalysisResultV3(**_base_result(priority="P3"))


def test_agent_v3_requires_null_image_fields_when_ticket_has_no_image():
    with pytest.raises(ValidationError):
        AgentAnalysisResultV3(**_base_result(red_flag_signal=False, is_relevant=True))


def test_agent_v3_limit_reached_requires_limit_and_not_confident():
    with pytest.raises(ValidationError):
        AgentAnalysisResultV3(
            **_base_result(
                exit_reason=AgentExitReason.LIMIT_REACHED,
                text_categories=[],
                is_confident=True,
                tool_usage=AgentToolUsage(total_tool_calls=4, ask_resident_rounds=2, ask_resident_elapsed_seconds=100),
            )
        )


def test_agent_v3_accepts_image_result_only_when_all_image_fields_are_present():
    result = AgentAnalysisResultV3(
        **_base_result(
            image_categories=[uuid4()],
            red_flag_signal=False,
            is_relevant=True,
            severity_source=AgentSeveritySource.IMAGE,
        )
    )

    assert result.image_categories
    assert result.is_relevant is True


def test_agent_v3_insufficient_input_allows_null_analysis_fields():
    result = AgentAnalysisResultV3(
        ticket_id=uuid4(),
        analysis_session_id=uuid4(),
        exit_reason=AgentExitReason.INSUFFICIENT_INPUT,
        text_categories=None,
        red_flag_text=False,
        image_categories=None,
        red_flag_signal=None,
        is_relevant=None,
        severity=None,
        severity_source=None,
        is_confident=False,
        grouping=None,
        tool_usage=AgentToolUsage(total_tool_calls=0, ask_resident_rounds=0, ask_resident_elapsed_seconds=0),
        category_catalog_version="catalog-v1",
        model_version="agent-v3",
        analyzed_at=datetime.now(UTC),
    )

    assert result.severity is None


def test_agent_v3_other_exit_reasons_still_require_analysis_fields():
    with pytest.raises(ValidationError):
        AgentAnalysisResultV3(**_base_result(severity=None, severity_source=None))
