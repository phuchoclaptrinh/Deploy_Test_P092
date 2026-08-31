"""Bộ dựng dùng chung cho các test mô phỏng.

Mọi test trong gói này phát biểu kịch bản bằng giờ treo tường Việt Nam và kiểm
tra bằng giờ treo tường Việt Nam, vì một lịch làm việc viết bằng epoch second là
một lịch không ai kiểm lại khi nó hỏng.

Không fixture database nào được dùng ở đâu trong gói này, và đó chính là điểm:
trình mô phỏng không có session để mà đưa cho.
`test_no_database_writes.py` biến điều đó thành một khẳng định thay vì một thói
quen.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.dispatch.shift import VN_TZ, to_local
from src.domain.sla_clock import SlaPolicy
from src.models.enums import Priority
from src.simulation.models import (
    BuildingConfig,
    NewAppSettings,
    OldAppSettings,
    ScenarioInput,
    Settings,
    SimTechnician,
    SimTicket,
)

#: Ba ngày làm việc trên đồng hồ giờ phục vụ; dài thoải mái, để một test không
#: nói về hạn P1 thì không bao giờ vấp phải một cái.
P1_MINUTES = 1800
P2_MINUTES = 180
P3_MINUTES = 5


def local(text: str) -> datetime:
    """Một mốc giờ treo tường Việt Nam, dưới dạng UTC mà trình mô phỏng dùng."""
    return datetime.fromisoformat(text).replace(tzinfo=VN_TZ).astimezone(UTC)


def stamp(moment: datetime | None) -> str:
    """UTC trở lại thành chuỗi giờ Việt Nam đọc được, để so sánh trong test."""
    return to_local(moment).strftime("%Y-%m-%d %H:%M") if moment else "-"


def ticket(
    ticket_id: str,
    *,
    created: str = "2026-09-01T08:00",
    floor: int = 1,
    unit: str | None = None,
    issue_type: str = "WATER",
    priority: Priority = Priority.P1,
    sla_minutes: int = P1_MINUTES,
    repair_minutes: int = 60,
    required_skill: str = "plumbing",
    need_hand_categorized: bool = False,
    score_total: float = 0.0,
    excluded_technician_ids: tuple[str, ...] = (),
) -> SimTicket:
    return SimTicket(
        ticket_id=ticket_id,
        created_at=local(created),
        floor=floor,
        unit=unit or f"{floor:02d}01",
        issue_type=issue_type,
        priority=priority,
        sla_minutes=sla_minutes,
        repair_minutes=repair_minutes,
        required_skill=required_skill,
        need_hand_categorized=need_hand_categorized,
        score_total=score_total,
        excluded_technician_ids=frozenset(excluded_technician_ids),
    )


def technician(
    technician_id: str = "KTV_01",
    *,
    skills: tuple[str, ...] = ("plumbing",),
    start_floor: int = 1,
    is_active: bool = True,
    is_available: bool = True,
) -> SimTechnician:
    return SimTechnician(
        technician_id=technician_id,
        skills=frozenset(skill.lower() for skill in skills),
        start_floor=start_floor,
        is_active=is_active,
        is_available=is_available,
    )


def scenario(
    tickets: list[SimTicket],
    technicians: list[SimTechnician],
    *,
    name: str = "Kịch bản kiểm thử",
    sla_policy: SlaPolicy = SlaPolicy.SERVICE_HOURS_DRAFT_V1,
    settings: Settings | None = None,
    building: BuildingConfig | None = None,
) -> ScenarioInput:
    """Một kịch bản, với mặc định MVP cho mọi thứ test không ghim.

    Đồng hồ giờ phục vụ là mặc định ở đây vì đó là chính sách màn hình được xây
    để đánh giá; test về hành vi treo tường truyền nó vào tường minh, và điều đó
    cũng khiến những test đó tự nói ra chúng nói về cái gì.
    """
    return ScenarioInput(
        scenario_name=name,
        building=building or BuildingConfig(),
        sla_policy=sla_policy,
        settings=settings or Settings(),
        technicians=tuple(technicians),
        tickets=tuple(tickets),
    )


DEFAULT_SETTINGS = Settings()
OLD_APP = OldAppSettings()
NEW_APP = NewAppSettings()


def outcomes_by_id(result) -> dict:
    return {outcome.ticket_id: outcome for outcome in result.tickets}


__all__ = [
    "DEFAULT_SETTINGS",
    "NEW_APP",
    "OLD_APP",
    "P1_MINUTES",
    "P2_MINUTES",
    "P3_MINUTES",
    "UTC",
    "local",
    "outcomes_by_id",
    "scenario",
    "stamp",
    "technician",
    "ticket",
]
