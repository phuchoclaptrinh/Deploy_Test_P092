from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.database.models.floor import Floor
from src.database.models.resident_profile import ResidentProfile
from src.database.models.unit import Unit
from src.database.models.user_profile import UserProfile
from src.models.api.errors import ACCOUNT_ALREADY_BOUND, DomainError
from src.models.enums import UserRole
from src.services.auth_service import AuthService


def _resident_actor(user, resident_profile=None):
    return SimpleNamespace(user=user, principal=SimpleNamespace(), resident_profile=resident_profile)


def _seed_unit(db_session):
    floor = Floor(floor_code="1", display_name="Floor 1", adjacency_index=1)
    unit = Unit(floor=floor, unit_code="A-101")
    db_session.add_all([floor, unit])
    db_session.commit()
    return unit


def test_first_resident_binds_successfully(db_session):
    unit = _seed_unit(db_session)
    user = UserProfile(user_id=uuid4(), phone_e164="+84901230001", role=UserRole.RESIDENT)
    db_session.add(user)
    db_session.commit()

    response = AuthService(db_session).bind_unit(_resident_actor(user), unit.unit_code)

    assert response.unit is not None
    assert response.unit.id == unit.id
    assert response.unit.is_primary is True


def test_same_resident_cannot_bind_again(db_session):
    unit = _seed_unit(db_session)
    user = UserProfile(user_id=uuid4(), phone_e164="+84901230002", role=UserRole.RESIDENT)
    profile = ResidentProfile(user=user, unit=unit, is_primary=True)
    db_session.add_all([user, profile])
    db_session.commit()

    with pytest.raises(DomainError) as exc:
        AuthService(db_session).bind_unit(_resident_actor(user), unit.unit_code)

    assert exc.value.code == ACCOUNT_ALREADY_BOUND


def test_second_resident_can_bind_already_bound_unit_as_non_primary(db_session):
    unit = _seed_unit(db_session)
    first = UserProfile(user_id=uuid4(), phone_e164="+84901230003", role=UserRole.RESIDENT)
    second = UserProfile(user_id=uuid4(), phone_e164="+84901230004", role=UserRole.RESIDENT)
    db_session.add_all([first, second, ResidentProfile(user=first, unit=unit, is_primary=True)])
    db_session.commit()

    response = AuthService(db_session).bind_unit(_resident_actor(second), unit.unit_code)

    assert response.unit is not None
    assert response.unit.id == unit.id
    assert response.unit.is_primary is False


def test_resident_profile_unit_uniqueness_protects_race_condition(db_session):
    unit = _seed_unit(db_session)
    first = UserProfile(user_id=uuid4(), phone_e164="+84901230005", role=UserRole.RESIDENT)
    second = UserProfile(user_id=uuid4(), phone_e164="+84901230006", role=UserRole.RESIDENT)
    db_session.add_all([first, second, ResidentProfile(user=first, unit=unit, is_primary=True)])
    db_session.commit()

    db_session.add(ResidentProfile(user=second, unit=unit, is_primary=True))
    with pytest.raises(Exception):
        db_session.commit()
