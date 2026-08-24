from src.models.enums import Category, Priority, Severity
from src.services.scoring_service import ScoringService


def test_common_light_fire_exit_example_is_p2():
    result = ScoringService().calculate(
        category=Category.COMMON_LIGHT,
        severity=Severity.MEDIUM,
        location_type_code="FIRE_EXIT",
        density_count=1,
        red_flag_detected=False,
    )
    assert result.score_total == 45
    assert result.priority_final == Priority.P2


def test_lock_door_high_score_is_capped_at_p2():
    result = ScoringService().calculate(
        category=Category.LOCK_DOOR,
        severity=Severity.HIGH,
        location_type_code="MAIN_DOOR",
        density_count=1,
        red_flag_detected=False,
        priority_ceiling=Priority.P2,
    )
    assert result.score_total == 75
    assert result.priority_raw == Priority.P3
    assert result.priority_final == Priority.P2
    assert result.ceiling_applied is True


def test_red_flag_forces_p3_and_bypasses_normal_score():
    result = ScoringService().calculate(
        category=Category.ODOR_HYGIENE,
        severity=Severity.LOW,
        location_type_code="CORRIDOR",
        density_count=1,
        red_flag_detected=True,
    )
    assert result.priority_final == Priority.P3
    assert result.score_total is None
    assert result.components == {"red_flag_override": 1}


def test_density_only_affects_water_and_electrical():
    service = ScoringService()
    assert service.density_score(Category.WATER_LEAK, 3) == 15
    assert service.density_score(Category.ELECTRICAL_SHORT, 4) == 30
    assert service.density_score(Category.HVAC, 9) == 0


def test_runtime_scoring_config_overrides_bootstrap_values():
    config = {
        "category_base": {category.value: 10 for category in Category},
        "location_bonus": {},
        "density": {
            "categories": [Category.WATER_LEAK.value, Category.ELECTRICAL_SHORT.value],
            "2-3": 15,
            "4+": 30,
        },
        "severity": {"LOW": 0, "MEDIUM": 10, "HIGH": 20},
        "thresholds": {"P1": "<30", "P2": "30-59", "P3": ">=60"},
        "sla_minutes": {"P1": 4320, "P2": 180, "P3": 5},
    }
    config["category_base"][Category.WATER_LEAK.value] = 30
    service = ScoringService(config)

    result = service.calculate(
        category=Category.WATER_LEAK,
        severity=Severity.LOW,
        location_type_code="CORRIDOR",
        density_count=1,
        red_flag_detected=False,
    )

    assert result.score_total == 30
    assert result.priority_final == Priority.P2
    assert service.sla_duration[Priority.P3].total_seconds() == 5 * 60


def test_dynamic_catalog_category_can_receive_a_location_bonus():
    config = {
        "category_base": {category.value: 10 for category in Category},
        "location_bonus": {"THAM_TUONG": {"ROOFTOP": 15}},
        "density": {"categories": [], "2-3": 15, "4+": 30},
        "severity": {"LOW": 0, "MEDIUM": 10, "HIGH": 20},
        "thresholds": {"P1": "<30", "P2": "30-59", "P3": ">=60"},
        "sla_minutes": {"P1": 4320, "P2": 180, "P3": 5},
    }

    result = ScoringService(config).calculate_dynamic(
        category_code="THAM_TUONG",
        base_score=20,
        severity=Severity.LOW,
        location_type_code="ROOFTOP",
        density_count=1,
        red_flag_detected=False,
        priority_ceiling=Priority.P2,
    )

    assert result.score_total == 35
    assert result.priority_final == Priority.P2
    assert result.components["location_category"] == 15
