"""Hành vi của App mới: xếp hàng, chọn người, và nhánh rủi ro.

Chính sách được mô phỏng, phát biểu một lần ở đây để đọc test khỏi phải suy ra:

    "P2 đứng trước mọi P1 chưa bắt đầu. Nếu có cách bắt đầu ticket đúng SLA, hệ
    thống ưu tiên cách đó. Nếu không có cách nào đúng SLA, phương án dự phòng
    chọn cách dự kiến trễ ít nhất. Mọi trường hợp không bảo đảm SLA đều được
    phân công nhưng đồng thời thông báo BQL và ghi audit."

Chính sách đó **chưa được áp dụng vào production**. Không test nào trong file
này so kết quả với `src.dispatch.scheduler`, và không nên: bộ điều phối đang
chạy xếp hàng đợi theo slack còn lại, không theo quy tắc trên.
"""

from __future__ import annotations

from src.models.enums import DispatchRiskState, Priority
from src.simulation.engine import run_scenario
from src.simulation.models import (
    DecisionSource,
    Outcome,
    Reason,
    RiskReason,
    Settings,
    SlaStatus,
)
from src.simulation.policies import new_app_policy, old_app_policy
from tests.test_simulation.conftest import (
    NEW_APP,
    OLD_APP,
    P1_MINUTES,
    P2_MINUTES,
    P3_MINUTES,
    outcomes_by_id,
    scenario,
    stamp,
    technician,
    ticket,
)


def run_new(tickets, technicians, **kwargs):
    return run_scenario(scenario(tickets, technicians, **kwargs), new_app_policy(NEW_APP))


def run_old(tickets, technicians, **kwargs):
    return run_scenario(scenario(tickets, technicians, **kwargs), old_app_policy(OLD_APP))


# ---------------------------------------------------------------------------
# Thứ tự hàng đợi.
# ---------------------------------------------------------------------------


def test_a_p2_goes_before_every_unstarted_p1():
    """Ba P1 gửi từ đêm hôm trước, một P2 gửi sau cùng, một kỹ thuật viên.

    Cả bốn cùng sẵn sàng lúc 08:00 nên chúng vào cùng một lượt gom, và P2 đi
    trước — dù nó là cái gửi muộn nhất trong bốn cái.
    """
    tickets = [
        ticket("P1_a", created="2026-09-01T05:00", priority=Priority.P1, repair_minutes=60),
        ticket("P1_b", created="2026-09-01T05:10", priority=Priority.P1, repair_minutes=60),
        ticket("P1_c", created="2026-09-01T05:20", priority=Priority.P1, repair_minutes=60),
        ticket("P2_x", created="2026-09-01T06:00", priority=Priority.P2, sla_minutes=P2_MINUTES, repair_minutes=30),
    ]
    outcomes = outcomes_by_id(run_new(tickets, [technician("KTV_01")]))
    order = sorted(outcomes.values(), key=lambda o: o.work_started_at)

    assert order[0].ticket_id == "P2_x"
    assert stamp(outcomes["P2_x"].work_started_at) == "2026-09-01 08:03"
    # Ba cái P1 bị đẩy lùi lại phía sau nó, không cái nào biến mất.
    assert [row.ticket_id for row in order] == ["P2_x", "P1_a", "P1_b", "P1_c"]


def test_work_already_started_is_never_interrupted():
    """Một P1 dài khởi động lúc 08:00; P2 tới lúc 10:00 vẫn phải chờ nó xong.

    Đây là quy tắc, không phải hệ quả: một khi công việc rời khỏi hàng đợi thì
    không phản ánh nào đến sau sắp xếp lại được nó. Không có quy tắc này, "P2
    trước P1" sẽ có nghĩa là kéo một kỹ thuật viên ra khỏi căn hộ đang sửa dở.
    """
    tickets = [
        ticket("P1_dai", created="2026-09-01T07:00", priority=Priority.P1, repair_minutes=300),
        ticket("P2_den_sau", created="2026-09-01T10:00", priority=Priority.P2, sla_minutes=P2_MINUTES, repair_minutes=30),
    ]
    outcomes = outcomes_by_id(run_new(tickets, [technician("KTV_01")]))

    assert stamp(outcomes["P1_dai"].work_started_at) == "2026-09-01 08:03"
    assert outcomes["P2_den_sau"].work_started_at > outcomes["P1_dai"].completed_at


