"""Hợp đồng đường truyền cho mô phỏng công suất & SLA.

Một kịch bản vào, hai luồng ra.

**Request là một tài liệu JSON duy nhất**, không phải ba nguồn dán riêng. Nó tới
dưới dạng object thô và `src.simulation.validation` là thứ duy nhất soi vào nó:
module đó sở hữu toàn bộ độ nghiêm ngặt (bắt buộc có múi giờ, boolean phải là
boolean, từ chối khóa lạ) và sinh ra thông báo tiếng Việt có định vị dòng để màn
hình đặt ngay cạnh chỗ sai. Một model pydantic thứ hai cùng hình dạng ở đây sẽ
là bộ kiểm tra thứ hai phải giữ cho đồng thuận, và cuối cùng hai bên sẽ bất đồng
về việc dòng nào mới là dòng có lỗi.

**Response thì nghiêm ngặt**, vì mọi trường một màn hình render đều là hợp đồng.

Dấu thời gian đi ra theo giờ Việt Nam (+07:00). Phần còn lại của API này trả lời
bằng UTC, và endpoint này là ngoại lệ có chủ ý: mọi con số ở đây là một phát biểu
theo đồng hồ treo tường về một ngày làm việc — "tới nơi 09:10", "hạn 10:00 sáng
mai" — và người đọc nó nghĩ bằng +07:00. Offset nằm rõ trong payload nên không
có gì mơ hồ.

Hai quy tắc đặt tên payload này giữ, vì cả hai đều là cách một bản mô phỏng có
thể âm thầm gây hiểu nhầm:

* `completed_at` ở đây là thời điểm hoàn tất **mô phỏng**. Nó không bao giờ chạm
  tới `Ticket.completed_at`, và không serializer nào hướng ra cư dân import
  module này. Nó cũng **không** phải mốc SLA — mốc SLA là `work_started_at`.
* Không luồng nào ở đây là production. `NEW_APP` là mô phỏng một chính sách giả
  định; payload không mang cờ parity nào, và `sla_policy` được lặp lại để một
  kết quả xuất ra file mang theo đồng hồ đã sinh ra nó.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.dispatch.shift import to_local
from src.simulation.models import (
    Comparison,
    ComparisonResult,
    ScenarioResult,
    ScenarioSummary,
    Settings,
    TechnicianLoad,
    TicketOutcome,
)


def _local(moment: datetime | None) -> datetime | None:
    """UTC vào, giờ Việt Nam ra. Xem docstring của module."""
    return to_local(moment) if moment is not None else None


# ----------------------------------------------------------------------------
# Request.
# ----------------------------------------------------------------------------


class SimulationRunRequest(BaseModel):
    """Một tài liệu kịch bản.

    Cố tình là một object tự do: schema nằm ở `src/simulation/validation.py`,
    nơi tự từ chối khóa lạ và nói được *khóa nào* ở *dòng nào*. Xem docstring
    của module.
    """

    model_config = ConfigDict(extra="forbid")

    scenario: dict[str, Any] = Field(
        description=(
            "Kịch bản JSON đầy đủ: scenario_name, building, sla_policy, settings, "
            "technicians, tickets. Xem src/simulation/validation.py."
        )
    )


# ----------------------------------------------------------------------------
# Response.
# ----------------------------------------------------------------------------


class TicketOutcomeResponse(BaseModel):
    """Một dòng của bảng chi tiết theo ticket.

    Ba mốc thời gian tách rời, và mốc SLA là mốc giữa:

    * `departed_at` — kỹ thuật viên rời việc trước và lên đường;
    * `work_started_at` — tới nơi và bắt đầu sửa. **Đây là mốc SLA.**
    * `completed_at` — sửa xong. Chỉ dùng cho công suất và lịch kỹ thuật viên.
    """

    ticket_id: str
    scenario: str
    #: ASSIGNED | REQUIRES_MANUAL_P3_REVIEW | REQUIRES_MANUAL_P5_REVIEW |
    #: NO_ELIGIBLE_TECHNICIAN. Which of the two manual values appears depends on
    #: the SLA policy the run used: P3 is the emergency band under v1, P5 under
    #: the risk rubric.
    #: `ASSIGNED` chỉ có nghĩa là đã có người, không có nghĩa là đã bắt đầu.
    outcome: str
    #: ON_TIME | LATE_STARTED | OPEN_OVERDUE | OPEN_NOT_DUE | NOT_EVALUABLE
    sla_status: str
    #: Vì sao không được phân công; null trên một ticket đã có người.
    reason: str | None = None
    priority: str
    floor: int
    unit: str
    required_skill: str
    sla_due_at: datetime
    #: Thời hạn đứng sau `sla_due_at`.
    sla_minutes: int
    #: POLICY | INPUT_OVERRIDE. `INPUT_OVERRIDE` nghĩa là kịch bản ghi một thời
    #: hạn khác chính sách, nên dòng này không được đo theo chính sách đang chạy.
    sla_duration_source: str
    ready_at: datetime
    assigned_technician_id: str | None = None
    #: SCHEDULER_SIMULATED | SCHEDULER_FALLBACK_SIMULATED | MANUAL_SIMULATED.
    #: Không giá trị nào tuyên bố một mô hình đã thực sự quyết định.
    decision_source: str | None = None
    #: SAFE | AT_RISK.
    risk_state: str | None = None
    #: START_SLA_RISK: không kỹ thuật viên hợp lệ nào bắt đầu được trước hạn.
    risk_reason: str | None = None
    #: Lần chạy dự kiến gì lúc quyết định phân công. Có thể lệch khỏi kết quả
    #: thật khi một P2 đến sau chen lên trước một P1 chưa bắt đầu.
    projected_start_at: datetime | None = None
    projected_start_late_minutes: int = 0
    #: Hệ thống thật sẽ báo Ban quản lý và ghi audit ở đây. Bản mô phỏng chỉ nói
    #: rằng nó *sẽ* làm; nó không gửi gì và không ghi gì.
    would_notify_bql: bool = False
    would_write_audit: bool = False
    departed_at: datetime | None = None
    #: Mốc SLA. Null khi tới hết thời gian mô phỏng vẫn chưa ai bắt đầu.
    work_started_at: datetime | None = None
    #: Thời điểm hoàn tất **mô phỏng**. Không bao giờ ghi vào `Ticket.completed_at`.
    completed_at: datetime | None = None
    wait_minutes: int = 0
    response_minutes: int = 0
    #: Trễ tại thời điểm bắt đầu, theo đồng hồ của chính sách đang chạy. Với
    #: `OPEN_OVERDUE` là số phút quá hạn tính tới hết thời gian mô phỏng.
    start_late_minutes: int = 0
    travel_minutes: int = 0
    repair_minutes: int = 0
    bql_minutes: int = 0

    @classmethod
    def of(cls, outcome: TicketOutcome) -> TicketOutcomeResponse:
        return cls(
            ticket_id=outcome.ticket_id,
            scenario=outcome.scenario.value,
            outcome=outcome.outcome.value,
            sla_status=outcome.sla_status.value,
            reason=outcome.reason.value if outcome.reason else None,
            priority=outcome.priority.value,
            floor=outcome.floor,
            unit=outcome.unit,
            required_skill=outcome.required_skill,
            sla_due_at=_local(outcome.sla_due_at),
            sla_minutes=outcome.sla_minutes,
            sla_duration_source=outcome.sla_duration_source.value,
            ready_at=_local(outcome.ready_at),
            assigned_technician_id=outcome.assigned_technician_id,
            decision_source=outcome.decision_source.value if outcome.decision_source else None,
            risk_state=outcome.risk_state.value if outcome.risk_state else None,
            risk_reason=outcome.risk_reason.value if outcome.risk_reason else None,
            projected_start_at=_local(outcome.projected_start_at),
            projected_start_late_minutes=outcome.projected_start_late_minutes,
            would_notify_bql=outcome.would_notify_bql,
            would_write_audit=outcome.would_write_audit,
            departed_at=_local(outcome.departed_at),
            work_started_at=_local(outcome.work_started_at),
            completed_at=_local(outcome.completed_at),
            wait_minutes=outcome.wait_minutes,
            response_minutes=outcome.response_minutes,
            start_late_minutes=outcome.start_late_minutes,
            travel_minutes=outcome.travel_minutes,
            repair_minutes=outcome.repair_minutes,
            bql_minutes=outcome.bql_minutes,
        )


class TechnicianLoadResponse(BaseModel):
    technician_id: str
    work_minutes: int
    travel_minutes: int
    busy_minutes: int
    assigned_ticket_count: int
    #: Số phút làm việc mà lần chạy trải qua — mẫu số của phần trăm.
    capacity_minutes: int
    utilization_percent: float

    @classmethod
    def of(cls, load: TechnicianLoad) -> TechnicianLoadResponse:
        return cls(
            technician_id=load.technician_id,
            work_minutes=load.work_minutes,
            travel_minutes=load.travel_minutes,
            busy_minutes=load.busy_minutes,
            assigned_ticket_count=load.assigned_ticket_count,
            capacity_minutes=load.capacity_minutes,
            utilization_percent=load.utilization_percent,
        )


class ScenarioSummaryResponse(BaseModel):
    """Một luồng bằng con số, với mẫu số để lộ ra ngoài.

    `sla_evaluable_tickets` là mẫu số của `compliance_rate`, và nó **bao gồm**
    `sla_open_overdue_tickets`: quá hạn mà chưa ai bắt đầu là vi phạm, không
    phải dữ liệu thiếu.
    """

    total_tickets: int
    assigned_tickets: int
    started_tickets: int
    #: ON_TIME + LATE_STARTED + OPEN_OVERDUE. Luôn render cạnh tỷ lệ.
    sla_evaluable_tickets: int
    sla_on_time_tickets: int
    sla_late_started_tickets: int
    sla_open_overdue_tickets: int
    sla_open_not_due_tickets: int
    sla_not_evaluable_tickets: int
    #: Null khi mẫu số rỗng, chứ không phải một con số 0% hay 100% gây hiểu nhầm.
    compliance_rate: float | None = None
    total_start_late_minutes: int
    average_start_late_minutes: float
    average_wait_minutes: float
    p95_wait_minutes: int
    average_response_minutes: float
    p95_response_minutes: int
    total_travel_minutes: int
    bql_effort_minutes: int
    at_risk_tickets: int
    last_completed_at: datetime | None = None
    technician_utilization: list[TechnicianLoadResponse] = Field(default_factory=list)

    @classmethod
    def of(cls, summary: ScenarioSummary) -> ScenarioSummaryResponse:
        return cls(
            total_tickets=summary.total_tickets,
            assigned_tickets=summary.assigned_tickets,
            started_tickets=summary.started_tickets,
            sla_evaluable_tickets=summary.sla_evaluable_tickets,
            sla_on_time_tickets=summary.sla_on_time_tickets,
            sla_late_started_tickets=summary.sla_late_started_tickets,
            sla_open_overdue_tickets=summary.sla_open_overdue_tickets,
            sla_open_not_due_tickets=summary.sla_open_not_due_tickets,
            sla_not_evaluable_tickets=summary.sla_not_evaluable_tickets,
            compliance_rate=summary.compliance_rate,
            total_start_late_minutes=summary.total_start_late_minutes,
            average_start_late_minutes=summary.average_start_late_minutes,
            average_wait_minutes=summary.average_wait_minutes,
            p95_wait_minutes=summary.p95_wait_minutes,
            average_response_minutes=summary.average_response_minutes,
            p95_response_minutes=summary.p95_response_minutes,
            total_travel_minutes=summary.total_travel_minutes,
            bql_effort_minutes=summary.bql_effort_minutes,
            at_risk_tickets=summary.at_risk_tickets,
            last_completed_at=_local(summary.last_completed_at),
            technician_utilization=[TechnicianLoadResponse.of(load) for load in summary.technician_utilization],
        )


class ScenarioResultResponse(BaseModel):
    """Một luồng. Không có cờ parity, vì không luồng nào là production."""

    #: OLD_APP hoặc NEW_APP.
    scenario: str
    summary: ScenarioSummaryResponse
    tickets: list[TicketOutcomeResponse]

    @classmethod
    def of(cls, result: ScenarioResult) -> ScenarioResultResponse:
        return cls(
            scenario=result.scenario.value,
            summary=ScenarioSummaryResponse.of(result.summary),
            tickets=[TicketOutcomeResponse.of(ticket) for ticket in result.tickets],
        )


class ComparisonResponse(BaseModel):
    """App mới đo với app cũ. Một quy ước dấu, cho mọi trường.

    **Dương nghĩa là app mới tốt hơn app cũ.** Mọi trường `_saved` / `_avoided`
    là `OLD_APP − NEW_APP`; `compliance_rate_gain` là `NEW_APP − OLD_APP`, vì ở
    đó nhiều hơn mới là tốt hơn. Màn hình không phải đảo dấu ở bất kỳ đâu, và đó
    chính là lý do những con số này được tính ở đây thay vì để frontend tự trừ.
    """

    bql_minutes_saved: int
    bql_hours_saved: float
    late_starts_avoided: int
    start_late_minutes_avoided: int
    average_response_minutes_saved: float
    p95_response_minutes_saved: int
    travel_minutes_saved: int
    compliance_rate_gain: float | None = None

    @classmethod
    def of(cls, comparison: Comparison) -> ComparisonResponse:
        return cls(**vars(comparison))


class SettingsResponse(BaseModel):
    """Các thiết lập lần chạy thực sự dùng, đã điền đầy mặc định.

    Lặp lại chứ không giả định: một kết quả xuất ra file phải mang theo các giả
    định đã sinh ra nó, nếu không nó chỉ là một bảng số không ai dựng lại được
    vào tháng sau.
    """

    travel_base_minutes: int
    travel_per_floor_minutes: int
    #: Nhịp gom batch, mặc định lấy từ cấu hình của bộ điều phối đang chạy.
    micro_batch_interval_ms: int
    micro_batch_size: int
    simulation_horizon_days: int
    old_app: dict[str, int]
    new_app: dict[str, int]

    @classmethod
    def of(cls, settings: Settings) -> SettingsResponse:
        return cls(
            travel_base_minutes=settings.travel_base_minutes,
            travel_per_floor_minutes=settings.travel_per_floor_minutes,
            micro_batch_interval_ms=settings.micro_batch_interval_ms,
            micro_batch_size=settings.micro_batch_size,
            simulation_horizon_days=settings.simulation_horizon_days,
            old_app={
                "manual_category_minutes": settings.old_app.manual_category_minutes,
                "manual_dispatch_minutes": settings.old_app.manual_dispatch_minutes,
            },
            new_app={
                "ai_classification_minutes": settings.new_app.ai_classification_minutes,
                "manual_review_minutes": settings.new_app.manual_review_minutes,
            },
        )


class SimulationRunResponse(BaseModel):
    """Một lần so sánh, sẵn sàng để render hoặc xuất ra file."""

    generated_at: datetime
    scenario_name: str
    #: WALL_CLOCK_V1 hoặc SERVICE_HOURS_DRAFT_V1. Lặp lại để một kết quả xuất ra
    #: file nói được đồng hồ nào đã sinh ra các mốc hạn của nó.
    sla_policy: str
    building: dict[str, int]
    settings: SettingsResponse
    #: Mốc mà mọi ticket chưa bắt đầu được đối chiếu tới.
    horizon_end: datetime
    old_app: ScenarioResultResponse
    new_app: ScenarioResultResponse
    comparison: ComparisonResponse
    #: Ghi chú tiếng Việt về lần chạy — cảnh báo app mới là mô phỏng, cảnh báo
    #: chính sách nháp, thời lượng đã giả định, kỹ thuật viên đang bận. Đáng
    #: hiện, không bao giờ đáng làm hỏng lần chạy.
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, comparison: ComparisonResult) -> SimulationRunResponse:
        return cls(
            generated_at=_local(comparison.generated_at),
            scenario_name=comparison.scenario_name,
            sla_policy=comparison.sla_policy.value,
            building={
                "floor_count": comparison.building.floor_count,
                "units_per_floor": comparison.building.units_per_floor,
            },
            settings=SettingsResponse.of(comparison.settings),
            horizon_end=_local(comparison.horizon_end),
            old_app=ScenarioResultResponse.of(comparison.old_app),
            new_app=ScenarioResultResponse.of(comparison.new_app),
            comparison=ComparisonResponse.of(comparison.comparison),
            warnings=list(comparison.warnings),
        )


__all__ = [
    "ComparisonResponse",
    "ScenarioResultResponse",
    "ScenarioSummaryResponse",
    "SettingsResponse",
    "SimulationRunRequest",
    "SimulationRunResponse",
    "TechnicianLoadResponse",
    "TicketOutcomeResponse",
]
