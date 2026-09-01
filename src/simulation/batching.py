"""Gom việc theo micro-batch: bộ điều phối nhìn thấy gì trong một lượt.

Bộ điều phối không quyết định từng ticket một theo thứ tự cư dân bấm gửi. Nó
thức dậy theo một nhịp cố định, **gom** những sự kiện đã sẵn sàng — cũ nhất
trước, tối đa `size` cái — rồi mới **sắp xếp** cái vừa gom theo mức khẩn cấp và
xử lý. Hai quy tắc đó khác nhau và trộn chúng vào nhau là một lỗi thật:

* **Thành viên của lượt gom** do `available_at` quyết định, rồi tới `enqueued_at`,
  rồi tới id để phá hòa cuối cùng. Không dùng `ticket_id` thay cho `enqueued_at`:
  mọi phản ánh gửi trước 08:00 đều bị đẩy `available_at` về đúng giờ mở cửa, nên
  cả một đêm tồn đọng có chung `available_at` — và khi đó thứ duy nhất phân biệt
  ai vào lượt gom đầu là *thời điểm được ghi vào hàng đợi*. Xếp theo id sẽ cho
  `T001` chen lên trước một phản ánh gửi sớm hơn nhiều chỉ vì tên nó nhỏ hơn.
* **Thứ tự xử lý bên trong lượt gom** do chính sách của luồng quyết định, và
  được truyền vào qua `order_key`.

Cơ chế gom ở đây là cơ chế thật của bộ điều phối đang chạy. Chính sách sắp xếp
bên trong thì **không** — nó là chính sách giả định của `NEW_APP`. Mô phỏng đúng
cơ chế không phải là tuyên bố parity với production, và không chỗ nào trong gói
này nói vậy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.dispatch.shift import next_shift_open
from src.simulation.models import SimTicket


@dataclass(frozen=True)
class MicroBatch:
    """Một lượt của bộ điều phối: chạy lúc nào, và gom được gì.

    `tickets` đã ở đúng thứ tự lượt này xử lý — thứ tự gom (cũ nhất trước, tới
    hết `size`) đã được sắp lại theo `order_key`. Đọc tuple này từ trên xuống là
    đọc đúng thứ tự các ticket sẽ được phân công.
    """

    tick: datetime
    tickets: tuple[SimTicket, ...]


def micro_batches(
    tickets: list[SimTicket],
    *,
    available_at: dict[str, datetime],
    enqueued_at: dict[str, datetime],
    interval: timedelta,
    size: int,
    order_key: Callable[[SimTicket], tuple],
) -> list[MicroBatch]:
    """Cắt một kịch bản thành đúng những lượt mà bộ điều phối sẽ chạy.

    1. Bộ điều phối thức dậy theo nhịp cố định. Các mốc là `origin + k * interval`,
       với `origin` đặt tại thời điểm đầu tiên có gì đó gom được — một pha, không
       phải một mốc tròn theo đồng hồ, vì pha của bộ điều phối thật là lúc nó
       được khởi động và không kịch bản nào biết được.
    2. Mỗi mốc gom những sự kiện có `available_at <= tick`, **cũ nhất trước**,
       tối đa `size` cái.
    3. Cái vừa gom mới được sắp lại theo mức khẩn cấp rồi đem đi phân công.
    4. Một mốc không gom được gì thì **không** được mô phỏng từng nhịp một: nó
       nhảy thẳng tới mốc đầu tiên trên cùng nhịp mà gom được. Mười hai tiếng
       yên ắng là 57.600 lượt rỗng trong production và không được phép là 57.600
       vòng lặp ở đây.

    Mốc rơi ngoài giờ làm việc bị đẩy sang giờ mở cửa kế tiếp, đúng như cổng ca
    làm khiến một lượt hoãn lại thay vì leo thang.
    """
    if not tickets:
        return []

    def claim_key(ticket: SimTicket) -> tuple[datetime, datetime, str]:
        # Ba khóa, đúng thứ tự đó. `ticket_id` chỉ là chốt chặn cuối để kết quả
        # ổn định giữa các lần chạy, không bao giờ là tiêu chí xếp hạng thật.
        return (available_at[ticket.ticket_id], enqueued_at[ticket.ticket_id], ticket.ticket_id)

    waiting = sorted(tickets, key=claim_key)
    tick = next_shift_open(available_at[waiting[0].ticket_id])
    batches: list[MicroBatch] = []
    index = 0

    while index < len(waiting):
        claimable = 0
        while index + claimable < len(waiting) and available_at[waiting[index + claimable].ticket_id] <= tick:
            claimable += 1
        if claimable == 0:
            # Không có gì để làm ở mốc này. Nhảy nguyên số nhịp tới mốc đầu tiên
            # gom được phản ánh kế tiếp, rồi lại qua cổng ca làm: một cú nhảy có
            # thể rơi vào 02:00, chỗ mà bộ điều phối chỉ hoãn lại chứ không chạy.
            nxt = available_at[waiting[index].ticket_id]
            steps = -(-(nxt - tick) // interval)  # làm tròn lên, theo số nhịp trọn vẹn
            tick = next_shift_open(tick + steps * interval)
            continue
        claimed = waiting[index : index + min(claimable, size)]
        index += len(claimed)
        batches.append(MicroBatch(tick=tick, tickets=tuple(sorted(claimed, key=order_key))))
        tick = next_shift_open(tick + interval)
    return batches


__all__ = ["MicroBatch", "micro_batches"]
