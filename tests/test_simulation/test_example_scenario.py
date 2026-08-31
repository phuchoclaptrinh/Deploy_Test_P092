"""The sample scenario shipped in `examples/simulation/`.

It is the first thing anybody runs, so it is the first thing that tells them
what scale the system is on. It shipped on `SERVICE_HOURS_DRAFT_V1` with P1-P3
tickets for the whole of the v2 rollout, which meant the simulator demonstrated
the retired rubric on every first click.

Pinned here rather than left as documentation. A sample that has drifted from
the contract teaches the drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domain.risk_scoring import SCORE_THRESHOLDS
from src.domain.sla_clock import CURRENT_POLICY
from src.models.enums import Priority
from src.simulation.models import MANUAL_PRIORITY, POLICY_PRIORITIES
from src.simulation.validation import parse_scenario

EXAMPLE = Path("examples/simulation/scenario.json")

pytestmark = pytest.mark.skipif(not EXAMPLE.exists(), reason=f"{EXAMPLE} is not in this checkout")


@pytest.fixture(scope="module")
def scenario():
    return parse_scenario(json.loads(EXAMPLE.read_text(encoding="utf-8")))


def test_the_sample_runs_the_policy_production_runs(scenario):
    assert scenario.sla_policy is CURRENT_POLICY


def test_every_sample_ticket_is_on_a_band_that_policy_accepts(scenario):
    allowed = POLICY_PRIORITIES[scenario.sla_policy]
    for ticket in scenario.tickets:
        assert ticket.priority in allowed, f"{ticket.ticket_id} is {ticket.priority.value}"


def test_each_ticket_carries_the_band_its_own_score_falls_in(scenario):
    """`score_total` and `priority` are two statements about one ticket.

    A sample where they disagree is worse than one with no scores at all: a
    reader checking the bands against §2.1 finds the file contradicting the
    document it is meant to illustrate.
    """
    for ticket in scenario.tickets:
        expected = next(band for floor, band in SCORE_THRESHOLDS if ticket.score_total >= floor)
        assert ticket.priority is expected, (
            f"{ticket.ticket_id}: score_total {ticket.score_total} is {expected.value}, "
            f"but the row says {ticket.priority.value}"
        )


def test_the_sample_reaches_the_emergency_band(scenario):
    """Otherwise the one behaviour most worth demonstrating never fires."""
    emergency = MANUAL_PRIORITY[scenario.sla_policy]
    assert emergency is Priority.P5
    manual = [t.ticket_id for t in scenario.tickets if t.requires_manual_review(scenario.sla_policy)]
    assert manual, "no sample ticket reaches the manual-review band"


def test_the_sample_spans_more_than_one_band(scenario):
    """A single-band sample cannot show the queue ordering, which is the thing
    a capacity comparison is actually about."""
    assert len({ticket.priority for ticket in scenario.tickets}) >= 3
