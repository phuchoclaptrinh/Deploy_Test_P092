"""Gom việc theo micro-batch: ai vào lượt gom, và theo thứ tự nào.

Hai quy tắc, và trộn chúng vào nhau là một lỗi thật:

* **Thành viên** của một lượt gom do `available_at` quyết định, rồi tới
  `enqueued_at`, rồi tới id để phá hòa cuối cùng.
* **Thứ tự xử lý** bên trong lượt gom do chính sách của luồng quyết định.

Lỗi được sửa ở đây là quy tắc thứ nhất từng dùng `ticket_id` thay cho
`enqueued_at`. Mọi phản ánh gửi trước 08:00 đều bị đẩy `available_at` về đúng
giờ mở cửa, nên cả một đêm tồn đọng có chung `available_at` — và khi đó xếp theo
id sẽ cho `T001` chen lên trước một phản ánh gửi sớm hơn nó nhiều tiếng, chỉ vì
tên nó nhỏ hơn.

Mô phỏng đúng cơ chế gom không phải là tuyên bố parity với production. Chính
sách sắp xếp bên trong lượt gom là chính sách giả định của `NEW_APP`.
"""

from __future__ import annotations

from datetime import timedelta

from src.dispatch.shift import next_shift_open
from src.domain.sla_clock import SlaPolicy
from src.models.enums import Priority
from src.simulation.batching import micro_batches
from src.simulation.models import (
    Settings,
    production_micro_batch_interval_ms,
    production_micro_batch_size,
)
from src.simulation.policies import new_app_policy
from tests.test_simulation.conftest import NEW_APP, P2_MINUTES, local, stamp, ticket

INTERVAL = timedelta(milliseconds=750)
POLICY = new_app_policy(NEW_APP)


def order_key(sim_ticket):
    return POLICY.queue_key(sim_ticket, SlaPolicy.SERVICE_HOURS_DRAFT_V1)


def batches_of(tickets, *, size=20, interval=INTERVAL, ready_offset=timedelta(minutes=1)):
    """Gom một danh sách ticket đúng cách engine gom.

    `available_at` là `next_shift_open(ready_at)` — cùng phép đẩy về giờ mở cửa
    mà `_defer_before_shift` thực hiện — còn `enqueued_at` là chính `ready_at`,
    tức thời điểm hàng đợi ghi nhận nó.
    """
    ready = {t.ticket_id: t.created_at + ready_offset for t in tickets}
    return micro_batches(
        tickets,
        available_at={t.ticket_id: next_shift_open(ready[t.ticket_id]) for t in tickets},
        enqueued_at=ready,
        interval=interval,
        size=size,
        order_key=order_key,
    )


# ---------------------------------------------------------------------------
# Thành viên của lượt gom.
# ---------------------------------------------------------------------------


def test_everything_available_at_once_is_one_claim():
    reports = [ticket(f"T{i:02d}", created="2026-09-01T06:00") for i in range(5)]
    batches = batches_of(reports)

    assert len(batches) == 1
    assert len(batches[0].tickets) == 5
    assert stamp(batches[0].tick) == "2026-09-01 08:00"


def test_the_twenty_first_report_waits_for_the_next_tick():
    """Trần lượt gom là một tính chất thật của bộ điều phối, không phải chi tiết
    làm tròn: cái thứ hai mươi mốt chờ 750ms, khẩn cấp đến mấy cũng vậy."""
    reports = [
        ticket(f"T{i:02d}", created="2026-09-01T06:00", priority=Priority.P1) for i in range(20)
    ]
    # Cái khẩn cấp nhất tòa nhà, và gửi *muộn nhất*, nên nó không lọt vào lượt đầu.
    reports.append(
        ticket("T99", created="2026-09-01T07:00", priority=Priority.P2, sla_minutes=P2_MINUTES,
               score_total=100)
    )
    batches = batches_of(reports, size=20)

    assert [len(batch.tickets) for batch in batches] == [20, 1]
    assert batches[1].tickets[0].ticket_id == "T99"
    assert batches[1].tick - batches[0].tick == INTERVAL


def test_the_claim_is_taken_by_enqueued_at_not_by_ticket_id():
    """Lỗi parity đã sửa, ghim lại.

    Hai mươi mốt phản ánh gửi trong đêm, tất cả cùng `available_at` = 08:00. Cái
    gửi **sớm nhất** có `ticket_id` lớn nhất (`T99`), và nó vẫn phải nằm trong
    lượt gom đầu; cái gửi **muộn nhất** có id nhỏ nhất (`T00`) và phải bị đẩy
    sang lượt sau. Xếp theo id sẽ cho kết quả ngược lại ở cả hai đầu.
    """
    # T99 gửi 05:00; T00..T19 gửi từ 06:00 trở đi, mỗi cái muộn hơn một phút.
    reports = [ticket("T99", created="2026-09-01T05:00")]
    reports += [
        ticket(f"T{i:02d}", created=f"2026-09-01T06:{i:02d}") for i in range(20)
    ]
    batches = batches_of(reports, size=20)

    assert [len(batch.tickets) for batch in batches] == [20, 1]
    first = {t.ticket_id for t in batches[0].tickets}
    assert "T99" in first, "phản ánh gửi sớm nhất phải nằm trong lượt gom đầu"
    assert "T19" not in first, "phản ánh gửi muộn nhất phải chờ lượt sau"
    assert batches[1].tickets[0].ticket_id == "T19"


