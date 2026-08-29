"""Initial category P80 configuration used by the scheduler."""

from datetime import timedelta

from src.dispatch.durations import DEFAULT_P80, P80_BY_CATEGORY_CODE, p80_for_code
from src.models.enums import Category


def test_initial_category_p80_configuration():
    assert P80_BY_CATEGORY_CODE == {
        Category.WATER.value: timedelta(hours=4),
        Category.WALL_DAMP.value: timedelta(hours=6),
        Category.ELEVATOR.value: timedelta(hours=4, minutes=30),
        Category.POWER_OUTAGE.value: timedelta(hours=3),
        Category.SECURITY_SAFETY.value: timedelta(hours=1),
        Category.NOISE.value: timedelta(hours=3),
        Category.LOCK_DOOR.value: timedelta(hours=2),
        Category.HVAC.value: timedelta(hours=5),
        Category.ODOR_HYGIENE.value: timedelta(hours=3),
        Category.INTERNET_TV.value: timedelta(hours=3),
        Category.COMMON_AREA_DAMAGE.value: timedelta(hours=2),
    }


def test_unknown_category_uses_the_longest_initial_p80():
    assert DEFAULT_P80 == timedelta(hours=6)
    assert p80_for_code("UNKNOWN_CATEGORY") == DEFAULT_P80
