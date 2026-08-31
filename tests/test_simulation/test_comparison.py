"""So sánh app cũ với app mới: một quy ước dấu, và không có luồng thứ ba.

Mọi trường `_saved` / `_avoided` là `OLD_APP − NEW_APP`; `compliance_rate_gain`
là `NEW_APP − OLD_APP`. **Dương luôn nghĩa là app mới tốt hơn.** Phép trừ xảy ra
đúng một lần, ở backend, để không màn hình nào phải đảo dấu — một dấu trừ nằm
rải rác trong JSX là chỗ một con số cuối cùng sẽ được đọc ngược.
"""

from __future__ import annotations

from pathlib import Path

from src.models.enums import Priority
from src.simulation.engine import run_comparison
from src.simulation.models import Scenario
from tests.test_simulation.conftest import P2_MINUTES, P3_MINUTES, scenario, technician, ticket

SIMULATION_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "simulation"

#: Một ngày mà app mới thật sự thắng: bốn P1 dài và một P2 gấp gửi cùng lúc.
#: App cũ xử lý theo thứ tự đến và để P2 nằm cuối; app mới đưa nó lên đầu.
CONTESTED = [
    ticket("P1_a", created="2026-09-01T05:00", priority=Priority.P1, repair_minutes=120, floor=2),
    ticket("P1_b", created="2026-09-01T05:10", priority=Priority.P1, repair_minutes=120, floor=4),
    ticket("P1_c", created="2026-09-01T05:20", priority=Priority.P1, repair_minutes=120, floor=6),
    ticket("P2_gap", created="2026-09-01T06:00", priority=Priority.P2, sla_minutes=P2_MINUTES,
           repair_minutes=30, floor=3),
    ticket("P3_bql", created="2026-09-01T06:30", priority=Priority.P3, sla_minutes=P3_MINUTES),
]
ROSTER = [technician("KTV_01")]


def contested_run():
    return run_comparison(scenario(CONTESTED, ROSTER))


# ---------------------------------------------------------------------------
# Chỉ hai luồng.
# ---------------------------------------------------------------------------


def test_the_scenario_enum_holds_exactly_two_flows():
    assert [member.value for member in Scenario] == ["OLD_APP", "NEW_APP"]


def test_the_result_carries_two_flows_and_one_comparison():
    run = contested_run()

    assert run.old_app.scenario is Scenario.OLD_APP
    assert run.new_app.scenario is Scenario.NEW_APP
    assert run.comparison is not None
    assert not hasattr(run, "proposed_optimized")
    assert not hasattr(run, "deltas")


def test_neither_flow_claims_to_be_production():
    """Không cờ parity nào tồn tại để mà đọc nhầm."""
    for result in (contested_run().old_app, contested_run().new_app):
        assert not hasattr(result, "planned_by_production")
        assert not hasattr(result, "parity")


def test_every_run_says_the_new_app_is_a_simulation():
    """Cảnh báo này đứng đầu danh sách và có mặt trong *mọi* lần chạy, kể cả lần
    chạy sạch nhất. Nó là điều dễ bị quên nhất khi đọc một bảng trông chính
    thức."""
    warnings = contested_run().warnings

    assert "CHƯA áp dụng vào production" in warnings[0]
    assert "App mới" in warnings[0]


def test_the_word_proposed_optimized_is_gone_from_the_simulator():
    """Không alias, không code chết, không nhãn cũ sót lại."""
    for path in SIMULATION_PACKAGE.rglob("*.py"):
        assert "PROPOSED_OPTIMIZED" not in path.read_text(encoding="utf-8"), path
        assert "CURRENT_APP" not in path.read_text(encoding="utf-8"), path


# ---------------------------------------------------------------------------
# Quy ước dấu.
# ---------------------------------------------------------------------------


