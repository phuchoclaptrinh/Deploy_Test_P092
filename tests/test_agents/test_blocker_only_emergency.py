"""A blocker the model flagged without scoring the five criteria is still an
emergency.

`UnifiedClassification` deliberately accepts a payload that names a blocker and
omits the criteria -- "someone is trapped in the elevator" is a complete report
even before anyone rates its `property_spread`. Three layers used to drop it on
the floor anyway:

* `prospective_priority` returned None whenever the criteria were incomplete, so
  the emergency gate never saw the P5 the blocker floor imposes;
* `_route_after_classify` checked for a pending question before the emergency,
  so a clarification round could park the alarm indefinitely, and its dead-end
  branch emitted `LIMIT_REACHED` on an unspent budget;
* `_record_assessment` rejected a criteria-less result outright at finalize.

These tests pin the fixed behaviour end to end.
"""

from __future__ import annotations

import pytest

from src.agents.graph import _route_after_classify
from src.agents.service import run_analysis
from src.agents.state import emergency_review_required, prospective_priority
from src.database.models.ticket_risk_assessment import TicketRiskAssessment
from src.models.enums import ClassificationStatus, EmergencyReviewStatus, Priority
from tests.test_agents.conftest import ScriptedLLM, classification, duplicate_judgement

CRITERIA_NAMES = [
    "human_safety",
    "property_spread",
    "essential_function",
    "affected_scope",
    "deterioration_speed",
]

#: A blocker, no category, and every criterion left unscored -- the payload the
#: contract allows and the pipeline used to lose.
BLOCKER_NO_CRITERIA = dict(
    category=None,
    text_category=None,
    image_category=None,
    human_safety=None,
    property_spread=None,
    essential_function=None,
    affected_scope=None,
    deterioration_speed=None,
    unknown_facts=list(CRITERIA_NAMES),
    criterion_evidence={name: [] for name in CRITERIA_NAMES},
    blockers=[
        {
            "code": "PERSON_TRAPPED_IN_ELEVATOR",
            "evidence": ["Cư dân nói đang kẹt trong cabin, cửa không mở."],
        }
    ],
    incident_facts=["người bị kẹt trong thang máy"],
    ai_reason="Cư dân xác nhận đang bị kẹt trong thang máy, chuông cứu hộ chưa có người trả lời.",
)


# ---------------------------------------------------------------------------
# Unit: prospective_priority falls back to the blocker floor.
# ---------------------------------------------------------------------------


def _state(**over):
    base = {
        "criteria": None,
        "blockers": [],
        "emergency_downgraded": False,
    }
    base.update(over)
    return base


@pytest.mark.parametrize(
    "code",
    [
        "FIRE_OR_SMOKE",
        "ELECTRIC_SHOCK_OR_LIVE_WIRE",
        "GAS_LEAK_OR_ASPHYXIATION",
        "SERIOUS_INJURY",
        "PERSON_TRAPPED_IN_ELEVATOR",
        "SOLE_ESCAPE_ROUTE_BLOCKED",
        "ONGOING_VIOLENCE",
    ],
)
def test_p5_blocker_alone_yields_p5(code):
    state = _state(criteria=None, blockers=[code])
    assert prospective_priority(state) == "P5"
    assert emergency_review_required(state) is True


def test_p4_blocker_alone_yields_its_own_floor():
    state = _state(criteria=None, blockers=["SEWAGE_OVERFLOW"])
    assert prospective_priority(state) == "P4"
    assert emergency_review_required(state) is False


def test_no_criteria_and_no_blocker_is_still_unscored():
    assert prospective_priority(_state()) is None


def test_unknown_blocker_code_is_not_a_priority():
    assert prospective_priority(_state(blockers=["NOT_A_REAL_CODE"])) is None


def test_a_downgraded_emergency_stays_down_even_with_a_blocker():
    state = _state(blockers=["FIRE_OR_SMOKE"], emergency_downgraded=True)
    assert emergency_review_required(state) is False


