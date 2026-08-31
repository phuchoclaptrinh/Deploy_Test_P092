"""What the Agent is allowed to say, and what happens when it says it wrong.

Three separate concerns, and the file is in three parts because conflating them
is how the second one gets lost:

1. **The payload carries judgements, never conclusions.** Five integers,
   blockers, evidence. No score, no priority, no severity.
2. **A question targets one criterion.** The five criterion questions replaced a
   single "how serious is it?", and each one is only legal when the Agent has
   admitted it does not know the thing it is asking about.
3. **A broken payload is a technical fault, not a verdict on the report.** The
   model gets a repair turn; if that fails the ticket reaches a human. It is
   never closed as invalid, because "our model could not produce valid JSON" is
   not a finding about somebody's leaking ceiling.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.agents.llm_client import ModelContractError, UnifiedClassification
from src.agents.nodes import CRITERION_ANSWER_OPTIONS, CRITERION_QUESTION_KINDS
from src.agents.service import run_analysis
from src.models.agent_schemas import (
    QUESTION_KIND_CRITERION,
    AgentAnalysisResult,
    AgentExitReason,
    AgentQuestionKind,
    AgentToolUsage,
    RiskCriteriaPayload,
)
from src.models.enums import AnalysisRunStatus, ClassificationStatus, InvalidReason
from tests.test_agents.conftest import ScriptedLLM, classification


def _result(**overrides) -> AgentAnalysisResult:
    payload = {
        "ticket_id": uuid4(),
        "analysis_session_id": uuid4(),
        "exit_reason": AgentExitReason.ANALYSIS_COMPLETE,
        "category_id": uuid4(),
        "criteria": RiskCriteriaPayload(
            human_safety=1, property_spread=1, essential_function=1, affected_scope=0, deterioration_speed=1
        ),
        "tool_usage": AgentToolUsage(total_tool_calls=0, ask_resident_rounds=0, ask_resident_elapsed_seconds=0),
        "category_catalog_version": "v1",
        "model_version": "test",
        "analyzed_at": datetime.now(UTC),
    }
    payload.update(overrides)
    return AgentAnalysisResult(**payload)


# ---------------------------------------------------------------------------
# 1. The payload carries judgements, never conclusions.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["risk_score", "score_total", "priority", "severity", "severity_source", "red_flag"])
def test_the_payload_refuses_any_field_that_would_be_a_conclusion(field):
    """`extra="forbid"` is what makes "the AI never sets the priority" a fact.

    Without it a model that volunteered `priority: "P5"` would have the field
    silently dropped, and the next person to read the contract would have no way
    to tell whether it was being honoured.
    """
    with pytest.raises(ValidationError):
        _result(**{field: "P5"})


@pytest.mark.parametrize("value", [-1, 5, 100])
def test_a_criterion_outside_the_scale_is_refused_at_the_boundary(value):
    with pytest.raises(ValidationError):
        RiskCriteriaPayload(
            human_safety=value, property_spread=0, essential_function=0, affected_scope=0, deterioration_speed=0
        )


def test_a_classification_exit_requires_all_five_criteria():
    with pytest.raises(ValidationError):
        _result(criteria=None)


def test_an_unreadable_report_must_not_report_a_classification_it_could_not_reach():
    with pytest.raises(ValidationError):
        _result(exit_reason=AgentExitReason.INSUFFICIENT_INPUT, category_id=None)
    with pytest.raises(ValidationError):
        _result(
            exit_reason=AgentExitReason.INSUFFICIENT_INPUT,
            category_id=None,
            criteria=None,
            blockers=["FIRE_OR_SMOKE"],
            evidence={"blockers": {"FIRE_OR_SMOKE": ["khói"]}},
        )
    # With nothing claimed, it is accepted.
    assert _result(exit_reason=AgentExitReason.INSUFFICIENT_INPUT, category_id=None, criteria=None)


def test_a_blocker_without_evidence_is_refused():
    """A blocker floors the priority with no score behind it, so the only thing
    a reviewer can check is what was seen."""
    with pytest.raises(ValidationError):
        _result(blockers=["FIRE_OR_SMOKE"])
    assert _result(
        blockers=["FIRE_OR_SMOKE"],
        evidence={"blockers": {"FIRE_OR_SMOKE": ["Khói bốc ra từ hộp kỹ thuật."]}},
    )


def test_each_blocker_needs_its_own_evidence_not_a_shared_line():
    """Two codes and one line used to pass, because the check was `if blockers
    and not evidence.blockers`. Each code sets its own floor, so each one is
    the subject of its own question."""
    with pytest.raises(ValidationError):
        _result(
            blockers=["FIRE_OR_SMOKE", "SERIOUS_INJURY"],
            evidence={"blockers": {"FIRE_OR_SMOKE": ["Khói bốc ra từ hộp kỹ thuật."]}},
        )
    assert _result(
        blockers=["FIRE_OR_SMOKE", "SERIOUS_INJURY"],
        evidence={
            "blockers": {
                "FIRE_OR_SMOKE": ["Khói bốc ra từ hộp kỹ thuật."],
                "SERIOUS_INJURY": ["Một người bị bỏng tay."],
            }
        },
    )


def test_a_blank_line_is_not_evidence():
    with pytest.raises(ValidationError):
        _result(blockers=["FIRE_OR_SMOKE"], evidence={"blockers": {"FIRE_OR_SMOKE": ["   "]}})


def test_evidence_for_a_blocker_nobody_claimed_is_refused():
    """It would show under an emergency heading for a floor nobody applied."""
    with pytest.raises(ValidationError):
        _result(
            blockers=["FIRE_OR_SMOKE"],
            evidence={
                "blockers": {
                    "FIRE_OR_SMOKE": ["Khói bốc ra từ hộp kỹ thuật."],
                    "GAS_LEAK_OR_ASPHYXIATION": ["Không ai báo mùi gas."],
                }
            },
        )


def test_an_unknown_blocker_code_is_refused_rather_than_dropped():
    with pytest.raises(ValidationError):
        _result(blockers=["ROOF_LOOKS_ODD"], evidence={"blockers": {"ROOF_LOOKS_ODD": ["gì đó"]}})


def test_a_repeated_blocker_is_refused():
    with pytest.raises(ValidationError):
        _result(
            blockers=["FIRE_OR_SMOKE", "FIRE_OR_SMOKE"],
            evidence={"blockers": {"FIRE_OR_SMOKE": ["Khói."]}},
        )


def test_unknown_facts_may_only_name_criteria():
    with pytest.raises(ValidationError):
        _result(criteria=None, exit_reason=AgentExitReason.LIMIT_REACHED,
                unknown_facts=["how_annoyed_the_resident_is"])
    assert _result(
        criteria=None, exit_reason=AgentExitReason.LIMIT_REACHED, unknown_facts=["affected_scope"]
    )


def test_a_fully_scored_payload_cannot_also_declare_a_gap():
    """The reported payload: `affected_scope` scored 0 and named unknown.

    It used to validate, and that is what made it dangerous. Five scores is a
    complete classification, so the round finishes, the ticket is published, and
    nobody ever asks the question the Agent said it needed -- while the stored
    assessment records a criterion the Agent said it could not establish.
    """
    with pytest.raises(ValidationError):
        _result(unknown_facts=["affected_scope"])


def test_the_emergency_exit_does_not_require_a_category():
    """An emergency is answered by speed, and refusing a genuine danger report
    for lacking a Category would turn it into a technical failure."""
    assert _result(exit_reason=AgentExitReason.EMERGENCY_REVIEW_REQUIRED, category_id=None)


# ---------------------------------------------------------------------------
# 2. A question targets one criterion.
# ---------------------------------------------------------------------------


def test_there_is_one_question_kind_per_criterion_and_no_severity_question():
    assert set(QUESTION_KIND_CRITERION.values()) == {
        "human_safety",
        "property_spread",
        "essential_function",
        "affected_scope",
        "deterioration_speed",
    }
    assert not hasattr(AgentQuestionKind, "SEVERITY_CONFIRMATION")


def test_every_criterion_question_has_a_fixed_answer_for_each_anchor():
    """Backend owns the options, so an answer maps onto a score rather than onto
    prose somebody would have to interpret back into one."""
    for kind in CRITERION_QUESTION_KINDS:
        criterion = QUESTION_KIND_CRITERION[AgentQuestionKind(kind)]
        options = CRITERION_ANSWER_OPTIONS[criterion]
        assert sorted(options.values()) == [0, 1, 2, 3, 4]
        assert len(set(options)) == 5


def test_the_model_may_not_ask_about_a_criterion_it_claims_to_know():
    with pytest.raises(ValidationError):
        UnifiedClassification(
            category="Nước",
            human_safety=1,
            property_spread=1,
            essential_function=1,
            affected_scope=0,
            deterioration_speed=1,
            understandable=True,
            ai_reason="Nước rỉ từ trần.",
            question_kind="SPREAD_CONFIRMATION",
            question_text="Nước có lan sang căn khác không?",
        )


def test_the_model_may_not_ask_about_a_criterion_it_did_not_declare_unknown():
    """The question and the gap have to agree, or a scarce question is spent on
    something the model had already decided."""
    with pytest.raises(ValidationError):
        UnifiedClassification(
            category="Nước",
            human_safety=1,
            property_spread=None,
            essential_function=1,
            affected_scope=0,
            deterioration_speed=1,
            unknown_facts=[],
            understandable=True,
            ai_reason="Nước rỉ từ trần.",
            question_kind="SPREAD_CONFIRMATION",
            question_text="Nước có lan sang căn khác không?",
        )


def test_a_criterion_question_is_accepted_when_the_gap_is_declared():
    payload = UnifiedClassification(
        category="Nước",
        human_safety=1,
        property_spread=None,
        essential_function=1,
        affected_scope=0,
        deterioration_speed=1,
        unknown_facts=["property_spread"],
        understandable=True,
        ai_reason="Nước rỉ từ trần, chưa rõ có lan không.",
        question_kind="SPREAD_CONFIRMATION",
        question_text="Nước có lan sang căn khác không?",
    )
    assert payload.criteria is None


def test_a_readable_report_that_asks_nothing_must_commit_to_all_five_scores():
    with pytest.raises(ValidationError):
        UnifiedClassification(
            category="Nước",
            human_safety=1,
            property_spread=None,
            essential_function=1,
            affected_scope=0,
            deterioration_speed=1,
            understandable=True,
            ai_reason="Nước rỉ từ trần.",
        )


def test_one_criterion_answer_moves_one_score_and_leaves_the_others_alone(agent_world):
    """A resident answering about spread has told us nothing new about safety."""
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    llm = ScriptedLLM(
        [
            classification(
                property_spread=None,
                unknown_facts=["property_spread"],
                question_kind="SPREAD_CONFIRMATION",
                question_text="Nước có đang lan sang căn bên cạnh không?",
            ),
            # The second pass is told the answer and re-scores everything.
            classification(),
        ]
    )
    run_analysis(ticket_id, llm=llm)
    question = agent_world.pending_question(ticket_id)
    assert question.question_kind == AgentQuestionKind.SPREAD_CONFIRMATION.value
    assert set(question.options or []) == set(CRITERION_ANSWER_OPTIONS["property_spread"])


def test_a_criterion_question_refuses_a_free_text_answer(agent_world):
    """The options are five anchors; prose cannot land on one of them, and a
    question that accepted prose would spend one of three rounds on nothing."""
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)
    llm = ScriptedLLM(
        [
            classification(
                deterioration_speed=None,
                unknown_facts=["deterioration_speed"],
                question_kind="DETERIORATION_CONFIRMATION",
                question_text="Tình trạng có xấu đi nhanh không?",
            ),
            classification(),
        ]
    )
    run_analysis(ticket_id, llm=llm)
    question = agent_world.pending_question(ticket_id)
    assert question.allow_free_text_fallback is False


# ---------------------------------------------------------------------------
# 3. A broken payload is a technical fault, not a verdict on the report.
# ---------------------------------------------------------------------------


class _BrokenModel:
    """Fails its own schema on every attempt, including the repair turn."""

    def __init__(self) -> None:
        self.attempts = 0

    def classify(self, **_kwargs):
        self.attempts += 1
        raise ModelContractError("UnifiedClassification", ["invalid"] * self.attempts)

    def judge_duplicate(self, **_kwargs):  # pragma: no cover - never reached
        raise AssertionError("a broken classification must not reach the duplicate stage")

    def judge_grouping(self, **_kwargs):  # pragma: no cover - never reached
        raise AssertionError("a broken classification must not reach the grouping stage")


def test_a_payload_that_stays_invalid_sends_the_ticket_to_a_human(agent_world):
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)

    run_analysis(ticket_id, llm=_BrokenModel())

    ticket = agent_world.ticket(ticket_id)
    run = agent_world.latest_run(ticket_id)
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW
    assert run.status is AnalysisRunStatus.FAILED
    assert run.error_code is not None
    # No business conclusion was reached, so none is recorded.
    assert run.exit_reason is None


def test_a_broken_payload_never_closes_the_residents_report_as_invalid(agent_world):
    """The one that matters. `INVALID` is a statement about the report; a model
    that could not produce valid JSON has made no statement about anything."""
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)

    run_analysis(ticket_id, llm=_BrokenModel())

    ticket = agent_world.ticket(ticket_id)
    assert ticket.status.value != "INVALID"
    assert ticket.invalid_reason is None
    assert InvalidReason.CONTENT_INSUFFICIENT.value not in (ticket.invalid_reason or "")


def test_a_broken_payload_leaves_the_ticket_unscored_rather_than_guessing(agent_world):
    ticket_id = agent_world.make_ticket(location_id=agent_world.bath_a, unit_id=agent_world.unit_a)

    run_analysis(ticket_id, llm=_BrokenModel())

    ticket = agent_world.ticket(ticket_id)
    assert ticket.priority is None
    assert ticket.risk_score is None
    assert ticket.current_risk_assessment_id is None
