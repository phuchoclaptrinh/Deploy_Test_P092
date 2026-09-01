"""The at-risk agent: what it may return, and what happens when it cannot (§7).

The agent is advisory over a decision the scheduler has already made a
defensible choice for. So the two properties worth testing are not "does it pick
well" -- that is a model-quality question -- but:

* it **cannot widen its candidate set**. §3 says the agent may never select a
  technician who fails a hard constraint, and the only way to guarantee that
  against a model is to refuse the answer rather than repair it;
* a **failure is a fact, not an outcome**. Timeout, transport error and an
  unusable answer all raise the same exception, because the dispatcher's
  fallback must not depend on telling them apart.

No network is touched: the model is a stub, which is the point of injecting it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from src.config import Settings
from src.dispatch.agent.schemas import (
    AtRiskAssignment,
    AtRiskBatchDecision,
    AtRiskBatchRequest,
    AtRiskDecisionError,
    AtRiskTicket,
    CandidateDispatchHistory,
)
from src.dispatch.agent.service import AtRiskAgent

NOW = datetime(2026, 8, 26, 3, 0, tzinfo=UTC)
ALLOWED = UUID(int=1)
FORBIDDEN = UUID(int=99)


def settings(**overrides) -> Settings:
    return Settings(app_env="test", at_risk_agent_model="stub-model", **overrides)


def request_for(*refs: str, eligible: list[UUID] | None = None) -> AtRiskBatchRequest:
    return AtRiskBatchRequest(
        batch_id=uuid4(),
        current_time=NOW,
        tickets=[
            AtRiskTicket(
                ticket_ref=ref,
                category_code="WATER",
                priority="P2",
                score=40.0,
                submitted_at=NOW,
                p80_working_seconds=18000,
                eligible_technician_ids=eligible if eligible is not None else [ALLOWED],
            )
            for ref in refs
        ],
        candidates=[CandidateDispatchHistory(technician_id=ALLOWED)],
    )


class StubModel:
    def __init__(self, answer=None, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.answer


def test_a_valid_pick_comes_back_with_its_reason():
    model = StubModel(
        AtRiskBatchDecision(
            assignments=[AtRiskAssignment(ticket_ref="a", technician_id=ALLOWED, reason="Ít việc nhất.")]
        )
    )
    outcomes = AtRiskAgent(llm=model, settings=settings()).decide(request_for("a"))

    assert outcomes["a"].technician_id == ALLOWED
    assert outcomes["a"].reason == "Ít việc nhất."
    assert outcomes["a"].model_name == "stub-model"


def test_the_whole_batch_goes_in_one_call():
    """§7: one call per micro-batch, not one per ticket."""
    model = StubModel(
        AtRiskBatchDecision(
            assignments=[
                AtRiskAssignment(ticket_ref="a", technician_id=ALLOWED, reason="x"),
                AtRiskAssignment(ticket_ref="b", technician_id=ALLOWED, reason="y"),
            ]
        )
    )
    outcomes = AtRiskAgent(llm=model, settings=settings()).decide(request_for("a", "b"))

    assert model.calls == 1
    assert set(outcomes) == {"a", "b"}


def test_a_technician_outside_the_eligible_set_is_discarded():
    """§3: the agent may never select someone who failed a hard constraint.

    Discarded rather than corrected -- repairing the answer would mean the
    backend inventing a decision and recording it as the agent's.
    """
    model = StubModel(
        AtRiskBatchDecision(
            assignments=[
                AtRiskAssignment(ticket_ref="a", technician_id=FORBIDDEN, reason="nope"),
                AtRiskAssignment(ticket_ref="b", technician_id=ALLOWED, reason="ok"),
            ]
        )
    )
    outcomes = AtRiskAgent(llm=model, settings=settings()).decide(request_for("a", "b"))

    assert "a" not in outcomes
    assert outcomes["b"].technician_id == ALLOWED


def test_an_answer_for_an_unknown_ticket_is_discarded():
    model = StubModel(
        AtRiskBatchDecision(
            assignments=[
                AtRiskAssignment(ticket_ref="invented", technician_id=ALLOWED, reason="?"),
                AtRiskAssignment(ticket_ref="a", technician_id=ALLOWED, reason="ok"),
            ]
        )
    )
    outcomes = AtRiskAgent(llm=model, settings=settings()).decide(request_for("a"))
    assert set(outcomes) == {"a"}


def test_a_duplicate_answer_keeps_the_first():
    model = StubModel(
        AtRiskBatchDecision(
            assignments=[
                AtRiskAssignment(ticket_ref="a", technician_id=ALLOWED, reason="first"),
                AtRiskAssignment(ticket_ref="a", technician_id=ALLOWED, reason="second"),
            ]
        )
    )
    outcomes = AtRiskAgent(llm=model, settings=settings()).decide(request_for("a"))
    assert outcomes["a"].reason == "first"


def test_nothing_usable_raises_rather_than_returning_empty():
    """An empty result would read as "the agent chose nobody, deliberately"."""
    model = StubModel(
        AtRiskBatchDecision(assignments=[AtRiskAssignment(ticket_ref="a", technician_id=FORBIDDEN, reason="x")])
    )
    with pytest.raises(AtRiskDecisionError) as exc:
        AtRiskAgent(llm=model, settings=settings()).decide(request_for("a"))
    assert exc.value.code == "AGENT_NO_VALID_PICK"


def test_a_transport_failure_is_one_fact():
    model = StubModel(error=RuntimeError("connection reset"))
    with pytest.raises(AtRiskDecisionError) as exc:
        AtRiskAgent(llm=model, settings=settings()).decide(request_for("a"))
    assert exc.value.code == "AGENT_ERROR"


def test_a_slow_model_times_out_inside_the_bound():
    """§8: bounded timeouts. A hung provider must not hold a dispatch pass."""
    import time

    class Hanging:
        def invoke(self, _messages):
            time.sleep(5)

    with pytest.raises(AtRiskDecisionError) as exc:
        AtRiskAgent(llm=Hanging(), settings=settings(at_risk_agent_timeout_seconds=1.0)).decide(
            request_for("a")
        )
    assert exc.value.code == "AGENT_TIMEOUT"


def test_a_disabled_agent_refuses_rather_than_pretending():
    model = StubModel(AtRiskBatchDecision(assignments=[]))
    with pytest.raises(AtRiskDecisionError) as exc:
        AtRiskAgent(llm=model, settings=settings(at_risk_agent_enabled=False)).decide(request_for("a"))
    assert exc.value.code == "AGENT_DISABLED"
    assert model.calls == 0


def test_an_unconfigured_model_refuses_before_calling_anything():
    model = StubModel(AtRiskBatchDecision(assignments=[]))
    bare = Settings(app_env="test", at_risk_agent_model="", model_name="")
    with pytest.raises(AtRiskDecisionError) as exc:
        AtRiskAgent(llm=model, settings=bare).decide(request_for("a"))
    assert exc.value.code == "AGENT_UNCONFIGURED"


def test_an_empty_batch_never_reaches_the_model():
    """A SAFE-only pass must not call the agent at all (§7)."""
    model = StubModel(AtRiskBatchDecision(assignments=[]))
    request = AtRiskBatchRequest(batch_id=uuid4(), current_time=NOW, tickets=[], candidates=[])

    assert AtRiskAgent(llm=model, settings=settings()).decide(request) == {}
    assert model.calls == 0


def test_the_prompt_carries_the_eligible_ids_and_no_ticket_text():
    """What the model is shown is the candidate set, not the report."""
    captured: list[list[tuple[str, str]]] = []

    class Recording:
        def invoke(self, messages):
            captured.append(messages)
            return AtRiskBatchDecision(
                assignments=[AtRiskAssignment(ticket_ref="a", technician_id=ALLOWED, reason="ok")]
            )

    AtRiskAgent(llm=Recording(), settings=settings()).decide(request_for("a"))
    prompt = "".join(part for _role, part in captured[0])

    assert str(ALLOWED) in prompt
    assert "WATER" in prompt
    # The request model has no field capable of carrying resident text, so this
    # asserts the shape rather than a filter.
    assert "eligible_technician_ids" in prompt