def test_an_unstarted_p1_is_pushed_back_by_a_later_p2():
    """Cùng dữ liệu, nhưng P1 thứ hai *chưa* khởi động khi P2 tới.

    KTV_01 bận với P1 đầu tiên, nên P1 thứ hai còn nằm trong hàng đợi — và P2
    tới lúc 10:00 vượt lên trước nó. Đó là nửa còn lại của quy tắc: chưa bắt đầu
    thì còn xếp lại được.
    """
    tickets = [
        ticket("P1_dang_lam", created="2026-09-01T07:00", priority=Priority.P1, repair_minutes=300),
        ticket("P1_dang_cho", created="2026-09-01T07:10", priority=Priority.P1, repair_minutes=60),
        ticket("P2_den_sau", created="2026-09-01T10:00", priority=Priority.P2, sla_minutes=P2_MINUTES, repair_minutes=30),
    ]
    outcomes = outcomes_by_id(run_new(tickets, [technician("KTV_01")]))

    assert outcomes["P2_den_sau"].work_started_at < outcomes["P1_dang_cho"].work_started_at


def test_within_one_priority_the_earlier_deadline_goes_first():
    """Hai P2 cùng lượt gom. Cái gửi *muộn* hơn có hạn sớm hơn và đi trước."""
    tickets = [
        # Hạn 180 phút kể từ 05:00 -> 09:00 giờ phục vụ, tức 11:00.
        ticket("P2_han_muon", created="2026-09-01T05:00", priority=Priority.P2, sla_minutes=P2_MINUTES, repair_minutes=60),
        # Hạn 30 phút kể từ 06:00 -> 08:30.
        ticket("P2_han_som", created="2026-09-01T06:00", priority=Priority.P2, sla_minutes=30, repair_minutes=60),
    ]
    outcomes = outcomes_by_id(run_new(tickets, [technician("KTV_01")]))

    assert outcomes["P2_han_som"].work_started_at < outcomes["P2_han_muon"].work_started_at


def test_score_breaks_a_tie_that_the_deadline_does_not():
    """Cùng ưu tiên, cùng hạn: điểm cao hơn đi trước."""
    tickets = [
        ticket("thap", created="2026-09-01T06:00", priority=Priority.P2, sla_minutes=P2_MINUTES, score_total=10),
        ticket("cao", created="2026-09-01T06:00", priority=Priority.P2, sla_minutes=P2_MINUTES, score_total=90),
    ]
    outcomes = outcomes_by_id(run_new(tickets, [technician("KTV_01")]))

    assert outcomes["cao"].work_started_at < outcomes["thap"].work_started_at


# ---------------------------------------------------------------------------
# Chọn kỹ thuật viên.
# ---------------------------------------------------------------------------


