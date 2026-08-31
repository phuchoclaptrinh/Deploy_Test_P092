"""What a resident's answer is allowed to change, and who has the last word.

Two answers carry authority beyond the transcript:

* a **location** answer moves the ticket, so the backend enforces the two-option
  contract rather than trusting a client to send a coherent pair;
* a **category** answer settles which problem the ticket is for, and a later
  model pass must not overrule the person who reported it.

Both are enforced in `AgentQuestionService`, not in the UI, because a client can
call the API directly.
"""

from __future__ import annotations

import pytest

from src.agents.nodes import CRITERION_ANSWER_OPTIONS
from src.agents.service import resume_analysis, run_analysis
from src.models.agent_schemas import (
    LOCATION_CHANGE_OPTION,
    LOCATION_KEEP_OPTION,
    AgentQuestionKind,
)
from src.models.api.errors import DomainError
from tests.test_agents.conftest import ScriptedLLM, classification

CONFLICT = dict(
    category=None,
    text_category="Nước",
    image_category="Tiếng ồn",
    question_kind="CATEGORY_CONFIRMATION",
    question_text="Bạn muốn xử lý vấn đề nào trong phản ánh này?",
    category_options=["Nước", "Tiếng ồn"],
)

LOCATION_MISMATCH = dict(
    location_consistent=False,
    question_kind="LOCATION_CONFIRMATION",
    question_text="Mô tả có vẻ không khớp với vị trí bạn đã chọn. Bạn muốn giữ hay chọn lại?",
)


def _ask_location(agent_world):
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    llm = ScriptedLLM([classification(**LOCATION_MISMATCH), classification()])
    run_analysis(ticket_id, llm=llm)
    question = agent_world.pending_question(ticket_id)
    assert question is not None
    assert question.question_kind == AgentQuestionKind.LOCATION_CONFIRMATION.value
    return ticket_id, question, llm


# ---------------------------------------------------------------------------
# Location confirmation.
# ---------------------------------------------------------------------------


def test_a_location_question_offers_exactly_keep_or_choose_another(agent_world):
    _ticket_id, question, _llm = _ask_location(agent_world)

    assert question.options == [LOCATION_KEEP_OPTION, LOCATION_CHANGE_OPTION]
    # No prose. A location is picked from the fixed selector or not at all.
    assert question.allow_free_text_fallback is False


def test_keeping_the_location_with_a_replacement_id_is_rejected(agent_world):
    """A contradictory answer. Guessing which half the caller meant is exactly
    the inference this design rules out."""
    ticket_id, question, _llm = _ask_location(agent_world)

    with pytest.raises(DomainError) as excinfo:
        agent_world.answer(ticket_id, question.id, LOCATION_KEEP_OPTION, location_id=agent_world.lift)

    assert excinfo.value.status_code == 400
    assert agent_world.ticket(ticket_id).location_id == agent_world.bath_a


def test_choosing_another_location_without_one_is_rejected(agent_world):
    ticket_id, question, _llm = _ask_location(agent_world)

    with pytest.raises(DomainError) as excinfo:
        agent_world.answer(ticket_id, question.id, LOCATION_CHANGE_OPTION)

    assert excinfo.value.status_code == 400
    assert agent_world.ticket(ticket_id).location_id == agent_world.bath_a


def test_choosing_a_location_outside_the_catalog_is_rejected(agent_world):
    from uuid import uuid4

    ticket_id, question, _llm = _ask_location(agent_world)

    with pytest.raises(DomainError) as excinfo:
        agent_world.answer(ticket_id, question.id, LOCATION_CHANGE_OPTION, location_id=uuid4())

    assert excinfo.value.status_code == 400
    assert agent_world.ticket(ticket_id).location_id == agent_world.bath_a


def test_keeping_the_location_leaves_the_ticket_where_it_was(agent_world):
    ticket_id, question, llm = _ask_location(agent_world)

    answered = agent_world.answer(ticket_id, question.id, LOCATION_KEEP_OPTION)
    resume_analysis(question.session_id, llm=llm)

    assert agent_world.ticket(ticket_id).location_id == agent_world.bath_a
    # The history management reads records the outcome, not only the button.
    assert answered.answer_payload["location_changed"] is False
    assert answered.answer_payload["final_location_id"] == str(agent_world.bath_a)
    assert agent_world.latest_run(ticket_id).exit_reason == "ANALYSIS_COMPLETE"


def test_choosing_another_location_moves_the_ticket_and_the_analysis_continues(agent_world):
    ticket_id, question, llm = _ask_location(agent_world)

    answered = agent_world.answer(ticket_id, question.id, LOCATION_CHANGE_OPTION, location_id=agent_world.lift)
    resume_analysis(question.session_id, llm=llm)

    assert agent_world.ticket(ticket_id).location_id == agent_world.lift
    assert answered.answer_payload["location_changed"] is True
    assert answered.answer_payload["final_location_id"] == str(agent_world.lift)
    assert agent_world.latest_run(ticket_id).exit_reason == "ANALYSIS_COMPLETE"


