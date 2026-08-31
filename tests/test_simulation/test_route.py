"""The HTTP layer: who may call it, what it refuses, and what it answers with.

The behaviour behind the endpoint is covered in `test_new_app_policy.py` and
`test_sla_metrics.py`. What is checked here is only what the route itself
decides -- the path, the guard, the envelope, the single-scenario body, the
shape of the response, and that a bad row comes back located rather than as a
500.
"""

from __future__ import annotations

import copy
import inspect

import pytest

from src.main import app
from src.models.api.simulation import ComparisonResponse, SimulationRunResponse
from src.simulation.engine import run_comparison
from src.simulation.validation import parse_scenario
from tests.test_simulation.conftest import scenario, technician, ticket

PATH = "/api/v1/coordinator/simulation/run"

SCENARIO = {
    "scenario_name": "Tòa nhà 30 tầng — tuần mẫu",
    "building": {"floor_count": 30, "units_per_floor": 7},
    "sla_policy": {"mode": "SERVICE_HOURS_DRAFT_V1"},
    "settings": {"travel_per_floor_minutes": 2},
    "technicians": [{"technician_id": "KTV_01", "skills": ["plumbing", "mechanical"]}],
    "tickets": [
        {
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
        },
        {
            "ticket_id": "T002",
            "created_at": "2026-09-01T09:00:00+07:00",
            "floor": 30,
            "unit": "3001",
            "issue_type": "ELEVATOR",
            "priority": "P3",
            "sla_minutes": 5,
            "repair_minutes": 30,
            "required_skill": "mechanical",
            "need_hand_categorized": False,
        },
    ],
}


def test_the_route_is_registered_as_a_post():
    # Through the OpenAPI schema: the sub-routers are included lazily, so the
    # route objects do not exist until something forces them to resolve.
    assert "post" in app.openapi()["paths"][PATH]


def test_the_route_is_coordinator_only():
    from src.api.routes.coordinator import simulation

    guards = {
        getattr(parameter.default, "dependency", None).__name__
        for parameter in inspect.signature(simulation.run_simulation).parameters.values()
        if getattr(parameter.default, "dependency", None) is not None
    }
    assert guards == {"require_coordinator"}


@pytest.mark.asyncio
async def test_an_anonymous_caller_is_rejected(client):
    response = await client.post(PATH, json={"scenario": SCENARIO})
    assert response.status_code in {401, 403}


def test_the_request_body_is_one_scenario_not_three_sources():
    """The contract is a single document. Tickets, technicians and settings
    arriving as three separate top-level fields is the old shape, and accepting
    both would leave two ways to describe one run."""
    from pydantic import ValidationError

    from src.models.api.simulation import SimulationRunRequest

    assert set(SimulationRunRequest.model_fields) == {"scenario"}
    with pytest.raises(ValidationError):
        SimulationRunRequest(tickets=[], technicians=[], config={})


@pytest.mark.asyncio
async def test_a_coordinator_gets_both_flows_back(client):
    """The whole round trip, with the guard stubbed out.

    `require_coordinator` is overridden rather than a real session being built:
    the actor is the one thing this handler never looks at, and standing up a
    Supabase identity to prove that would be testing the auth dependency twice.
    """
    from src.api.dependencies.auth import require_coordinator
    from src.main import app as application

    application.dependency_overrides[require_coordinator] = lambda: None
    try:
        response = await client.post(PATH, json={"scenario": SCENARIO})
    finally:
        application.dependency_overrides.pop(require_coordinator, None)

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["sla_policy"] == "SERVICE_HOURS_DRAFT_V1"
    assert body["settings"]["travel_per_floor_minutes"] == 2
    assert set(body) == {
        "generated_at", "scenario_name", "sla_policy", "building", "settings",
        "horizon_end", "old_app", "new_app", "comparison", "warnings",
    }
    for key in ("old_app", "new_app"):
        assert {t["ticket_id"] for t in body[key]["tickets"]} == {"T001", "T002"}

    p3 = next(t for t in body["new_app"]["tickets"] if t["ticket_id"] == "T002")
    assert p3["outcome"] == "REQUIRES_MANUAL_P3_REVIEW"
    assert p3["assigned_technician_id"] is None


#: The same shape under the v2 policy: five bands, and the emergency at P5.
SCENARIO_V2 = {
    "scenario_name": "Tòa nhà 30 tầng — rubric rủi ro v2",
    "building": {"floor_count": 30, "units_per_floor": 7},
    "sla_policy": {"mode": "SERVICE_HOURS_RISK_V2"},
    "settings": {"travel_per_floor_minutes": 2},
    "technicians": [{"technician_id": "KTV_01", "skills": ["plumbing", "mechanical"]}],
    "tickets": [
        {
            "ticket_id": "T001",
            "created_at": "2026-09-01T09:00:00+07:00",
            "floor": 8,
            "unit": "0801",
            "issue_type": "WATER",
            "priority": "P4",
            "sla_minutes": 180,
            "repair_minutes": 90,
            "required_skill": "plumbing",
            "need_hand_categorized": False,
            # A v2 export puts `risk_score` in this field; the ordering contract
            # is unchanged by that.
            "score_total": 72.5,
        },
        {
            "ticket_id": "T002",
            "created_at": "2026-09-01T09:00:00+07:00",
            "floor": 30,
            "unit": "3001",
            "issue_type": "ELEVATOR",
            "priority": "P5",
            "sla_minutes": 5,
            "repair_minutes": 30,
            "required_skill": "mechanical",
            "need_hand_categorized": False,
        },
    ],
}


