"""expand the resident location catalog and version the location score matrix

Revision ID: 3f4a5b6c7d8e
Revises: 2f3a4b5c6d7e
Create Date: 2026-08-24 18:30:00.000000

The original catalog only supplied corridor and fire-exit rows broadly enough
for a resident dropdown.  This revision adds the building-common locations
approved for the product, materializes them on the applicable floors, and
activates a new immutable scoring configuration.  Existing tickets retain the
rule version pinned on their analysis run.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3f4a5b6c7d8e"
down_revision: str | Sequence[str] | None = "2f3a4b5c6d7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULE_VERSION = "self-dev-v4.1.0-location-matrix"

SCORING_CONFIG = {
    "category_base": {
        "WATER_LEAK": 10,
        "ELECTRICAL_SHORT": 50,
        "ELEVATOR": 35,
        "SERIOUS_SECURITY_DISORDER": 40,
        "LOCK_DOOR": 25,
        "HVAC": 20,
        "LOCAL_POWER_OUTAGE": 25,
        "STRUCTURAL_ISSUE": 20,
        "COMMON_LIGHT": 10,
        "ODOR_HYGIENE": 10,
        "NOISE_NEIGHBOR": 10,
    },
    "location_bonus": {
        "LOCK_DOOR": {"MAIN_DOOR": 30, "SECURITY_DOOR": 30, "ENTRANCE_GATE": 25},
        "COMMON_LIGHT": {
            "FIRE_EXIT": 25,
            "ELEVATOR_LOBBY": 10,
            "LOBBY_RECEPTION": 10,
            "BASEMENT_PARKING": 10,
            "ENTRANCE_GATE": 10,
            "DRIVEWAY": 10,
        },
        "ELEVATOR": {"ELEVATOR": 15, "ELEVATOR_LOBBY": 15},
        "ELECTRICAL_SHORT": {
            "ELECTRICAL_ROOM": 20,
            "TECHNICAL_ROOM": 20,
            "PUMP_ROOM": 20,
            "BASEMENT_PARKING": 10,
            "ELEVATOR": 10,
            "ELEVATOR_LOBBY": 10,
        },
        "LOCAL_POWER_OUTAGE": {
            "ELECTRICAL_ROOM": 15,
            "TECHNICAL_ROOM": 15,
            "PUMP_ROOM": 15,
            "BASEMENT_PARKING": 10,
            "ELEVATOR": 10,
            "ELEVATOR_LOBBY": 10,
        },
        "WATER_LEAK": {
            "PUMP_ROOM": 15,
            "ELECTRICAL_ROOM": 15,
            "BASEMENT_PARKING": 10,
            "TECHNICAL_ROOM": 10,
        },
        "STRUCTURAL_ISSUE": {
            "ROOFTOP": 15,
            "EXTERIOR_FACADE": 15,
            "BASEMENT_PARKING": 10,
            "TECHNICAL_ROOM": 10,
        },
        "HVAC": {"TECHNICAL_ROOM": 10, "COMMUNITY_ROOM": 10},
        "SERIOUS_SECURITY_DISORDER": {
            "ENTRANCE_GATE": 10,
            "SECURITY_BOOTH": 10,
            "BASEMENT_PARKING": 10,
            "PLAYGROUND": 10,
        },
    },
    "density": {"1": 0, "2-3": 15, "4+": 30, "categories": ["WATER_LEAK", "ELECTRICAL_SHORT"]},
    "severity": {"LOW": 0, "MEDIUM": 10, "HIGH": 20},
    "thresholds": {"P1": "<30", "P2": "30-59", "P3": ">=60"},
    "sla_minutes": {"P3": 5, "P2": 180, "P1": 4320},
}


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO location_types (code, display_name, is_active) VALUES
              ('ELEVATOR', 'Thang máy', true),
              ('ELEVATOR_LOBBY', 'Sảnh thang máy', true),
              ('LOBBY_RECEPTION', 'Sảnh chính / Lễ tân', true),
              ('ENTRANCE_GATE', 'Cổng / Lối ra vào', true),
              ('DRIVEWAY', 'Đường nội bộ / Lối xe', true),
              ('COURTYARD', 'Sân chung', true),
              ('PLAYGROUND', 'Khu vui chơi', true),
              ('ROOFTOP', 'Sân thượng / Mái', true),
              ('EXTERIOR_FACADE', 'Mặt ngoài tòa nhà', true),
              ('TECHNICAL_ROOM', 'Phòng kỹ thuật', true),
              ('ELECTRICAL_ROOM', 'Phòng điện / Tủ điện chung', true),
              ('PUMP_ROOM', 'Phòng bơm / Bể nước', true),
              ('TRASH_ROOM', 'Điểm tập kết rác', true),
              ('COMMUNITY_ROOM', 'Phòng sinh hoạt cộng đồng', true),
              ('SECURITY_BOOTH', 'Chốt bảo vệ', true)
            ON CONFLICT (code) DO UPDATE
              SET display_name = EXCLUDED.display_name, is_active = true
            """
        )
    )

    # Elevator and refuse locations are meaningful on every non-basement floor.
    op.execute(
        sa.text(
            """
            WITH candidates AS (
              SELECT f.building_id, f.id AS floor_id, lt.id AS location_type_id,
                     lt.display_name || ' - ' || f.display_name AS label
              FROM floors f
              JOIN location_types lt ON lt.code IN ('ELEVATOR', 'ELEVATOR_LOBBY', 'TRASH_ROOM')
              WHERE upper(f.floor_code) !~ '^B[0-9]+$'
            )
            INSERT INTO locations (building_id, floor_id, location_type_id, unit_id, label, is_active)
            SELECT c.building_id, c.floor_id, c.location_type_id, NULL, c.label, true
            FROM candidates c
            WHERE NOT EXISTS (
              SELECT 1 FROM locations l
              WHERE l.building_id = c.building_id
                AND l.floor_id = c.floor_id
                AND l.location_type_id = c.location_type_id
                AND l.unit_id IS NULL
                AND l.label = c.label
            )
            """
        )
    )

    # Ground-level shared spaces use the lowest non-basement floor per building.
    op.execute(
        sa.text(
            """
            WITH ground_floor AS (
              SELECT DISTINCT ON (f.building_id) f.building_id, f.id AS floor_id, f.display_name
              FROM floors f
              WHERE upper(f.floor_code) !~ '^B[0-9]+$'
              ORDER BY f.building_id, f.adjacency_index
            ), candidates AS (
              SELECT g.building_id, g.floor_id, lt.id AS location_type_id,
                     lt.display_name || ' - ' || g.display_name AS label
              FROM ground_floor g
              JOIN location_types lt ON lt.code IN (
                'LOBBY_RECEPTION', 'ENTRANCE_GATE', 'DRIVEWAY', 'COURTYARD',
                'PLAYGROUND', 'EXTERIOR_FACADE', 'COMMUNITY_ROOM', 'SECURITY_BOOTH'
              )
            )
            INSERT INTO locations (building_id, floor_id, location_type_id, unit_id, label, is_active)
            SELECT c.building_id, c.floor_id, c.location_type_id, NULL, c.label, true
            FROM candidates c
            WHERE NOT EXISTS (
              SELECT 1 FROM locations l
              WHERE l.building_id = c.building_id
                AND l.floor_id = c.floor_id
                AND l.location_type_id = c.location_type_id
                AND l.unit_id IS NULL
                AND l.label = c.label
            )
            """
        )
    )

    # The highest non-basement floor represents the building rooftop; technical
    # rooms are modeled on each basement because their actual placement differs.
    op.execute(
        sa.text(
            """
            WITH top_floor AS (
              SELECT DISTINCT ON (f.building_id) f.building_id, f.id AS floor_id, f.display_name
              FROM floors f
              WHERE upper(f.floor_code) !~ '^B[0-9]+$'
              ORDER BY f.building_id, f.adjacency_index DESC
            ), candidates AS (
              SELECT t.building_id, t.floor_id, lt.id AS location_type_id,
                     lt.display_name || ' - ' || t.display_name AS label
              FROM top_floor t JOIN location_types lt ON lt.code = 'ROOFTOP'
            )
            INSERT INTO locations (building_id, floor_id, location_type_id, unit_id, label, is_active)
            SELECT c.building_id, c.floor_id, c.location_type_id, NULL, c.label, true
            FROM candidates c
            WHERE NOT EXISTS (
              SELECT 1 FROM locations l
              WHERE l.building_id = c.building_id AND l.floor_id = c.floor_id
                AND l.location_type_id = c.location_type_id AND l.unit_id IS NULL AND l.label = c.label
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH candidates AS (
              SELECT f.building_id, f.id AS floor_id, lt.id AS location_type_id,
                     lt.display_name || ' - ' || f.display_name AS label
              FROM floors f
              JOIN location_types lt ON lt.code IN ('TECHNICAL_ROOM', 'ELECTRICAL_ROOM', 'PUMP_ROOM')
              WHERE upper(f.floor_code) ~ '^B[0-9]+$'
            )
            INSERT INTO locations (building_id, floor_id, location_type_id, unit_id, label, is_active)
            SELECT c.building_id, c.floor_id, c.location_type_id, NULL, c.label, true
            FROM candidates c
            WHERE NOT EXISTS (
              SELECT 1 FROM locations l
              WHERE l.building_id = c.building_id AND l.floor_id = c.floor_id
                AND l.location_type_id = c.location_type_id AND l.unit_id IS NULL AND l.label = c.label
            )
            """
        )
    )

    op.execute(sa.text("UPDATE scoring_rule_versions SET is_active = false WHERE is_active"))
    op.execute(
        sa.text(
            """
            INSERT INTO scoring_rule_versions (version, config, is_active)
            VALUES (:version, CAST(:config AS jsonb), true)
            """
        ).bindparams(version=RULE_VERSION, config=json.dumps(SCORING_CONFIG, ensure_ascii=False))
    )


