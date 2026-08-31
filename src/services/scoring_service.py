"""The words a resident reads about a priority.

Everything about how a priority is *decided* moved to `src.domain.risk_scoring`
when the v2 rubric replaced the base-score formula, and everything about when it
falls due moved to `src.domain.sla_clock`, which the simulator already used --
two implementations of "when is this due" would eventually disagree, and the
disagreement would surface as a report contradicting the screen a coordinator
was looking at. What is left here is the sentence shown to the person waiting.

The old `ScoringService` is gone with the thing it configured. So is
`scoring_rule_versions`: a versioned JSON blob of category base scores, location
bonuses and severity weights is exactly the configuration surface v2 removed,
and keeping the table would have left a second, editable definition of a
priority sitting next to the one in `docs/risk_scoring_v2.md`.
"""

from __future__ import annotations

from src.models.enums import Priority


def resident_status_text(status) -> str:
    if getattr(status, "value", status) == "LINKED_DUPLICATE":
        return "Đã gộp phản ánh"
    mapping = {
        "NEW": "Mới",
        "WAITING_RESIDENT_INFO": "Chờ bổ sung thông tin",
        "APPROVED": "Đã duyệt",
        "IN_PROGRESS": "Đang xử lý",
        "COMPLETED": "Hoàn thành",
        "UNRESOLVABLE": "Không xử lý được",
        "CANCELLED": "Đã hủy",
        "INVALID": "Không hợp lệ",
    }
    return mapping.get(getattr(status, "value", status), str(status))


def priority_description(priority: Priority | None) -> str | None:
    """What the band means, in the resident's terms.

    Deliberately about *handling*, not about score. A resident has no use for
    "72.5 out of 100", and telling them their report scored badly is a worse
    answer than telling them when somebody will come.
    """
    if priority is None:
        return None
    return {
        Priority.P5: "Sự cố khẩn cấp, Ban quản lý đang xử lý thủ công",
        Priority.P4: "Cần xử lý ngay trong ca",
        Priority.P3: "Cần xử lý sớm",
        Priority.P2: "Xử lý theo lịch thường",
        Priority.P1: "Mức xử lý thông thường",
    }[priority]


__all__ = [
    "priority_description",
    "resident_status_text",
]
