"""One strict JSON scenario in, and everything else refused.

Half of these tests assert that something is *rejected*. That is the point: each
leniency this parser could offer is a way for a run to answer a question nobody
asked -- a `"false"` that reads as true, a timestamp seven hours out, a typo'd
key silently replaced by a default while the screen shows the ignored value.
"""

from __future__ import annotations

import copy

import pytest

from src.dispatch.durations import p80_for_code
from src.domain.sla_clock import CURRENT_POLICY, SlaPolicy
from src.models.enums import Priority
from src.simulation.validation import (
    MAX_TECHNICIANS,
    MAX_TICKETS,
    SimulationInputError,
    parse_datetime,
    parse_scenario,
)
from tests.test_simulation.conftest import local

TICKET = {
    "ticket_id": "T001",
    "created_at": "2026-09-01T17:00:00+07:00",
    "floor": 8,
    "unit": "0801",
    "issue_type": "WATER",
    "priority": "P2",
    "sla_minutes": 180,
    "repair_minutes": 90,
    "required_skill": "plumbing",
    "need_hand_categorized": False,
    "score_total": 45,
}

TECHNICIAN = {
    "technician_id": "KTV_01",
    "skills": ["plumbing", "electrical"],
    "start_floor": 1,
    "is_active": True,
    "is_available": True,
}

SCENARIO = {
    "scenario_name": "Tòa nhà 30 tầng — tuần mẫu",
    "building": {"floor_count": 30, "units_per_floor": 7},
    "sla_policy": {"mode": "SERVICE_HOURS_DRAFT_V1"},
    "settings": {
        "travel_base_minutes": 3,
        "travel_per_floor_minutes": 1,
        "micro_batch_interval_ms": 750,
        "micro_batch_size": 20,
        "simulation_horizon_days": 14,
        "old_app": {"manual_category_minutes": 10, "manual_dispatch_minutes": 8},
        "new_app": {"ai_classification_minutes": 1, "manual_review_minutes": 10},
    },
    "technicians": [TECHNICIAN],
    "tickets": [TICKET],
}


def scenario_with(**overrides):
    document = copy.deepcopy(SCENARIO)
    document.update(overrides)
    return document


def with_ticket(**overrides):
    ticket = {**copy.deepcopy(TICKET), **overrides}
    return scenario_with(tickets=[ticket])


def without_ticket_key(key: str):
    ticket = copy.deepcopy(TICKET)
    ticket.pop(key)
    return scenario_with(tickets=[ticket])


def with_technician(**overrides):
    return scenario_with(technicians=[{**copy.deepcopy(TECHNICIAN), **overrides}])


# ----------------------------------------------------------------------------
# The reference contract parses.
# ----------------------------------------------------------------------------


def test_the_reference_scenario_parses():
    scenario = parse_scenario(SCENARIO)
    assert scenario.scenario_name == "Tòa nhà 30 tầng — tuần mẫu"
    assert scenario.sla_policy is SlaPolicy.SERVICE_HOURS_DRAFT_V1
    assert scenario.building.floor_count == 30
    assert scenario.settings.simulation_horizon_days == 14

    ticket = scenario.tickets[0]
    assert ticket.priority is Priority.P2
    assert ticket.sla_minutes == 180
    assert ticket.created_at == local("2026-09-01T17:00")
    assert ticket.need_hand_categorized is False
    assert ticket.repair_minutes_source == "INPUT"

    technician = scenario.technicians[0]
    assert technician.skills == frozenset({"plumbing", "electrical"})
    assert technician.has_skill("PLUMBING")


def test_sla_minutes_expresses_the_five_minute_emergency_exactly():
    """The reason the contract is in minutes rather than hours."""
    scenario = parse_scenario(with_ticket(priority="P3", sla_minutes=5))
    assert scenario.tickets[0].sla_minutes == 5


def test_an_absent_settings_block_is_the_documented_default():
    scenario = parse_scenario(scenario_with(settings={}))
    assert scenario.settings.travel_base_minutes == 3
    assert scenario.settings.old_app.manual_category_minutes == 10
    assert scenario.settings.new_app.ai_classification_minutes == 1