def test_the_technician_who_can_start_in_time_is_chosen_over_the_nearer_one():
    """KTV gần hơn đang bận tới quá hạn; KTV xa hơn đang rảnh.

    Chính sách chọn người xa hơn, chịu 31 phút thang máy để tới nơi lúc 09:02
    thay vì tới lúc 13:07. Cam kết là *có mặt đúng hạn*, không phải tiết kiệm
    thang máy, nên khi hai điều đó xung đột thì cái sau nhường.

    Việc giữ chỗ được gửi từ đêm hôm trước nên nó đã **thực sự bắt đầu** lúc
    08:03; nếu nó còn nằm trong hàng đợi thì P2 đã chen lên trước nó và bài test
    này sẽ nói về một quy tắc khác.
    """
    tickets = [
        ticket("giu_cho", created="2026-09-01T05:00", priority=Priority.P1, repair_minutes=300, floor=1),
        ticket("gap", created="2026-09-01T08:30", priority=Priority.P2, sla_minutes=60, repair_minutes=30, floor=2),
    ]
    roster = [technician("KTV_01", start_floor=1), technician("KTV_02", start_floor=30)]
    outcomes = outcomes_by_id(run_new(tickets, roster))

    assert stamp(outcomes["giu_cho"].work_started_at) == "2026-09-01 08:03"
    assert outcomes["gap"].assigned_technician_id == "KTV_02"
    assert stamp(outcomes["gap"].work_started_at) == "2026-09-01 09:02"
    assert outcomes["gap"].travel_minutes == 31
    assert outcomes["gap"].sla_status is SlaStatus.ON_TIME
    assert outcomes["gap"].risk_state is DispatchRiskState.SAFE


def test_among_technicians_who_all_meet_the_sla_the_nearer_one_wins():
    """Khi ai cũng kịp hạn, tiêu chí tiếp theo mới được dùng tới: đi ít hơn."""
    tickets = [ticket("T1", created="2026-09-01T08:00", priority=Priority.P2, sla_minutes=P2_MINUTES, floor=28)]
    roster = [technician("KTV_01", start_floor=1), technician("KTV_02", start_floor=27)]
    outcomes = outcomes_by_id(run_new(tickets, roster))

    assert outcomes["T1"].assigned_technician_id == "KTV_02"
    assert outcomes["T1"].travel_minutes == 4


def test_only_technicians_passing_every_hard_constraint_are_considered():
    """Kỹ năng, khả dụng, và không bị loại trừ. Cả ba đều cứng."""
    tickets = [
        ticket("T1", created="2026-09-01T08:00", required_skill="plumbing", excluded_technician_ids=("KTV_02",))
    ]
    roster = [
        technician("KTV_01", skills=("electrical",)),           # sai kỹ năng
        technician("KTV_02", skills=("plumbing",)),             # bị loại trừ
        technician("KTV_03", skills=("plumbing",), is_available=False),  # đang bận
        technician("KTV_04", skills=("plumbing",)),             # người duy nhất hợp lệ
    ]
    outcomes = outcomes_by_id(run_new(tickets, roster))

    assert outcomes["T1"].assigned_technician_id == "KTV_04"


def test_an_excluded_technician_is_never_reconsidered_for_want_of_anyone_else():
    """Ràng buộc cứng không mềm ra khi không còn ai khác. Không phân công còn
    trung thực hơn là phân cho một người không được phép nhận."""
    tickets = [ticket("T1", created="2026-09-01T08:00", excluded_technician_ids=("KTV_01",))]
    outcomes = outcomes_by_id(run_new(tickets, [technician("KTV_01")]))

    assert outcomes["T1"].outcome is Outcome.NO_ELIGIBLE_TECHNICIAN
    assert outcomes["T1"].reason is Reason.TECHNICIAN_EXCLUDED
    assert outcomes["T1"].assigned_technician_id is None


# ---------------------------------------------------------------------------
# Số học của một lần đi làm.
# ---------------------------------------------------------------------------


