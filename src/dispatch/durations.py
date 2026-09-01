"""Internal P80 handling durations, by category (§5).

These are **capacity estimates, not promises**. §4 is explicit that the number
a resident sees is the expected *start*, never a completion time, and §5 calls
these "internal scheduling estimates, not resident-facing completion SLAs".
Nothing in this module may be rendered into a resident-facing payload; the
resident serializers do not import it.

The table is keyed by the machine `code` on `categories`, not by the catalog
row's display name and not by its UUID. Display names are editable by a
coordinator and UUIDs differ per deployment, so either one would make the
configured durations drift away from the categories they describe after the
first rename or the first fresh database.

`DEFAULT_P80` covers a category added to the catalog after this table was
written. It is the **longest** duration in the table rather than the mean,
because the failure modes are not symmetric: over-estimating a new category
makes the scheduler book conservatively and flag more tickets AT_RISK, while
under-estimating it silently overfills a technician's day and breaks the
commitments already made to other tickets.
"""

from __future__ import annotations

from datetime import timedelta

from src.models.enums import Category

#: Initial operating P80s approved on 2026-08-27. Hours, converted once here
#: so no caller re-does the arithmetic. These are capacity estimates, not
#: resident-facing completion promises.
P80_BY_CATEGORY_CODE: dict[str, timedelta] = {
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

#: The longest entry above. Computed rather than written out, so adding a
#: longer category to the table cannot leave this stale.
DEFAULT_P80: timedelta = max(P80_BY_CATEGORY_CODE.values())


def p80_for_code(category_code: str | None) -> timedelta:
    """The P80 duration for one category code, or the conservative default."""
    if not category_code:
        return DEFAULT_P80
    return P80_BY_CATEGORY_CODE.get(category_code.upper(), DEFAULT_P80)


def p80_for_unit(category_codes: list[str]) -> timedelta:
    """The P80 duration for one *work unit*.

    A grouped work unit is one technician doing every member ticket in sequence
    on the same visit, so its cost is the sum rather than the maximum. Using the
    maximum would let a five-member cluster be booked as though it were a single
    ticket, which is the specific way a board full of groups overfills a day.

    Automatic Assignment never reaches this function with more than one code:
    §2 skips grouping outright, so only the Visual Assignment board builds
    multi-ticket units.
    """
    if not category_codes:
        return DEFAULT_P80
    total = timedelta()
    for code in category_codes:
        total += p80_for_code(code)
    return total


__all__ = ["DEFAULT_P80", "P80_BY_CATEGORY_CODE", "p80_for_code", "p80_for_unit"]
