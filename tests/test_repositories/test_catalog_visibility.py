from src.database.models.floor import Floor
from src.database.models.location import Location
from src.database.models.location_type import LocationType
from src.database.models.unit import Unit
from src.repositories.catalog_repository import CatalogRepository


def test_private_locations_stay_tied_to_the_apartment_that_owns_them(db_session):
    floor = Floor(floor_code="10", display_name="Tầng 10", adjacency_index=10)
    own_unit = Unit(floor=floor, unit_code="A-1001")
    other_unit = Unit(floor=floor, unit_code="A-1002")
    common_type = LocationType(code="CORRIDOR", display_name="Hành lang")
    private_type = LocationType(code="INSIDE_UNIT", display_name="Trong căn hộ")
    common = Location(
        floor=floor,
        location_type=common_type,
        unit=None,
        label="Hành lang tầng 10",
    )
    own = Location(
        floor=floor,
        location_type=private_type,
        unit=own_unit,
        label="Trong căn A-1001",
    )
    foreign = Location(
        floor=floor,
        location_type=private_type,
        unit=other_unit,
        label="Trong căn A-1002",
    )
    db_session.add_all([floor, own_unit, other_unit, common_type, private_type, common, own, foreign])
    db_session.commit()

    rows = CatalogRepository(db_session).list_locations()

    # The repository returns the whole active catalog; which apartment a
    # resident may pick from is decided above it, so the assertion here is that
    # every private location is still tied to the unit that owns it.
    by_id = {row.id: row for row in rows}
    assert set(by_id) == {common.id, own.id, foreign.id}
    assert by_id[common.id].unit_id is None
    assert by_id[own.id].unit_id == own_unit.id
    assert by_id[foreign.id].unit_id == other_unit.id


def test_location_catalog_orders_floors_by_adjacency_index(db_session):
    location_type = LocationType(code="CORRIDOR", display_name="Hành lang")
    floor_10 = Floor(floor_code="10", display_name="Tầng 10", adjacency_index=10)
    floor_2 = Floor(floor_code="2", display_name="Tầng 2", adjacency_index=2)
    floor_1 = Floor(floor_code="1", display_name="Tầng 1", adjacency_index=1)
    db_session.add_all(
        [
            location_type,
            Location(floor=floor_10, location_type=location_type, label="Hành lang - Tầng 10"),
            Location(floor=floor_2, location_type=location_type, label="Hành lang - Tầng 2"),
            Location(floor=floor_1, location_type=location_type, label="Hành lang - Tầng 1"),
        ]
    )
    db_session.commit()

    rows = CatalogRepository(db_session).list_locations()

    assert [row.floor.floor_code for row in rows] == ["1", "2", "10"]
