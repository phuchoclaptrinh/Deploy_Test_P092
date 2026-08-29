"""Align the active scoring rules with the fixed resident category catalog.

Revision ID: 5b6c7d8e9f0a
Revises: 5a6b7c8d9e0f
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5b6c7d8e9f0a"
down_revision: str | Sequence[str] | None = "5a6b7c8d9e0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONFIG = {
    "category_base": {
        "WATER": 10,
        "WALL_DAMP": 20,
        "ELEVATOR": 35,
        "POWER_OUTAGE": 25,
        "SECURITY_SAFETY": 40,
        "NOISE": 10,
        "LOCK_DOOR": 25,
        "HVAC": 20,
        "ODOR_HYGIENE": 10,
        "INTERNET_TV": 10,
        "COMMON_AREA_DAMAGE": 10,
    },
    "location_bonus": {
        "WALL_DAMP": {"ROOFTOP": 15, "EXTERIOR_FACADE": 15},
        "ELEVATOR": {"ELEVATOR": 15, "ELEVATOR_LOBBY": 15},
        "POWER_OUTAGE": {"ELEVATOR": 10, "ELEVATOR_LOBBY": 10},
        "SECURITY_SAFETY": {"ENTRANCE_GATE": 10, "SECURITY_BOOTH": 10, "PLAYGROUND": 10},
        "HVAC": {"COMMUNITY_ROOM": 10},
        "COMMON_AREA_DAMAGE": {
            "FIRE_EXIT": 25,
            "ELEVATOR_LOBBY": 10,
            "LOBBY_RECEPTION": 10,
            "ENTRANCE_GATE": 10,
            "DRIVEWAY": 10,
        },
    },
    "severity": {"LOW": 0, "MEDIUM": 10, "HIGH": 20},
    "density": {"categories": ["WATER"], "1": 0, "2-3": 15, "4+": 30},
    "thresholds": {"P1": "<30", "P2": "30-59", "P3": ">=60"},
    "sla_minutes": {"P1": 4320, "P2": 180, "P3": 5},
}


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE scoring_rule_versions
            SET version = 'self-dev-v5.0.0-single-building-catalog',
                config = CAST(:config AS jsonb)
            WHERE is_active = true
            """
        ),
        {"config": json.dumps(_CONFIG)},
    )


def downgrade() -> None:
    raise NotImplementedError("The previous category scoring configuration is intentionally retired.")
