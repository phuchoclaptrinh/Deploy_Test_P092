"""Chạy mô phỏng công suất & SLA ngay trên máy, không cần server và không cần đăng nhập.

    python scripts/run_simulation.py
    python scripts/run_simulation.py --input examples/simulation/scenario.json
    python scripts/run_simulation.py --input kich-ban.json --json ket-qua.json

Không tham số thì chạy kịch bản mẫu trong `examples/simulation/scenario.json`.

Script gọi thẳng `src.simulation.engine.run_comparison`, đúng hàm mà endpoint
`POST /api/v1/coordinator/simulation/run` gọi. Khác biệt duy nhất là bỏ qua tầng
HTTP và Supabase Auth.

Hai luồng được chạy trên cùng một dữ liệu:
  OLD_APP  luồng thủ công, đến trước xử lý trước
  NEW_APP  luồng tự động theo chính sách giả định (P2 trước mọi P1 chưa bắt
           đầu, chọn KTV theo thời điểm bắt đầu) — CHƯA áp dụng production

SLA được tính tại thời điểm KTV tới nơi và bắt đầu sửa, không phải lúc hoàn tất.

KHÔNG ghi database, KHÔNG gọi LLM, KHÔNG tạo ticket/phân công nào.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.dispatch.shift import to_local  # noqa: E402
from src.simulation.engine import run_comparison  # noqa: E402
from src.simulation.models import ComparisonResult, ScenarioResult  # noqa: E402
from src.simulation.validation import SimulationInputError, parse_scenario  # noqa: E402

DEFAULT_INPUT = REPO_ROOT / "examples" / "simulation" / "scenario.json"

SCENARIO_TITLES = {
    "OLD_APP": "APP CŨ (thủ công, đến trước xử lý trước)",
    "NEW_APP": "APP MỚI (mô phỏng, chưa áp dụng production)",
}


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description="Mô phỏng công suất & SLA (chỉ đọc).")
    argument_parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="File kịch bản JSON.")
    argument_parser.add_argument("--json", type=Path, default=None, help="Ghi payload đầy đủ (như API trả về) ra file.")
    return argument_parser


def clock(moment) -> str:
    return to_local(moment).strftime("%d/%m %H:%M") if moment else "—"


def print_summary(result: ScenarioResult) -> None:
    summary = result.summary
    title = SCENARIO_TITLES.get(result.scenario.value, result.scenario.value)
    rate = "—" if summary.compliance_rate is None else f"{summary.compliance_rate * 100:.1f}%"
    print(f"\n=== {title} ===")
    print(f"  Tổng ticket                 : {summary.total_tickets}")
    print(f"  Đã phân công                : {summary.assigned_tickets}")
    print(f"  Đã bắt đầu xử lý            : {summary.started_tickets}")
    print(f"  Đánh giá được SLA (mẫu số)  : {summary.sla_evaluable_tickets}")
    print(f"    ├ bắt đầu đúng hạn        : {summary.sla_on_time_tickets}")
    print(f"    ├ bắt đầu trễ             : {summary.sla_late_started_tickets}")
    print(f"    └ chưa bắt đầu, quá hạn   : {summary.sla_open_overdue_tickets}")
    print(f"  Ngoài mẫu số                : {summary.sla_open_not_due_tickets} chưa tới hạn · "
          f"{summary.sla_not_evaluable_tickets} không đánh giá được")
    print(f"  Tỷ lệ bắt đầu đúng hạn      : {rate} ({summary.sla_on_time_tickets}/{summary.sla_evaluable_tickets})")
    print(f"  Trễ thời điểm bắt đầu       : {summary.total_start_late_minutes} phút "
          f"(TB {summary.average_start_late_minutes} phút/vi phạm)")
    print(f"  Phân công không bảo đảm SLA : {summary.at_risk_tickets}")
    print(f"  Phản hồi TB / P95           : {summary.average_response_minutes} / {summary.p95_response_minutes} phút")
    print(f"  Di chuyển (tổng)            : {summary.total_travel_minutes} phút")
    print(f"  Thời gian BQL bỏ ra         : {summary.bql_effort_minutes} phút")


def print_tickets(result: ScenarioResult) -> None:
    """Ba mốc tách rời: rời việc trước, tới nơi và bắt đầu, rồi sửa xong.

    Mốc giữa là mốc SLA. Cột hoàn tất có mặt để đọc được lịch của kỹ thuật viên,
    không phải để đánh giá cam kết.
    """
    title = SCENARIO_TITLES.get(result.scenario.value, result.scenario.value)
    print(f"\n--- Chi tiết từng ticket · {title} ---")
    print(f"{'TICKET':<7} {'PRI':<4} {'RỜI ĐI':<13} {'BẮT ĐẦU':<13} {'HOÀN TẤT':<13} "
          f"{'HẠN SLA':<13} {'TRỄ':>6}  {'KTV':<7} TRẠNG THÁI")
    print("-" * 108)
    for outcome in result.tickets:
        late = f"{outcome.start_late_minutes}p" if outcome.start_late_minutes else "—"
        risk = " ⚠" if outcome.risk_state is not None and outcome.risk_state.value == "AT_RISK" else ""
        print(
            f"{outcome.ticket_id:<7} {outcome.priority.value:<4} "
            f"{clock(outcome.departed_at):<13} {clock(outcome.work_started_at):<13} "
            f"{clock(outcome.completed_at):<13} {clock(outcome.sla_due_at):<13} {late:>6}  "
            f"{(outcome.assigned_technician_id or '—'):<7} {outcome.sla_status.value}{risk}"
        )


def print_at_risk(result: ScenarioResult) -> None:
    rows = [o for o in result.tickets if o.risk_state is not None and o.risk_state.value == "AT_RISK"]
    if not rows:
        return
    title = SCENARIO_TITLES.get(result.scenario.value, result.scenario.value)
    print(f"\n--- Phân công không bảo đảm SLA · {title} ---")
    print("Việc vẫn được giao; hệ thống thật sẽ đồng thời báo BQL và ghi audit.")
    for outcome in rows:
        print(
            f"  {outcome.ticket_id:<7} dự kiến bắt đầu {clock(outcome.projected_start_at)} · "
            f"hạn {clock(outcome.sla_due_at)} · trễ {outcome.projected_start_late_minutes} phút · "
            f"KTV {outcome.assigned_technician_id} · "
            f"{'sẽ báo BQL' if outcome.would_notify_bql else '—'} · "
            f"{outcome.decision_source.value if outcome.decision_source else '—'}"
        )


def print_load(result: ScenarioResult) -> None:
    title = SCENARIO_TITLES.get(result.scenario.value, result.scenario.value)
    print(f"\n--- Tải kỹ thuật viên · {title} ---")
    for load in result.summary.technician_utilization:
        print(
            f"  {load.technician_id:<8} {load.assigned_ticket_count} ticket · "
            f"sửa {load.work_minutes}p · di chuyển {load.travel_minutes}p · "
            f"{load.utilization_percent}% của {load.capacity_minutes}p"
        )


def print_comparison(comparison: ComparisonResult) -> None:
    """Một quy ước dấu: dương nghĩa là app mới tốt hơn app cũ.

    Các con số này đã được backend trừ sẵn theo đúng chiều đó, nên ở đây không
    có phép đảo dấu nào.
    """
    c = comparison.comparison
    gain = "—" if c.compliance_rate_gain is None else f"{c.compliance_rate_gain * 100:+.1f} điểm"
    print("\n=== APP MỚI SO VỚI APP CŨ (dương = app mới tốt hơn) ===")
    print(f"  Giờ BQL tiết kiệm            : {c.bql_hours_saved} giờ ({c.bql_minutes_saved} phút)")
    print(f"  Ticket bắt đầu trễ tránh được: {c.late_starts_avoided}")
    print(f"  Phút bắt đầu trễ giảm        : {c.start_late_minutes_avoided}")
    print(f"  Tỷ lệ bắt đầu đúng hạn       : {gain}")
    print(f"  Phản hồi nhanh hơn TB        : {c.average_response_minutes_saved} phút/ticket")
    print(f"  Phản hồi P95 nhanh hơn       : {c.p95_response_minutes_saved} phút")
    print(f"  Di chuyển tiết kiệm          : {c.travel_minutes_saved} phút")


def main() -> int:
    arguments = parser().parse_args()
    try:
        document = json.loads(arguments.input.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise SystemExit(f"Không tìm thấy file: {error.filename}") from error
    except json.JSONDecodeError as error:
        raise SystemExit(f"JSON không hợp lệ ({arguments.input}): {error.msg} ở dòng {error.lineno}.") from error

    try:
        scenario = parse_scenario(document)
    except SimulationInputError as error:
        location = f" (dòng {error.index + 1})" if error.index is not None else ""
        field = f" [{error.field}]" if error.field else ""
        raise SystemExit(f"Kịch bản không hợp lệ{location}{field}: {error.message}") from error

    comparison = run_comparison(scenario)
    print(f"Kịch bản: {comparison.scenario_name}")
    print(
        f"  {len(scenario.tickets)} ticket · {len(scenario.technicians)} KTV · "
        f"tòa {scenario.building.floor_count} tầng × {scenario.building.units_per_floor} căn · "
        f"SLA {comparison.sla_policy.value}"
    )
    for warning in comparison.warnings:
        print(f"  ⚠ {warning}")

    for result in (comparison.old_app, comparison.new_app):
        print_summary(result)
    print_comparison(comparison)
    print_at_risk(comparison.new_app)
    print_tickets(comparison.new_app)
    print_load(comparison.new_app)

    if arguments.json:
        # Cùng payload mà API trả về, để đối chiếu hoặc nạp lại vào màn hình.
        from src.models.api.simulation import SimulationRunResponse

        arguments.json.write_text(SimulationRunResponse.of(comparison).model_dump_json(indent=2), encoding="utf-8")
        print(f"\nĐã ghi kết quả đầy đủ: {arguments.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
