"""Hai luồng: nền thủ công, và chính sách giả định.

Engine hỏi mỗi chính sách bốn câu:

1. **Phản ánh này sẵn sàng điều phối lúc nào?** (`ready_at`)
2. **Trong số đang chờ, cái nào đi trước?** (`queue_key`)
3. **Kỹ thuật viên nào nhận?** (`select`)
4. **Ban quản lý tốn bao nhiêu phút vì nó?** (`bql_minutes`)

Cả hai đi trên cùng một đồng hồ, cùng lịch ca làm, cùng mô hình di chuyển và
cùng cơ chế gom batch, nên mọi khác biệt trong kết quả đến từ bốn câu trả lời
này và không từ đâu khác.

Không luồng nào ở đây là production
------------------------------------
`NEW_APP` là **chính sách giả định**. Nó đặt P2 trước mọi P1 chưa bắt đầu, chọn
kỹ thuật viên theo thời điểm bắt đầu sửa, và khi không ai kịp hạn thì vẫn giao
việc kèm cảnh báo. Bộ điều phối đang chạy không làm như vậy — nó xếp hàng đợi
theo slack còn lại và có thể để P1 lên trước. Không có hàm nào trong file này
gọi vào `src.dispatch.scheduler`, và không nhãn nào trong gói này gọi `NEW_APP`
là hành vi production.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from src.domain.sla_clock import SlaPolicy
from src.models.enums import Priority
from src.simulation.models import DecisionSource, NewAppSettings, OldAppSettings, Scenario, SimTicket

#: Thứ tự khẩn cấp của V1, khẩn nhất trước. P3 là mức khẩn cấp của thang cũ
#: (SLA năm phút), P2 là mức trong ca, P1 là mức thường nhiều ngày — nên "P2
#: trước P1" là cách đọc thông thường, và P3 không bao giờ vào hàng đợi vì luồng
#: tự động từ chối nó.
PRIORITY_RANK: dict[Priority, int] = {Priority.P3: 0, Priority.P2: 1, Priority.P1: 2}

#: Thứ tự khẩn cấp của rubric V2: P4 → P3 → P2 → P1, đúng như
#: `docs/risk_scoring_v2.md` §6.3. P5 không có thứ hạng vì nó không bao giờ vào
#: hàng đợi; giá trị dự phòng đặt nó xuống cuối chứ không phải lên đầu, để một
#: P5 lọt vào đây do lỗi không chen ngang việc thật.
PRIORITY_RANK_V2: dict[Priority, int] = {
    Priority.P4: 0,
    Priority.P3: 1,
    Priority.P2: 2,
    Priority.P1: 3,
}

#: Bảng thứ hạng theo chính sách. Tra bảng chứ không viết cứng, vì thang điểm
#: đã đảo: dùng bảng V1 cho một lần chạy V2 sẽ xếp P4 — mức khẩn nhất còn được
#: phân việc — xuống dưới cùng.
POLICY_RANK: dict[SlaPolicy, dict[Priority, int]] = {
    SlaPolicy.WALL_CLOCK_V1: PRIORITY_RANK,
    SlaPolicy.SERVICE_HOURS_DRAFT_V1: PRIORITY_RANK,
    SlaPolicy.SERVICE_HOURS_RISK_V2: PRIORITY_RANK_V2,
}


def priority_rank(priority: Priority, sla_policy: SlaPolicy) -> int:
    """Thứ hạng của một mức dưới một chính sách. Mức ngoài bảng xếp cuối."""
    ranks = POLICY_RANK[sla_policy]
    return ranks.get(priority, len(ranks))


@dataclass(frozen=True)
class Policy:
    """Những gì engine cần ở một luồng."""

    scenario: Scenario
    #: Nhãn nguồn quyết định gắn lên mọi phân công của luồng này.
    decision_source: DecisionSource
    #: True khi luồng biết tới hạn SLA lúc chọn người. Nền thủ công thì không,
    #: và cho nó biết sẽ là tặng cho quy trình cũ một năng lực nó chưa từng có.
    sla_aware: bool
    #: True khi luồng gom việc theo micro-batch. Nền thủ công xử lý từng cái
    #: một, nên nó chạy với lượt gom bằng một.
    batches: bool

    def ready_at(self, ticket: SimTicket) -> datetime:  # pragma: no cover - trừu tượng
        raise NotImplementedError

    def queue_key(self, ticket: SimTicket, sla_policy: SlaPolicy) -> tuple:  # pragma: no cover
        raise NotImplementedError

    def bql_minutes(self, ticket: SimTicket, sla_policy: SlaPolicy) -> int:  # pragma: no cover
        raise NotImplementedError


@dataclass(frozen=True)
class OldAppPolicy(Policy):
    """Nền thủ công: đến trước xử lý trước, người rảnh sớm nhất nhận.

    Không biết ưu tiên khi xếp hàng, không biết hạn SLA, không biết ai đứng ở
    tầng nào. Đó là điểm của nó: mọi thứ `NEW_APP` cải thiện được đều phải đo so
    với một quy trình thật sự không có những thứ đó, chứ không phải so với một
    phiên bản đã được tặng thêm nửa số năng lực mới.

    Chi phí thời gian của Ban quản lý được tính cho **mọi** phản ánh, vì có
    người đọc từng cái và có người điều phối từng cái.
    """

    settings: OldAppSettings

    def ready_at(self, ticket: SimTicket) -> datetime:
        return ticket.created_at + timedelta(minutes=self.settings.total_minutes)

    def queue_key(self, ticket: SimTicket, sla_policy: SlaPolicy) -> tuple:
        # Thuần thứ tự đến. `sla_policy` không được dùng, và đó là điều muốn nói.
        return (ticket.created_at, ticket.ticket_id)

    def bql_minutes(self, ticket: SimTicket, sla_policy: SlaPolicy) -> int:
        return self.settings.total_minutes


@dataclass(frozen=True)
class NewAppPolicy(Policy):
    """Chính sách giả định: P2 trước mọi P1 chưa bắt đầu.

    Thứ tự hàng đợi, theo đúng thứ tự đó:

    1. **Mức ưu tiên.** P2 đứng trước mọi P1 *chưa bắt đầu*. Việc đã bắt đầu thì
       không bị chen ngang — điều đó do engine bảo đảm bằng cách khóa cứng công
       việc đã khởi động, chứ không phải bằng một quy tắc sắp xếp.
    2. **Hạn SLA sớm hơn đi trước**, trong cùng một mức ưu tiên. Hạn phụ thuộc
       chính sách SLA đang chạy, nên một lần chạy trên đồng hồ giờ phục vụ xếp
       hàng theo hạn giờ phục vụ; dùng hạn treo tường ở đây sẽ là xếp hàng theo
       một lời hứa mà lần chạy này không đưa ra.
    3. **Điểm cao hơn đi trước.** Bộ chấm điểm đã cân mức độ nghiêm trọng, số
       căn hộ ảnh hưởng và lịch sử; xếp lại theo nó là tôn trọng việc đó, không
       phải quyết định lại.
    4. **Gửi sớm hơn đi trước**, rồi tới `ticket_id` để kết quả ổn định.

    Chi phí phân tích chỉ tính cho phản ánh mô hình không tự phân loại được.
    """

    settings: NewAppSettings

    def ready_at(self, ticket: SimTicket) -> datetime:
        minutes = self.settings.ai_classification_minutes
        if ticket.need_hand_categorized:
            minutes += self.settings.manual_review_minutes
        return ticket.created_at + timedelta(minutes=minutes)

    def queue_key(self, ticket: SimTicket, sla_policy: SlaPolicy) -> tuple:
        return (
            priority_rank(ticket.priority, sla_policy),
            ticket.sla_due_at(sla_policy),
            -ticket.score_total,
            ticket.created_at,
            ticket.ticket_id,
        )

    def bql_minutes(self, ticket: SimTicket, sla_policy: SlaPolicy) -> int:
        # Một lần, không phải hai: một phản ánh khẩn cấp đồng thời cần phân loại
        # tay vẫn là một lần mở một màn hình bởi một người.
        if ticket.need_hand_categorized or ticket.requires_manual_review(sla_policy):
            return self.settings.manual_review_minutes
        return 0


def old_app_policy(settings: OldAppSettings) -> OldAppPolicy:
    return OldAppPolicy(
        scenario=Scenario.OLD_APP,
        decision_source=DecisionSource.MANUAL_SIMULATED,
        sla_aware=False,
        batches=False,
        settings=settings,
    )


def new_app_policy(settings: NewAppSettings) -> NewAppPolicy:
    return NewAppPolicy(
        scenario=Scenario.NEW_APP,
        decision_source=DecisionSource.SCHEDULER_SIMULATED,
        sla_aware=True,
        batches=True,
        settings=settings,
    )


__all__ = [
    "POLICY_RANK",
    "PRIORITY_RANK",
    "PRIORITY_RANK_V2",
    "NewAppPolicy",
    "OldAppPolicy",
    "Policy",
    "new_app_policy",
    "old_app_policy",
    "priority_rank",
]
