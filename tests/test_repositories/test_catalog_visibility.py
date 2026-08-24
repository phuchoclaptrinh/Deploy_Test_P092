from src.database.models.building import Building
from src.database.models.floor import Floor
from src.database.models.location import Location
from src.database.models.location_type import LocationType
from src.database.models.unit import Unit
from src.repositories.catalog_repository import CatalogRepository


def test_resident_location_catalog_hides_other_units(db_session):
    building = Building(code="A", name="Tòa A")
    floor = Floor(building=building, floor_code="10", display_name="Tầng 10", adjacency_index=10)
    own_unit = Unit(building=building, floor=floor, unit_code="A-1001")
    other_unit = Unit(building=building, floor=floor, unit_code="A-1002")
    common_type = LocationType(code="CORRIDOR", display_name="Hành lang")
    private_type = LocationType(code="INSIDE_UNIT", display_name="Trong căn hộ")
    common = Location(
        building=building,
        floor=floor,
        location_type=common_type,
        unit=None,
        label="Hành lang tầng 10",
    )
    own = Location(
        building=building,
        floor=floor,
        location_type=private_type,
        unit=own_unit,
        label="Trong căn A-1001",
    )
    foreign = Location(
        building=building,
        floor=floor,
        location_type=private_type,
        unit=other_unit,
        label="Trong căn A-1002",
    )
    db_session.add_all([building, floor, own_unit, other_unit, common_type, private_type, common, own, foreign])
    db_session.commit()

    rows = CatalogRepository(db_session).list_locations(
        building_id=building.id,
        resident_unit_id=own_unit.id,
    )

    assert {row.id for row in rows} == {common.id, own.id}
    assert foreign.id not in {row.id for row in rows}


def test_location_catalog_orders_floors_by_adjacency_index(db_session):
    building = Building(code="A", name="Tòa A")
    location_type = LocationType(code="CORRIDOR", display_name="Hành lang")
    floor_10 = Floor(building=building, floor_code="10", display_name="Tầng 10", adjacency_index=10)
    floor_2 = Floor(building=building, floor_code="2", display_name="Tầng 2", adjacency_index=2)
    floor_1 = Floor(building=building, floor_code="1", display_name="Tầng 1", adjacency_index=1)
    db_session.add_all(
        [
            building,
            location_type,
            Location(building=building, floor=floor_10, location_type=location_type, label="Hành lang - Tầng 10"),
            Location(building=building, floor=floor_2, location_type=location_type, label="Hành lang - Tầng 2"),
            Location(building=building, floor=floor_1, location_type=location_type, label="Hành lang - Tầng 1"),
        ]
    )
    db_session.commit()

    rows = CatalogRepository(db_session).list_locations()

    assert [row.floor.floor_code for row in rows] == ["1", "2", "10"]
