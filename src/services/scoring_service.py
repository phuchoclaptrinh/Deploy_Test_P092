"""Transparent, versioned priority scoring defined in Logic_xử_lý_chính_v4.

Runtime may load the active `scoring_rule_versions.config` row. Canonical v2
constants remain as a bootstrap/test fallback so scoring can be unit-tested
without a migrated PostgreSQL database.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from src.models.enums import Category, Priority, Severity

CANONICAL_CATEGORY_BASE = {
    Category.WATER: 10,
    Category.WALL_DAMP: 20,
    Category.ELEVATOR: 35,
    Category.POWER_OUTAGE: 40,
    Category.SECURITY_SAFETY: 40,
    Category.NOISE: 40,
    Category.LOCK_DOOR: 30,
    Category.HVAC: 20,
    Category.ODOR_HYGIENE: 40,
    Category.INTERNET_TV: 40,
    Category.COMMON_AREA_DAMAGE: 10,
}
CANONICAL_LOCATION_BONUS = {
    Category.WALL_DAMP: {"ROOFTOP": 15, "EXTERIOR_FACADE": 15},
    Category.ELEVATOR: {"ELEVATOR": 15, "ELEVATOR_LOBBY": 15},
    Category.POWER_OUTAGE: {"ELEVATOR": 10, "ELEVATOR_LOBBY": 10},
    Category.SECURITY_SAFETY: {"ENTRANCE_GATE": 10, "SECURITY_BOOTH": 10, "PLAYGROUND": 10},
    Category.HVAC: {"COMMUNITY_ROOM": 10},
    Category.COMMON_AREA_DAMAGE: {
        "FIRE_EXIT": 25,
        "ELEVATOR_LOBBY": 10,
        "LOBBY_RECEPTION": 10,
        "ENTRANCE_GATE": 10,
        "DRIVEWAY": 10,
    },
}
CANONICAL_SEVERITY_SCORE = {Severity.LOW: 0, Severity.MEDIUM: 10, Severity.HIGH: 20}
CANONICAL_SLA_DURATION = {
    Priority.P3: timedelta(minutes=5),
    Priority.P2: timedelta(hours=3),
    Priority.P1: timedelta(hours=72),
}
PRIORITY_RANK = {Priority.P1: 1, Priority.P2: 2, Priority.P3: 3}

# Backward-compatible aliases for code/tests importing the old names.
CATEGORY_BASE = CANONICAL_CATEGORY_BASE
SEVERITY_SCORE = CANONICAL_SEVERITY_SCORE
SLA_DURATION = CANONICAL_SLA_DURATION


@dataclass(frozen=True)
class ScoringResult:
    score_total: Decimal | None
    priority_raw: Priority
    priority_final: Priority
    ceiling_applied: bool
    components: dict[str, int]


class ScoringService:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.category_base = dict(CANONICAL_CATEGORY_BASE)
        self.location_matrix = {
            category.value: dict(values) for category, values in CANONICAL_LOCATION_BONUS.items()
        }
        self.severity_scores = dict(CANONICAL_SEVERITY_SCORE)
        self.density_categories = {Category.WATER}
        self.density_2_3 = 15
        self.density_4_plus = 30
        self.p1_upper_exclusive = 30
        self.p3_lower_inclusive = 60
        self.sla_duration = dict(CANONICAL_SLA_DURATION)
        if config:
            self._load_config(config)

    def _load_config(self, config: dict[str, Any]) -> None:
        try:
            category_base = config["category_base"]
            severity = config["severity"]
            density = config["density"]
            thresholds = config["thresholds"]
            sla_minutes = config.get("sla_minutes", {"P3": 5, "P2": 180, "P1": 4320})
        except (KeyError, TypeError) as exc:
            raise ValueError("Invalid scoring_rule_versions config.") from exc

        self.category_base = {Category(key): int(value) for key, value in category_base.items()}
        if set(self.category_base) != set(Category):
            raise ValueError("Scoring config must define every Self Dev Category.")

        self.severity_scores = {Severity(key): int(value) for key, value in severity.items()}
        if set(self.severity_scores) != set(Severity):
            raise ValueError("Scoring config must define every Severity.")

        self.location_matrix = {}
        for category_key, location_values in (config.get("location_bonus") or {}).items():
            self.location_matrix[str(category_key).upper()] = {
                str(code).upper(): int(value) for code, value in location_values.items()
            }

        self.density_categories = {Category(value) for value in density.get("categories", [])}
        self.density_2_3 = int(density.get("2-3", 15))
        self.density_4_plus = int(density.get("4+", 30))

        # The v2 config serializes threshold expressions as strings. Parse the
        # canonical boundaries rather than duplicating them in coordinator code.
        p1 = str(thresholds.get("P1", "<30")).strip()
        p3 = str(thresholds.get("P3", ">=60")).strip()
        if not p1.startswith("<") or not p3.startswith(">="):
            raise ValueError("Unsupported scoring threshold syntax.")
        self.p1_upper_exclusive = int(p1[1:])
        self.p3_lower_inclusive = int(p3[2:])
        if self.p1_upper_exclusive >= self.p3_lower_inclusive:
            raise ValueError("Scoring thresholds overlap.")

        self.sla_duration = {
            Priority.P1: timedelta(minutes=int(sla_minutes["P1"])),
            Priority.P2: timedelta(minutes=int(sla_minutes["P2"])),
            Priority.P3: timedelta(minutes=int(sla_minutes["P3"])),
        }

    def location_bonus(self, category: Category, location_type_code: str | None) -> int:
        return self.location_bonus_by_code(category.value, location_type_code)

    def density_score(self, category: Category, density_count: int) -> int:
        if category not in self.density_categories:
            return 0
        if density_count >= 4:
            return self.density_4_plus
        if density_count >= 2:
            return self.density_2_3
        return 0

    def calculate(
        self,
        *,
        category: Category,
        severity: Severity,
        location_type_code: str | None,
        density_count: int,
        red_flag_detected: bool,
        priority_ceiling: Priority | None = None,
    ) -> ScoringResult:
        if red_flag_detected:
            # Self Dev: red flag forces P3 immediately and bypasses normal scoring/ceiling.
            return ScoringResult(
                score_total=None,
                priority_raw=Priority.P3,
                priority_final=Priority.P3,
                ceiling_applied=False,
                components={"red_flag_override": 1},
            )

        category_base = self.category_base[category]
        location = self.location_bonus(category, location_type_code)
        density = self.density_score(category, density_count)
        severity_score = self.severity_scores[severity]
        total = category_base + location + density + severity_score

        if total < self.p1_upper_exclusive:
            raw = Priority.P1
        elif total < self.p3_lower_inclusive:
            raw = Priority.P2
        else:
            raw = Priority.P3

        final = raw
        ceiling_applied = False
        if priority_ceiling is not None and PRIORITY_RANK[raw] > PRIORITY_RANK[priority_ceiling]:
            final = priority_ceiling
            ceiling_applied = True

        return ScoringResult(
            score_total=Decimal(total),
            priority_raw=raw,
            priority_final=final,
            ceiling_applied=ceiling_applied,
            components={
                "category_base": category_base,
                "location_category": location,
                "density": density,
                "severity": severity_score,
                "red_flag_override": 0,
            },
        )

    def calculate_dynamic(
        self,
        *,
        category_code: str,
        base_score: int | None,
        severity: Severity,
        location_type_code: str | None,
        density_count: int,
        red_flag_detected: bool,
        priority_ceiling: Priority | None = None,
    ) -> ScoringResult:
        if red_flag_detected:
            return ScoringResult(
                score_total=None,
                priority_raw=Priority.P3,
                priority_final=Priority.P3,
                ceiling_applied=False,
                components={"red_flag_override": 1},
            )
        if base_score is None:
            raise ValueError("Active Category requires base_score for scoring.")

        location = self.location_bonus_by_code(category_code, location_type_code)
        density = self.density_score_by_code(category_code, density_count)
        severity_score = self.severity_scores[severity]
        total = int(base_score) + location + density + severity_score

        if total < self.p1_upper_exclusive:
            raw = Priority.P1
        elif total < self.p3_lower_inclusive:
            raw = Priority.P2
        else:
            raw = Priority.P3

        final = raw
        ceiling_applied = False
        if priority_ceiling is not None and PRIORITY_RANK[raw] > PRIORITY_RANK[priority_ceiling]:
            final = priority_ceiling
            ceiling_applied = True

        return ScoringResult(
            score_total=Decimal(total),
            priority_raw=raw,
            priority_final=final,
            ceiling_applied=ceiling_applied,
            components={
                "category_base": int(base_score),
                "location_category": location,
                "density": density,
                "severity": severity_score,
                "red_flag_override": 0,
            },
        )

    def location_bonus_by_code(self, category_code: str, location_type_code: str | None) -> int:
        category = (category_code or "").upper()
        location = (location_type_code or "").upper()
        return int(self.location_matrix.get(category, {}).get(location, 0))

    def density_score_by_code(self, category_code: str, density_count: int) -> int:
        if category_code != Category.WATER.value:
            return 0
        return self.density_score(Category(category_code), density_count)


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
    if priority is None:
        return None
    return {
        Priority.P3: "Mức khẩn cấp cao nhất",
        Priority.P2: "Cần xử lý sớm",
        Priority.P1: "Mức xử lý thông thường",
    }[priority]
