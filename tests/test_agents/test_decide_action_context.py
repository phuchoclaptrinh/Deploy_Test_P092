"""Node `decide_action` phải cho LLM thấy hội thoại đã diễn ra.

Session 6dd9968c hỏi cư dân đúng một câu ba lần ("phạm vi mất điện?"), cư dân
trả lời "Cả tầng" cả ba lần, rồi vòng lặp chỉ dừng vì hết hạn mức chứ không
phải vì model tự thấy đủ. Nguyên nhân: answer_notes chỉ được truyền vào bước
trích xuất, còn decide_next_action thì không nhận, nên model không có cách nào
biết mình đã hỏi gì. Hướng dẫn "đừng hỏi lại" trong prompt là vô nghĩa khi
model không nhìn thấy câu trả lời.
"""

from __future__ import annotations

from uuid import uuid4

from src.agents.llm_client import ActionDecision
from src.agents.nodes import AgentNodes
from src.services.agent_common import MAX_ASK_ROUNDS

CATALOG = [{"category_id": "11111111-1111-1111-1111-111111111111", "display_name": "Mất điện cục bộ"}]


class _RecordingLLM:
    """Ghi lại kwargs đã nhận, trả về một quyết định cố định."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def extract(self, **kwargs):  # pragma: no cover - không dùng ở đây
        raise NotImplementedError

    def decide_next_action(self, **kwargs) -> ActionDecision:
        self.calls.append(kwargs)
        return ActionDecision(action="CONCLUDE", reason="xong")


class _FakeSession:
    def __init__(self, ask_rounds: int) -> None:
        self.total_tool_calls = 0
        self.ask_resident_rounds = ask_rounds
        self.ask_resident_elapsed_seconds = 0


class _FakeBackend:
    def __init__(self, session: _FakeSession) -> None:
        self._fake = session

    def _session(self, session_id):
        return self._fake


def _decide(*, answer_notes: list[str], ask_rounds: int) -> dict:
    nodes = AgentNodes.__new__(AgentNodes)  # __init__ thật cần một DB Session
    llm = _RecordingLLM()
    nodes.llm = llm
    nodes.backend = _FakeBackend(_FakeSession(ask_rounds))

    AgentNodes.decide_action(
        nodes,
        {
            "session_id": str(uuid4()),
            "catalog": CATALOG,
            "description": "Mất điện",
            "text_categories": ["11111111-1111-1111-1111-111111111111"],
            "severity": "MEDIUM",
            "severity_source": "IMAGE",
            "is_confident": True,
            "answer_notes": answer_notes,
            "iterations": ask_rounds,
        },
    )
    return llm.calls[-1]


def test_the_decision_step_receives_what_the_resident_already_answered():
    notes = ["Trả lời của cư dân (vòng 1): Cả tầng"]

    call = _decide(answer_notes=notes, ask_rounds=1)

    assert call["answer_notes"] == notes


def test_the_decision_step_receives_how_much_ask_budget_is_left():
    call = _decide(answer_notes=["Trả lời của cư dân (vòng 1): Cả tầng"], ask_rounds=1)

    assert call["ask_rounds_used"] == 1
    assert call["max_ask_rounds"] == MAX_ASK_ROUNDS


def test_a_first_pass_reports_an_empty_conversation_rather_than_omitting_it():
    call = _decide(answer_notes=[], ask_rounds=0)

    assert call["answer_notes"] == []
    assert call["ask_rounds_used"] == 0


def test_the_prompt_actually_renders_the_answers_for_the_model():
    """Truyền xuống thôi chưa đủ — nó phải xuất hiện trong text gửi cho model."""
    from src.agents.llm_client import ExtractionResult, OpenAIAgentLLMClient

    captured: dict = {}

    class _Structured:
        def invoke(self, messages):
            captured["messages"] = messages
            return ActionDecision(action="CONCLUDE", reason="xong")

    class _LLM:
        def with_structured_output(self, schema):
            return _Structured()

    client = OpenAIAgentLLMClient(llm=_LLM())
    client.decide_next_action(
        description="Mất điện",
        extraction=ExtractionResult(
            text_categories=["Mất điện cục bộ"],
            red_flag_text=False,
            text_understandable=True,
            severity="MEDIUM",
            severity_source="IMAGE",
            is_confident=True,
        ),
        available_actions=["ASK_RESIDENT", "CONCLUDE"],
        related_tickets=[],
        grouping_eligible=False,
        answer_notes=["Trả lời của cư dân (vòng 1): Cả tầng"],
        ask_rounds_used=1,
        max_ask_rounds=3,
    )

    user_prompt = captured["messages"][1]["content"]
    assert "Cả tầng" in user_prompt
    assert "1/3" in user_prompt


def test_the_prompt_says_plainly_when_nobody_has_been_asked_yet():
    from src.agents.llm_client import ExtractionResult, OpenAIAgentLLMClient

    captured: dict = {}

    class _Structured:
        def invoke(self, messages):
            captured["messages"] = messages
            return ActionDecision(action="ASK_RESIDENT", reason="cần hỏi")

    class _LLM:
        def with_structured_output(self, schema):
            return _Structured()

    OpenAIAgentLLMClient(llm=_LLM()).decide_next_action(
        description="Mất điện",
        extraction=ExtractionResult(
            text_categories=["Mất điện cục bộ"],
            red_flag_text=False,
            text_understandable=True,
            severity="MEDIUM",
            severity_source="IMAGE",
            is_confident=True,
        ),
        available_actions=["ASK_RESIDENT", "CONCLUDE"],
        related_tickets=[],
        grouping_eligible=False,
    )

    assert "chưa hỏi cư dân lần nào" in captured["messages"][1]["content"]
