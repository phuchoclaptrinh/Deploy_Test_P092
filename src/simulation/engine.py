"""Bản phát lại: hai luồng trên cùng một tập dữ liệu, không tác dụng phụ.

Đây là gì
---------
Một cỗ máy, chạy hai lần với hai chính sách. `OLD_APP` là nền thủ công — đến
trước xử lý trước, người rảnh sớm nhất nhận. `NEW_APP` là chính sách giả định —
P2 trước mọi P1 chưa bắt đầu, chọn người theo thời điểm bắt đầu sửa, và khi
không ai kịp hạn thì vẫn giao việc kèm cảnh báo cho Ban quản lý.

Đây không phải là gì
--------------------
Không phải một bộ điều phối. Nó không tạo ticket, không tạo phân công, không tạo
dispatch event; không mở session và không import gì từ `src.database`,
`src.repositories` hay `src.services` — điều mà `test_no_database_writes` kiểm
tra bằng cách đọc đồ thị import chứ không bằng cách tin đoạn văn này. Nó không
gọi mô hình nào và không gọi agent nào: mọi con số bên dưới là số học trên kịch
bản dán vào.

Và nó không mô tả production. `NEW_APP` là **mô phỏng một chính sách chưa được
áp dụng**; bộ điều phối đang chạy xếp hàng đợi theo slack còn lại chứ không theo
quy tắc ở đây, nên không dòng nào trong module này gọi vào
`src.dispatch.scheduler` và không nhãn nào nói `NEW_APP` là hành vi hiện tại.

Vòng lặp, trong một đoạn
------------------------
Giữ một đồng hồ chạy theo nhịp gom batch. Ở mỗi lượt: **khóa** những việc đã
thực sự bắt đầu trước mốc này — việc đã bắt đầu thì không bị chen ngang, đó là
quy tắc chứ không phải hệ quả; **gom** những phản ánh đã sẵn sàng, cũ nhất
trước, tối đa `micro_batch_size` cái; **sắp lại** cái vừa gom theo chính sách;
rồi với từng cái, **dự kiến** thời điểm bắt đầu trên hàng đợi của từng kỹ thuật
viên hợp lệ và chọn một người. Hết mọi lượt thì **rút cạn** hàng đợi tới hết
thời gian mô phỏng.

Ba mốc, không mốc nào suy ra được từ mốc khác: `departed_at` (rời việc trước),
`work_started_at = departed_at + di chuyển` (mốc SLA), và `completed_at` (chỉ
dùng cho công suất).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import ceil

from src.dispatch.shift import advance, as_utc, next_shift_open, working_seconds_between
from src.domain.sla_clock import POLICY_SLA_MINUTES, SlaPolicy, sla_late_seconds
from src.models.enums import DispatchRiskState, Priority
from src.simulation.batching import micro_batches
from src.simulation.models import (
    EVALUABLE_STATUSES,
    MANUAL_OUTCOME,
    MANUAL_OUTCOMES,
    WORKING_MINUTES_PER_DAY,
    Comparison,
    ComparisonResult,
    DecisionSource,
    Outcome,
    Reason,
    RiskReason,
    Scenario,
    ScenarioInput,
    ScenarioResult,
    ScenarioSummary,
    Settings,
    SimTechnician,
    SimTicket,
    SlaDurationSource,
    SlaStatus,
    TechnicianLoad,
    TicketOutcome,
    utc_now,
)
from src.simulation.policies import Policy, new_app_policy, old_app_policy
from src.simulation.travel import travel_minutes

# ----------------------------------------------------------------------------
# Trạng thái trong lúc chạy. Có thể thay đổi, cục bộ theo lần chạy, rồi bỏ đi.
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Assignment:
    """Một việc đã giao nhưng chưa bắt đầu.

    `assigned_at` là mốc của lượt gom đã giao nó: kỹ thuật viên không thể lên
    đường trước khi được giao, kể cả khi họ đã rảnh từ lâu.
    """

    ticket: SimTicket
    assigned_at: datetime


@dataclass(frozen=True)
class _Execution:
    """Số học của một việc đã thực sự làm."""

    departed_at: datetime
    work_started_at: datetime
    completed_at: datetime
    travel: int


@dataclass(frozen=True)
class _Projection:
    """Lần chạy *dự kiến* gì cho một ticket trên một kỹ thuật viên cụ thể."""

    departed_at: datetime
    work_started_at: datetime
    travel: int
    #: Số phút bắt đầu trễ hạn, đo bằng đồng hồ của chính sách SLA đang chạy.
    start_late_minutes: int


@dataclass
class _TechnicianState:
    """Vị trí của một kỹ thuật viên trong bản phát lại."""

    technician: SimTechnician
    #: Kết thúc của việc **đã bắt đầu**. Không có gì đẩy mốc này lùi lại được.
    busy_until: datetime
    floor: int
    queue: list[_Assignment] = field(default_factory=list)
    done: dict[str, _Execution] = field(default_factory=dict)
    work_minutes: int = 0
    travel_minutes: int = 0
    assigned_ticket_count: int = 0

    @property
    def technician_id(self) -> str:
        return self.technician.technician_id

    @property
    def busy_minutes(self) -> int:
        """Tải hiện tại, dùng làm tiêu chí phá hòa ổn định khi chọn người."""
        return self.work_minutes + self.travel_minutes


@dataclass
class _Partition:
    """Phản ánh chia theo việc luồng tự động có được đụng vào hay không.

    `settled` mang theo kết luận chứ không mang một dòng kết quả hoàn chỉnh, vì
    cùng một phân hoạch được hai luồng dùng lại và mỗi luồng dựng dòng của mình
    với thời điểm sẵn sàng riêng và chi phí Ban quản lý riêng.
    """

    schedulable: list[SimTicket] = field(default_factory=list)
    settled: list[tuple[SimTicket, Outcome, Reason]] = field(default_factory=list)


def minutes_between(start: datetime, end: datetime) -> int:
    """Số phút treo tường, chặn dưới ở không.

    Treo tường chứ không phải giờ làm việc, có chủ ý: đây là thứ cư dân trải
    qua. Số học giờ phục vụ thuộc về đồng hồ SLA, và trộn hai thứ là cách khiến
    một phản ánh nằm qua đêm được mô tả là chờ hai mươi phút.
    """
    return max(0, int((as_utc(end) - as_utc(start)).total_seconds() // 60))


# ----------------------------------------------------------------------------
# Ràng buộc cứng và thời gian mô phỏng.
# ----------------------------------------------------------------------------


def eligible_technicians(ticket: SimTicket, technicians: list[SimTechnician]) -> list[SimTechnician]:
    """Những kỹ thuật viên thỏa **toàn bộ** ràng buộc cứng cho ticket này.

    Ba ràng buộc, không có ràng buộc mềm nào lẫn vào: có kỹ năng, không bị dữ
    liệu đầu vào loại trừ, và đang khả dụng. Một người bị loại ở đây không được
    "cân nhắc lại vì không còn ai khác" — đó là ý nghĩa của từ *cứng*.
    """
    return [
        technician
        for technician in technicians
        if technician.has_skill(ticket.required_skill)
        and technician.technician_id not in ticket.excluded_technician_ids
        and technician.is_usable
    ]


def _partition(
    tickets: list[SimTicket], technicians: list[SimTechnician], sla_policy: SlaPolicy
) -> _Partition:
    """Tách ra những gì luồng tự động không được phép phân công.

    Mức khẩn cấp trước, rồi tới ràng buộc cứng. Lý do được báo theo thứ tự từ
    "sự thật về đội ngũ" tới "sự thật về hôm nay": không ai có kỹ năng này là
    một sự thật về đội ngũ, còn những người có kỹ năng đang bận thì mai sẽ khác.

    Mức nào là mức khẩn cấp do chính sách quyết định — P3 ở V1, P5 ở V2 — nên
    một lần chạy V2 vẫn phân việc bình thường cho P3 mười giờ của nó.
    """
    partition = _Partition()
    manual_outcome, manual_reason = MANUAL_OUTCOME[sla_policy]
    for ticket in tickets:
        if ticket.requires_manual_review(sla_policy):
            # Luồng tự động từ chối mức khẩn cấp thẳng thừng, và bản mô phỏng
            # cũng vậy. Không chọn kỹ thuật viên, không xếp lịch.
            partition.settled.append((ticket, manual_outcome, manual_reason))
            continue
        skilled = [t for t in technicians if t.has_skill(ticket.required_skill)]
        if not skilled:
            partition.settled.append((ticket, Outcome.NO_ELIGIBLE_TECHNICIAN, Reason.MISSING_SKILL))
            continue
        allowed = [t for t in skilled if t.technician_id not in ticket.excluded_technician_ids]
        if not allowed:
            partition.settled.append((ticket, Outcome.NO_ELIGIBLE_TECHNICIAN, Reason.TECHNICIAN_EXCLUDED))
            continue
        if not any(t.is_usable for t in allowed):
            partition.settled.append((ticket, Outcome.NO_ELIGIBLE_TECHNICIAN, Reason.TECHNICIAN_UNAVAILABLE))
            continue
        partition.schedulable.append(ticket)
    return partition


def scenario_horizon(scenario: ScenarioInput) -> datetime:
    """Mốc duy nhất mà cả hai luồng được đối chiếu tới.

    Neo vào **phản ánh đầu tiên**, không neo vào thời điểm đầu tiên một luồng
    coi phản ánh đó là điều phối được. Mỗi luồng trả chi phí phân tích riêng
    trước `ready_at` — mười tám phút thủ công cho app cũ, một phút cho app mới —
    nên neo theo `ready_at` sẽ kết thúc ngày của app cũ muộn hơn mười bảy phút và
    âm thầm so sánh hai cửa sổ khác nhau.
    """
    if not scenario.tickets:  # pragma: no cover - parser đã từ chối kịch bản rỗng
        return utc_now() + timedelta(days=scenario.settings.simulation_horizon_days)
    first = min(ticket.created_at for ticket in scenario.tickets)
    return next_shift_open(first) + timedelta(days=scenario.settings.simulation_horizon_days)


# ----------------------------------------------------------------------------
# Dự kiến và chọn người.
# ----------------------------------------------------------------------------


def _project(
    state: _TechnicianState,
    ticket: SimTicket,
    assigned_at: datetime,
    settings: Settings,
    policy: Policy,
    sla_policy: SlaPolicy,
    due_at: datetime,
) -> _Projection:
    """Nếu giao `ticket` cho người này lúc `assigned_at` thì họ bắt đầu lúc nào.

    Đi qua hàng đợi của họ theo đúng thứ tự chính sách, có chèn `ticket` vào, và
    dừng lại ngay khi tới nó. Đây là chỗ "P2 trước mọi P1 chưa bắt đầu" biến
    thành một con số: chèn một P2 vào hàng đợi đang toàn P1 chưa khởi động thì nó
    xếp lên đầu, và thời điểm bắt đầu dự kiến của nó là ngay sau việc *đang* làm.

    Việc đã bắt đầu không nằm trong hàng đợi nữa — nó đã thành `busy_until` —
    nên không có đường nào để phép tính này chen ngang nó.
    """
    ordered = sorted(
        [*state.queue, _Assignment(ticket=ticket, assigned_at=assigned_at)],
        key=lambda item: policy.queue_key(item.ticket, sla_policy),
    )
    clock = state.busy_until
    floor = state.floor
    for item in ordered:
        # Không ai lên đường trước khi rảnh, và cũng không trước khi được giao.
        departed = next_shift_open(max(clock, item.assigned_at))
        travel = travel_minutes(
            floor,
            item.ticket.floor,
            base_minutes=settings.travel_base_minutes,
            per_floor_minutes=settings.travel_per_floor_minutes,
        )
        started = advance(departed, timedelta(minutes=travel))
        if item.ticket.ticket_id == ticket.ticket_id:
            return _Projection(
                departed_at=departed,
                work_started_at=started,
                travel=travel,
                start_late_minutes=sla_late_seconds(due_at, started, ticket.priority, sla_policy) // 60,
            )
        clock = advance(started, timedelta(minutes=item.ticket.repair_minutes))
        floor = item.ticket.floor
    raise AssertionError("ticket vừa chèn vào phải nằm trong hàng đợi vừa sắp")  # pragma: no cover


@dataclass(frozen=True)
class _Decision:
    """Ai nhận ticket, và lần chạy đã dự kiến gì khi quyết định."""

    state: _TechnicianState
    projection: _Projection
    decision_source: DecisionSource
    risk_state: DispatchRiskState | None
    risk_reason: RiskReason | None


def _select(
    ticket: SimTicket,
    candidates: list[_TechnicianState],
    assigned_at: datetime,
    settings: Settings,
    policy: Policy,
    sla_policy: SlaPolicy,
    due_at: datetime,
) -> _Decision:
    """Chọn một kỹ thuật viên trong số những người thỏa ràng buộc cứng.

    Nền thủ công chọn người rảnh sớm nhất và không biết gì hơn.

    Chính sách giả định thì dự kiến trước rồi mới chọn:

    * **Nếu có ít nhất một người kịp hạn**, chỉ chọn trong nhóm đó — bắt đầu sớm
      hơn trước, rồi di chuyển ít hơn, rồi tải nhẹ hơn, rồi `technician_id` để
      kết quả ổn định. Một người bắt đầu sớm hơn nhưng phải đi xa hơn vẫn thắng:
      cam kết là có mặt đúng hạn, không phải là tiết kiệm thang máy.
    * **Nếu không ai kịp hạn**, ticket **vẫn được giao** — bỏ trống một phản ánh
      không làm nó biến mất — nhưng bị đánh dấu AT_RISK và chọn phương án trễ ít
      phút nhất. Trong đời thật nhánh này sẽ hỏi AI trước; bản mô phỏng
      deterministic không gọi mô hình nào và đi thẳng vào phương án dự phòng bảo
      thủ, nên nhãn nguồn quyết định nói đúng như vậy.
    """
    projections = [
        (state, _project(state, ticket, assigned_at, settings, policy, sla_policy, due_at))
        for state in candidates
    ]

    if not policy.sla_aware:
        state, projection = min(projections, key=lambda pair: (pair[0].busy_until, pair[0].technician_id))
        return _Decision(state, projection, policy.decision_source, None, None)

    in_time = [pair for pair in projections if pair[1].start_late_minutes == 0]
    if in_time:
        state, projection = min(
            in_time,
            key=lambda pair: (
                pair[1].work_started_at,
                pair[1].travel,
                pair[0].busy_minutes,
                pair[0].technician_id,
            ),
        )
        return _Decision(state, projection, DecisionSource.SCHEDULER_SIMULATED, DispatchRiskState.SAFE, None)

    state, projection = min(
        projections,
        key=lambda pair: (
            pair[1].start_late_minutes,
            pair[1].travel,
            pair[0].busy_minutes,
            pair[0].technician_id,
        ),
    )
    return _Decision(
        state,
        projection,
        DecisionSource.SCHEDULER_FALLBACK_SIMULATED,
        DispatchRiskState.AT_RISK,
        RiskReason.START_SLA_RISK,
    )


def _promote(
    state: _TechnicianState,
    until: datetime,
    settings: Settings,
    policy: Policy,
    sla_policy: SlaPolicy,
) -> None:
    """Biến việc đã giao thành việc đã bắt đầu, cho tới mốc `until`.

    Đây là chỗ quy tắc "việc đã bắt đầu không bị chen ngang" được thi hành: một
    khi công việc rời khỏi `queue`, không có phản ánh nào đến sau sắp xếp lại
    được nó nữa. Hàng đợi còn lại thì vẫn sắp lại tự do ở mỗi vòng, nên một P2
    đến muộn vẫn vượt lên trước những P1 chưa khởi động.
    """
    while state.queue:
        head = min(state.queue, key=lambda item: policy.queue_key(item.ticket, sla_policy))
        departed = next_shift_open(max(state.busy_until, head.assigned_at))
        travel = travel_minutes(
            state.floor,
            head.ticket.floor,
            base_minutes=settings.travel_base_minutes,
            per_floor_minutes=settings.travel_per_floor_minutes,
        )
        started = advance(departed, timedelta(minutes=travel))
        if started > until:
            return
        completed = advance(started, timedelta(minutes=head.ticket.repair_minutes))
        state.queue.remove(head)
        state.done[head.ticket.ticket_id] = _Execution(
            departed_at=departed, work_started_at=started, completed_at=completed, travel=travel
        )
        state.busy_until = completed
        state.floor = head.ticket.floor
        state.work_minutes += head.ticket.repair_minutes
        state.travel_minutes += travel


# ----------------------------------------------------------------------------
# Dựng một dòng kết quả.
# ----------------------------------------------------------------------------


def _sla_status_started(due_at: datetime, started_at: datetime, priority: Priority, policy: SlaPolicy) -> tuple[SlaStatus, int]:
    late = sla_late_seconds(due_at, started_at, priority, policy) // 60
    return (SlaStatus.LATE_STARTED if late > 0 else SlaStatus.ON_TIME), late


def _sla_status_unstarted(due_at: datetime, limit: datetime, priority: Priority, policy: SlaPolicy) -> tuple[SlaStatus, int]:
    """Chưa ai chạm tới. Đã quá hạn hay chưa quyết định đây là vi phạm hay chưa.

    Quá hạn mà chưa bắt đầu là vi phạm rõ ràng nhất trong cả bảng, nên nó nằm
    trong mẫu số. Chưa tới hạn thì chưa phải vi phạm — và cũng chưa phải thành
    công, nên nó đứng ngoài mẫu số thay vì được đếm là đúng hạn.
    """
    overdue = sla_late_seconds(due_at, limit, priority, policy) // 60
    return (SlaStatus.OPEN_OVERDUE if overdue > 0 else SlaStatus.OPEN_NOT_DUE), overdue


def _settled_outcome(
    ticket: SimTicket,
    scenario: Scenario,
    ready_at: datetime,
    outcome: Outcome,
    reason: Reason,
    sla_policy: SlaPolicy,
    limit: datetime,
    bql_minutes: int,
) -> TicketOutcome:
    """Một phản ánh luồng tự động không được phép phân công.

    Mức khẩn cấp không đánh giá được — nó đang chờ một con người theo đúng
    thiết kế. Còn
    một phản ánh không ai đủ điều kiện nhận thì **vẫn** bị đối chiếu với hạn của
    nó: không ai nhận không làm cho lời hứa với cư dân biến mất, và cho nó ra
    ngoài mẫu số sẽ là cải thiện tỷ lệ bằng cách đánh rơi ticket.
    """
    due_at = ticket.sla_due_at(sla_policy)
    if outcome in MANUAL_OUTCOMES:
        status, late = SlaStatus.NOT_EVALUABLE, 0
    else:
        status, late = _sla_status_unstarted(due_at, limit, ticket.priority, sla_policy)
    return TicketOutcome(
        ticket_id=ticket.ticket_id,
        scenario=scenario,
        outcome=outcome,
        sla_status=status,
        reason=reason,
        priority=ticket.priority,
        floor=ticket.floor,
        unit=ticket.unit,
        required_skill=ticket.required_skill,
        sla_due_at=due_at,
        sla_minutes=ticket.sla_minutes,
        sla_duration_source=ticket.sla_duration_source,
        ready_at=ready_at,
        repair_minutes=ticket.repair_minutes,
        bql_minutes=bql_minutes,
        start_late_minutes=late,
    )


def _assigned_outcome(
    ticket: SimTicket,
    scenario: Scenario,
    ready_at: datetime,
    decision: _Decision,
    execution: _Execution | None,
    sla_policy: SlaPolicy,
    limit: datetime,
    bql_minutes: int,
) -> TicketOutcome:
    """Một phản ánh đã có người. Bắt đầu chưa lại là chuyện khác."""
    due_at = ticket.sla_due_at(sla_policy)
    at_risk = decision.risk_state is DispatchRiskState.AT_RISK
    if execution is None:
        status, late = _sla_status_unstarted(due_at, limit, ticket.priority, sla_policy)
        started = completed = departed = None
        travel = 0
        wait = response = 0
    else:
        status, late = _sla_status_started(due_at, execution.work_started_at, ticket.priority, sla_policy)
        departed = execution.departed_at
        started = execution.work_started_at
        completed = execution.completed_at
        travel = execution.travel
        wait = minutes_between(ready_at, started)
        response = minutes_between(ticket.created_at, started)
    return TicketOutcome(
        ticket_id=ticket.ticket_id,
        scenario=scenario,
        outcome=Outcome.ASSIGNED,
        sla_status=status,
        priority=ticket.priority,
        floor=ticket.floor,
        unit=ticket.unit,
        required_skill=ticket.required_skill,
        sla_due_at=due_at,
        sla_minutes=ticket.sla_minutes,
        sla_duration_source=ticket.sla_duration_source,
        ready_at=ready_at,
        assigned_technician_id=decision.state.technician_id,
        decision_source=decision.decision_source,
        risk_state=decision.risk_state,
        risk_reason=decision.risk_reason,
        projected_start_at=decision.projection.work_started_at,
        projected_start_late_minutes=decision.projection.start_late_minutes,
        # Hệ thống thật sẽ báo Ban quản lý và ghi audit ở đây. Bản mô phỏng chỉ
        # nói rằng nó *sẽ* làm; nó không gửi gì và không ghi gì.
        would_notify_bql=at_risk,
        would_write_audit=at_risk,
        departed_at=departed,
        work_started_at=started,
        completed_at=completed,
        wait_minutes=wait,
        response_minutes=response,
        start_late_minutes=late,
        travel_minutes=travel,
        repair_minutes=ticket.repair_minutes,
        bql_minutes=bql_minutes,
    )


# ----------------------------------------------------------------------------
# Một lần chạy.
# ----------------------------------------------------------------------------


def run_scenario(scenario: ScenarioInput, policy: Policy, limit: datetime | None = None) -> ScenarioResult:
    """Phát lại một luồng trên kịch bản.

    `limit` do `run_comparison` truyền vào để cả hai luồng dừng ở cùng một mốc;
    giá trị mặc định có ở đây cho lần gọi đơn lẻ và tính ra đúng con số đó.
    """
    tickets = list(scenario.tickets)
    technicians = list(scenario.technicians)
    settings = scenario.settings
    sla_policy = scenario.sla_policy
    limit = limit if limit is not None else scenario_horizon(scenario)

    ready_at = {ticket.ticket_id: policy.ready_at(ticket) for ticket in tickets}
    bql = {ticket.ticket_id: policy.bql_minutes(ticket, sla_policy) for ticket in tickets}
    partition = _partition(tickets, technicians, sla_policy)

    outcomes = [
        _settled_outcome(
            ticket, policy.scenario, ready_at[ticket.ticket_id], outcome, reason,
            sla_policy, limit, bql[ticket.ticket_id],
        )
        for ticket, outcome, reason in partition.settled
    ]

    run_start = min(ready_at.values()) if ready_at else utc_now()
    states = {
        technician.technician_id: _TechnicianState(
            technician=technician,
            # Ai cũng rảnh từ lúc phản ánh đầu tiên sẵn sàng. `next_shift_open`
            # được áp ở từng lần lên đường, nên một ngày bắt đầu lúc 21:00 vẫn
            # đặt việc đầu tiên vào 08:00 sáng hôm sau.
            busy_until=next_shift_open(run_start),
            floor=technician.start_floor,
        )
        for technician in technicians
        if technician.is_usable
    }

    # `available_at` phản chiếu đúng cột cùng tên của dispatch event: thời điểm
    # phản ánh trở nên gom được, đẩy về giờ mở cửa như `_defer_before_shift`
    # làm. `enqueued_at` là lúc hàng đợi ghi nhận nó, và là thứ phân biệt hai
    # phản ánh cùng bị đẩy về 08:00.
    available_at = {t.ticket_id: next_shift_open(ready_at[t.ticket_id]) for t in partition.schedulable}
    enqueued_at = {t.ticket_id: ready_at[t.ticket_id] for t in partition.schedulable}

    decisions: dict[str, _Decision] = {}
    for batch in micro_batches(
        partition.schedulable,
        available_at=available_at,
        enqueued_at=enqueued_at,
        interval=settings.micro_batch_interval,
        # Nền thủ công không gom việc: một người xử lý từng phản ánh một.
        size=settings.micro_batch_size if policy.batches else 1,
        order_key=lambda ticket: policy.queue_key(ticket, sla_policy),
    ):
        # Khóa cứng những gì đã thực sự bắt đầu trước mốc này, trước khi lượt gom
        # mới có cơ hội sắp xếp lại bất cứ thứ gì.
        for state in states.values():
            _promote(state, batch.tick, settings, policy, sla_policy)

        for ticket in batch.tickets:
            candidates = [
                states[technician.technician_id]
                for technician in eligible_technicians(ticket, technicians)
            ]
            decision = _select(
                ticket, candidates, batch.tick, settings, policy, sla_policy,
                ticket.sla_due_at(sla_policy),
            )
            decision.state.queue.append(_Assignment(ticket=ticket, assigned_at=batch.tick))
            decision.state.assigned_ticket_count += 1
            decisions[ticket.ticket_id] = decision

    # Rút cạn hàng đợi tới hết thời gian mô phỏng. Những gì còn lại sau mốc này
    # là việc chưa ai bắt đầu, và được báo đúng như vậy.
    for state in states.values():
        _promote(state, limit, settings, policy, sla_policy)

    executions = {
        ticket_id: execution for state in states.values() for ticket_id, execution in state.done.items()
    }
    outcomes.extend(
        _assigned_outcome(
            ticket, policy.scenario, ready_at[ticket.ticket_id], decisions[ticket.ticket_id],
            executions.get(ticket.ticket_id), sla_policy, limit, bql[ticket.ticket_id],
        )
        for ticket in partition.schedulable
    )

    outcomes.sort(key=lambda outcome: (ready_at[outcome.ticket_id], outcome.ticket_id))
    return ScenarioResult(
        scenario=policy.scenario,
        summary=_summarize(outcomes, states, settings, run_start),
        tickets=tuple(outcomes),
    )


# ----------------------------------------------------------------------------
# Tổng hợp.
# ----------------------------------------------------------------------------


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    index = min(len(values) - 1, max(0, ceil(fraction * len(values)) - 1))
    return values[index]


def _utilization(
    states: dict[str, _TechnicianState], settings: Settings, run_start: datetime
) -> tuple[TechnicianLoad, ...]:
    """Tải của từng người, trên cùng một mẫu số.

    Mẫu số là số phút làm việc mà cả lần chạy trải qua — dùng chung cho mọi kỹ
    thuật viên, để hai người so được trực tiếp với nhau. Chặn dưới ở một ngày
    làm việc, vì một lần chạy chỉ có một công việc ngắn không nên báo 400%.
    """
    last = max((state.busy_until for state in states.values()), default=next_shift_open(run_start))
    span = int(working_seconds_between(next_shift_open(run_start), last) // 60)
    capacity = max(WORKING_MINUTES_PER_DAY, span)
    return tuple(
        TechnicianLoad(
            technician_id=state.technician_id,
            work_minutes=state.work_minutes,
            travel_minutes=state.travel_minutes,
            assigned_ticket_count=state.assigned_ticket_count,
            capacity_minutes=capacity,
            utilization_percent=round((state.work_minutes + state.travel_minutes) / capacity * 100, 1),
        )
        for state in sorted(states.values(), key=lambda state: state.technician_id)
    )


def _summarize(
    outcomes: list[TicketOutcome],
    states: dict[str, _TechnicianState],
    settings: Settings,
    run_start: datetime,
) -> ScenarioSummary:
    started = [o for o in outcomes if o.has_started]
    evaluable = [o for o in outcomes if o.sla_status in EVALUABLE_STATUSES]
    on_time = [o for o in outcomes if o.sla_status is SlaStatus.ON_TIME]
    late_started = [o for o in outcomes if o.sla_status is SlaStatus.LATE_STARTED]
    open_overdue = [o for o in outcomes if o.sla_status is SlaStatus.OPEN_OVERDUE]
    open_not_due = [o for o in outcomes if o.sla_status is SlaStatus.OPEN_NOT_DUE]
    not_evaluable = [o for o in outcomes if o.sla_status is SlaStatus.NOT_EVALUABLE]

    waits = sorted(o.wait_minutes for o in started)
    responses = sorted(o.response_minutes for o in started)
    # Phút trễ đến từ hai nguồn và cộng được với nhau: một cái đo tới lúc bắt
    # đầu, cái kia đo tới hết thời gian mô phỏng, và cả hai đều là "đã quá hạn
    # bao lâu mà chưa được xử lý".
    total_late = sum(o.start_late_minutes for o in late_started) + sum(
        o.start_late_minutes for o in open_overdue
    )
    breaches = len(late_started) + len(open_overdue)
    completions = [o.completed_at for o in outcomes if o.completed_at is not None]

    return ScenarioSummary(
        total_tickets=len(outcomes),
        assigned_tickets=len([o for o in outcomes if o.is_assigned]),
        started_tickets=len(started),
        sla_evaluable_tickets=len(evaluable),
        sla_on_time_tickets=len(on_time),
        sla_late_started_tickets=len(late_started),
        sla_open_overdue_tickets=len(open_overdue),
        sla_open_not_due_tickets=len(open_not_due),
        sla_not_evaluable_tickets=len(not_evaluable),
        # None chứ không phải 0% hay 100% khi không có gì đánh giá được: một tỷ
        # lệ trên mẫu số rỗng không phải là một tỷ lệ.
        compliance_rate=round(len(on_time) / len(evaluable), 4) if evaluable else None,
        total_start_late_minutes=total_late,
        average_start_late_minutes=round(total_late / breaches, 1) if breaches else 0.0,
        average_wait_minutes=round(sum(waits) / len(waits), 1) if waits else 0.0,
        p95_wait_minutes=_percentile(waits, 0.95),
        average_response_minutes=round(sum(responses) / len(responses), 1) if responses else 0.0,
        p95_response_minutes=_percentile(responses, 0.95),
        total_travel_minutes=sum(o.travel_minutes for o in outcomes),
        bql_effort_minutes=sum(o.bql_minutes for o in outcomes),
        at_risk_tickets=len([o for o in outcomes if o.risk_state is DispatchRiskState.AT_RISK]),
        last_completed_at=max(completions) if completions else None,
        technician_utilization=_utilization(states, settings, run_start),
    )


# ----------------------------------------------------------------------------
# Cả hai luồng.
# ----------------------------------------------------------------------------


def run_comparison(scenario: ScenarioInput) -> ComparisonResult:
    """Chạy cả hai luồng trên cùng đầu vào và so chúng với nhau.

    Mỗi luồng có bộ trạng thái kỹ thuật viên riêng, nên lịch của luồng này không
    thể để lại một người đứng nhầm tầng cho luồng kia.
    """
    # Một mốc kết thúc, tính một lần và đưa cho cả hai. Mỗi luồng trả chi phí
    # phân tích khác nhau trước khi bắt đầu làm việc, nên nếu để mỗi bên tự suy
    # ra thì hai lần chạy sẽ dừng ở hai thời điểm khác nhau và bảng so sánh sẽ so
    # mẫu số chứ không so lịch làm việc.
    limit = scenario_horizon(scenario)
    old = run_scenario(scenario, old_app_policy(scenario.settings.old_app), limit)
    new = run_scenario(scenario, new_app_policy(scenario.settings.new_app), limit)

    return ComparisonResult(
        generated_at=utc_now(),
        scenario_name=scenario.scenario_name,
        sla_policy=scenario.sla_policy,
        building=scenario.building,
        settings=scenario.settings,
        horizon_end=limit,
        old_app=old,
        new_app=new,
        comparison=_comparison(old, new),
        warnings=_warnings(scenario),
    )


def _comparison(old: ScenarioResult, new: ScenarioResult) -> Comparison:
    """App mới đo với app cũ, một quy ước dấu cho mọi trường.

    Dương nghĩa là app mới tốt hơn. Mọi thứ được gọi là "tiết kiệm" hay "tránh
    được" là `OLD_APP − NEW_APP`; `compliance_rate_gain` là `NEW_APP − OLD_APP`
    vì ở đó nhiều hơn mới là tốt hơn. Đảo dấu ở đây một lần, đúng một chỗ, là
    cách để không ai phải đảo lại ở màn hình.
    """
    bql_saved = old.summary.bql_effort_minutes - new.summary.bql_effort_minutes
    gain = None
    if old.summary.compliance_rate is not None and new.summary.compliance_rate is not None:
        gain = round(new.summary.compliance_rate - old.summary.compliance_rate, 4)
    return Comparison(
        bql_minutes_saved=bql_saved,
        bql_hours_saved=round(bql_saved / 60, 2),
        late_starts_avoided=(
            old.summary.sla_late_started_tickets - new.summary.sla_late_started_tickets
        ),
        start_late_minutes_avoided=(
            old.summary.total_start_late_minutes - new.summary.total_start_late_minutes
        ),
        average_response_minutes_saved=round(
            old.summary.average_response_minutes - new.summary.average_response_minutes, 1
        ),
        p95_response_minutes_saved=old.summary.p95_response_minutes - new.summary.p95_response_minutes,
        travel_minutes_saved=old.summary.total_travel_minutes - new.summary.total_travel_minutes,
        compliance_rate_gain=gain,
    )


def _warnings(scenario: ScenarioInput) -> tuple[str, ...]:
    """Ghi chú đáng hiện nhưng không đáng làm hỏng lần chạy.

    Tiếng Việt, vì chúng được in nguyên văn lên màn hình của Ban quản lý chứ
    không được máy nào phân tích.
    """
    warnings: list[str] = [
        "“App mới” là mô phỏng một chính sách giả định, CHƯA áp dụng vào production. "
        "Bộ điều phối đang chạy xếp hàng đợi theo cách khác."
    ]
    if scenario.sla_policy is SlaPolicy.SERVICE_HOURS_DRAFT_V1:
        warnings.append(
            "SERVICE_HOURS_DRAFT_V1 là chính sách đề xuất, CHƯA áp dụng cho production. "
            "Hạn SLA trong kết quả này không phải hạn đang cam kết với cư dân."
        )
    overrides = [t for t in scenario.tickets if t.sla_duration_source is SlaDurationSource.INPUT_OVERRIDE]
    if overrides:
        named = ", ".join(
            f"{t.ticket_id} ({t.priority.value}: {t.sla_minutes} thay vì "
            f"{POLICY_SLA_MINUTES[scenario.sla_policy][t.priority]})"
            for t in overrides[:5]
        )
        more = f" và {len(overrides) - 5} ticket khác" if len(overrides) > 5 else ""
        warnings.append(
            f"{len(overrides)} ticket dùng hạn SLA tự đặt, khác chính sách {scenario.sla_policy.value}: "
            f"{named}{more}. Con số SLA của những ticket này không đo theo chính sách đang chạy."
        )
    fallbacks = [t for t in scenario.tickets if t.repair_minutes_source == "P80_FALLBACK"]
    if fallbacks:
        warnings.append(
            f"{len(fallbacks)} ticket thiếu repair_minutes, đã dùng ước lượng P80 nội bộ theo loại sự cố."
        )
    idle = [t for t in scenario.technicians if not t.is_usable]
    if idle:
        warnings.append(f"{len(idle)} kỹ thuật viên không hoạt động hoặc bận, không được xét phân công.")
    if not any(t.is_usable for t in scenario.technicians):
        warnings.append("Không có kỹ thuật viên nào khả dụng: mọi ticket sẽ chuyển Ban quản lý xử lý tay.")
    return tuple(warnings)


__all__ = [
    "eligible_technicians",
    "minutes_between",
    "run_comparison",
    "run_scenario",
    "scenario_horizon",
]
