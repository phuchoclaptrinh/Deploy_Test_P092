"""Bộ từ vựng của trình mô phỏng: một kịch bản vào, hai kết quả ra.

Cố tình tách khỏi `src.database.models`. Không có gì ở đây được lưu xuống:
một lần chạy là một request và một response, không có id nào sống lâu hơn lời
gọi, và `completed_at` ở đây không bao giờ được ghi vào `Ticket.completed_at`.

Ba điều đáng đọc trước phần còn lại của gói này.

**Hai luồng, và không luồng nào là production.** `OLD_APP` là nền thủ công:
người đọc, người phân loại, người điều phối từng phản ánh. `NEW_APP` là luồng
tự động theo **chính sách giả định** — P2 trước mọi P1 chưa bắt đầu, chọn kỹ
thuật viên theo thời điểm bắt đầu, và có nhánh dự phòng khi không ai kịp hạn.
Chính sách đó **chưa được áp dụng vào production**, nên không có cột nào trong
gói này mang nhãn "production" và không có hàm nào ở đây tuyên bố parity.

**SLA được tính tại thời điểm bắt đầu sửa, không phải lúc hoàn tất.** Ba mốc
được tách rời và không suy ra được từ nhau: `departed_at` (kỹ thuật viên rời
việc trước), `work_started_at` (tới nơi và bắt đầu), `completed_at` (sửa xong).
Công thức là `work_started_at = departed_at + thời gian di chuyển`, và cam kết
với cư dân được đo bằng `work_started_at <= sla_due_at`. Thời gian hoàn tất chỉ
dùng để tính công suất và lịch của kỹ thuật viên.

**Chưa bắt đầu mà đã quá hạn vẫn là vi phạm.** `ScenarioSummary` công bố mẫu số
của mình, và mẫu số đó *bao gồm* `OPEN_OVERDUE`. Một tỷ lệ đúng hạn giấu mẫu số
là một tỷ lệ có thể cải thiện bằng cách đánh rơi ticket.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum

from src.config import get_settings
from src.domain.sla_clock import SlaPolicy, add_sla_duration
from src.models.enums import DispatchRiskState, Priority

#: Một ngày làm việc, tính bằng phút. 08:00-18:00 (`shift.SHIFT_LENGTH`), nêu ở
#: đây vì nó là mẫu số của mọi con số công suất.
WORKING_MINUTES_PER_DAY = 600


# ----------------------------------------------------------------------------
# Nhịp gom batch.
# ----------------------------------------------------------------------------
#
# Đọc từ `src.config`, không viết thành hằng số ở đây. Cơ chế gom micro-batch là
# cơ chế của bộ điều phối đang chạy, nên nhịp mô phỏng phải là nhịp thật; chép
# `750` vào file này sẽ đúng cho tới ngày ai đó chỉnh production.
#
# Lưu ý: mô phỏng đúng *cơ chế gom* không có nghĩa là mô phỏng đúng *chính sách*.
# Thứ tự xử lý bên trong một batch ở đây là chính sách giả định của `NEW_APP`,
# không phải thứ tự production đang dùng.


def production_micro_batch_interval_ms() -> int:
    """Bộ điều phối thức dậy gom việc sau mỗi bao nhiêu mili-giây."""
    return get_settings().dispatch_micro_batch_interval_ms


def production_micro_batch_size() -> int:
    """Một lượt gom được nhiều nhất bao nhiêu ticket."""
    return get_settings().dispatch_micro_batch_size


class Scenario(str, Enum):  # noqa: UP042
    """Hai luồng của một lần so sánh.

    `OLD_APP` là nền thủ công: một người phân loại và một người điều phối từng
    phản ánh, xử lý theo thứ tự đến. `NEW_APP` là luồng tự động theo chính sách
    giả định mô tả trong `policies.py`. Không luồng nào mô tả hành vi production
    hiện tại, và không nhãn nào trong gói này được nói ngược lại.
    """

    OLD_APP = "OLD_APP"
    NEW_APP = "NEW_APP"


class Outcome(str, Enum):  # noqa: UP042
    """Trình mô phỏng kết luận gì về một ticket. Ba giá trị, không hơn.

    `ASSIGNED` chỉ có nghĩa là ticket đã có kỹ thuật viên — **không** có nghĩa là
    công việc đã bắt đầu. Ticket được phân nhưng tới hết thời gian mô phỏng vẫn
    chưa tới lượt vẫn mang `ASSIGNED`, với `work_started_at` để trống; trạng thái
    SLA mới là chỗ nói điều đó.
    """

    ASSIGNED = "ASSIGNED"
    #: Mức khẩn cấp của chính sách V1 (P3). Giữ nguyên tên để mọi kết quả V1 đã
    #: ghi lại vẫn đọc được y hệt.
    REQUIRES_MANUAL_P3_REVIEW = "REQUIRES_MANUAL_P3_REVIEW"
    #: Mức khẩn cấp của rubric V2 (P5). Một giá trị riêng chứ không đổi tên giá
    #: trị cũ: hai lần chạy dưới hai chính sách nói về hai thang điểm khác nhau,
    #: và gộp chúng vào một nhãn sẽ khiến một bản so sánh cũ đọc sai.
    REQUIRES_MANUAL_P5_REVIEW = "REQUIRES_MANUAL_P5_REVIEW"
    NO_ELIGIBLE_TECHNICIAN = "NO_ELIGIBLE_TECHNICIAN"


class SlaStatus(str, Enum):  # noqa: UP042
    """Cam kết với cư dân, đo tại **thời điểm bắt đầu sửa**.

    Bắt đầu trước hạn rồi sửa lâu vẫn là `ON_TIME`: cam kết là có người tới xử
    lý, không phải là sửa xong trong bao lâu. Một ticket chưa ai chạm tới mà đã
    qua hạn thì `OPEN_OVERDUE` — và nó nằm trong mẫu số, vì đó là vi phạm rõ
    ràng nhất trong cả bảng.
    """

    #: Đã bắt đầu, và `work_started_at <= sla_due_at`.
    ON_TIME = "ON_TIME"
    #: Đã bắt đầu, nhưng `work_started_at > sla_due_at`.
    LATE_STARTED = "LATE_STARTED"
    #: Tới hết thời gian mô phỏng vẫn chưa bắt đầu, và đã quá hạn. Vi phạm.
    OPEN_OVERDUE = "OPEN_OVERDUE"
    #: Chưa bắt đầu nhưng cũng chưa tới hạn. Chưa phải vi phạm, và cũng chưa
    #: phải thành công — nên không nằm trong mẫu số.
    OPEN_NOT_DUE = "OPEN_NOT_DUE"
    #: Mức khẩn cấp chuyển BQL xử lý tay (P3 ở V1, P5 ở V2), hoặc không có hạn
    #: SLA hợp lệ để đối chiếu.
    NOT_EVALUABLE = "NOT_EVALUABLE"


#: Ba trạng thái tạo nên mẫu số của `compliance_rate`. `OPEN_NOT_DUE` và
#: `NOT_EVALUABLE` đứng ngoài, và được công bố riêng thay vì bị giấu đi.
EVALUABLE_STATUSES = frozenset({SlaStatus.ON_TIME, SlaStatus.LATE_STARTED, SlaStatus.OPEN_OVERDUE})


class Reason(str, Enum):  # noqa: UP042
    """Vì sao một ticket không được phân công tự động."""

    P3_MANUAL_REVIEW = "P3_MANUAL_REVIEW"
    P5_MANUAL_REVIEW = "P5_MANUAL_REVIEW"
    MISSING_SKILL = "MISSING_SKILL"
    TECHNICIAN_UNAVAILABLE = "TECHNICIAN_UNAVAILABLE"
    TECHNICIAN_EXCLUDED = "TECHNICIAN_EXCLUDED"


class RiskReason(str, Enum):  # noqa: UP042
    """Vì sao một phân công bị đánh dấu AT_RISK."""

    #: Không kỹ thuật viên hợp lệ nào bắt đầu được trước hạn SLA.
    START_SLA_RISK = "START_SLA_RISK"


class DecisionSource(str, Enum):  # noqa: UP042
    """Ai đã chọn kỹ thuật viên trong lần chạy này.

    Không giá trị nào ở đây tuyên bố một mô hình đã thực sự quyết định.
    `SCHEDULER_FALLBACK_SIMULATED` được đặt tên như vậy có chủ ý: trong đời thật
    nhánh này sẽ hỏi AI trước, còn bản mô phỏng deterministic thì không gọi mô
    hình nào cả và đi thẳng vào phương án dự phòng bảo thủ. Chất lượng riêng của
    lựa chọn AI **không** được tính vào kết quả.
    """

    #: Nhánh thường của `NEW_APP`: có ít nhất một kỹ thuật viên kịp hạn.
    SCHEDULER_SIMULATED = "SCHEDULER_SIMULATED"
    #: Nhánh rủi ro: không ai kịp hạn, chọn phương án trễ ít nhất.
    SCHEDULER_FALLBACK_SIMULATED = "SCHEDULER_FALLBACK_SIMULATED"
    #: `OLD_APP`: một người điều phối chọn bằng tay.
    MANUAL_SIMULATED = "MANUAL_SIMULATED"


class SlaDurationSource(str, Enum):  # noqa: UP042
    """Thời hạn SLA của một ticket đến từ đâu.

    Cả điểm của việc chạy một tập dữ liệu dưới hai chính sách là chính sách
    quyết định hạn. Kịch bản vẫn được phép ghi đè — "nếu ta hứa P2 bốn tiếng thì
    sao?" là câu hỏi có thật — nhưng khi đó dòng đó phải nói ra, nếu không so
    sánh sẽ âm thầm thôi còn là so sánh giữa hai chính sách.
    """

    #: `POLICY_SLA_MINUTES[policy][priority]`. Mặc định, và là giá trị duy nhất
    #: một kịch bản bỏ trống `sla_minutes` có thể tạo ra.
    POLICY = "POLICY"
    #: Kịch bản ghi một thời hạn khác với chính sách.
    INPUT_OVERRIDE = "INPUT_OVERRIDE"


# ----------------------------------------------------------------------------
# Đầu vào.
# ----------------------------------------------------------------------------


#: Mức mà mỗi chính sách giao cho con người thay vì đưa vào hàng đợi.
MANUAL_PRIORITY: dict[SlaPolicy, Priority] = {
    SlaPolicy.WALL_CLOCK_V1: Priority.P3,
    SlaPolicy.SERVICE_HOURS_DRAFT_V1: Priority.P3,
    SlaPolicy.SERVICE_HOURS_RISK_V2: Priority.P5,
}

#: Kết luận và lý do tương ứng cho mức đó, theo chính sách.
MANUAL_OUTCOME: dict[SlaPolicy, tuple[Outcome, Reason]] = {
    SlaPolicy.WALL_CLOCK_V1: (Outcome.REQUIRES_MANUAL_P3_REVIEW, Reason.P3_MANUAL_REVIEW),
    SlaPolicy.SERVICE_HOURS_DRAFT_V1: (Outcome.REQUIRES_MANUAL_P3_REVIEW, Reason.P3_MANUAL_REVIEW),
    SlaPolicy.SERVICE_HOURS_RISK_V2: (Outcome.REQUIRES_MANUAL_P5_REVIEW, Reason.P5_MANUAL_REVIEW),
}

#: Mọi kết luận "chuyển BQL xử lý tay", bất kể chính sách. Dùng ở chỗ chỉ cần
#: biết ticket có đang chờ người hay không — ví dụ khi quyết định trạng thái SLA
#: là `NOT_EVALUABLE`.
MANUAL_OUTCOMES: frozenset[Outcome] = frozenset(outcome for outcome, _reason in MANUAL_OUTCOME.values())

#: Những mức mỗi chính sách chấp nhận trong đầu vào. V1 chỉ hiểu ba mức và chưa
#: từng được hiệu chỉnh cho P4/P5; nhận thêm sẽ cho ra một con số so sánh vô
#: nghĩa mà không báo lỗi.
POLICY_PRIORITIES: dict[SlaPolicy, tuple[Priority, ...]] = {
    SlaPolicy.WALL_CLOCK_V1: (Priority.P1, Priority.P2, Priority.P3),
    SlaPolicy.SERVICE_HOURS_DRAFT_V1: (Priority.P1, Priority.P2, Priority.P3),
    SlaPolicy.SERVICE_HOURS_RISK_V2: (
        Priority.P1,
        Priority.P2,
        Priority.P3,
        Priority.P4,
        Priority.P5,
    ),
}


@dataclass(frozen=True)
class SimTicket:
    """Một phản ánh, dưới dạng trình mô phỏng cần.

    `sla_minutes` tính bằng phút chứ không phải giờ, để năm phút của P3 viết
    được chính xác mà không cần phân số.
    """

    ticket_id: str
    created_at: datetime
    floor: int
    unit: str
    issue_type: str
    priority: Priority
    sla_minutes: int
    repair_minutes: int
    required_skill: str
    need_hand_categorized: bool
    score_total: float = 0.0
    #: Kỹ thuật viên bị dữ liệu đầu vào loại khỏi ticket này — đã nghỉ việc,
    #: đang tranh chấp với cư dân, hoặc vừa bị trả lại chính ticket này. Một
    #: ràng buộc cứng, không phải một mức ưu tiên.
    excluded_technician_ids: frozenset[str] = frozenset()
    #: `INPUT` hoặc `P80_FALLBACK`. Không ảnh hưởng lịch, chỉ để đối soát.
    repair_minutes_source: str = "INPUT"
    #: `sla_minutes` đến từ chính sách hay từ kịch bản. Do `validation` đặt.
    sla_duration_source: SlaDurationSource = SlaDurationSource.POLICY

    @property
    def is_p3(self) -> bool:
        """Giữ lại cho V1. Luồng dùng chung hỏi `requires_manual_review`."""
        return self.priority is Priority.P3

    def requires_manual_review(self, policy: SlaPolicy) -> bool:
        """Mức này có phải mức BQL xử lý tay dưới chính sách đang chạy không?

        Hỏi theo chính sách chứ không theo một hằng số, vì thang điểm đã đảo:
        ở V1 mức khẩn cấp là P3, ở V2 là P5. Viết cứng `is Priority.P3` sẽ đẩy
        P3 của V2 — một mức mười giờ hoàn toàn bình thường — ra khỏi luồng tự
        động, và đồng thời cho P5 đi thẳng vào đó.
        """
        return self.priority is MANUAL_PRIORITY[policy]

    def sla_due_at(self, policy: SlaPolicy) -> datetime:
        """Hạn của ticket này dưới một chính sách.

        Là một phương thức chứ không phải property, vì câu trả lời phụ thuộc
        chính sách; một property sẽ mời gọi người gọi quên mất rằng cùng một
        ticket có hai hạn khác nhau dưới hai đồng hồ — mà đó chính là câu hỏi
        màn hình này sinh ra để trả lời.
        """
        return add_sla_duration(self.created_at, timedelta(minutes=self.sla_minutes), self.priority, policy)


@dataclass(frozen=True)
class SimTechnician:
    """Một kỹ thuật viên, dưới dạng trình mô phỏng cần.

    `skills` là thẻ chữ tự do, đối chiếu với `SimTicket.required_skill` và đã
    được `validation` hạ về chữ thường một lần. Trình mô phỏng không dùng UUID
    danh mục: một màn hình what-if được cho ăn kịch bản dán vào, không phải khóa
    ngoại vào catalog.
    """

    technician_id: str
    skills: frozenset[str]
    start_floor: int = 1
    is_active: bool = True
    is_available: bool = True

    @property
    def is_usable(self) -> bool:
        return self.is_active and self.is_available

    def has_skill(self, required_skill: str) -> bool:
        return required_skill.strip().lower() in self.skills


# ----------------------------------------------------------------------------
# Cấu hình.
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildingConfig:
    floor_count: int = 30
    units_per_floor: int = 7


@dataclass(frozen=True)
class OldAppSettings:
    """Mô hình chi phí của nền thủ công.

    Cả hai con số được tính cho **mọi** phản ánh, vì đó đúng là những gì quy
    trình cũ làm: có người đọc từng cái và có người điều phối từng cái.
    """

    manual_category_minutes: int = 10
    manual_dispatch_minutes: int = 8

    @property
    def total_minutes(self) -> int:
        return self.manual_category_minutes + self.manual_dispatch_minutes


@dataclass(frozen=True)
class NewAppSettings:
    """Chi phí phân tích của luồng tự động.

    `manual_review_minutes` chỉ tính cho phản ánh mà mô hình không tự phân loại
    được, cộng với P3 vốn luôn phải qua tay người.
    """

    ai_classification_minutes: int = 1
    manual_review_minutes: int = 10


@dataclass(frozen=True)
class Settings:
    """Các núm vặn một kịch bản được phép chỉnh."""

    travel_base_minutes: int = 3
    travel_per_floor_minutes: int = 1
    #: Nhịp gom batch. Bộ điều phối thức dậy theo nhịp này và gom mọi thứ đang
    #: sẵn sàng; hai phản ánh rơi vào cùng một nhịp được quyết định cùng lúc,
    #: theo độ ưu tiên, chứ không theo thứ tự đến.
    micro_batch_interval_ms: int = field(default_factory=production_micro_batch_interval_ms)
    #: Một lượt gom được nhiều nhất bao nhiêu phản ánh. Cái thứ hai mươi mốt
    #: phải chờ nhịp sau, khẩn cấp đến mấy cũng vậy.
    micro_batch_size: int = field(default_factory=production_micro_batch_size)
    #: Bản phát lại được chạy bao xa tính từ phản ánh đầu tiên trước khi ngừng
    #: giao việc. Một tồn đọng cần ba tuần tăng ca là một phát hiện, không phải
    #: một lịch làm việc, và đây là thứ chặn vòng lặp biến nó thành lịch.
    simulation_horizon_days: int = 14
    old_app: OldAppSettings = field(default_factory=OldAppSettings)
    new_app: NewAppSettings = field(default_factory=NewAppSettings)

    @property
    def micro_batch_interval(self) -> timedelta:
        return timedelta(milliseconds=self.micro_batch_interval_ms)


@dataclass(frozen=True)
class ScenarioInput:
    """Cả một kịch bản: tòa nhà, đồng hồ, các núm vặn, và dữ liệu."""

    scenario_name: str
    building: BuildingConfig
    sla_policy: SlaPolicy
    settings: Settings
    technicians: tuple[SimTechnician, ...]
    tickets: tuple[SimTicket, ...]


# ----------------------------------------------------------------------------
# Kết quả.
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class TicketOutcome:
    """Một dòng của bảng chi tiết theo ticket.

    Ba mốc thời gian được tách rời và không mốc nào suy ra được từ mốc khác:

    * `departed_at` — kỹ thuật viên rời công việc trước và lên đường;
    * `work_started_at` — tới nơi và thực sự bắt đầu sửa. **Đây là mốc SLA.**
    * `completed_at` — sửa xong. Chỉ dùng cho công suất và lịch kỹ thuật viên.

    `projected_*` là những gì lần chạy *dự kiến* tại thời điểm quyết định phân
    công. Nó có thể lệch khỏi kết quả thật khi một P2 đến sau chen lên trước một
    P1 chưa bắt đầu — và giữ cả hai là cách duy nhất để đọc lại được vì sao một
    phân công đã bị đánh dấu AT_RISK.
    """

    ticket_id: str
    scenario: Scenario
    outcome: Outcome
    sla_status: SlaStatus
    priority: Priority
    floor: int
    unit: str
    required_skill: str
    sla_due_at: datetime
    #: Thời hạn đứng sau `sla_due_at`, và nó đến từ đâu.
    sla_minutes: int
    sla_duration_source: SlaDurationSource
    #: Thời điểm phản ánh trở nên có thể điều phối được, sau chi phí phân tích
    #: của luồng đó.
    ready_at: datetime
    reason: Reason | None = None
    assigned_technician_id: str | None = None
    decision_source: DecisionSource | None = None
    risk_state: DispatchRiskState | None = None
    risk_reason: RiskReason | None = None
    #: Lần chạy dự kiến gì lúc quyết định phân công.
    projected_start_at: datetime | None = None
    projected_start_late_minutes: int = 0
    #: Một phân công không bảo đảm SLA vẫn được giao — và đồng thời báo BQL và
    #: ghi audit. Hai cờ này là những gì hệ thống thật *sẽ* làm; bản mô phỏng
    #: không gửi gì và không ghi gì.
    would_notify_bql: bool = False
    would_write_audit: bool = False
    #: Ngày làm việc thật sự diễn ra thế nào.
    departed_at: datetime | None = None
    work_started_at: datetime | None = None
    completed_at: datetime | None = None
    #: `ready_at` tới `work_started_at`: thời gian nằm hàng đợi.
    wait_minutes: int = 0
    #: `created_at` tới `work_started_at`. Đây mới là con số so sánh được giữa
    #: hai luồng: app cũ tiêu các phút thủ công *trước* `ready_at`, nên đo từ đó
    #: sẽ ghi công cho nó chính độ trễ nó gây ra.
    response_minutes: int = 0
    #: Trễ tại thời điểm bắt đầu, đo bằng đồng hồ của chính sách đang chạy.
    #: `LATE_STARTED` đo tới `work_started_at`; `OPEN_OVERDUE` đo tới hết thời
    #: gian mô phỏng. Mọi trạng thái khác là 0.
    start_late_minutes: int = 0
    travel_minutes: int = 0
    repair_minutes: int = 0
    #: Số phút thời gian của Ban quản lý mà ticket này tiêu tốn trong lần chạy.
    bql_minutes: int = 0

    @property
    def is_assigned(self) -> bool:
        return self.outcome is Outcome.ASSIGNED

    @property
    def has_started(self) -> bool:
        return self.work_started_at is not None

    @property
    def is_late_start(self) -> bool:
        return self.sla_status is SlaStatus.LATE_STARTED

    @property
    def is_evaluable(self) -> bool:
        return self.sla_status in EVALUABLE_STATUSES


@dataclass(frozen=True)
class TechnicianLoad:
    """Một ngày của một kỹ thuật viên, theo cách lần chạy này chất tải.

    `utilization_percent` có tính di chuyển: một người mất chín mươi phút đi
    thang máy thì không rảnh chín mươi phút đó, và một mô hình nói ngược lại sẽ
    khiến lịch xếp lộn xộn trông như lịch tốt.
    """

    technician_id: str
    work_minutes: int = 0
    travel_minutes: int = 0
    assigned_ticket_count: int = 0
    #: Số phút làm việc mà lần chạy trải qua — mẫu số của phần trăm.
    capacity_minutes: int = WORKING_MINUTES_PER_DAY
    utilization_percent: float = 0.0

    @property
    def busy_minutes(self) -> int:
        return self.work_minutes + self.travel_minutes


@dataclass(frozen=True)
class ScenarioSummary:
    """Một luồng bằng con số, với mẫu số để lộ ra ngoài.

    `sla_evaluable_tickets` là mẫu số của `compliance_rate`, và nó **bao gồm**
    `OPEN_OVERDUE`: một ticket đã qua hạn mà chưa ai bắt đầu là vi phạm, không
    phải là dữ liệu thiếu. Hai nhóm nằm ngoài mẫu số — chưa tới hạn, và không
    đánh giá được — được công bố ngay bên cạnh.
    """

    total_tickets: int = 0
    #: Đã có kỹ thuật viên. Không đồng nghĩa với đã bắt đầu.
    assigned_tickets: int = 0
    started_tickets: int = 0
    #: ON_TIME + LATE_STARTED + OPEN_OVERDUE. Mẫu số.
    sla_evaluable_tickets: int = 0
    sla_on_time_tickets: int = 0
    sla_late_started_tickets: int = 0
    sla_open_overdue_tickets: int = 0
    sla_open_not_due_tickets: int = 0
    sla_not_evaluable_tickets: int = 0
    #: `sla_on_time_tickets / sla_evaluable_tickets`, hoặc None khi mẫu số rỗng
    #: — thay vì một con số 0% hay 100% gây hiểu nhầm.
    compliance_rate: float | None = None
    total_start_late_minutes: int = 0
    average_start_late_minutes: float = 0.0
    average_wait_minutes: float = 0.0
    p95_wait_minutes: int = 0
    average_response_minutes: float = 0.0
    p95_response_minutes: int = 0
    total_travel_minutes: int = 0
    #: Số phút thời gian Ban quản lý mà luồng này tiêu tốn.
    bql_effort_minutes: int = 0
    at_risk_tickets: int = 0
    last_completed_at: datetime | None = None
    technician_utilization: tuple[TechnicianLoad, ...] = ()


@dataclass(frozen=True)
class ScenarioResult:
    scenario: Scenario
    summary: ScenarioSummary
    tickets: tuple[TicketOutcome, ...]

    @property
    def late_started_tickets(self) -> tuple[TicketOutcome, ...]:
        return tuple(ticket for ticket in self.tickets if ticket.is_late_start)

    @property
    def at_risk_tickets(self) -> tuple[TicketOutcome, ...]:
        return tuple(ticket for ticket in self.tickets if ticket.risk_state is DispatchRiskState.AT_RISK)


@dataclass(frozen=True)
class Comparison:
    """App mới đo với app cũ. Một quy ước dấu, áp dụng cho mọi trường.

    **Dương nghĩa là app mới tốt hơn app cũ.** Mọi trường `_saved` / `_avoided`
    là `OLD_APP − NEW_APP`; `compliance_rate_gain` là `NEW_APP − OLD_APP`, vì ở
    đó nhiều hơn mới là tốt hơn. Frontend không phải đảo dấu ở bất kỳ đâu.
    """

    bql_minutes_saved: int = 0
    bql_hours_saved: float = 0.0
    late_starts_avoided: int = 0
    start_late_minutes_avoided: int = 0
    average_response_minutes_saved: float = 0.0
    p95_response_minutes_saved: int = 0
    travel_minutes_saved: int = 0
    compliance_rate_gain: float | None = None


@dataclass(frozen=True)
class ComparisonResult:
    generated_at: datetime
    scenario_name: str
    sla_policy: SlaPolicy
    building: BuildingConfig
    settings: Settings
    #: Mốc mà mọi ticket chưa bắt đầu được đối chiếu tới.
    horizon_end: datetime
    old_app: ScenarioResult
    new_app: ScenarioResult
    comparison: Comparison
    warnings: tuple[str, ...] = ()


def utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "EVALUABLE_STATUSES",
    "WORKING_MINUTES_PER_DAY",
    "BuildingConfig",
    "Comparison",
    "ComparisonResult",
    "DecisionSource",
    "NewAppSettings",
    "OldAppSettings",
    "Outcome",
    "Reason",
    "RiskReason",
    "Scenario",
    "ScenarioInput",
    "ScenarioResult",
    "ScenarioSummary",
    "Settings",
    "SimTechnician",
    "SimTicket",
    "SlaDurationSource",
    "SlaStatus",
    "TechnicianLoad",
    "TicketOutcome",
    "production_micro_batch_interval_ms",
    "production_micro_batch_size",
    "utc_now",
]
