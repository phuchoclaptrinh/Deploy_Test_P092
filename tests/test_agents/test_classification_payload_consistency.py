"""The model boundary: what `UnifiedClassification` will and will not accept.

Two rules, both of which used to be missing, and both of which are about the
same thing -- a payload that is internally consistent but says something the
rest of the pipeline cannot act on.

**A criterion is scored or it is unknown, never both.** The reported payload was
`affected_scope: 0` with `affected_scope` in `unknown_facts` and
`question_kind: "NONE"`. Every field is individually valid. Together they mean
the round finishes -- five scores is a complete classification -- the ticket is
published on a scope of 0, and the question the Agent said it needed is never
asked. The stored assessment then records, in `unknown_facts`, a criterion the
Agent declared it could not establish and scored anyway.

**Every blocker carries its own evidence.** A blocker sets a floor with no score
behind it, so what the Agent saw is the only thing a reviewer can check. The old
shape was two parallel lists, and the old rule was "if there are blockers there
must be some evidence somewhere" -- three codes backed by one line passed, and
nobody could say which line belonged to which floor.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.llm_client import UnifiedClassification
from tests.test_agents.conftest import classification

CRITERIA = ("human_safety", "property_spread", "essential_function", "affected_scope", "deterioration_speed")


def _payload(**overrides) -> UnifiedClassification:
    return classification(**overrides)


# ---------------------------------------------------------------------------
# unknown_facts and the five scores are one fact, spelled two ways.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("criterion", CRITERIA)
def test_a_criterion_cannot_be_both_scored_and_unknown(criterion):
    """The reported payload, for every criterion it could have been about."""
    with pytest.raises(ValidationError, match="both scored and unknown"):
        _payload(unknown_facts=[criterion])


@pytest.mark.parametrize("criterion", CRITERIA)
def test_a_criterion_with_no_score_must_say_so(criterion):
    """The other direction. A silent gap reads as a missing field rather than a
    declared one, and nothing downstream knows to ask about it."""
    with pytest.raises(ValidationError, match="must be named in unknown_facts"):
        _payload(**{criterion: None}, unknown_facts=[])


@pytest.mark.parametrize("criterion", CRITERIA)
def test_a_declared_gap_with_a_matching_question_is_accepted(criterion):
    kind = {
        "human_safety": "SAFETY_CONFIRMATION",
        "property_spread": "SPREAD_CONFIRMATION",
        "essential_function": "ESSENTIAL_FUNCTION_CONFIRMATION",
        "affected_scope": "AFFECTED_SCOPE_CONFIRMATION",
        "deterioration_speed": "DETERIORATION_CONFIRMATION",
    }[criterion]
    payload = _payload(
        **{criterion: None},
        unknown_facts=[criterion],
        question_kind=kind,
        question_text="Hiện tại nước còn đang chảy không?",
    )
    assert getattr(payload, criterion) is None
    assert payload.criteria is None


def test_a_declared_gap_with_no_question_cannot_finish_the_round():
    """Leaving the gap open and asking nothing is the dead end the graph turns
    into LIMIT_REACHED. The contract refuses it before it gets that far."""
    with pytest.raises(ValidationError):
        _payload(affected_scope=None, unknown_facts=["affected_scope"], question_kind="NONE")


def test_a_complete_payload_still_validates():
    payload = _payload()
    assert payload.unknown_facts == []
    assert payload.criteria == {
        "human_safety": 1,
        "property_spread": 1,
        "essential_function": 1,
        "affected_scope": 0,
        "deterioration_speed": 1,
    }


# ---------------------------------------------------------------------------
# Blockers carry their own evidence.
# ---------------------------------------------------------------------------


def test_a_blocker_without_evidence_cannot_be_expressed():
    with pytest.raises(ValidationError):
        _payload(blockers=[{"code": "FIRE_OR_SMOKE", "evidence": []}])


def test_a_blocker_with_only_blank_evidence_is_refused():
    with pytest.raises(ValidationError):
        _payload(blockers=[{"code": "FIRE_OR_SMOKE", "evidence": ["  "]}])


def test_two_blockers_keep_their_evidence_apart():
    payload = _payload(
        blockers=[
            {"code": "FIRE_OR_SMOKE", "evidence": ["Khói bốc ra từ hộp kỹ thuật."]},
            {"code": "SERIOUS_INJURY", "evidence": ["Một người bị bỏng tay."]},
        ]
    )
    assert payload.blocker_codes == ["FIRE_OR_SMOKE", "SERIOUS_INJURY"]
    assert payload.evidence["blockers"] == {
        "FIRE_OR_SMOKE": ["Khói bốc ra từ hộp kỹ thuật."],
        "SERIOUS_INJURY": ["Một người bị bỏng tay."],
    }


def test_a_repeated_blocker_code_is_refused():
    with pytest.raises(ValidationError, match="repeat a code"):
        _payload(
            blockers=[
                {"code": "FIRE_OR_SMOKE", "evidence": ["Khói."]},
                {"code": "FIRE_OR_SMOKE", "evidence": ["Vẫn khói."]},
            ]
        )


# ---------------------------------------------------------------------------
# Criterion evidence is attributed, not broadcast.
# ---------------------------------------------------------------------------


def test_evidence_goes_only_where_the_model_put_it():
    """The old derivation copied every observed fact into every criterion scored
    above zero, so one sentence appeared as the reason for four numbers."""
    payload = _payload(
        incident_facts=["thang máy dừng giữa tầng", "có người bên trong"],
        criterion_evidence={
            "human_safety": ["có người bên trong"],
            "property_spread": [],
            "essential_function": ["thang máy dừng giữa tầng"],
            "affected_scope": [],
            "deterioration_speed": [],
        },
    )
    evidence = payload.evidence
    assert evidence["human_safety"] == ["có người bên trong"]
    assert evidence["essential_function"] == ["thang máy dừng giữa tầng"]
    # Scored 1 in the default payload, and still empty: a score with nothing
    # cited is a different claim from a score with the whole report cited.
    assert evidence["property_spread"] == []
    assert evidence["deterioration_speed"] == []


def test_an_empty_criterion_evidence_list_is_allowed():
    """"Nothing in the report spoke to this" is a legitimate reason for a 0, and
    it is not the same as "I could not tell"."""
    payload = _payload(criterion_evidence={})
    assert all(payload.evidence[name] == [] for name in CRITERIA)
    assert payload.unknown_facts == []