def test_an_absent_sla_policy_is_whatever_production_runs():
    """Production's behaviour is the default, so a scenario that says nothing
    about the clock is not silently run under some other one.

    Asserted against `CURRENT_POLICY` rather than against a named policy, which
    is the whole point. This test used to name `WALL_CLOCK_V1`, and when the
    system moved to the risk scale it kept passing while the default it was
    guarding became the thing it was written to prevent: an unspecified scenario
    silently scored under a rubric production had stopped using.
    """
    document = copy.deepcopy(SCENARIO)
    document.pop("sla_policy")
    assert parse_scenario(document).sla_policy is CURRENT_POLICY


def test_a_scenario_that_names_a_v1_policy_still_gets_it():
    """The default moved; the policies did not."""
    for policy in (SlaPolicy.WALL_CLOCK_V1, SlaPolicy.SERVICE_HOURS_DRAFT_V1):
        document = copy.deepcopy(SCENARIO)
        document["sla_policy"] = {"mode": policy.value}
        assert parse_scenario(document).sla_policy is policy


def test_a_missing_duration_falls_back_to_the_internal_p80():
    scenario = parse_scenario(without_ticket_key("repair_minutes"))
    ticket = scenario.tickets[0]
    assert ticket.repair_minutes == int(p80_for_code("WATER").total_seconds() // 60)
    assert ticket.repair_minutes_source == "P80_FALLBACK"


def test_a_missing_score_is_zero_not_an_error():
    """The score only breaks ties, so a scenario without it falls through to
    arrival order rather than being refused."""
    assert parse_scenario(without_ticket_key("score_total")).tickets[0].score_total == 0


def test_a_technician_without_flags_is_assumed_to_be_working():
    technician = parse_scenario(
        scenario_with(technicians=[{"technician_id": "KTV_09", "skills": ["hvac"]}])
    ).technicians[0]
    assert technician.is_active and technician.is_available and technician.start_floor == 1


# ----------------------------------------------------------------------------
# CSV and the string-typed world are gone.
# ----------------------------------------------------------------------------


def test_csv_is_no_longer_accepted_anywhere():
    """The input is one JSON document. There is no CSV path left to drift."""
    import src.simulation.validation as validation

    for removed in ("rows_from_csv", "rows_from_text", "tickets_from_text", "technicians_from_text"):
        assert not hasattr(validation, removed), f"{removed} should be gone with the CSV path"
    with pytest.raises(SimulationInputError):
        parse_scenario("ticket_id,created_at\nT001,2026-09-01T08:00:00+07:00")


def test_a_semicolon_skill_string_is_refused():
    """`"plumbing;electrical"` and `["plumbing", "electrical"]` look the same to
    a person and different to a parser."""
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(with_technician(skills="plumbing;electrical"))
    assert error.value.field == "skills"


@pytest.mark.parametrize("value", ["Yes", "No", "true", 1, 0])
def test_a_non_boolean_need_hand_categorized_is_refused(value):
    """In a CSV these were unavoidable; in JSON they mean the producer did not
    know the type, and guessing is how a run flips a flag silently."""
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(with_ticket(need_hand_categorized=value))
    assert error.value.field == "need_hand_categorized"


@pytest.mark.parametrize("value", ["Yes", "no", 1])
def test_a_non_boolean_availability_is_refused(value):
    with pytest.raises(SimulationInputError):
        parse_scenario(with_technician(is_available=value))


@pytest.mark.parametrize("value", ["8", "8.0", True])
def test_a_string_typed_number_is_refused(value):
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(with_ticket(floor=value))
    assert error.value.field == "floor"


@pytest.mark.parametrize("value", [3, "3", "P4", "", "HIGH"])
def test_a_priority_that_is_not_p1_p2_p3_is_refused(value):
    """A bare digit is refused: `3` could be a priority band or an index, and
    the wrong reading silently reclassifies the report."""
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(with_ticket(priority=value))
    assert error.value.field == "priority"


def test_priority_case_is_the_one_leniency_kept():
    """`"p2"` is accepted. Case is not a type ambiguity -- unlike `"Yes"` for a
    boolean or a bare `3` for a band, there is exactly one thing it can mean.
    """
    assert parse_scenario(with_ticket(priority="p2")).tickets[0].priority is Priority.P2


# ----------------------------------------------------------------------------
# Timestamps must carry their own offset.
# ----------------------------------------------------------------------------


def test_a_timestamp_without_a_timezone_is_refused():
    """Reading it as UTC shifts every report seven hours into the night;
    reading it as Vietnam time is a guess about a producer we have never met.
    Either way the deadlines in the result would be wrong by a shift."""
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(with_ticket(created_at="2026-09-01T17:00:00"))
    assert error.value.field == "created_at"
    assert "múi giờ" in error.value.message


def test_a_utc_z_suffix_is_accepted():
    scenario = parse_scenario(with_ticket(created_at="2026-09-01T10:00:00Z"))
    assert scenario.tickets[0].created_at == local("2026-09-01T17:00")


def test_an_unparsable_timestamp_is_refused():
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(with_ticket(created_at="01/09/2026 5pm"))
    assert error.value.field == "created_at"


def test_parse_datetime_refuses_a_non_string():
    with pytest.raises(SimulationInputError):
        parse_datetime(20260901, field="created_at")


# ----------------------------------------------------------------------------
# Unknown keys.
# ----------------------------------------------------------------------------


def test_an_unknown_scenario_key_is_refused():
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(scenario_with(config={}))
    assert "config" in error.value.message


def test_an_unknown_settings_key_is_refused():
    """`travel_per_floor` instead of `travel_per_floor_minutes` would otherwise
    run with the default while the screen shows the value that was ignored."""
    settings = {**copy.deepcopy(SCENARIO["settings"]), "travel_per_floor": 2}
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(scenario_with(settings=settings))
    assert "travel_per_floor" in error.value.message


def test_an_unknown_ticket_key_is_refused():
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(with_ticket(sla_hours=3))
    assert error.value.index == 0
    assert "sla_hours" in error.value.message


def test_an_unknown_technician_key_is_refused():
    with pytest.raises(SimulationInputError):
        parse_scenario(with_technician(break_minutes=60))


# ----------------------------------------------------------------------------
# Ranges, duplicates and limits.
# ----------------------------------------------------------------------------


def test_a_missing_required_field_names_the_field_and_the_row():
    document = scenario_with(tickets=[copy.deepcopy(TICKET), copy.deepcopy(TICKET)])
    document["tickets"][1]["ticket_id"] = "T002"
    document["tickets"][1].pop("required_skill")
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(document)
    assert (error.value.field, error.value.index) == ("required_skill", 1)


def test_a_floor_above_the_building_is_refused():
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(with_ticket(floor=31))
    assert error.value.field == "floor"


def test_a_start_floor_above_the_building_is_refused():
    with pytest.raises(SimulationInputError):
        parse_scenario(with_technician(start_floor=45))


@pytest.mark.parametrize(("field", "value"), [("sla_minutes", 0), ("repair_minutes", 0), ("floor", 0)])
def test_non_positive_quantities_are_refused(field, value):
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(with_ticket(**{field: value}))
    assert error.value.field == field


def test_a_duplicated_ticket_id_is_refused():
    """Two rows claiming to be the same report make every per-ticket number
    ambiguous, and the screen is per-ticket."""
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(scenario_with(tickets=[TICKET, TICKET]))
    assert error.value.field == "ticket_id"


def test_a_duplicated_technician_id_is_refused():
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(scenario_with(technicians=[TECHNICIAN, TECHNICIAN]))
    assert error.value.field == "technician_id"


def test_an_empty_dataset_is_refused():
    with pytest.raises(SimulationInputError):
        parse_scenario(scenario_with(tickets=[]))
    with pytest.raises(SimulationInputError):
        parse_scenario(scenario_with(technicians=[]))


def test_the_ticket_cap_protects_the_api():
    rows = [{**TICKET, "ticket_id": f"T{index:04d}"} for index in range(MAX_TICKETS + 1)]
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(scenario_with(tickets=rows))
    assert str(MAX_TICKETS) in error.value.message


def test_exactly_the_ticket_cap_is_allowed():
    rows = [{**TICKET, "ticket_id": f"T{index:04d}"} for index in range(MAX_TICKETS)]
    assert len(parse_scenario(scenario_with(tickets=rows)).tickets) == MAX_TICKETS


def test_the_technician_cap_protects_the_api():
    rows = [{**TECHNICIAN, "technician_id": f"KTV_{index:04d}"} for index in range(MAX_TECHNICIANS + 1)]
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(scenario_with(technicians=rows))
    assert str(MAX_TECHNICIANS) in error.value.message


def test_an_unknown_sla_policy_is_refused():
    with pytest.raises(SimulationInputError) as error:
        parse_scenario(scenario_with(sla_policy={"mode": "SERVICE_HOURS_V2"}))
    assert error.value.field == "sla_policy.mode"


def test_a_technician_with_no_skills_is_refused():
    with pytest.raises(SimulationInputError):
        parse_scenario(with_technician(skills=[]))


def test_a_scenario_that_is_not_an_object_is_refused():
    with pytest.raises(SimulationInputError):
        parse_scenario([TICKET])


# ----------------------------------------------------------------------------
# Kỹ thuật viên bị loại trừ khỏi một ticket.
# ----------------------------------------------------------------------------


def test_excluded_technicians_are_optional_and_default_to_nobody():
    """Không ghi trường này nghĩa là không loại ai, không phải loại tất cả."""
    assert parse_scenario(SCENARIO).tickets[0].excluded_technician_ids == frozenset()


def test_excluded_technicians_are_read_as_a_set_of_ids():
    scenario = parse_scenario(with_ticket(excluded_technician_ids=["KTV_02", "KTV_07"]))
    assert scenario.tickets[0].excluded_technician_ids == frozenset({"KTV_02", "KTV_07"})


def test_a_semicolon_separated_exclusion_string_is_refused():
    """Cùng lý do với `skills`: "KTV_02;KTV_07" trông giống hai mã với người đọc
    và giống một mã với bộ phân tích."""
    with pytest.raises(SimulationInputError):
        parse_scenario(with_ticket(excluded_technician_ids="KTV_02;KTV_07"))


def test_a_non_string_technician_id_in_the_exclusion_list_is_refused():
    with pytest.raises(SimulationInputError):
        parse_scenario(with_ticket(excluded_technician_ids=[7]))


# ----------------------------------------------------------------------------
# Hình dạng lượt gom.
# ----------------------------------------------------------------------------


def test_the_batch_shape_defaults_to_the_deployed_cadence():
    from src.config import get_settings

    config = get_settings()
    settings = parse_scenario(scenario_with(settings={})).settings

    assert settings.micro_batch_interval_ms == config.dispatch_micro_batch_interval_ms
    assert settings.micro_batch_size == config.dispatch_micro_batch_size


def test_a_scenario_may_narrow_the_batch():
    settings = parse_scenario(scenario_with(settings={"micro_batch_size": 1})).settings
    assert settings.micro_batch_size == 1


@pytest.mark.parametrize("field", ["micro_batch_size", "micro_batch_interval_ms"])
def test_a_zero_batch_knob_is_refused(field):
    """Một lượt gom rỗng, hoặc một nhịp bằng không, là một bộ điều phối không
    bao giờ chạy — và một vòng lặp không bao giờ kết thúc."""
    with pytest.raises(SimulationInputError):
        parse_scenario(scenario_with(settings={field: 0}))


def test_the_retired_settings_keys_are_refused_rather_than_ignored():
    """`safety_buffer_minutes` và `current_app` đã biến mất khỏi hợp đồng. Một
    kịch bản cũ phải nhận lỗi có định vị, chứ không phải chạy im lặng với mặc
    định trong khi màn hình hiển thị con số đã bị bỏ qua."""
    for key in ("safety_buffer_minutes", "current_app"):
        with pytest.raises(SimulationInputError) as error:
            parse_scenario(scenario_with(settings={key: 30}))
        assert key in str(error.value)