def downgrade() -> None:
    op.execute(sa.text("UPDATE scoring_rule_versions SET is_active = false WHERE version = :version").bindparams(version=RULE_VERSION))
    op.execute(
        sa.text(
            """
            UPDATE scoring_rule_versions SET is_active = true
            WHERE version = 'self-dev-v2.0.0'
              AND NOT EXISTS (SELECT 1 FROM scoring_rule_versions WHERE is_active)
            """
        )
    )
    op.execute(sa.text("DELETE FROM scoring_rule_versions WHERE version = :version").bindparams(version=RULE_VERSION))

    # Location rows are safely removable because this revision created no ticket
    # references.  Keep the types themselves if a later migration added rows.
    op.execute(
        sa.text(
            """
            DELETE FROM locations
            WHERE location_type_id IN (
              SELECT id FROM location_types WHERE code IN (
                'ELEVATOR', 'ELEVATOR_LOBBY', 'LOBBY_RECEPTION', 'ENTRANCE_GATE', 'DRIVEWAY',
                'COURTYARD', 'PLAYGROUND', 'ROOFTOP', 'EXTERIOR_FACADE', 'TECHNICAL_ROOM',
                'ELECTRICAL_ROOM', 'PUMP_ROOM', 'TRASH_ROOM', 'COMMUNITY_ROOM', 'SECURITY_BOOTH'
              )
            )
              AND NOT EXISTS (SELECT 1 FROM tickets t WHERE t.location_id = locations.id)
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM location_types lt
            WHERE lt.code IN (
              'ELEVATOR', 'ELEVATOR_LOBBY', 'LOBBY_RECEPTION', 'ENTRANCE_GATE', 'DRIVEWAY',
              'COURTYARD', 'PLAYGROUND', 'ROOFTOP', 'EXTERIOR_FACADE', 'TECHNICAL_ROOM',
              'ELECTRICAL_ROOM', 'PUMP_ROOM', 'TRASH_ROOM', 'COMMUNITY_ROOM', 'SECURITY_BOOTH'
            )
              AND NOT EXISTS (SELECT 1 FROM locations l WHERE l.location_type_id = lt.id)
            """
        )
    )
