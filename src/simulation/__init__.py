"""Mô phỏng công suất & SLA cho Ban quản lý — một màn hình chỉ đọc.

Một câu hỏi, hỏi từ một màn hình: *với đúng tập phản ánh này và đúng đội kỹ
thuật viên này, quy trình thủ công cũ đã làm gì, và một luồng tự động sẽ làm
gì?* Câu trả lời có theo từng ticket — cái nào bắt đầu trễ, trễ bao nhiêu phút,
vì sao, ai đang giữ — cộng với hai bản tổng hợp đặt cạnh nhau.

Hai luồng, và không luồng nào là production
--------------------------------------------
* ``OLD_APP`` — nền thủ công. Đến trước xử lý trước, một người đứng trước mọi
  phản ánh.
* ``NEW_APP`` — **mô phỏng một chính sách giả định**: P2 trước mọi P1 chưa bắt
  đầu, chọn kỹ thuật viên theo thời điểm bắt đầu sửa, và khi không ai kịp hạn
  thì vẫn giao việc kèm cảnh báo Ban quản lý. Chính sách này **chưa được áp dụng
  vào production** — bộ điều phối đang chạy xếp hàng đợi theo slack còn lại — nên
  không cột nào ở đây mang nhãn "production".

Các quy tắc gói này được xây dưới, đều mang tính cấu trúc chứ không phải quy ước
-------------------------------------------------------------------------------
* **Không ghi gì cả.** Không ticket, không phân công, không dispatch event,
  không dòng nào hết. Không có session trong gói này và không có import nào tới
  `src.database`, `src.repositories` hay `src.services`;
  `tests/test_simulation/test_no_database_writes.py` đọc đồ thị import và fail
  nếu có một cái xuất hiện.
* **Không đụng vào bộ điều phối production.** Gói này không import
  `src.dispatch.service`, `src.dispatch.scheduler` hay
  `src.workers.dispatch_worker`. Nó chỉ mượn lịch ca làm và bảng ước lượng P80.
* **P3 không bao giờ được phân tự động.** Nó quay về
  ``REQUIRES_MANUAL_P3_REVIEW``.
* **Không gọi mô hình nào.** Mọi con số là số học trên kịch bản dán vào, nên một
  lần chạy là deterministic, tức thì, và kiểm lại bằng tay được. Ở chỗ hệ thống
  thật sẽ hỏi AI, bản mô phỏng đi thẳng vào nhánh dự phòng bảo thủ và gắn nhãn
  ``SCHEDULER_FALLBACK_SIMULATED`` thay vì nhận công cho một quyết định không mô
  hình nào đưa ra.
* **Đồng hồ SLA dùng chung.** ``src.domain.sla_clock`` quyết định khi nào một
  lời hứa tới hạn.
* **SLA đo tại thời điểm bắt đầu sửa**, không phải lúc hoàn tất. Xem
  ``models.SlaStatus``.

Bản đồ module: ``models`` (từ vựng), ``validation`` (một kịch bản JSON nghiêm
ngặt vào), ``travel`` (số phút giữa hai tầng), ``batching`` (cơ chế gom
micro-batch), ``policies`` (hai luồng), ``engine`` (bản phát lại và tổng hợp).
"""

from src.domain.sla_clock import SlaPolicy
from src.simulation.batching import MicroBatch, micro_batches
from src.simulation.engine import run_comparison, run_scenario, scenario_horizon
from src.simulation.models import (
    BuildingConfig,
    Comparison,
    ComparisonResult,
    DecisionSource,
    NewAppSettings,
    OldAppSettings,
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
)
from src.simulation.policies import new_app_policy, old_app_policy
from src.simulation.travel import travel_minutes
from src.simulation.validation import (
    MAX_TECHNICIANS,
    MAX_TICKETS,
    SimulationInputError,
    parse_scenario,
)

__all__ = [
    "MAX_TECHNICIANS",
    "MAX_TICKETS",
    "BuildingConfig",
    "Comparison",
    "ComparisonResult",
    "DecisionSource",
    "MicroBatch",
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
    "SimulationInputError",
    "SlaDurationSource",
    "SlaPolicy",
    "SlaStatus",
    "TechnicianLoad",
    "TicketOutcome",
    "micro_batches",
    "new_app_policy",
    "old_app_policy",
    "parse_scenario",
    "run_comparison",
    "run_scenario",
    "scenario_horizon",
    "travel_minutes",
]