def test_the_sla_clock_stops_when_the_technician_arrives_not_when_they_leave():
    """Rời việc trước lúc 12:10, đi mất 10 phút, hạn 12:15.

    `work_started_at` là 12:20, tức trễ 5 phút. Tính theo lúc *rời đi* sẽ báo
    đúng hạn; tính theo lúc *hoàn tất* sẽ báo trễ hơn nhiều. Cả hai đều trả lời
    một câu hỏi khác với câu đã hứa với cư dân.
    """
    tickets = [
        # Giữ KTV_01 bận tới đúng 12:10: 08:00 + 3 phút đi + 247 phút sửa.
        ticket("giu_cho", created="2026-09-01T07:00", priority=Priority.P1, repair_minutes=247, floor=1),
        # Tầng 8 -> 3 + 7 = 10 phút đi. Hạn 12:15.
        ticket("do_dac", created="2026-09-01T09:15", priority=Priority.P2, sla_minutes=180, repair_minutes=30, floor=8),
    ]
    outcomes = outcomes_by_id(run_new(tickets, [technician("KTV_01", start_floor=1)]))
    row = outcomes["do_dac"]

    assert stamp(row.departed_at) == "2026-09-01 12:10"
    assert row.travel_minutes == 10
    assert stamp(row.work_started_at) == "2026-09-01 12:20"
    assert stamp(row.sla_due_at) == "2026-09-01 12:15"
    assert row.sla_status is SlaStatus.LATE_STARTED
    assert row.start_late_minutes == 5


def test_starting_before_the_deadline_is_on_time_however_long_the_repair_takes():
    """Bắt đầu trước hạn nhưng hoàn tất sau hạn vẫn là ON_TIME.

    Lời hứa là có người tới xử lý, không phải sửa xong trong bao lâu. Đo theo
    lúc hoàn tất sẽ biến một công việc khó thành một vi phạm SLA.
    """
    tickets = [
        ticket("sua_lau", created="2026-09-01T08:00", priority=Priority.P2, sla_minutes=P2_MINUTES, repair_minutes=400)
    ]
    row = outcomes_by_id(run_new(tickets, [technician("KTV_01")]))["sua_lau"]

    assert row.work_started_at < row.sla_due_at
    assert row.completed_at > row.sla_due_at
    assert row.sla_status is SlaStatus.ON_TIME
    assert row.start_late_minutes == 0


def test_the_three_timestamps_are_distinct_and_related_by_travel_then_repair():
    tickets = [ticket("T1", created="2026-09-01T08:00", floor=11, repair_minutes=45)]
    row = outcomes_by_id(run_new(tickets, [technician("KTV_01", start_floor=1)]))["T1"]

    assert row.travel_minutes == 13  # 3 + |11 - 1| * 1
    assert stamp(row.departed_at) == "2026-09-01 08:01"
    assert stamp(row.work_started_at) == "2026-09-01 08:14"
    assert stamp(row.completed_at) == "2026-09-01 08:59"


# ---------------------------------------------------------------------------
# Nhánh rủi ro.
# ---------------------------------------------------------------------------


def at_risk_fixture():
    """Một P2 mà không kỹ thuật viên nào bắt đầu kịp hạn.

    Cả hai đều đã **thực sự bắt đầu** một việc dài lúc 08:03 — nên không ai bị
    chen ngang được — và hạn của P2 là 09:00. KTV_01 xong lúc 14:43 và ở ngay
    cạnh; KTV_02 xong lúc 13:03 nhưng ở tầng 30. Câu hỏi chuyển từ "ai kịp" sang
    "ai trễ ít nhất", và đó là hai câu hỏi khác nhau.
    """
    tickets = [
        ticket("giu_01", created="2026-09-01T05:00", priority=Priority.P1, repair_minutes=400, floor=1),
        ticket("giu_02", created="2026-09-01T05:00", priority=Priority.P1, repair_minutes=300, floor=30,
               required_skill="hvac"),
        ticket("khong_kip", created="2026-09-01T08:30", priority=Priority.P2, sla_minutes=30,
               repair_minutes=30, floor=2),
    ]
    roster = [
        technician("KTV_01", skills=("plumbing",), start_floor=1),
        technician("KTV_02", skills=("plumbing", "hvac"), start_floor=30),
    ]
    return tickets, roster


def test_a_ticket_nobody_can_start_in_time_is_still_assigned():
    """Bỏ trống một phản ánh không làm nó biến mất khỏi tòa nhà."""
    row = outcomes_by_id(run_new(*at_risk_fixture()))["khong_kip"]

    assert row.outcome is Outcome.ASSIGNED
    assert row.assigned_technician_id is not None
    assert row.sla_status is SlaStatus.LATE_STARTED


