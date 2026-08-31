"""SLA đo tại thời điểm bắt đầu, và mẫu số không được làm đẹp.

Trước đây các chỉ số của trình mô phỏng nói về việc *hoàn tất* đúng hạn. Bây giờ
chúng nói về việc *bắt đầu* đúng hạn, và đó là một câu hỏi khác hẳn: một công
việc khó kéo dài bốn tiếng không phải là một vi phạm cam kết, còn một phản ánh
qua hạn mà chưa ai chạm tới thì đúng là.

Năm trạng thái, và ba trong số đó nằm trong mẫu số:

    ON_TIME        đã bắt đầu, work_started_at <= sla_due_at      ┐
    LATE_STARTED   đã bắt đầu, work_started_at >  sla_due_at      ├ mẫu số
    OPEN_OVERDUE   chưa bắt đầu, đã qua hạn                       ┘
    OPEN_NOT_DUE   chưa bắt đầu, chưa tới hạn
    NOT_EVALUABLE  P3 chờ người, hoặc không có hạn hợp lệ

`OPEN_OVERDUE` nằm trong mẫu số vì nó là vi phạm rõ ràng nhất trong cả bảng.
Cho nó ra ngoài sẽ là cải thiện tỷ lệ bằng cách đánh rơi đúng những ticket tệ
nhất.
"""

from __future__ import annotations

from datetime import timedelta

