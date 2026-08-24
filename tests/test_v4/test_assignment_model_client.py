"""Regression coverage for the provider-neutral assignment envelope client."""

from __future__ import annotations

from src.assignment_agent.model_client import LangChainAssignmentModelClient


class _StructuredRunnable:
    def __init__(self) -> None:
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return {"decisions": []}


class _RecordingLLM:
    def __init__(self) -> None:
        self.schema = None
        self.kwargs = None
        self.runnable = _StructuredRunnable()

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema
        self.kwargs = kwargs
        return self.runnable


def test_assignment_client_uses_function_calling_for_the_open_decision_envelope():
    """OpenAI rejects the envelope's ``list[Any]`` in json-schema mode."""
    llm = _RecordingLLM()
    client = LangChainAssignmentModelClient(
        "gpt-4o-mini",
        timeout_seconds=30,
        llm=llm,
    )

    result = client.decide(system_prompt="system", user_prompt="user")

    assert result.decisions == []
    assert llm.kwargs == {"method": "function_calling"}
    assert llm.runnable.messages == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
