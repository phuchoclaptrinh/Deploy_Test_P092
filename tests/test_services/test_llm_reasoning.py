"""Provider configuration for the latency-sensitive classification path."""

from src.services.llm import openai_no_thinking_kwargs


def test_newer_gpt5_models_disable_reasoning_explicitly():
    assert openai_no_thinking_kwargs("gpt-5.1") == {"reasoning_effort": "none"}
    assert openai_no_thinking_kwargs("gpt-5.4-mini") == {"reasoning_effort": "none"}


def test_models_without_none_reasoning_do_not_receive_an_invalid_parameter():
    assert openai_no_thinking_kwargs("gpt-4o-mini") == {}
    assert openai_no_thinking_kwargs("gpt-5") == {}
    assert openai_no_thinking_kwargs("o3-mini") == {}