from src.domain.sla_clock import SlaPolicy
from src.models.enums import Priority
from src.simulation.engine import run_comparison, run_scenario, scenario_horizon
from src.simulation.models import EVALUABLE_STATUSES, Outcome, Settings, SlaStatus
from src.simulation.policies import new_app_policy, old_app_policy
from tests.test_simulation.conftest import (
    NEW_APP,
    OLD_APP,
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


#: Mười hai việc năm trăm phút, một kỹ thuật viên, một ngày làm việc. Không cái
#: nào xong được; câu hỏi là cả hai luồng có nói ra điều đó theo cùng một cách
#: hay không.
BACKLOG = [ticket(f"T{i:02d}", created="2026-09-01T08:00", repair_minutes=500) for i in range(12)]
#: Cùng tồn đọng đó nhưng toàn P2, hạn ba tiếng. Cái nào không tới lượt trong
#: ngày là `OPEN_OVERDUE` chứ không phải `OPEN_NOT_DUE`, và đó là điều những
#: test về vi phạm cần.
OVERDUE_BACKLOG = [
    ticket(f"P{i:02d}", created="2026-09-01T08:00", priority=Priority.P2, sla_minutes=P2_MINUTES,
           repair_minutes=500)
    for i in range(12)
]
ONE_TECHNICIAN = [technician("KTV_01")]
ONE_DAY = Settings(simulation_horizon_days=1)


# ---------------------------------------------------------------------------
# Năm trạng thái.
# ---------------------------------------------------------------------------


def test_a_ticket_started_before_its_deadline_is_on_time():
    row = outcomes_by_id(run_new([ticket("T1", created="2026-09-01T08:00")], ONE_TECHNICIAN))["T1"]

    assert row.sla_status is SlaStatus.ON_TIME
    assert row.start_late_minutes == 0
    assert row.is_evaluable


def test_a_ticket_started_after_its_deadline_is_late_started():
    tickets = [
        ticket("chan", created="2026-09-01T05:00", priority=Priority.P1, repair_minutes=300),
        ticket("tre", created="2026-09-01T08:30", priority=Priority.P2, sla_minutes=30, repair_minutes=30),
    ]
    row = outcomes_by_id(run_new(tickets, ONE_TECHNICIAN))["tre"]

    assert row.sla_status is SlaStatus.LATE_STARTED
    assert row.start_late_minutes > 0
    assert row.is_evaluable


def test_an_unstarted_ticket_past_its_deadline_is_a_breach_not_a_gap():
    """Chưa bắt đầu và đã qua hạn: `OPEN_OVERDUE`, và nó nằm **trong** mẫu số.

    Phút trễ đo từ hạn tới hết thời gian mô phỏng, vì cư dân vẫn đang chờ ở đó.
    """
    tickets = [
        # Gửi từ đêm hôm trước nên nó đã *thực sự bắt đầu* lúc 08:03 và không bị
        # P2 chen ngang. Không có chi tiết đó thì P2 sẽ chạy trước và bài test
        # này sẽ không còn nói về ticket chưa bắt đầu nữa.
        ticket("chan", created="2026-09-01T05:00", priority=Priority.P1, repair_minutes=5000),
        ticket("bi_bo_quen", created="2026-09-01T08:30", priority=Priority.P2, sla_minutes=P2_MINUTES),
    ]
    scene = scenario(tickets, ONE_TECHNICIAN, settings=ONE_DAY)
    result = run_scenario(scene, new_app_policy(NEW_APP))
    row = outcomes_by_id(result)["bi_bo_quen"]

    assert row.work_started_at is None
    assert row.sla_status is SlaStatus.OPEN_OVERDUE
    assert row.outcome is Outcome.ASSIGNED  # đã có người, chỉ là chưa tới lượt
    assert row.start_late_minutes > 0
    assert row.is_evaluable
    assert result.summary.sla_evaluable_tickets == 2


def test_an_unstarted_ticket_not_yet_due_is_neither_a_success_nor_a_breach():
    """`OPEN_NOT_DUE` đứng ngoài mẫu số. Đếm nó là đúng hạn sẽ là ghi công cho
    một lời hứa còn chưa tới lúc phải giữ."""
    tickets = [
        ticket("chan", created="2026-09-01T05:00", priority=Priority.P1, repair_minutes=5000),
        # P1: hạn 1800 phút giờ phục vụ, tức 03/09 18:00 — còn xa mốc kết thúc.
        ticket("chua_toi_han", created="2026-09-01T08:30", priority=Priority.P1),
    ]
    scene = scenario(tickets, ONE_TECHNICIAN, settings=ONE_DAY)
    result = run_scenario(scene, new_app_policy(NEW_APP))
    row = outcomes_by_id(result)["chua_toi_han"]

    assert row.work_started_at is None
    assert row.sla_status is SlaStatus.OPEN_NOT_DUE
    assert row.start_late_minutes == 0
    assert not row.is_evaluable
    assert result.summary.sla_open_not_due_tickets == 1


def test_a_p3_is_not_evaluable_and_never_counted_against_anyone():
    tickets = [ticket("khan", created="2026-09-01T09:00", priority=Priority.P3, sla_minutes=P3_MINUTES)]
    result = run_new(tickets, ONE_TECHNICIAN)
    row = outcomes_by_id(result)["khan"]

    assert row.sla_status is SlaStatus.NOT_EVALUABLE
    assert row.start_late_minutes == 0
    assert result.summary.sla_evaluable_tickets == 0
    assert result.summary.compliance_rate is None


def test_a_ticket_nobody_can_take_still_faces_its_deadline():
    """Không ai đủ điều kiện nhận không làm cho lời hứa với cư dân biến mất.

    Nó không được phân công — và cũng không được cho ra ngoài mẫu số.
    """
    tickets = [
        ticket("khong_ai_lam_duoc", created="2026-09-01T08:00", priority=Priority.P2,
               sla_minutes=P2_MINUTES, required_skill="welding")
    ]
    result = run_new(tickets, ONE_TECHNICIAN)
    row = outcomes_by_id(result)["khong_ai_lam_duoc"]

    assert row.outcome is Outcome.NO_ELIGIBLE_TECHNICIAN
    assert row.assigned_technician_id is None
    assert row.sla_status is SlaStatus.OPEN_OVERDUE
    assert result.summary.sla_evaluable_tickets == 1
    assert result.summary.compliance_rate == 0.0


# ---------------------------------------------------------------------------
# Mẫu số.
# ---------------------------------------------------------------------------


def test_the_denominator_is_exactly_the_three_evaluable_statuses():
    assert EVALUABLE_STATUSES == frozenset(
        {SlaStatus.ON_TIME, SlaStatus.LATE_STARTED, SlaStatus.OPEN_OVERDUE}
    )


def test_every_ticket_lands_in_exactly_one_bucket():
    """Mẫu số cộng với hai nhóm ngoài mẫu số phải bằng tổng. Nếu không, có ticket
    đang biến mất khỏi bảng."""
    scene = scenario(
        [
            ticket("ok", created="2026-09-01T08:00"),
            ticket("p3", created="2026-09-01T08:00", priority=Priority.P3, sla_minutes=P3_MINUTES),
            ticket("khong_ky_nang", created="2026-09-01T08:00", required_skill="welding"),
            *BACKLOG,
        ],
        ONE_TECHNICIAN,
        settings=ONE_DAY,
    )
    for policy in (old_app_policy(OLD_APP), new_app_policy(NEW_APP)):
        s = run_scenario(scene, policy).summary
        assert (
            s.sla_evaluable_tickets + s.sla_open_not_due_tickets + s.sla_not_evaluable_tickets
            == s.total_tickets
        )


def test_a_rate_over_an_empty_denominator_is_null_not_zero_or_one_hundred():
    result = run_new(
        [ticket("p3", created="2026-09-01T08:00", priority=Priority.P3, sla_minutes=P3_MINUTES)],
        ONE_TECHNICIAN,
    )
    assert result.summary.sla_evaluable_tickets == 0
    assert result.summary.compliance_rate is None


def test_the_late_minutes_total_counts_both_kinds_of_breach():
    """Bắt đầu trễ và chưa bắt đầu là hai cách vi phạm, và tổng phút trễ đếm cả
    hai — chúng cộng được vì cả hai đều là "đã quá hạn bao lâu mà chưa xử lý"."""
    scene = scenario(OVERDUE_BACKLOG, ONE_TECHNICIAN, settings=ONE_DAY)
    result = run_scenario(scene, new_app_policy(NEW_APP))
    s = result.summary

    assert s.sla_late_started_tickets > 0
    assert s.sla_open_overdue_tickets > 0
    by_hand = sum(
        row.start_late_minutes
        for row in result.tickets
        if row.sla_status in {SlaStatus.LATE_STARTED, SlaStatus.OPEN_OVERDUE}
    )
    assert s.total_start_late_minutes == by_hand
    assert s.total_start_late_minutes > 0


def test_late_minutes_are_zero_for_every_status_that_is_not_a_breach():
    scene = scenario(
        [
            ticket("ok", created="2026-09-01T08:00"),
            ticket("p3", created="2026-09-01T08:00", priority=Priority.P3, sla_minutes=P3_MINUTES),
        ],
        ONE_TECHNICIAN,
    )
    for row in run_scenario(scene, new_app_policy(NEW_APP)).tickets:
        assert row.start_late_minutes == 0


# ---------------------------------------------------------------------------
# Đồng hồ nào.
# ---------------------------------------------------------------------------


def overnight_fixture():
    """Một P2 tới hạn 17:50 mà mãi 08:10 sáng hôm sau mới có người bắt đầu.

    Việc chặn bắt đầu lúc 08:04 và chiếm đúng 596 phút, tức tới 18:00. Việc thứ
    hai ở tầng 8 nên mất 10 phút thang máy: rời đi lúc 08:00 hôm sau, tới nơi
    08:10. Hai đồng hồ mô tả cùng một khoảng đó bằng hai con số rất khác nhau, và
    đó là cả điểm của cặp test này.
    """
    return [
        ticket("chan", created="2026-09-01T08:00", priority=Priority.P1, repair_minutes=596, floor=1),
        ticket("qua_dem", created="2026-09-01T14:50", priority=Priority.P2, sla_minutes=180,
               repair_minutes=30, floor=8),
    ]


def test_late_minutes_use_the_service_clock_under_the_service_policy():
    """Hạn 17:50, bắt đầu 08:10 sáng hôm sau: **20 phút** giờ phục vụ, không phải
    mười bốn tiếng hai mươi treo tường."""
    row = outcomes_by_id(
        run_new(overnight_fixture(), ONE_TECHNICIAN, sla_policy=SlaPolicy.SERVICE_HOURS_DRAFT_V1)
    )["qua_dem"]

    assert stamp(row.sla_due_at) == "2026-09-01 17:50"
    assert stamp(row.work_started_at) == "2026-09-02 08:10"
    assert row.start_late_minutes == 20


def test_the_same_run_on_the_wall_clock_reports_the_whole_night():
    row = outcomes_by_id(
        run_new(overnight_fixture(), ONE_TECHNICIAN, sla_policy=SlaPolicy.WALL_CLOCK_V1)
    )["qua_dem"]

    assert row.start_late_minutes == 14 * 60 + 20


# ---------------------------------------------------------------------------
# Thời gian mô phỏng.
# ---------------------------------------------------------------------------


def test_the_horizon_is_anchored_on_the_first_report_not_on_readiness():
    """`created_at`, vì đó là sự thật về kịch bản. `ready_at` là sự thật về
    *chính sách*, và neo vào nó sẽ đưa cho mỗi luồng một vạch đích khác nhau."""
    scene = scenario(
        [ticket("T1", created="2026-09-01T07:50")], ONE_TECHNICIAN, settings=Settings(simulation_horizon_days=2)
    )
    assert stamp(scenario_horizon(scene)) == "2026-09-03 08:00"


def test_both_flows_stop_at_the_same_instant():
    scene = scenario(BACKLOG, ONE_TECHNICIAN, settings=ONE_DAY)
    run = run_comparison(scene)

    assert run.horizon_end == scenario_horizon(scene)
    for result in (run.old_app, run.new_app):
        assert result.summary.started_tickets < len(BACKLOG)


def test_the_horizon_moves_with_its_setting():
    short = scenario(BACKLOG, ONE_TECHNICIAN, settings=ONE_DAY)
    long = scenario(BACKLOG, ONE_TECHNICIAN, settings=Settings(simulation_horizon_days=30))
    assert scenario_horizon(long) - scenario_horizon(short) == timedelta(days=29)


def test_a_job_that_started_before_the_horizon_runs_to_completion():
    """Quy tắc nói về việc *bắt đầu*. Bỏ đi một công việc đã được xếp lịch tử tế
    chỉ vì nó dài là một câu trả lời khác, và là câu sai."""
    scene = scenario(
        [ticket("T1", created="2026-09-01T08:00", repair_minutes=2000)],
        ONE_TECHNICIAN,
        settings=ONE_DAY,
    )
    row = outcomes_by_id(run_scenario(scene, new_app_policy(NEW_APP)))["T1"]

    assert row.work_started_at < scenario_horizon(scene)
    assert row.completed_at > scenario_horizon(scene)
    assert row.sla_status is SlaStatus.ON_TIME


def test_a_generous_horizon_lets_the_whole_backlog_through():
    scene = scenario(BACKLOG, ONE_TECHNICIAN, settings=Settings(simulation_horizon_days=60))
    result = run_scenario(scene, new_app_policy(NEW_APP))

    assert result.summary.started_tickets == len(BACKLOG)
    assert result.summary.sla_open_overdue_tickets == 0


def test_work_refused_by_the_horizon_costs_the_technician_no_time():
    """Một việc chưa tới lượt không được tiêu mất ca làm của ai, nếu không những
    ticket phía sau sẽ thừa hưởng một độ trễ chưa từng xảy ra."""
    scene = scenario(BACKLOG, ONE_TECHNICIAN, settings=ONE_DAY)
    result = run_scenario(scene, new_app_policy(NEW_APP))
    load = result.summary.technician_utilization[0]

    assert load.work_minutes == sum(
        row.repair_minutes for row in result.tickets if row.work_started_at is not None
    )