@pytest.mark.asyncio
async def test_the_route_accepts_a_risk_policy_scenario(client):
    """The endpoint takes either policy family, and says which one it ran.

    The screen can be handed a v1 export or a v2 one, and a response that did
    not name the policy would leave a manager reading a P3 without knowing
    whether it is a ten-hour band or a five-minute emergency.
    """
    from src.api.dependencies.auth import require_coordinator
    from src.main import app as application

    application.dependency_overrides[require_coordinator] = lambda: None
    try:
        response = await client.post(PATH, json={"scenario": SCENARIO_V2})
    finally:
        application.dependency_overrides.pop(require_coordinator, None)

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["sla_policy"] == "SERVICE_HOURS_RISK_V2"

    tickets = {t["ticket_id"]: t for t in body["new_app"]["tickets"]}
    # P4 is dispatched; P5 is handed to a human, with no technician chosen.
    assert tickets["T001"]["outcome"] == "ASSIGNED"
    assert tickets["T002"]["outcome"] == "REQUIRES_MANUAL_P5_REVIEW"
    assert tickets["T002"]["assigned_technician_id"] is None
    assert tickets["T002"]["sla_status"] == "NOT_EVALUABLE"


@pytest.mark.asyncio
async def test_a_v1_scenario_carrying_a_v2_band_is_refused_by_the_route(client):
    """Located, not a 500. A P4 under a v1 policy has no deadline at all."""
    from src.api.dependencies.auth import require_coordinator
    from src.main import app as application

    payload = {
        **SCENARIO,
        "tickets": [{**SCENARIO["tickets"][0], "priority": "P4"}],
    }
    application.dependency_overrides[require_coordinator] = lambda: None
    try:
        response = await client.post(PATH, json={"scenario": payload})
    finally:
        application.dependency_overrides.pop(require_coordinator, None)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "SIMULATION_INPUT_INVALID"
    assert "priority" in error["message"]


@pytest.mark.asyncio
async def test_a_bad_row_comes_back_located_rather_than_as_a_500(client):
    """The JSON was well formed and one row inside it is wrong. The response
    names the row and the field so the screen can mark the line."""
    from src.api.dependencies.auth import require_coordinator
    from src.main import app as application

    broken = copy.deepcopy(SCENARIO)
    broken["tickets"][0]["created_at"] = "2026-09-01T17:00:00"  # no timezone

    application.dependency_overrides[require_coordinator] = lambda: None
    try:
        response = await client.post(PATH, json={"scenario": broken})
    finally:
        application.dependency_overrides.pop(require_coordinator, None)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "SIMULATION_INPUT_INVALID"


def test_the_response_serializes_timestamps_in_vietnam_local_time():
    """Every figure on this screen is a wall-clock statement about a working
    day, and the person reading it thinks in +07:00."""
    payload = SimulationRunResponse.of(run_comparison(parse_scenario(SCENARIO)))
    first = payload.new_app.tickets[0]
    assert first.ready_at.utcoffset().total_seconds() == 7 * 3600


def test_the_response_echoes_the_policy_and_settings_it_ran_under():
    """A results table without its assumptions cannot be reproduced next month,
    and a deadline without its clock cannot be interpreted at all."""
    payload = SimulationRunResponse.of(run_comparison(parse_scenario(SCENARIO)))
    assert payload.sla_policy == "SERVICE_HOURS_DRAFT_V1"
    assert payload.building == {"floor_count": 30, "units_per_floor": 7}
    assert payload.settings.micro_batch_interval_ms == 750
    assert payload.settings.micro_batch_size == 20
    assert payload.settings.old_app["manual_category_minutes"] == 10
    assert payload.settings.new_app["ai_classification_minutes"] == 1
    assert payload.horizon_end is not None


# ----------------------------------------------------------------------------
# Hai luồng, một phần so sánh, và không có gì khác.
# ----------------------------------------------------------------------------


def test_the_payload_carries_no_third_scenario_and_no_delta_list():
    """Breaking change có chủ ý. Một danh sách delta chung chung là thứ buộc
    frontend phải tự tìm xem cột nào là mốc so sánh — và đảo dấu khi tìm sai."""
    fields = set(SimulationRunResponse.model_fields)

    assert "new_app" in fields
    assert "comparison" in fields
    assert "proposed_optimized" not in fields
    assert "current_app" not in fields
    assert "deltas" not in fields