def test_a_replacement_location_on_a_criterion_question_is_rejected(agent_world):
    """`selected_location_id` means one thing on one kind of question. Anywhere
    else it would let a caller believe it had moved the ticket."""
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    llm = ScriptedLLM(
        [
            classification(
                property_spread=None,
                unknown_facts=["property_spread"],
                question_kind="SPREAD_CONFIRMATION",
                question_text="Nước có đang lan sang căn bên cạnh không?",
            ),
            classification(),
        ]
    )
    run_analysis(ticket_id, llm=llm)
    question = agent_world.pending_question(ticket_id)
    assert question.question_kind == AgentQuestionKind.SPREAD_CONFIRMATION.value

    with pytest.raises(DomainError) as excinfo:
        agent_world.answer(
            ticket_id,
            question.id,
            next(iter(CRITERION_ANSWER_OPTIONS["property_spread"])),
            location_id=agent_world.lift,
        )

    assert excinfo.value.status_code == 400
    assert agent_world.ticket(ticket_id).location_id == agent_world.bath_a


# ---------------------------------------------------------------------------
# Category confirmation.
# ---------------------------------------------------------------------------


def test_a_category_conflict_asks_which_problem_and_never_offers_both(agent_world):
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.bath_a,
        unit_id=agent_world.unit_a,
        description="Nhà tắm có vấn đề.",
    )
    llm = ScriptedLLM([classification(**CONFLICT), classification()])

    run_analysis(ticket_id, llm=llm)

    question = agent_world.pending_question(ticket_id)
    assert question.question_kind == AgentQuestionKind.CATEGORY_CONFIRMATION.value
    assert question.options == ["Nước", "Tiếng ồn"]
    # One ticket, one problem. Nothing that means "both".
    assert not any("cả hai" in option.lower() or "tất cả" in option.lower() for option in question.options)


def test_the_resident_choice_becomes_the_category_and_the_model_cannot_change_it(agent_world):
    """The bug this guards: after the resident picked a category, the next
    classification pass could name a different one and win."""
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.bath_a,
        unit_id=agent_world.unit_a,
        description="Nhà tắm có vấn đề.",
    )
    llm = ScriptedLLM(
        [
            classification(**CONFLICT),
            # The second pass disagrees with the resident, on purpose.
            classification(category="Nước", text_category="Nước", image_category="Tiếng ồn"),
        ]
    )
    run_analysis(ticket_id, llm=llm)
    question = agent_world.pending_question(ticket_id)

    agent_world.answer(ticket_id, question.id, "Tiếng ồn")
    resume_analysis(question.session_id, llm=llm)

    run = agent_world.latest_run(ticket_id)
    assert run.final_category_id == agent_world.noise
    assert agent_world.ticket(ticket_id).category_id == agent_world.noise
    # The disagreement survives as evidence, which is the only role the two
    # side categories ever have.
    assert run.text_category_id == agent_world.water
    assert run.image_category_id is None or run.image_category_id == agent_world.noise


def test_the_category_question_is_not_asked_twice(agent_world):
    """Asking again would be asking the resident to defend a decision they
    already made, out of the same three-question budget."""
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.bath_a,
        unit_id=agent_world.unit_a,
        description="Nhà tắm có vấn đề.",
    )
    llm = ScriptedLLM(
        [
            classification(**CONFLICT),
            # The model asks the very same thing again.
            classification(**CONFLICT),
        ]
    )
    run_analysis(ticket_id, llm=llm)
    first = agent_world.pending_question(ticket_id)

    agent_world.answer(ticket_id, first.id, "Nước")
    resume_analysis(first.session_id, llm=llm)

    kinds = [q.question_kind for q in agent_world.questions(ticket_id)]
    assert kinds.count(AgentQuestionKind.CATEGORY_CONFIRMATION.value) == 1
    assert agent_world.latest_run(ticket_id).final_category_id == agent_world.water


def test_the_confirmed_category_is_handed_to_the_next_classification_as_fixed_context(agent_world):
    """Overruling the model afterwards is the guarantee; telling it up front is
    what keeps the two from disagreeing in the first place."""
    seen: list[str | None] = []

    class RecordingLLM(ScriptedLLM):
        def classify(self, **kwargs):
            seen.append(kwargs.get("confirmed_category"))
            return super().classify(**kwargs)

    ticket_id = agent_world.make_ticket(
        location_id=agent_world.bath_a,
        unit_id=agent_world.unit_a,
        description="Nhà tắm có vấn đề.",
    )
    llm = RecordingLLM([classification(**CONFLICT), classification(category="Tiếng ồn")])
    run_analysis(ticket_id, llm=llm)
    question = agent_world.pending_question(ticket_id)

    agent_world.answer(ticket_id, question.id, "Tiếng ồn")
    resume_analysis(question.session_id, llm=llm)

    assert seen == [None, "Tiếng ồn"]
