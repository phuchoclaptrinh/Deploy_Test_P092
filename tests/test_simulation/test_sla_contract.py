"""Who decides a ticket's SLA duration: the policy, or the input.

The screen exists to answer "what happens to this dataset under a different SLA
policy". That sentence is only meaningful if the policy is what changes the
deadline -- and until this contract existed, it was not: a P1 carrying
`sla_minutes: 4320` under `SERVICE_HOURS_DRAFT_V1` was measured as 4320
**service** minutes, or 7.2 working days, under a policy whose entire purpose was
to keep P1 at three. The number was right, the units were wrong, and nothing on
the screen said so.

So: the policy supplies the duration. A scenario may still pin its own -- "what
if we promised P2 four hours?" is a real question -- but the ticket is marked
`INPUT_OVERRIDE` and the run says which tickets, and against what.
"""

from __future__ import annotations

import copy

import pytest

from src.domain.sla_clock import POLICY_SLA_MINUTES, SlaPolicy
from src.models.enums import Priority
from src.simulation.engine import run_comparison
from src.simulation.models import SlaDurationSource
from src.simulation.validation import parse_scenario
from tests.test_simulation.conftest import stamp

BASE = {
    "sla_policy": {"mode": "SERVICE_HOURS_DRAFT_V1"},
    "technicians": [{"technician_id": "KTV_01", "skills": ["plumbing"]}],
    "tickets": [
        {
            "ticket_id": "T001",
            "created_at": "2026-09-01T08:00:00+07:00",
            "floor": 3,
            "unit": "0301",
            "issue_type": "WATER",
            "priority": "P1",
            "repair_minutes": 60,
            "required_skill": "plumbing",
            "need_hand_categorized": False,
        }
    ],
}


def document(*, policy: str | None = None, **ticket_overrides):
    doc = copy.deepcopy(BASE)
    if policy is not None:
        doc["sla_policy"] = {"mode": policy}
    doc["tickets"][0].update(ticket_overrides)
    return doc


def only_ticket(doc):
    return parse_scenario(doc).tickets[0]


# ---------------------------------------------------------------------------
# The policy is the default.
# ---------------------------------------------------------------------------


def test_an_omitted_duration_comes_from_the_policy():
    ticket = only_ticket(document())
    assert ticket.sla_minutes == POLICY_SLA_MINUTES[SlaPolicy.SERVICE_HOURS_DRAFT_V1][Priority.P1]
    assert ticket.sla_minutes == 1800
    assert ticket.sla_duration_source is SlaDurationSource.POLICY


def test_the_same_dataset_gets_a_different_duration_under_the_other_policy():
    """The point of the whole feature, in one assertion: change nothing but the
    policy and the P1 deadline changes, because the policy owns it."""
    service = only_ticket(document(policy="SERVICE_HOURS_DRAFT_V1"))
    wall = only_ticket(document(policy="WALL_CLOCK_V1"))

    assert service.sla_minutes == 1800
    assert wall.sla_minutes == 4320
    assert service.sla_duration_source is wall.sla_duration_source is SlaDurationSource.POLICY


@pytest.mark.parametrize(
    ("policy", "priority", "expected"),
    [
        ("WALL_CLOCK_V1", "P1", 4320),
        ("WALL_CLOCK_V1", "P2", 180),
        ("WALL_CLOCK_V1", "P3", 5),
        ("SERVICE_HOURS_DRAFT_V1", "P1", 1800),
        ("SERVICE_HOURS_DRAFT_V1", "P2", 180),
        ("SERVICE_HOURS_DRAFT_V1", "P3", 5),
    ],
)
def test_every_cell_of_the_policy_table_is_reachable_from_the_input(policy, priority, expected):
    assert only_ticket(document(policy=policy, priority=priority)).sla_minutes == expected


def test_a_supplied_duration_that_agrees_with_the_policy_is_not_an_override():
    """An exported scenario writes every field out. Marking that `INPUT_OVERRIDE`
    would fill the screen with warnings about nothing."""
    ticket = only_ticket(document(sla_minutes=1800))
    assert ticket.sla_duration_source is SlaDurationSource.POLICY


# ---------------------------------------------------------------------------
# An override is allowed, and is never silent.
# ---------------------------------------------------------------------------


def test_a_disagreeing_duration_is_honoured_and_marked():
    ticket = only_ticket(document(sla_minutes=240))
    assert ticket.sla_minutes == 240
    assert ticket.sla_duration_source is SlaDurationSource.INPUT_OVERRIDE


def test_the_accident_the_contract_was_written_for_is_now_visible():
    """A P1 carrying production's 4320 while running on the service clock. The
    number is still honoured -- and it is still 7.2 working days -- but the run
    now says which ticket did it and what the policy would have said."""
    doc = document(sla_minutes=4320)
    ticket = only_ticket(doc)
    assert ticket.sla_duration_source is SlaDurationSource.INPUT_OVERRIDE
    # 4320 service minutes from 08:00 Tuesday: 7.2 ten-hour days.
    assert stamp(ticket.sla_due_at(SlaPolicy.SERVICE_HOURS_DRAFT_V1)) == "2026-09-08 10:00"
    # What the policy would have promised.
    assert stamp(
        only_ticket(document()).sla_due_at(SlaPolicy.SERVICE_HOURS_DRAFT_V1)
    ) == "2026-09-03 18:00"


def test_the_warning_names_the_ticket_the_priority_and_both_numbers():
    doc = document(sla_minutes=240, priority="P2")
    run = run_comparison(parse_scenario(doc))

    notes = [note for note in run.warnings if "hạn SLA tự đặt" in note]
    assert len(notes) == 1
    assert "T001" in notes[0]
    assert "240" in notes[0]
    assert "180" in notes[0]
    assert "SERVICE_HOURS_DRAFT_V1" in notes[0]


def test_a_run_with_no_override_carries_no_override_warning():
    run = run_comparison(parse_scenario(document()))
    assert not [note for note in run.warnings if "hạn SLA tự đặt" in note]


def test_many_overrides_are_summarised_rather_than_listed_forever():
    """Naming every one of five hundred tickets would push the draft-policy
    warning off the screen, which is the warning that matters most."""
    doc = copy.deepcopy(BASE)
    doc["tickets"] = [
        {**copy.deepcopy(BASE["tickets"][0]), "ticket_id": f"T{i:03d}", "sla_minutes": 999}
        for i in range(8)
    ]
    run = run_comparison(parse_scenario(doc))

    note = next(note for note in run.warnings if "hạn SLA tự đặt" in note)
    assert note.startswith("8 ticket")
    assert "và 3 ticket khác" in note


def test_the_source_travels_onto_every_result_row():
    """The warning is a summary; the row is where a coordinator checks one
    ticket, so both flows carry it on every row."""
    run = run_comparison(parse_scenario(document(sla_minutes=240)))

    for result in (run.old_app, run.new_app):
        outcome = result.tickets[0]
        assert outcome.sla_minutes == 240
        assert outcome.sla_duration_source is SlaDurationSource.INPUT_OVERRIDE


def test_a_zero_or_negative_override_is_still_refused():
    """Permissive about the number, not about the type."""
    from src.simulation.validation import SimulationInputError

    with pytest.raises(SimulationInputError):
        parse_scenario(document(sla_minutes=0))
    with pytest.raises(SimulationInputError):
        parse_scenario(document(sla_minutes="180"))
