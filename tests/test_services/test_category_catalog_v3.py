from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.database.models.building import Building
from src.database.models.category import CategoryCatalog
from src.database.models.floor import Floor
from src.database.models.location import Location
from src.database.models.location_type import LocationType
from src.database.models.ticket import Ticket
from src.models.api.errors import DomainError
from src.models.enums import ClassificationStatus, Priority, Severity, TicketStatus
from src.repositories.catalog_repository import CatalogRepository
from src.services.coordinator_support import CoordinatorScoringSupport
from src.services.scoring_service import ScoringService


def test_category_catalog_accepts_dynamic_code_and_rejects_duplicate(db_session):
    repo = CatalogRepository(db_session)
    created = repo.create_category("plumbing_custom", "Plumbing custom", 12, None)
    db_session.commit()

    assert created.code == "PLUMBING_CUSTOM"
    with pytest.raises(DomainError) as exc:
        repo.create_category("PLUMBING_CUSTOM", "Duplicate", 12, None)
    assert exc.value.status_code == 409


def test_deactivated_category_is_excluded_but_historical_ticket_stays_readable(db_session):
    repo = CatalogRepository(db_session)
    category = repo.create_category("CUSTOM_HISTORY", "Custom history", 10, None)
    building = Building(code="A", name="Tower A")
    floor = Floor(building=building, floor_code="1", display_name="Floor 1", adjacency_index=1)
    location_type = LocationType(code="CORRIDOR", display_name="Corridor")
    location = Location(building=building, floor=floor, location_type=location_type, label="Corridor")
    ticket = Ticket(
        reporter_user_id=uuid4(),
        source_unit_id=uuid4(),
        location=location,
        description="Historical",
        status=TicketStatus.NEW,
        classification_status=ClassificationStatus.RESOLVED,
        category=category,
    )
    db_session.add_all([building, floor, location_type, location, ticket])
    db_session.commit()

    category.is_active = False
    db_session.commit()

    assert category not in repo.list_categories()
    assert repo.get_category_any_status(category.id).tickets[0].id == ticket.id


def test_unknown_category_scoring_configuration_fails_safely(db_session):
    category = CategoryCatalog(code="CUSTOM_UNKNOWN", display_name="Unknown", is_active=True)
    ticket = Ticket(
        reporter_user_id=uuid4(),
        source_unit_id=uuid4(),
        location_id=uuid4(),
        description="Needs scoring",
        status=TicketStatus.NEW,
        classification_status=ClassificationStatus.RESOLVED,
        severity=Severity.MEDIUM,
        red_flag_detected=False,
    )

    with pytest.raises(DomainError) as exc:
        CoordinatorScoringSupport(db_session, ScoringService()).apply_scoring(ticket, category)

    assert exc.value.code == "CATEGORY_REQUIRED"
    assert exc.value.status_code == 409


def test_dynamic_scoring_uses_catalog_base_score_and_ceiling(db_session):
    category = CategoryCatalog(
        code="CUSTOM_DYNAMIC",
        display_name="Dynamic",
        base_score=65,
        priority_ceiling=Priority.P2,
        is_active=True,
    )
    ticket = Ticket(
        reporter_user_id=uuid4(),
        source_unit_id=uuid4(),
        location_id=uuid4(),
        description="Needs scoring",
        status=TicketStatus.NEW,
        classification_status=ClassificationStatus.RESOLVED,
        severity=Severity.LOW,
        red_flag_detected=False,
        sla_started_at=datetime.now(UTC),
    )

    CoordinatorScoringSupport(db_session, ScoringService()).apply_scoring(ticket, category)

    assert ticket.score_total == 65
    assert ticket.priority.value == "P2"


def test_dynamic_scoring_red_flag_forces_p3_and_bypasses_ceiling(db_session):
    result = ScoringService().calculate_dynamic(
        category_code="CUSTOM_DYNAMIC",
        base_score=1,
        severity=Severity.LOW,
        location_type_code=None,
        density_count=1,
        red_flag_detected=True,
        priority_ceiling=Priority.P1,
    )

    assert result.priority_final.value == "P3"
    assert result.score_total is None


def test_density_bonus_is_limited_to_canonical_density_codes():
    scoring = ScoringService()

    assert scoring.density_score_by_code("WATER_LEAK", 4) == 30
    assert scoring.density_score_by_code("ELECTRICAL_SHORT", 4) == 30
    assert scoring.density_score_by_code("CUSTOM_DYNAMIC", 4) == 0
