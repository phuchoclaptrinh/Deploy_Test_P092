"""One strict JSON scenario in. Nothing else is accepted.

The input is a single JSON object describing a whole scenario -- the building,
the SLA policy, the settings, the roster and the reports. Not three pasted
files, not CSV, not a spreadsheet export with `Yes` where a boolean belongs.

**Why strict.** A simulation is only worth running if its input is the input the
coordinator thinks they submitted. Every leniency this parser could offer is a
way for a run to answer a question nobody asked:

* a CSV cell is always a string, so `false` and `"false"` become the same value
  and `need_hand_categorized` silently flips;
* a timestamp with no offset is seven hours wrong in one direction or the other,
  and either way it moves every deadline in the result;
* `"plumbing;electrical"` and `["plumbing", "electrical"]` look the same to a
  person and different to a parser;
* an unknown key is almost always a typo for a known one -- `travel_per_floor`
  instead of `travel_per_floor_minutes` -- and accepting it means running with a
  default while the screen shows the value that was ignored.

So all of those are refused, each with a message that names the field and the
row, in Vietnamese, so the screen can put the cursor on the offending line.

**Two places a value is filled in rather than refused**, both of them recorded
on the ticket so the result can say which numbers were assumed:

* `repair_minutes` falls back to the internal P80 for the issue type
  (`src.dispatch.durations`), the same capacity estimate production schedules
  with, and the ticket is marked `P80_FALLBACK`.
* `sla_minutes` falls back to **the policy the run is under**
  (`POLICY_SLA_MINUTES[policy][priority]`) and is marked `POLICY`. A scenario
  may still supply its own number -- that is a real question Building Management
  asks -- but then the ticket is marked `INPUT_OVERRIDE` and the run warns,
  because a deadline typed into the input is not a deadline the policy makes.
  Leaving that unmarked is how a P1 carrying `4320` gets measured as 4320
  *service* minutes, or 7.2 working days, under a policy whose whole purpose was
  to keep it at three.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from src.dispatch.durations import p80_for_code
from src.domain.sla_clock import CURRENT_POLICY, POLICY_SLA_MINUTES, SlaPolicy
from src.models.enums import Priority
from src.simulation.models import (
    POLICY_PRIORITIES,
    BuildingConfig,
    NewAppSettings,
    OldAppSettings,
    ScenarioInput,
    Settings,
    SimTechnician,
    SimTicket,
    SlaDurationSource,
    production_micro_batch_interval_ms,
    production_micro_batch_size,
)

#: The API's ceiling on one run. Five hundred reports is more than a month of
#: this building's volume and still replays in well under a second; the limit is
#: there so a pasted million-row file is refused rather than tying up a worker.
MAX_TICKETS = 500
MAX_TECHNICIANS = 200

_SCENARIO_KEYS = {"scenario_name", "building", "sla_policy", "settings", "technicians", "tickets"}
_BUILDING_KEYS = {"floor_count", "units_per_floor"}
_SLA_POLICY_KEYS = {"mode"}
_SETTINGS_KEYS = {
    "travel_base_minutes",
    "travel_per_floor_minutes",
    "micro_batch_interval_ms",
    "micro_batch_size",
    "simulation_horizon_days",
    "old_app",
    "new_app",
}
_OLD_APP_KEYS = {"manual_category_minutes", "manual_dispatch_minutes"}
_NEW_APP_KEYS = {"ai_classification_minutes", "manual_review_minutes"}
_TICKET_KEYS = {
    "ticket_id",
    "created_at",
    "floor",
    "unit",
    "issue_type",
    "priority",
    "sla_minutes",
    "repair_minutes",
    "required_skill",
    "need_hand_categorized",
    "score_total",
    "excluded_technician_ids",
}
_TECHNICIAN_KEYS = {"technician_id", "skills", "start_floor", "is_active", "is_available"}


class SimulationInputError(ValueError):
    """One located input problem, ready to be shown next to the offending line."""

    def __init__(self, message: str, *, field: str | None = None, index: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.index = index

    def as_details(self) -> dict[str, Any]:
        return {"field": self.field, "index": self.index}


# ----------------------------------------------------------------------------
# Primitives. Every one of them refuses a coerced string.
# ----------------------------------------------------------------------------


def _object(value: Any, *, field: str, index: int | None = None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SimulationInputError(f"'{field}' phải là một object JSON.", field=field, index=index)
    return value


def _reject_unknown(row: Mapping[str, Any], allowed: set[str], *, field: str, index: int | None = None) -> None:
    """An unknown key is a typo, not an extension.

    Accepting it would mean running with a default while the screen shows the
    value that was quietly ignored -- the worst of both.
    """
    unknown = sorted(set(row) - allowed)
    if unknown:
        raise SimulationInputError(
            f"Trường không hợp lệ trong '{field}': {', '.join(unknown)}. "
            f"Cho phép: {', '.join(sorted(allowed))}.",
            field=field,
            index=index,
        )


def _require(row: Mapping[str, Any], key: str, *, field: str, index: int | None = None) -> Any:
    if key not in row or row[key] is None:
        raise SimulationInputError(f"Thiếu trường bắt buộc '{key}'.", field=key, index=index)
    return row[key]


def _string(value: Any, *, field: str, index: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SimulationInputError(f"'{field}' phải là chuỗi không rỗng.", field=field, index=index)
    return value.strip()


def _int(value: Any, *, field: str, index: int | None = None, minimum: int | None = None) -> int:
    # `bool` is an `int` in Python, and `True` arriving where a count belongs is
    # a mistake worth naming rather than reading as 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise SimulationInputError(f"'{field}' phải là số nguyên (không phải chuỗi).", field=field, index=index)
    if minimum is not None and value < minimum:
        raise SimulationInputError(f"'{field}' phải >= {minimum}.", field=field, index=index)
    return value


def _number(value: Any, *, field: str, index: int | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SimulationInputError(f"'{field}' phải là số.", field=field, index=index)
    return float(value)


def _bool(value: Any, *, field: str, index: int | None = None) -> bool:
    """A JSON boolean, and only a JSON boolean.

    `"Yes"`, `"true"` and `1` are all refused. In a CSV they were unavoidable;
    in a JSON document they mean the producer did not know what type the field
    was, and guessing on their behalf is how a run flips a flag silently.
    """
    if not isinstance(value, bool):
        raise SimulationInputError(
            f"'{field}' phải là boolean JSON true/false (không nhận \"Yes\"/\"No\" hay 0/1).",
            field=field,
            index=index,
        )
    return value


def parse_datetime(value: Any, *, field: str, index: int | None = None) -> datetime:
    """An ISO-8601 instant that **carries its own offset**.

    A naive timestamp is refused rather than assumed. Reading it as UTC shifts
    every report seven hours into the night; reading it as Vietnam time is a
    guess about a producer this parser has never met. Either way the deadlines
    in the result would be wrong by a shift, which is exactly the error this
    screen exists to measure.
    """
    if not isinstance(value, str):
        raise SimulationInputError(f"'{field}' phải là chuỗi thời gian ISO-8601.", field=field, index=index)
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise SimulationInputError(
            f"'{field}' không phải thời điểm ISO-8601 hợp lệ (ví dụ 2026-09-01T17:00:00+07:00).",
            field=field,
            index=index,
        ) from error
    if parsed.tzinfo is None:
        raise SimulationInputError(
            f"'{field}' phải kèm múi giờ, ví dụ 2026-09-01T17:00:00+07:00.",
            field=field,
            index=index,
        )
    return parsed.astimezone(UTC)


#: Những mức mà chính sách V1 hiểu. Hệ đã mở `Priority` lên P1-P5 cho rubric
#: rủi ro v2, nhưng `WALL_CLOCK_V1`/`SERVICE_HOURS_DRAFT_V1` được đo trên đúng
#: ba mức này — nhận thêm P4 hay P5 ở đây sẽ để một kịch bản mới lọt vào một
#: chính sách chưa từng được hiệu chỉnh cho nó, và im lặng cho ra một con số so
#: sánh vô nghĩa.
V1_PRIORITIES: tuple[Priority, ...] = (Priority.P1, Priority.P2, Priority.P3)


def parse_priority(
    value: Any, *, index: int | None = None, sla_policy: SlaPolicy = CURRENT_POLICY
) -> Priority:
    """Một mức mà chính sách đang chạy hiểu được. Chữ số trần bị từ chối.

    Tập hợp cho phép đến từ chính sách chứ không phải từ `Priority`: một kịch
    bản V1 chỉ được dùng P1-P3, còn V2 nhận cả năm mức. Chấp nhận mọi mức ở đây
    sẽ cho một kịch bản P4 chạy dưới một chính sách không có hạn SLA nào cho P4.
    """
    allowed = POLICY_PRIORITIES[sla_policy]
    names = ", ".join(item.value for item in allowed)
    if not isinstance(value, str):
        raise SimulationInputError(f"'priority' phải là chuỗi: {names}.", field="priority", index=index)
    try:
        priority = Priority(value.strip().upper())
    except ValueError as error:
        raise SimulationInputError(f"'priority' chỉ nhận: {names}.", field="priority", index=index) from error
    if priority not in allowed:
        raise SimulationInputError(f"'priority' chỉ nhận: {names}.", field="priority", index=index)
    return priority


# ----------------------------------------------------------------------------
# Rows.
# ----------------------------------------------------------------------------


def _sla_duration(
    row: Mapping[str, Any],
    *,
    index: int,
    priority: Priority,
    sla_policy: SlaPolicy,
) -> tuple[int, SlaDurationSource]:
    """How long this ticket has, and who decided that.

    The policy decides by default, because "run the same dataset under two SLA
    policies" is only a meaningful sentence if the policy is what changes the
    deadline. A scenario that pins its own number is answering a different and
    equally real question -- "what if we promised P2 four hours?" -- and gets to,
    as long as the row says so afterwards.

    A supplied number that *agrees* with the policy is not an override. It is a
    scenario written out in full, which is the normal case for an exported file,
    and marking it `INPUT_OVERRIDE` would fill the screen with warnings about
    nothing.
    """
    from_policy = POLICY_SLA_MINUTES[sla_policy][priority]
    if row.get("sla_minutes") is None:
        return from_policy, SlaDurationSource.POLICY
    supplied = _int(row["sla_minutes"], field="sla_minutes", index=index, minimum=1)
    if supplied == from_policy:
        return supplied, SlaDurationSource.POLICY
    return supplied, SlaDurationSource.INPUT_OVERRIDE


def parse_ticket(row: Any, index: int, building: BuildingConfig, sla_policy: SlaPolicy) -> SimTicket:
    row = _object(row, field="tickets", index=index)
    _reject_unknown(row, _TICKET_KEYS, field="tickets", index=index)

    floor = _int(_require(row, "floor", field="tickets", index=index), field="floor", index=index, minimum=1)
    if floor > building.floor_count:
        raise SimulationInputError(
            f"'floor' = {floor} vượt quá số tầng của tòa nhà ({building.floor_count}).",
            field="floor",
            index=index,
        )

    issue_type = _string(_require(row, "issue_type", field="tickets", index=index), field="issue_type", index=index)
    if row.get("repair_minutes") is None:
        # The same P80 production schedules with, so a run over an incomplete
        # export is still comparable with a real day.
        repair_minutes = max(1, int(p80_for_code(issue_type).total_seconds() // 60))
        repair_source = "P80_FALLBACK"
    else:
        repair_minutes = _int(row["repair_minutes"], field="repair_minutes", index=index, minimum=1)
        repair_source = "INPUT"

    priority = parse_priority(
        _require(row, "priority", field="tickets", index=index), index=index, sla_policy=sla_policy
    )
    sla_minutes, sla_source = _sla_duration(row, index=index, priority=priority, sla_policy=sla_policy)

    return SimTicket(
        ticket_id=_string(_require(row, "ticket_id", field="tickets", index=index), field="ticket_id", index=index),
        created_at=parse_datetime(
            _require(row, "created_at", field="tickets", index=index), field="created_at", index=index
        ),
        floor=floor,
        unit=_string(_require(row, "unit", field="tickets", index=index), field="unit", index=index),
        issue_type=issue_type,
        priority=priority,
        sla_minutes=sla_minutes,
        sla_duration_source=sla_source,
        repair_minutes=repair_minutes,
        required_skill=_string(
            _require(row, "required_skill", field="tickets", index=index), field="required_skill", index=index
        ),
        need_hand_categorized=_bool(
            _require(row, "need_hand_categorized", field="tickets", index=index),
            field="need_hand_categorized",
            index=index,
        ),
        # Absent means zero. The score only breaks ties between reports of the
        # same priority and deadline, so a scenario without it falls through to
        # arrival order rather than being refused.
        score_total=_number(row.get("score_total", 0), field="score_total", index=index),
        excluded_technician_ids=_excluded_technicians(row, index=index),
        repair_minutes_source=repair_source,
    )


def _excluded_technicians(row: Mapping[str, Any], *, index: int) -> frozenset[str]:
    """Technicians this ticket may not be given to, whatever else is true.

    A hard constraint rather than a preference: somebody who has left, somebody
    in dispute with this resident, somebody the ticket was just handed back
    from. The simulator never reconsiders them "because nobody else is free",
    which is what makes it a hard constraint.
    """
    raw = row.get("excluded_technician_ids")
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise SimulationInputError(
            "'excluded_technician_ids' phải là mảng JSON các mã kỹ thuật viên.",
            field="excluded_technician_ids",
            index=index,
        )
    return frozenset(
        _string(value, field="excluded_technician_ids", index=index) for value in raw
    )


def parse_technician(row: Any, index: int, building: BuildingConfig) -> SimTechnician:
    row = _object(row, field="technicians", index=index)
    _reject_unknown(row, _TECHNICIAN_KEYS, field="technicians", index=index)

    raw_skills = _require(row, "skills", field="technicians", index=index)
    if not isinstance(raw_skills, list):
        raise SimulationInputError(
            "'skills' phải là mảng JSON, ví dụ [\"plumbing\", \"electrical\"].", field="skills", index=index
        )
    skills = frozenset(_string(skill, field="skills", index=index).lower() for skill in raw_skills)
    if not skills:
        raise SimulationInputError("'skills' không được rỗng.", field="skills", index=index)

    start_floor = _int(row.get("start_floor", 1), field="start_floor", index=index, minimum=1)
    if start_floor > building.floor_count:
        raise SimulationInputError(
            f"'start_floor' = {start_floor} vượt quá số tầng của tòa nhà ({building.floor_count}).",
            field="start_floor",
            index=index,
        )
    return SimTechnician(
        technician_id=_string(
            _require(row, "technician_id", field="technicians", index=index), field="technician_id", index=index
        ),
        skills=skills,
        start_floor=start_floor,
        # Absent means yes: a roster listing somebody at all is listing somebody
        # who works here.
        is_active=_bool(row.get("is_active", True), field="is_active", index=index),
        is_available=_bool(row.get("is_available", True), field="is_available", index=index),
    )


# ----------------------------------------------------------------------------
# The scenario.
# ----------------------------------------------------------------------------


def parse_building(raw: Any) -> BuildingConfig:
    raw = _object(raw or {}, field="building")
    _reject_unknown(raw, _BUILDING_KEYS, field="building")
    return BuildingConfig(
        floor_count=_int(raw.get("floor_count", 30), field="building.floor_count", minimum=1),
        units_per_floor=_int(raw.get("units_per_floor", 7), field="building.units_per_floor", minimum=1),
    )


def parse_sla_policy(raw: Any) -> SlaPolicy:
    """Mặc định là chính sách production đang chạy, không phải một hằng số cố định.

    Lấy từ `CURRENT_POLICY` chứ không viết thẳng tên chính sách vào đây. Trước
    đây chỗ này ghi cứng `WALL_CLOCK_V1`, và khi hệ thống chuyển sang thang P1–P5
    thì mặc định của simulator ở lại thang cũ: một kịch bản không khai
    `sla_policy` chạy dưới rubric mà production đã bỏ, cho ra P3 là mức khẩn cấp
    và từ chối mọi ticket P4/P5 — im lặng, vì không khai gì thì không có gì để
    báo lỗi. Buộc nó bám vào `CURRENT_POLICY` là cách để hai chỗ không lệch nhau
    lần nữa.

    Kịch bản muốn chạy chính sách V1 vẫn khai `mode` như cũ và nhận đúng kết quả
    cũ; hai chính sách V1 không đổi một dòng nào.
    """
    raw = _object(raw or {}, field="sla_policy")
    _reject_unknown(raw, _SLA_POLICY_KEYS, field="sla_policy")
    mode = raw.get("mode", CURRENT_POLICY.value)
    try:
        return SlaPolicy(_string(mode, field="sla_policy.mode"))
    except ValueError as error:
        allowed = ", ".join(policy.value for policy in SlaPolicy)
        raise SimulationInputError(
            f"'sla_policy.mode' chỉ nhận: {allowed}.", field="sla_policy.mode"
        ) from error


def parse_settings(raw: Any) -> Settings:
    raw = _object(raw or {}, field="settings")
    _reject_unknown(raw, _SETTINGS_KEYS, field="settings")
    old_raw = _object(raw.get("old_app") or {}, field="settings.old_app")
    _reject_unknown(old_raw, _OLD_APP_KEYS, field="settings.old_app")
    new_raw = _object(raw.get("new_app") or {}, field="settings.new_app")
    _reject_unknown(new_raw, _NEW_APP_KEYS, field="settings.new_app")
    return Settings(
        travel_base_minutes=_int(raw.get("travel_base_minutes", 3), field="travel_base_minutes", minimum=0),
        travel_per_floor_minutes=_int(
            raw.get("travel_per_floor_minutes", 1), field="travel_per_floor_minutes", minimum=0
        ),
        # The batch shape defaults to the deployed dispatcher's own cadence, so
        # a scenario that omits it models the real gathering mechanism rather
        # than a guess at it. The *policy* applied inside a batch is still the
        # simulator's own; see `policies.py`.
        micro_batch_interval_ms=_int(
            raw.get("micro_batch_interval_ms", production_micro_batch_interval_ms()),
            field="micro_batch_interval_ms",
            minimum=1,
        ),
        micro_batch_size=_int(
            raw.get("micro_batch_size", production_micro_batch_size()),
            field="micro_batch_size",
            minimum=1,
        ),
        simulation_horizon_days=_int(
            raw.get("simulation_horizon_days", 14), field="simulation_horizon_days", minimum=1
        ),
        old_app=OldAppSettings(
            manual_category_minutes=_int(
                old_raw.get("manual_category_minutes", 10), field="old_app.manual_category_minutes", minimum=0
            ),
            manual_dispatch_minutes=_int(
                old_raw.get("manual_dispatch_minutes", 8), field="old_app.manual_dispatch_minutes", minimum=0
            ),
        ),
        new_app=NewAppSettings(
            ai_classification_minutes=_int(
                new_raw.get("ai_classification_minutes", 1),
                field="new_app.ai_classification_minutes",
                minimum=0,
            ),
            manual_review_minutes=_int(
                new_raw.get("manual_review_minutes", 10),
                field="new_app.manual_review_minutes",
                minimum=0,
            ),
        ),
    )


def parse_scenario(raw: Any) -> ScenarioInput:
    """One whole scenario, or the first problem found -- located by field and row."""
    raw = _object(raw, field="scenario")
    _reject_unknown(raw, _SCENARIO_KEYS, field="scenario")

    building = parse_building(raw.get("building"))
    # Parsed before the tickets, because it is what decides their deadlines.
    sla_policy = parse_sla_policy(raw.get("sla_policy"))
    return ScenarioInput(
        scenario_name=_string(raw.get("scenario_name") or "Kịch bản mô phỏng", field="scenario_name"),
        building=building,
        sla_policy=sla_policy,
        settings=parse_settings(raw.get("settings")),
        technicians=tuple(_parse_technicians(raw.get("technicians"), building)),
        tickets=tuple(_parse_tickets(raw.get("tickets"), building, sla_policy)),
    )


def _parse_tickets(raw: Any, building: BuildingConfig, sla_policy: SlaPolicy) -> list[SimTicket]:
    if not isinstance(raw, list):
        raise SimulationInputError("'tickets' phải là mảng JSON.", field="tickets")
    if not raw:
        raise SimulationInputError("Cần ít nhất một ticket để chạy mô phỏng.", field="tickets")
    if len(raw) > MAX_TICKETS:
        raise SimulationInputError(
            f"Tối đa {MAX_TICKETS} ticket mỗi lần chạy (đang nhận {len(raw)}).", field="tickets"
        )
    tickets: list[SimTicket] = []
    seen: set[str] = set()
    for index, row in enumerate(raw):
        ticket = parse_ticket(row, index, building, sla_policy)
        if ticket.ticket_id in seen:
            raise SimulationInputError(
                f"'ticket_id' bị trùng: {ticket.ticket_id}.", field="ticket_id", index=index
            )
        seen.add(ticket.ticket_id)
        tickets.append(ticket)
    return tickets


def _parse_technicians(raw: Any, building: BuildingConfig) -> list[SimTechnician]:
    if not isinstance(raw, list):
        raise SimulationInputError("'technicians' phải là mảng JSON.", field="technicians")
    if not raw:
        raise SimulationInputError("Cần ít nhất một kỹ thuật viên để chạy mô phỏng.", field="technicians")
    if len(raw) > MAX_TECHNICIANS:
        raise SimulationInputError(
            f"Tối đa {MAX_TECHNICIANS} kỹ thuật viên mỗi lần chạy (đang nhận {len(raw)}).", field="technicians"
        )
    technicians: list[SimTechnician] = []
    seen: set[str] = set()
    for index, row in enumerate(raw):
        technician = parse_technician(row, index, building)
        if technician.technician_id in seen:
            raise SimulationInputError(
                f"'technician_id' bị trùng: {technician.technician_id}.", field="technician_id", index=index
            )
        seen.add(technician.technician_id)
        technicians.append(technician)
    return technicians


__all__ = [
    "MAX_TECHNICIANS",
    "MAX_TICKETS",
    "SimulationInputError",
    "parse_building",
    "parse_datetime",
    "V1_PRIORITIES",
    "parse_priority",
    "parse_scenario",
    "parse_settings",
    "parse_sla_policy",
    "parse_technician",
    "parse_ticket",
]
