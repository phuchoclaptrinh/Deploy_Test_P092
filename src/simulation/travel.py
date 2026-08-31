"""Travel time between two floors of one tower.

    travel_minutes = base + abs(current_floor - target_floor) * per_floor

One lift, one building, no horizontal distance: the model is deliberately the
simplest thing that makes sequencing matter at all. Its job is to punish a
schedule that sends the same technician from floor 2 to floor 29 and back, and
it does that with two numbers a coordinator can change on screen and reason
about afterwards.

This is the piece production's scheduler does not have. `src.dispatch.scheduler`
knows durations and deadlines but has no notion of where anyone is standing, so
the cost of a badly ordered day is invisible to it. Rather than teach production
about floors to serve a screen that writes nothing, the simulator carries its
own travel model here.

`base` is charged even for a job on the technician's current floor: walking to
the unit, finding the resident and opening the toolbox is not free, and a zero
there would make a floor-clustered schedule look impossibly good.
"""

from __future__ import annotations

DEFAULT_BASE_MINUTES = 3
DEFAULT_PER_FLOOR_MINUTES = 1


def travel_minutes(
    current_floor: int,
    target_floor: int,
    *,
    base_minutes: int = DEFAULT_BASE_MINUTES,
    per_floor_minutes: int = DEFAULT_PER_FLOOR_MINUTES,
) -> int:
    """Minutes to get from `current_floor` to `target_floor`.

    Symmetric: the model does not distinguish going up from coming down, and
    inventing a difference would be a guess dressed up as a measurement.
    """
    return base_minutes + abs(current_floor - target_floor) * per_floor_minutes


__all__ = ["DEFAULT_BASE_MINUTES", "DEFAULT_PER_FLOOR_MINUTES", "travel_minutes"]