def test_ticket_id_still_breaks_a_genuine_tie():
    """Khi cả `available_at` lẫn `enqueued_at` bằng nhau, id là chốt chặn cuối —
    không phải để xếp hạng, mà để kết quả ổn định giữa các lần chạy."""
    reports = [ticket(f"T{i:02d}", created="2026-09-01T06:00") for i in range(21)]
    batches = batches_of(reports, size=20)

    assert batches[1].tickets[0].ticket_id == "T20"


def test_a_claim_is_taken_oldest_first_then_re_sorted_by_urgency():
    """Hai quy tắc khác nhau, thấy rõ trong một lần chạy.

    Cả hai phản ánh qua đêm cùng vào một lượt gom — và bên trong lượt đó P2 đi
    trước, dù nó được gửi muộn hơn.
    """
    reports = [
        ticket("T_p1_cu", created="2026-09-01T06:00", priority=Priority.P1),
        ticket("T_p2_moi", created="2026-09-01T06:30", priority=Priority.P2, sla_minutes=P2_MINUTES),
        ticket("T_sang", created="2026-09-01T10:00", priority=Priority.P2, sla_minutes=P2_MINUTES),
    ]
    batches = batches_of(reports)

    assert [t.ticket_id for t in batches[0].tickets] == ["T_p2_moi", "T_p1_cu"]
    assert [t.ticket_id for t in batches[1].tickets] == ["T_sang"]
    assert stamp(batches[1].tick) == "2026-09-01 10:01"


# ---------------------------------------------------------------------------
# Nhịp.
# ---------------------------------------------------------------------------


def test_a_quiet_stretch_is_skipped_rather_than_ticked_through():
    """Mười hai tiếng yên ắng là 57.600 lượt rỗng trong production. Mô phỏng
    từng nhịp một sẽ khiến một kịch bản dài hai tuần không chạy nổi."""
    reports = [
        ticket("T_sang", created="2026-09-01T08:00"),
        ticket("T_may_ngay_sau", created="2026-09-05T09:00"),
    ]
    batches = batches_of(reports)

    assert len(batches) == 2
    assert stamp(batches[1].tick) == "2026-09-05 09:01"
    # Vẫn nằm trên đúng lưới 750ms mà mốc đầu đã đặt ra.
    assert (batches[1].tick - batches[0].tick) % INTERVAL == timedelta(0)


def test_a_tick_never_lands_outside_the_working_window():
    """Cổng ca làm khiến một lượt hoãn lại chứ không leo thang, nên không lượt
    gom nào được đề ngày lúc 02:00 — không có lượt nào lúc 02:00 để mà đề."""
    batches = batches_of([ticket("T_dem", created="2026-09-01T23:30")])

    assert stamp(batches[0].tick) == "2026-09-02 08:00"


def test_no_tickets_is_no_batches():
    assert micro_batches([], available_at={}, enqueued_at={}, interval=INTERVAL, size=20, order_key=order_key) == []


def test_a_wider_interval_pushes_the_overflow_further_out():
    reports = [ticket(f"T{i:02d}", created="2026-09-01T06:00") for i in range(21)]
    batches = batches_of(reports, size=20, interval=timedelta(seconds=5))

    assert batches[1].tick - batches[0].tick == timedelta(seconds=5)


# ---------------------------------------------------------------------------
# Nhịp lấy từ cấu hình thật.
# ---------------------------------------------------------------------------


def test_the_batch_shape_defaults_to_the_deployed_cadence():
    """Một hằng số `750` viết cứng trong trình mô phỏng sẽ đúng cho tới ngày ai
    đó chỉnh production. Cơ chế gom là cơ chế thật, nên nhịp cũng phải thật."""
    from src.config import get_settings

    config = get_settings()
    defaults = Settings()

    assert defaults.micro_batch_interval_ms == config.dispatch_micro_batch_interval_ms
    assert defaults.micro_batch_size == config.dispatch_micro_batch_size
    assert defaults.micro_batch_interval_ms == production_micro_batch_interval_ms()
    assert defaults.micro_batch_size == production_micro_batch_size()
    assert defaults.micro_batch_interval == INTERVAL


def test_the_order_key_is_the_new_app_policy_not_the_dispatchers():
    """Trình mô phỏng không mượn khóa sắp xếp của bộ điều phối đang chạy, và
    không nên: bộ đó xếp theo `-score` rồi `submitted_at`, còn chính sách giả
    định xếp theo hạn SLA trước. Hai thứ khác nhau, và gọi cái sau là parity với
    cái trước sẽ là nói sai."""
    som = ticket("som", created="2026-09-01T08:00", priority=Priority.P2, sla_minutes=30, score_total=0)
    diem_cao = ticket("diem_cao", created="2026-09-01T08:00", priority=Priority.P2,
                      sla_minutes=P2_MINUTES, score_total=99)

    assert order_key(som) < order_key(diem_cao)
    assert som.sla_due_at(SlaPolicy.SERVICE_HOURS_DRAFT_V1) == local("2026-09-01T08:30")
