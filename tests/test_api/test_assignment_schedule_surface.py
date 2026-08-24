"""The two automation features stay two features at the API boundary.

`/coordinator/auto-assignment-settings` decides whether Backend assigns an
approved ticket by itself. `/coordinator/assignment-schedule` decides how often
Backend opens a new draft table for a human to confirm. The previous UI expressed
the second by writing to the first, which told coordinators a review was coming
when an assignment was.

These assert the separation where it is cheapest to break: the schema. If the
two ever share a field name, a value vocabulary or a path, the next person to
read the OpenAPI document will reasonably assume they are the same thing.
"""

from __future__ import annotations

from src.main import app


def _schema():
    return app.openapi()


def test_both_endpoints_exist_and_are_separate_paths():
    paths = set(_schema()["paths"])
    assert "/api/v1/coordinator/assignment-schedule" in paths
    assert "/api/v1/coordinator/auto-assignment-settings" in paths
    assert "/api/v1/coordinator/assignment-history" in paths
    assert "/api/v1/coordinator/assignment-history/{batch_id}" in paths


def test_the_schedule_speaks_of_intervals_and_the_switch_of_delays():
    schemas = _schema()["components"]["schemas"]
    schedule = schemas["AssignmentScheduleResponse"]["properties"]
    switch = schemas["AutoAssignmentSettingsResponse"]["properties"]

    assert "interval" in schedule and "activation_delay" not in schedule
    assert "activation_delay" in switch and "interval" not in switch
    # The schedule is the only one with a due time, because it is the only one
    # that runs on a clock of its own.
    assert "next_run_at" in schedule


def test_the_schedule_offers_no_immediate_interval():
    """"Open a new draft table immediately, forever" is not a schedule."""
    pattern = _schema()["components"]["schemas"]["AssignmentScheduleUpdateRequest"]["properties"]["interval"]
    text = str(pattern)
    assert "2_HOURS" in text and "1_DAY" in text and "3_DAYS" in text
    assert "IMMEDIATE" not in text
    assert "5_HOURS" not in text


def test_history_returns_snapshot_fields_and_no_model_internals():
    record = _schema()["components"]["schemas"]["AssignmentHistoryRecordResponse"]["properties"]

    assert {"confirmed_by_name", "created_by_type", "followup_schedule", "has_snapshot"} <= set(record)
    # §9: a history record carries a decision, never what produced it.
    for leak in ("raw_model_output", "candidate_snapshot", "error_detail", "prompt"):
        assert leak not in record


def test_a_history_member_carries_what_it_carried_at_confirmation():
    member = _schema()["components"]["schemas"]["AssignmentHistoryMemberResponse"]["properties"]

    assert {"display_code", "category", "location_label", "priority", "created_at", "sla_due_at"} <= set(member)


def test_the_direct_switch_publishes_where_its_on_state_came_from():
    switch = _schema()["components"]["schemas"]["AutoAssignmentSettingsResponse"]["properties"]

    assert {"activated_by_batch_id", "activated_by_user_id", "activated_at"} <= set(switch)


def test_the_confirm_request_advertises_no_way_to_start_direct():
    """The schema is the contract; a field here is a lever clients will pull."""
    confirm = _schema()["components"]["schemas"]["AssignmentProposalConfirmRequest"]

    assert "continue_auto_assignment" not in confirm["properties"]
    # `extra="forbid"`, so an old client still sending it is rejected rather
    # than having its request silently reinterpreted.
    assert confirm.get("additionalProperties") is False
