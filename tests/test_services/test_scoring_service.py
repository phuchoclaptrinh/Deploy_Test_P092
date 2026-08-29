from src.models.enums import Category, Priority, Severity
from src.services.scoring_service import ScoringService


def test_common_area_damage_at_a_fire_exit_is_p2():
    # 10 base + 25 for a fire exit + 10 for MEDIUM. The fire-exit bonus is the
    # largest location bonus in the catalog, and this is why.
    result = ScoringService().calculate(
        category=Category.COMMON_AREA_DAMAGE,
        severity=Severity.MEDIUM,
        location_type_code="FIRE_EXIT",
        density_count=1,
        red_flag_detected=False,
    )
    assert result.score_total == 45
    assert result.priority_final == Priority.P2


def test_a_ceiling_caps_a_score_that_would_otherwise_be_p3():
    # 40 base + 10 at the entrance gate + 20 for HIGH = 70, which is P3 on its
    # own. The ceiling is what decides the final priority, not the score.
    result = ScoringService().calculate(
        category=Category.SECURITY_SAFETY,
        severity=Severity.HIGH,
        location_type_code="ENTRANCE_GATE",
        density_count=1,
        red_flag_detected=False,
        priority_ceiling=Priority.P2,
    )
    assert result.score_total == 70
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


def test_density_only_affects_water():
    # Density is what "one leak spreading through the stack" scores, so it is
    # deliberately limited to the one category that spreads that way.
    service = ScoringService()
    assert service.density_score(Category.WATER, 3) == 15
    assert service.density_score(Category.WATER, 4) == 30
    assert service.density_score(Category.POWER_OUTAGE, 4) == 0
    assert service.density_score(Category.HVAC, 9) == 0


def test_runtime_scoring_config_overrides_bootstrap_values():
    config = {
        "category_base": {category.value: 10 for category in Category},
        "location_bonus": {},
        "density": {
            "categories": [Category.WATER.value, Category.POWER_OUTAGE.value],
            "2-3": 15,
            "4+": 30,
        },
        "severity": {"LOW": 0, "MEDIUM": 10, "HIGH": 20},
        "thresholds": {"P1": "<30", "P2": "30-59", "P3": ">=60"},
        "sla_minutes": {"P1": 4320, "P2": 180, "P3": 5},
    }
    config["category_base"][Category.WATER.value] = 30
    service = ScoringService(config)

    result = service.calculate(
        category=Category.WATER,
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