def test_the_at_risk_branch_marks_its_risk_and_its_reason():
    row = outcomes_by_id(run_new(*at_risk_fixture()))["khong_kip"]

    assert row.risk_state is DispatchRiskState.AT_RISK
    assert row.risk_reason is RiskReason.START_SLA_RISK
    assert row.projected_start_late_minutes > 0


def test_the_at_risk_branch_says_the_agent_was_not_asked():
    """Trong đời thật nhánh này hỏi AI trước. Bản mô phỏng deterministic không
    gọi mô hình nào, nên nhãn nguồn quyết định phải nói đúng như vậy — nếu không
    chất lượng của AI sẽ bị ghi công cho một quyết định nó chưa từng đưa ra."""
    row = outcomes_by_id(run_new(*at_risk_fixture()))["khong_kip"]

    assert row.decision_source is DecisionSource.SCHEDULER_FALLBACK_SIMULATED


def test_the_at_risk_branch_would_notify_and_would_audit():
    row = outcomes_by_id(run_new(*at_risk_fixture()))["khong_kip"]

    assert row.would_notify_bql is True
    assert row.would_write_audit is True


def test_a_safe_assignment_neither_notifies_nor_audits():
    """Hai cờ đó là chuông báo động. Bật chúng trên mọi dòng là tắt chúng."""
    row = outcomes_by_id(run_new([ticket("T1", created="2026-09-01T08:00")], [technician("KTV_01")]))["T1"]

    assert row.risk_state is DispatchRiskState.SAFE
    assert row.decision_source is DecisionSource.SCHEDULER_SIMULATED
    assert row.would_notify_bql is False
    assert row.would_write_audit is False


def test_the_fallback_picks_the_least_late_option():
    """Hai kỹ thuật viên, cả hai đều trễ, và phương án trễ ít nhất thắng.

    KTV_02 rảnh sớm hơn 100 phút nhưng phải đi 31 phút; KTV_01 gần nhưng rảnh
    muộn. Chọn theo tổng — tức theo `work_started_at` — chứ không theo lúc rảnh.
    """
    result = run_new(*at_risk_fixture())
    row = outcomes_by_id(result)["khong_kip"]

    # KTV_02 xong lúc 13:03 rồi đi 31 phút -> bắt đầu 13:34, trễ 274 phút.
    # KTV_01 ở ngay cạnh nhưng mãi 14:43 mới xong -> 14:47, trễ 347.
    # Chọn theo *thời điểm bắt đầu*, không theo quãng đường.
    assert row.assigned_technician_id == "KTV_02"
    assert stamp(row.work_started_at) == "2026-09-01 13:34"
    assert row.start_late_minutes == 274
    assert result.summary.at_risk_tickets == 1


# ---------------------------------------------------------------------------
# P3 và nền thủ công.
# ---------------------------------------------------------------------------


def test_p3_never_gets_a_technician_in_either_flow():
    """Luồng tự động từ chối P3 thẳng thừng, và nền thủ công cũng vậy: cả hai
    chuyển nó cho Ban quản lý."""
    tickets = [ticket("khan_cap", created="2026-09-01T09:00", priority=Priority.P3, sla_minutes=P3_MINUTES)]
    for result in (run_new(tickets, [technician("KTV_01")]), run_old(tickets, [technician("KTV_01")])):
        row = outcomes_by_id(result)["khan_cap"]
        assert row.outcome is Outcome.REQUIRES_MANUAL_P3_REVIEW
        assert row.reason is Reason.P3_MANUAL_REVIEW
        assert row.sla_status is SlaStatus.NOT_EVALUABLE
        assert row.assigned_technician_id is None
        assert row.start_late_minutes == 0