def test_positive_always_means_the_new_app_is_better():
    run = contested_run()
    old, new, c = run.old_app.summary, run.new_app.summary, run.comparison

    assert c.bql_minutes_saved == old.bql_effort_minutes - new.bql_effort_minutes
    assert c.late_starts_avoided == old.sla_late_started_tickets - new.sla_late_started_tickets
    assert c.start_late_minutes_avoided == old.total_start_late_minutes - new.total_start_late_minutes
    assert c.average_response_minutes_saved == round(
        old.average_response_minutes - new.average_response_minutes, 1
    )
    assert c.p95_response_minutes_saved == old.p95_response_minutes - new.p95_response_minutes
    assert c.travel_minutes_saved == old.total_travel_minutes - new.total_travel_minutes


def test_the_compliance_gain_runs_the_other_way_because_more_is_better():
    run = contested_run()

    assert run.comparison.compliance_rate_gain == round(
        run.new_app.summary.compliance_rate - run.old_app.summary.compliance_rate, 4
    )


def test_the_new_app_actually_wins_on_this_fixture():
    """Một quy ước dấu chỉ kiểm được bằng một lần chạy mà nó thật sự có dấu."""
    run = contested_run()

    assert run.old_app.summary.sla_late_started_tickets == 1
    assert run.new_app.summary.sla_late_started_tickets == 0
    assert run.comparison.late_starts_avoided == 1
    assert run.comparison.start_late_minutes_avoided > 0
    assert run.comparison.compliance_rate_gain > 0


def test_the_new_app_saves_building_management_its_manual_minutes():
    run = contested_run()

    assert run.comparison.bql_minutes_saved > 0
    assert run.comparison.bql_hours_saved == round(run.comparison.bql_minutes_saved / 60, 2)


def test_a_trade_off_shows_up_as_a_negative_rather_than_being_hidden():
    """Đưa P2 lên trước có giá của nó, và giá đó phải hiện ra.

    Với đội chỉ có một người và một P2 ở tầng xa, app mới đi thang máy nhiều hơn
    app cũ. Làm tròn con số đó về không sẽ là quảng cáo, không phải đo lường.
    """
    tickets = [
        ticket("P1_gan", created="2026-09-01T05:00", priority=Priority.P1, repair_minutes=60, floor=1),
        ticket("P2_xa", created="2026-09-01T05:10", priority=Priority.P2, sla_minutes=P2_MINUTES,
               repair_minutes=30, floor=30),
    ]
    run = run_comparison(scenario(tickets, ROSTER))

    assert run.comparison.travel_minutes_saved < 0
    assert run.new_app.summary.total_travel_minutes > run.old_app.summary.total_travel_minutes


def test_the_gain_is_null_when_one_side_has_no_denominator():
    """Không phải 0.0 — một hiệu số giữa một tỷ lệ và không-có-tỷ-lệ không phải
    là không."""
    tickets = [ticket("chi_co_p3", created="2026-09-01T09:00", priority=Priority.P3, sla_minutes=P3_MINUTES)]
    run = run_comparison(scenario(tickets, ROSTER))

    assert run.old_app.summary.compliance_rate is None
    assert run.comparison.compliance_rate_gain is None


# ---------------------------------------------------------------------------
# Cùng đầu vào, hai lần chạy độc lập.
# ---------------------------------------------------------------------------


def test_each_flow_gets_its_own_technician_positions():
    """Lịch của luồng này không được để một kỹ thuật viên đứng nhầm tầng cho
    luồng kia."""
    run = contested_run()
    old_loads = {load.technician_id: load for load in run.old_app.summary.technician_utilization}
    new_loads = {load.technician_id: load for load in run.new_app.summary.technician_utilization}

    assert set(old_loads) == set(new_loads)
    assert sum(load.work_minutes for load in old_loads.values()) == sum(
        load.work_minutes for load in new_loads.values()
    )


def test_a_run_is_deterministic():
    first, second = contested_run(), contested_run()

    assert [row.assigned_technician_id for row in first.new_app.tickets] == [
        row.assigned_technician_id for row in second.new_app.tickets
    ]
    assert first.comparison == second.comparison
