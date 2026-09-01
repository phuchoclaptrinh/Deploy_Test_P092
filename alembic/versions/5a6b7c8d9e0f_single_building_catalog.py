"""Replace the multi-building catalog with one 13-floor apartment catalog.

Revision ID: 5a6b7c8d9e0f
Revises: 4a5b6c7d8e9f
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5a6b7c8d9e0f"
down_revision: str | Sequence[str] | None = "4a5b6c7d8e9f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tickets (and their dependent audit/analysis rows) are deliberately removed
    # by the rollout procedure before this migration. The remaining incident
    # cases are old empty shells and cannot outlive the catalog they describe.
    op.execute("DELETE FROM incident_cases")
    op.execute("DELETE FROM locations")

    # Add the 39 new homes while the legacy building FK still exists, then move
    # resident bindings that use the retained x01..x03 homes to their new codes.
    op.execute(
        """
        INSERT INTO units (id, building_id, floor_id, unit_code, status)
        SELECT gen_random_uuid(), f.building_id, f.id, f.floor_code || lpad(slot::text, 2, '0'), 'ACTIVE'
        FROM floors f
        CROSS JOIN generate_series(1, 3) AS slot
        """
    )
    op.execute(
        """
        UPDATE resident_profiles rp
        SET unit_id = replacement.id
        FROM units old_unit
        JOIN floors old_floor ON old_floor.id = old_unit.floor_id
        JOIN units replacement
          ON replacement.unit_code = old_floor.floor_code || right(old_unit.unit_code, 2)
        WHERE rp.unit_id = old_unit.id
          AND right(old_unit.unit_code, 2) IN ('01', '02', '03')
          AND replacement.unit_code !~ '^A-'
        """
    )
    op.execute("DELETE FROM units WHERE unit_code LIKE 'A-%'")

    # The application now has exactly one building, so building is no longer a
    # domain entity. Floors, units, locations and cases carry no building key.
    op.drop_constraint("uq_floors_building_code", "floors", type_="unique")
    op.drop_constraint("floors_building_id_fkey", "floors", type_="foreignkey")
    op.drop_column("floors", "building_id")
    op.create_unique_constraint("uq_floors_code", "floors", ["floor_code"])

    op.drop_constraint("uq_units_building_code", "units", type_="unique")
    op.drop_constraint("fk_units_building_v2", "units", type_="foreignkey")
    op.drop_column("units", "building_id")
    op.create_unique_constraint("uq_units_code", "units", ["unit_code"])

    op.drop_constraint("locations_building_id_fkey", "locations", type_="foreignkey")
    op.drop_column("locations", "building_id")

    op.drop_constraint("incident_cases_building_id_fkey", "incident_cases", type_="foreignkey")
    op.drop_column("incident_cases", "building_id")
    op.drop_table("buildings")

    # Replace the previous broad catalog with the fixed resident-facing list.
    op.execute("DELETE FROM location_types")
    op.execute(
        """
        INSERT INTO location_types (id, code, display_name, is_active) VALUES
          (gen_random_uuid(), 'LIVING_ROOM', 'Phòng khách', true),
          (gen_random_uuid(), 'BEDROOM', 'Phòng ngủ', true),
          (gen_random_uuid(), 'KITCHEN', 'Bếp', true),
          (gen_random_uuid(), 'BATHROOM', 'Phòng tắm / WC', true),
          (gen_random_uuid(), 'BALCONY', 'Ban công', true),
          (gen_random_uuid(), 'CORRIDOR', 'Hành lang', true),
          (gen_random_uuid(), 'ELEVATOR_LOBBY', 'Sảnh thang máy', true),
          (gen_random_uuid(), 'ELEVATOR', 'Thang máy', true),
          (gen_random_uuid(), 'FIRE_EXIT', 'Cầu thang / lối thoát hiểm', true),
          (gen_random_uuid(), 'TRASH_ROOM', 'Điểm tập kết rác', true),
          (gen_random_uuid(), 'LOBBY_RECEPTION', 'Sảnh chính / Lễ tân', true),
          (gen_random_uuid(), 'SECURITY_BOOTH', 'Chốt bảo vệ', true),
          (gen_random_uuid(), 'ENTRANCE_GATE', 'Cổng / lối ra vào', true),
          (gen_random_uuid(), 'DRIVEWAY', 'Đường nội bộ / lối xe', true),
          (gen_random_uuid(), 'COURTYARD', 'Sân chung', true),
          (gen_random_uuid(), 'PLAYGROUND', 'Khu vui chơi', true),
          (gen_random_uuid(), 'COMMUNITY_ROOM', 'Phòng sinh hoạt cộng đồng', true),
          (gen_random_uuid(), 'EXTERIOR_FACADE', 'Mặt ngoài tòa nhà', true),
          (gen_random_uuid(), 'ROOFTOP', 'Sân thượng / mái', true)
        """
    )
    op.execute(
        """
        INSERT INTO locations (id, floor_id, location_type_id, unit_id, label, is_active)
        SELECT gen_random_uuid(), u.floor_id, lt.id, u.id, lt.display_name || ' · Căn ' || u.unit_code, true
        FROM units u
        JOIN location_types lt ON lt.code IN ('LIVING_ROOM', 'BEDROOM', 'KITCHEN', 'BATHROOM', 'BALCONY')
        """
    )
    op.execute(
        """
        INSERT INTO locations (id, floor_id, location_type_id, unit_id, label, is_active)
        SELECT gen_random_uuid(), f.id, lt.id, NULL, lt.display_name || ' · ' || f.display_name, true
        FROM floors f
        JOIN location_types lt ON lt.code IN ('CORRIDOR', 'ELEVATOR_LOBBY', 'ELEVATOR', 'FIRE_EXIT', 'TRASH_ROOM')
        """
    )
    op.execute(
        """
        INSERT INTO locations (id, floor_id, location_type_id, unit_id, label, is_active)
        SELECT gen_random_uuid(), f.id, lt.id, NULL, lt.display_name || ' · ' || f.display_name, true
        FROM floors f
        JOIN location_types lt ON lt.code IN (
          'LOBBY_RECEPTION', 'SECURITY_BOOTH', 'ENTRANCE_GATE', 'DRIVEWAY', 'COURTYARD',
          'PLAYGROUND', 'COMMUNITY_ROOM', 'EXTERIOR_FACADE'
        )
        WHERE f.floor_code = '1'
        """
    )
    op.execute(
        """
        INSERT INTO locations (id, floor_id, location_type_id, unit_id, label, is_active)
        SELECT gen_random_uuid(), f.id, lt.id, NULL, lt.display_name || ' · ' || f.display_name, true
        FROM floors f
        JOIN location_types lt ON lt.code = 'ROOFTOP'
        WHERE f.floor_code = '13'
        """
    )

    # Keep the existing category ids (technician skills may reference them), but
    # make the live catalog exactly match the approved names and codes.
    op.execute("DELETE FROM categories WHERE is_active = false")
    op.execute(
        """
        UPDATE categories SET code = 'WATER', display_name = 'Nước', base_score = 10, priority_ceiling = NULL, is_active = true WHERE code = 'WATER_LEAK';
        UPDATE categories SET code = 'WALL_DAMP', display_name = 'Thấm tường', base_score = 20, priority_ceiling = 'P2', is_active = true WHERE code = 'STRUCTURAL_ISSUE';
        UPDATE categories SET code = 'ELEVATOR', display_name = 'Thang máy', base_score = 35, priority_ceiling = NULL, is_active = true WHERE code = 'ELEVATOR';
        UPDATE categories SET code = 'POWER_OUTAGE', display_name = 'Mất điện', base_score = 25, priority_ceiling = 'P2', is_active = true WHERE code = 'LOCAL_POWER_OUTAGE';
        UPDATE categories SET code = 'SECURITY_SAFETY', display_name = 'An ninh / An toàn', base_score = 40, priority_ceiling = NULL, is_active = true WHERE code = 'SERIOUS_SECURITY_DISORDER';
        UPDATE categories SET code = 'NOISE', display_name = 'Ồn ào', base_score = 10, priority_ceiling = 'P1', is_active = true WHERE code = 'NOISE_NEIGHBOR';
        UPDATE categories SET code = 'LOCK_DOOR', display_name = 'Khóa / cửa', base_score = 25, priority_ceiling = 'P2', is_active = true WHERE code = 'LOCK_DOOR';
        UPDATE categories SET code = 'HVAC', display_name = 'Điều hòa', base_score = 20, priority_ceiling = 'P2', is_active = true WHERE code = 'HVAC';
        UPDATE categories SET code = 'ODOR_HYGIENE', display_name = 'Mùi / vệ sinh', base_score = 10, priority_ceiling = 'P1', is_active = true WHERE code = 'ODOR_HYGIENE';
        UPDATE categories SET code = 'INTERNET_TV', display_name = 'Internet / truyền hình', base_score = 10, priority_ceiling = 'P1', is_active = true WHERE code = 'ELECTRICAL_SHORT';
        UPDATE categories SET code = 'COMMON_AREA_DAMAGE', display_name = 'Hư hỏng khu vực chung', base_score = 10, priority_ceiling = 'P2', is_active = true WHERE code = 'COMMON_LIGHT';
        """
    )


def downgrade() -> None:
    raise NotImplementedError("This catalog reset intentionally has no safe downgrade.")
