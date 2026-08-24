"""Tests cho node `extract`, tập trung vào hợp đồng của các trường ảnh.

`ExtractionResult` để default=None cho red_flag_signal/is_relevant nhằm phục vụ
ca không có ảnh, nên khi model bỏ trống chúng thì Pydantic vẫn điền None kể cả
lúc ticket CÓ ảnh. `AgentAnalysisResultV3` lại bắt buộc hai trường đó khác None
mỗi khi image_categories khác None — nên một lần model quên trả lời là cả ticket
chết ở node kết thúc. Đã xảy ra thật với session 10052a14.
"""

from __future__ import annotations

from src.agents.llm_client import ExtractionResult
from src.agents.nodes import AgentNodes

CATALOG = [
    {"category_id": "11111111-1111-1111-1111-111111111111", "display_name": "Mất điện cục bộ"},
    {"category_id": "22222222-2222-2222-2222-222222222222", "display_name": "Thấm tường"},
]


class _StubLLM:
    def __init__(self, result: ExtractionResult) -> None:
        self._result = result

    def extract(self, **kwargs) -> ExtractionResult:
        return self._result

    def decide_next_action(self, **kwargs):  # pragma: no cover - không dùng ở đây
        raise NotImplementedError


def _extract(result: ExtractionResult, *, image_urls: list[str]) -> dict:
    nodes = AgentNodes.__new__(AgentNodes)  # bỏ qua __init__ vì nó cần một DB Session
    nodes.llm = _StubLLM(result)
    return AgentNodes.extract(nodes, {"catalog": CATALOG, "description": "x", "image_urls": image_urls})


def _result(**overrides) -> ExtractionResult:
    base = {
        "text_categories": ["Mất điện cục bộ"],
        "red_flag_text": False,
        "text_understandable": True,
        "image_categories": ["Mất điện cục bộ"],
        "severity": "MEDIUM",
        "severity_source": "IMAGE",
        "is_confident": True,
    }
    return ExtractionResult(**{**base, **overrides})


def test_omitted_image_fields_are_filled_in_when_the_ticket_has_an_image():
    """Model bỏ trống hai trường ảnh -> vẫn phải ra bool, không được để None."""
    updates = _extract(_result(), image_urls=["https://example/a.jpg"])

    assert updates["red_flag_signal"] is False
    assert updates["is_relevant"] is True
    assert updates["image_categories"] == ["11111111-1111-1111-1111-111111111111"]


def test_an_explicit_image_danger_signal_survives():
    updates = _extract(_result(red_flag_signal=True, is_relevant=True), image_urls=["https://example/a.jpg"])

    assert updates["red_flag_signal"] is True


def test_an_explicit_irrelevant_image_survives():
    updates = _extract(_result(red_flag_signal=False, is_relevant=False), image_urls=["https://example/a.jpg"])

    assert updates["is_relevant"] is False


def test_image_fields_stay_null_when_there_is_no_image():
    """Chiều ngược lại của validator: không ảnh thì cả ba trường ảnh phải null."""
    updates = _extract(_result(image_categories=None, red_flag_signal=True, is_relevant=True), image_urls=[])

    assert updates["image_categories"] is None
    assert updates["red_flag_signal"] is None
    assert updates["is_relevant"] is None


def test_the_filled_in_values_satisfy_the_result_contract():
    """Chốt đúng thứ đã làm hỏng session 10052a14: dựng AgentAnalysisResultV3 thật."""
    from datetime import UTC, datetime
    from uuid import UUID, uuid4

    from src.models.agent_schemas import AgentAnalysisResultV3, AgentExitReason, AgentSeveritySource, AgentToolUsage
    from src.models.enums import Severity

    updates = _extract(_result(), image_urls=["https://example/a.jpg"])

    result = AgentAnalysisResultV3(
        ticket_id=uuid4(),
        analysis_session_id=uuid4(),
        exit_reason=AgentExitReason.CONFIDENT_MATCH,
        text_categories=[UUID(item) for item in updates["text_categories"]],
        red_flag_text=updates["red_flag_text"],
        image_categories=[UUID(item) for item in updates["image_categories"]],
        red_flag_signal=updates["red_flag_signal"],
        is_relevant=updates["is_relevant"],
        severity=Severity.MEDIUM,
        severity_source=AgentSeveritySource.IMAGE,
        is_confident=True,
        confidence_notes=None,
        grouping=None,
        tool_usage=AgentToolUsage(total_tool_calls=0, ask_resident_rounds=0, ask_resident_elapsed_seconds=0),
        category_catalog_version="v1",
        model_version="test",
        analyzed_at=datetime.now(UTC),
    )

    assert result.exit_reason is AgentExitReason.CONFIDENT_MATCH
