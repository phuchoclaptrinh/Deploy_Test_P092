"""A report the Agent understood but that is not a maintenance incident is
rejected, not classified.

Before `is_incident`, the only reject lever for text was `understandable`, which
means "is this comprehensible". A grammatical question about parking prices is
comprehensible, so the model was forced to pick a Category and score it -- the
identical text landed as ANALYSIS_COMPLETE, DUPLICATE_UNCERTAIN and
INSUFFICIENT_INPUT across three runs. `is_incident=false` gives it a real exit.
"""

from __future__ import annotations

import pytest

from src.agents.service import run_analysis
from src.agents.state import input_insufficient
from src.models.enums import TicketStatus
from tests.test_agents.conftest import ScriptedLLM, classification

OUT_OF_SCOPE = dict(
    category=None,
    text_category=None,
    image_category=None,
    human_safety=None,
    property_spread=None,
    essential_function=None,
    affected_scope=None,
    deterioration_speed=None,
    criterion_evidence={
        "human_safety": [],
        "property_spread": [],
        "essential_function": [],
        "affected_scope": [],
        "deterioration_speed": [],
    },
    blockers=[],
    unknown_facts=[],
    understandable=True,
    is_incident=False,
    incident_facts=[],
    ai_reason="Cư dân chỉ hỏi giá mua thêm chỗ đỗ xe và nói rõ không có sự cố cần sửa.",
)


# ---------------------------------------------------------------------------
# Unit: input_insufficient covers the new case.
# ---------------------------------------------------------------------------


def test_understandable_non_incident_is_insufficient_input_even_without_an_image():
    assert input_insufficient({"understandable": True, "is_incident": False, "image_urls": []}) is True


def test_an_ordinary_incident_is_not_swept_up():
    assert input_insufficient({"understandable": True, "is_incident": True, "image_urls": []}) is False


def test_missing_is_incident_key_defaults_to_incident():
    assert input_insufficient({"understandable": True, "image_urls": []}) is False


# ---------------------------------------------------------------------------
# Contract: a non-incident payload may not smuggle a classification.
# ---------------------------------------------------------------------------


def test_contract_rejects_a_non_incident_that_also_names_a_category():
    from src.agents.llm_client import UnifiedClassification

    with pytest.raises(ValueError, match="is_incident=false"):
        UnifiedClassification(
            understandable=True,
            is_incident=False,
            category="Nước",
            ai_reason="mâu thuẫn: vừa nói không phải sự cố vừa gán Category.",
        )


def test_contract_rejects_a_non_incident_that_asks_a_question():
    from src.agents.llm_client import UnifiedClassification

    with pytest.raises(ValueError, match="is_incident=false"):
        UnifiedClassification(
            understandable=True,
            is_incident=False,
            question_kind="LOCATION_CONFIRMATION",
            question_text="Xác nhận vị trí?",
            ai_reason="không phải sự cố thì không hỏi.",
        )


# ---------------------------------------------------------------------------
# Integration: the whole round rejects it.
# ---------------------------------------------------------------------------


def test_a_parking_price_question_is_rejected_not_classified(agent_world):
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_a,
        description="Tôi muốn hỏi giá mua thêm chỗ đỗ xe và không có sự cố nào cần sửa.",
    )
    run_analysis(ticket_id, llm=ScriptedLLM([classification(**OUT_OF_SCOPE)]))

    ticket = agent_world.ticket(ticket_id)
    run = agent_world.latest_run(ticket_id)
    assert run.exit_reason == "INSUFFICIENT_INPUT"
    assert ticket.status is TicketStatus.INVALID
    assert ticket.priority is None
    assert ticket.category_id is None
    # The recorded reason says what actually happened -- a non-incident -- not
    # "we could not read this".
    assert "chỗ đỗ xe" in (run.ai_reason or "") or "không phải" in (run.ai_reason or "")


def test_the_model_does_not_get_to_ask_its_way_around_a_non_incident(agent_world):
    """Even if the scripted model tries to pair is_incident=false with a
    pending question, the contract refuses the payload upstream; a well-formed
    non-incident just exits."""
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_a,
        description="Cho tôi hỏi thủ tục đăng ký thẻ xe máy mới.",
    )
    run_analysis(
        ticket_id,
        llm=ScriptedLLM(
            [
                classification(
                    **{
                        **OUT_OF_SCOPE,
                        "ai_reason": "Cư dân hỏi thủ tục đăng ký thẻ xe, không phải sự cố bảo trì.",
                    }
                )
            ]
        ),
    )
    run = agent_world.latest_run(ticket_id)
    assert run.exit_reason == "INSUFFICIENT_INPUT"
    assert agent_world.ticket(ticket_id).status is TicketStatus.INVALID