def test_the_old_app_knows_nothing_about_priority_or_deadlines():
    """Nền thủ công xử lý theo thứ tự đến, kể cả khi cái đến sau khẩn hơn nhiều.

    Cho nó biết ưu tiên sẽ là tặng cho quy trình cũ một năng lực nó chưa từng
    có, và mọi cải thiện đo được sau đó sẽ nhỏ đi một cách giả tạo.
    """
    tickets = [
        ticket("den_truoc_P1", created="2026-09-01T05:00", priority=Priority.P1, repair_minutes=60),
        ticket("den_sau_P2", created="2026-09-01T06:00", priority=Priority.P2, sla_minutes=P2_MINUTES, repair_minutes=30),
    ]
    outcomes = outcomes_by_id(run_old(tickets, [technician("KTV_01")]))

    assert outcomes["den_truoc_P1"].work_started_at < outcomes["den_sau_P2"].work_started_at


def test_the_old_app_never_reports_a_risk_state():
    """Không có khái niệm rủi ro trong một quy trình không nhìn vào hạn."""
    tickets = [ticket("T1", created="2026-09-01T08:00", priority=Priority.P2, sla_minutes=5)]
    row = outcomes_by_id(run_old(tickets, [technician("KTV_01")]))["T1"]

    assert row.risk_state is None
    assert row.risk_reason is None
    assert row.decision_source is DecisionSource.MANUAL_SIMULATED
    assert row.would_notify_bql is False


def test_the_old_app_charges_its_manual_minutes_to_every_report():
    tickets = [ticket(f"T{i}", created="2026-09-01T08:00") for i in range(4)]
    result = run_old(tickets, [technician("KTV_01")])

    assert result.summary.bql_effort_minutes == 4 * OLD_APP.total_minutes


def test_the_new_app_charges_review_minutes_only_where_a_human_was_needed():
    tickets = [
        ticket("tu_dong", created="2026-09-01T08:00", need_hand_categorized=False),
        ticket("phai_xem", created="2026-09-01T08:00", need_hand_categorized=True),
    ]
    result = run_new(tickets, [technician("KTV_01")])

    assert result.summary.bql_effort_minutes == NEW_APP.manual_review_minutes


def test_a_p3_costs_review_minutes_once_not_twice():
    """Một P3 đồng thời cần phân loại tay vẫn là một lần mở một màn hình."""
    tickets = [
        ticket("p3", created="2026-09-01T09:00", priority=Priority.P3, sla_minutes=P3_MINUTES,
               need_hand_categorized=True)
    ]
    result = run_new(tickets, [technician("KTV_01")])

    assert result.summary.bql_effort_minutes == NEW_APP.manual_review_minutes


# ---------------------------------------------------------------------------
# Vẫn có thể tắt hẳn phần gom batch.
# ---------------------------------------------------------------------------


def test_the_old_app_handles_one_report_per_pass_however_the_batch_is_configured():
    """Nền thủ công không gom việc: một người xử lý từng phản ánh một, và một
    thiết lập batch rộng cũng không cho nó khả năng nhìn cả đống cùng lúc."""
    tickets = [
        ticket("P1_truoc", created="2026-09-01T05:00", priority=Priority.P1, repair_minutes=60),
        ticket("P2_sau", created="2026-09-01T06:00", priority=Priority.P2, sla_minutes=P2_MINUTES, repair_minutes=30),
    ]
    outcomes = outcomes_by_id(
        run_old(tickets, [technician("KTV_01")], settings=Settings(micro_batch_size=20))
    )

    assert outcomes["P1_truoc"].work_started_at < outcomes["P2_sau"].work_started_at


def test_p1_minutes_are_the_service_clock_default():
    """Kiểm tra hằng số của conftest thật sự là hằng số của chính sách, để một
    test không nói về hạn P1 thì không vô tình phụ thuộc vào nó."""
    from src.domain.sla_clock import POLICY_SLA_MINUTES, SlaPolicy

    assert POLICY_SLA_MINUTES[SlaPolicy.SERVICE_HOURS_DRAFT_V1][Priority.P1] == P1_MINUTES
