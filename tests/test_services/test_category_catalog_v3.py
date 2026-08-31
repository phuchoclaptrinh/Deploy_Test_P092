"""The Category catalog, now that it takes no part in scoring.

Three of this file's tests are gone with the thing they tested: the base score,
the priority ceiling and the density bonus. Under `docs/risk_scoring_v2.md` a
category is a routing and reporting label and nothing else, so the interesting
question is no longer "does it score correctly" but "does anything still let a
category reach a priority". The last test here answers that one.
"""

from uuid import uuid4

import pytest

from src.database.models.category import CategoryCatalog
from src.database.models.floor import Floor
from src.database.models.location import Location
from src.database.models.location_type import LocationType
from src.database.models.ticket import Ticket
from src.models.api.errors import DomainError
from src.models.enums import ClassificationStatus, TicketStatus
from src.repositories.catalog_repository import CatalogRepository


def test_category_catalog_accepts_dynamic_code_and_rejects_duplicate(db_session):
    repo = CatalogRepository(db_session)
    created = repo.create_category("plumbing_custom", "Plumbing custom")
    db_session.commit()

    assert created.code == "PLUMBING_CUSTOM"
    with pytest.raises(DomainError) as exc:
        repo.create_category("PLUMBING_CUSTOM", "Duplicate")
    assert exc.value.status_code == 409


def test_deactivated_category_is_excluded_but_historical_ticket_stays_readable(db_session):
    repo = CatalogRepository(db_session)
    category = repo.create_category("CUSTOM_HISTORY", "Custom history")
    floor = Floor(floor_code="1", display_name="Floor 1", adjacency_index=1)
    location_type = LocationType(code="CORRIDOR", display_name="Corridor")
    location = Location(floor=floor, location_type=location_type, label="Corridor")
    ticket = Ticket(
        reporter_user_id=uuid4(),
        source_unit_id=uuid4(),
        location=location,
        description="Historical",
        status=TicketStatus.NEW,
        classification_status=ClassificationStatus.RESOLVED,
        category=category,
    )
    db_session.add_all([floor, location_type, location, ticket])
    db_session.commit()

    category.is_active = False
    db_session.commit()

    assert category not in repo.list_categories()
    assert repo.get_category_any_status(category.id).tickets[0].id == ticket.id


def test_a_category_can_be_created_without_any_scoring_configuration(db_session):
    """The rule that blocked this is gone, and its absence is the point.

    An active category used to require a `base_score`, because without one a
    ticket filed under it could not be scored at all. Nothing about a category
    reaches the score any more, so a category with no configuration beyond its
    name is complete.
    """
    repo = CatalogRepository(db_session)
    row = repo.create_category("CUSTOM_UNSCORED", "Unscored")
    db_session.commit()

    assert row.is_active
    assert not hasattr(row, "base_score")
    assert not hasattr(row, "priority_ceiling")


def test_the_catalog_model_carries_no_field_that_could_reach_a_priority(db_session):
    """A structural check, so a future migration cannot quietly add one back."""
    columns = set(CategoryCatalog.__table__.columns.keys())
    assert columns == {"id", "code", "display_name", "is_active"}