# ---------------------------------------------------------------------------
# Unit: routing after classify.
# ---------------------------------------------------------------------------


def _route_state(**over):
    base = {
        "technical_failure": None,
        "image_urls": [],
        "understandable": True,
        "criteria": None,
        "blockers": [],
        "emergency_downgraded": False,
        "emergency_warned": False,
        "requested_question": None,
        "category_id": None,
        "location_id": "loc-1",
        "tool_calls_used": 0,
        "ask_rounds_used": 0,
        "ask_elapsed_seconds": 0,
        "evidence_revision": 0,
        "duplicate_searched_revision": -1,
    }
    base.update(over)
    return base


def test_blocker_only_emergency_warns_before_asking():
    """Even with a question queued, a blocker-floored P5 goes to the alarm."""
    state = _route_state(
        blockers=["PERSON_TRAPPED_IN_ELEVATOR"],
        requested_question={"kind": "LOCATION_CONFIRMATION", "text": "?", "options": ["a", "b"]},
    )
    assert _route_after_classify(state) == "warn_emergency"


def test_readable_but_uncommitted_payload_is_a_technical_fault_not_a_limit():
    """No criteria, no category, no question, no blocker, nothing spent: the
    model produced an impossible payload. That is abort_technical, not
    LIMIT_REACHED."""
    assert _route_after_classify(_route_state()) == "abort_technical"


def test_a_genuinely_spent_budget_still_reads_as_limit_reached():
    state = _route_state(ask_rounds_used=3)
    assert _route_after_classify(state) == "exit_limit"


# ---------------------------------------------------------------------------
# Integration: the whole round.
# ---------------------------------------------------------------------------


def test_person_trapped_with_no_criteria_reaches_the_gate_as_p5(agent_world):
    ticket_id = agent_world.make_ticket(
        location_id=agent_world.lift,
        unit_id=agent_world.unit_a,
        description="Tôi đang bị kẹt trong thang máy, cửa không mở.",
    )
    run_analysis(
        ticket_id,
        llm=ScriptedLLM(
            [classification(**BLOCKER_NO_CRITERIA)],
            judgements=[duplicate_judgement(verdict="DIFFERENT_INCIDENT", reason="Không phải cùng sự cố.")],
        ),
    )

    ticket = agent_world.ticket(ticket_id)
    run = agent_world.latest_run(ticket_id)
    assert run.exit_reason == "EMERGENCY_REVIEW_REQUIRED"
    assert run.emergency_review_status == EmergencyReviewStatus.PENDING.value
    assert ticket.priority is Priority.P5
    assert ticket.classification_status is ClassificationStatus.MANUAL_REVIEW

    with agent_world.session_factory() as db:
        assessment = db.get(TicketRiskAssessment, ticket.current_risk_assessment_id)
        assert assessment.blocker_floor is Priority.P5
        assert assessment.blocker_codes == ["PERSON_TRAPPED_IN_ELEVATOR"]
        assert assessment.score_priority is Priority.P1
        # The five judgements are absent, and the record says so rather than
        # inventing zeros that look scored.
        assert sorted(assessment.unknown_facts) == sorted(CRITERIA_NAMES)


def test_emergency_result_without_criteria_or_blocker_is_still_refused():
    """The relaxation is scoped to blocker payloads: an emergency exit that
    carries neither the five criteria nor a blocker is still a contract
    violation."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from src.models.agent_schemas import AgentAnalysisResult, AgentExitReason, AgentToolUsage

    with pytest.raises(ValueError, match="five risk criteria or a blocker"):
        AgentAnalysisResult(
            ticket_id=uuid4(),
            analysis_session_id=uuid4(),
            exit_reason=AgentExitReason.EMERGENCY_REVIEW_REQUIRED,
            criteria=None,
            blockers=[],
            tool_usage=AgentToolUsage(total_tool_calls=0, ask_resident_rounds=0, ask_resident_elapsed_seconds=0),
            category_catalog_version="v1",
            model_version="test",
            analyzed_at=datetime.now(UTC),
        )