def test_no_scenario_on_the_wire_claims_to_be_production():
    from src.models.api.simulation import ScenarioResultResponse

    fields = set(ScenarioResultResponse.model_fields)
    assert "planned_by_production" not in fields
    assert "parity" not in fields
    assert fields == {"scenario", "summary", "tickets"}


def test_the_two_scenarios_name_themselves():
    payload = SimulationRunResponse.of(run_comparison(parse_scenario(SCENARIO)))
    assert payload.old_app.scenario == "OLD_APP"
    assert payload.new_app.scenario == "NEW_APP"


def test_every_run_warns_that_the_new_app_is_not_production():
    payload = SimulationRunResponse.of(run_comparison(parse_scenario(SCENARIO)))
    assert any("CHƯA áp dụng vào production" in warning for warning in payload.warnings)


def test_the_draft_policy_warning_reaches_the_wire():
    payload = SimulationRunResponse.of(run_comparison(parse_scenario(SCENARIO)))
    assert any("CHƯA áp dụng cho production" in warning for warning in payload.warnings)


def test_the_comparison_is_old_minus_new_so_positive_favours_the_new_app():
    """Kiểm ngay trên payload, không chỉ trên dataclass: đây là con số màn hình
    render, và nó phải đúng dấu ở chỗ đó."""
    run = run_comparison(parse_scenario(SCENARIO))
    payload = SimulationRunResponse.of(run)

    assert payload.comparison.bql_minutes_saved == (
        run.old_app.summary.bql_effort_minutes - run.new_app.summary.bql_effort_minutes
    )
    assert payload.comparison.bql_minutes_saved > 0
    assert set(ComparisonResponse.model_fields) == {
        "bql_minutes_saved", "bql_hours_saved", "late_starts_avoided",
        "start_late_minutes_avoided", "average_response_minutes_saved",
        "p95_response_minutes_saved", "travel_minutes_saved", "compliance_rate_gain",
    }


# ----------------------------------------------------------------------------
# Chỉ số SLA nói về thời điểm bắt đầu.
# ----------------------------------------------------------------------------


def test_the_summary_publishes_its_denominator():
    """`compliance_rate` mà không có `sla_evaluable_tickets` bên cạnh là một con
    số cải thiện được bằng cách đánh rơi ticket."""
    payload = SimulationRunResponse.of(run_comparison(parse_scenario(SCENARIO)))
    summary = payload.new_app.summary

    assert summary.sla_evaluable_tickets == (
        summary.sla_on_time_tickets
        + summary.sla_late_started_tickets
        + summary.sla_open_overdue_tickets
    )
    assert (
        summary.sla_evaluable_tickets
        + summary.sla_open_not_due_tickets
        + summary.sla_not_evaluable_tickets
        == summary.total_tickets
    )


def test_no_summary_field_still_talks_about_finishing_on_time():
    """Đổi tên chứ không tái sử dụng: một trường tên `sla_late_completed_tickets`
    trên một payload đo thời điểm bắt đầu sẽ được đọc là số ticket sửa xong
    muộn, và đó là một con số khác hẳn."""
    from src.models.api.simulation import ScenarioSummaryResponse

    fields = set(ScenarioSummaryResponse.model_fields)
    assert "sla_late_completed_tickets" not in fields
    assert "total_sla_late_minutes" not in fields
    assert "sla_unresolved_tickets" not in fields
    assert {"sla_late_started_tickets", "total_start_late_minutes"} <= fields


def test_a_ticket_row_separates_departure_start_and_completion():
    payload = SimulationRunResponse.of(
        run_comparison(scenario([ticket("T001", floor=11)], [technician()]))
    )
    row = payload.new_app.tickets[0]

    assert row.departed_at < row.work_started_at < row.completed_at
    assert (row.work_started_at - row.departed_at).total_seconds() == row.travel_minutes * 60


def test_an_at_risk_row_carries_everything_a_coordinator_must_act_on():
    """Dự kiến bắt đầu lúc nào, trễ bao nhiêu, sẽ báo ai, và ai đã quyết định."""
    from src.models.api.simulation import TicketOutcomeResponse

    fields = set(TicketOutcomeResponse.model_fields)
    assert {
        "risk_state", "risk_reason", "projected_start_at", "projected_start_late_minutes",
        "would_notify_bql", "would_write_audit", "decision_source",
    } <= fields


def test_every_ticket_row_carries_its_sla_duration_and_source():
    payload = SimulationRunResponse.of(run_comparison(parse_scenario(SCENARIO)))
    for result in (payload.old_app, payload.new_app):
        for row in result.tickets:
            assert row.sla_minutes > 0
            assert row.sla_duration_source in {"POLICY", "INPUT_OVERRIDE"}


def test_a_pinned_deadline_is_marked_as_such_on_the_wire():
    document = copy.deepcopy(SCENARIO)
    document["tickets"][0]["sla_minutes"] = 999
    payload = SimulationRunResponse.of(run_comparison(parse_scenario(document)))

    row = next(t for t in payload.new_app.tickets if t.ticket_id == "T001")
    assert row.sla_duration_source == "INPUT_OVERRIDE"
    assert row.sla_minutes == 999
