from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.api.dependencies.auth import get_current_actor
from src.database.models.category import CategoryCatalog
from src.database.models.floor import Floor
from src.database.models.resident_profile import ResidentProfile
from src.database.models.technician import TechnicianProfile, TechnicianSkill
from src.database.models.unit import Unit
from src.database.models.user_profile import UserProfile
from src.models.api.coordinator import ManagerCreateResidentRequest, ManagerCreateTechnicianRequest
from src.models.api.errors import VALIDATION_ERROR, DomainError
from src.models.enums import UserRole
from src.security.supabase_jwt import AuthenticatedPrincipal
from src.services.manager_account_service import ManagerAccountService


class FakeAuthProvider:
    def __init__(self, existing_emails=None):
        self.created_residents = []
        self.created_email_users = []
        self.deleted_users = []
        self.updated_users = []
        self.existing_emails = set(existing_emails or [])
        self.user_emails = {}

    def create_resident_user(self, *, phone: str, full_name: str | None = None):
        user_id = uuid4()
        self.created_residents.append({"user_id": user_id, "phone": phone, "full_name": full_name})
        return user_id

    def create_email_user(self, *, email: str, password: str, full_name: str | None = None):
        if email in self.existing_emails:
            raise DomainError(VALIDATION_ERROR, "User already registered.", 409)
        user_id = uuid4()
        self.existing_emails.add(email)
        self.user_emails[user_id] = email
        self.created_email_users.append(
            {"user_id": user_id, "email": email, "password": password, "full_name": full_name}
        )
        return user_id

    def delete_user(self, user_id):
        self.deleted_users.append(user_id)

    def get_user_email(self, user_id):
        return self.user_emails[user_id]

    def update_user(self, user_id, payload):
        self.updated_users.append({"user_id": user_id, "payload": payload})


def _seed_unit(db_session, unit_code="A-1203"):
    floor = Floor(floor_code="12", display_name="Floor 12", adjacency_index=12)
    unit = Unit(floor=floor, unit_code=unit_code)
    db_session.add_all([floor, unit])
    db_session.commit()
    return unit


def test_manager_creates_resident_account_bound_to_unit(db_session):
    unit = _seed_unit(db_session)
    auth = FakeAuthProvider()

    response = ManagerAccountService(db_session, auth).create_resident(
        ManagerCreateResidentRequest(
            email="LAN@fixit.vn",
            password="resident123",
            phone="+84901234567",
            full_name="Lan Nguyen",
            unit_id=unit.id,
        )
    )

    user = db_session.get(UserProfile, response.user_id)
    resident = db_session.get(ResidentProfile, response.user_id)

    assert response.role == UserRole.RESIDENT
    assert response.phone_e164 == "+84901234567"
    assert response.unit_id == unit.id
    assert user is not None
    assert user.role == UserRole.RESIDENT
    assert resident is not None
    assert resident.unit_id == unit.id
    assert auth.created_email_users[0]["email"] == "lan@fixit.vn"


def test_manager_generates_resident_email_from_name(db_session):
    unit = _seed_unit(db_session)
    auth = FakeAuthProvider()

    response = ManagerAccountService(db_session, auth).create_resident(
        ManagerCreateResidentRequest(
            full_name="Nguyễn Văn Anh",
            unit_id=unit.id,
        )
    )

    assert response.email == "anh.nguyen@fixit.vn"
    assert response.temporary_password is not None
    assert len(response.temporary_password) == 6
    assert auth.created_email_users[0]["email"] == "anh.nguyen@fixit.vn"
    assert auth.created_email_users[0]["password"] == response.temporary_password


def test_manager_generates_next_email_when_auth_email_exists(db_session):
    unit = _seed_unit(db_session)
    auth = FakeAuthProvider(existing_emails={"anh.nguyen@fixit.vn"})

    response = ManagerAccountService(db_session, auth).create_resident(
        ManagerCreateResidentRequest(
            password="resident123",
            full_name="Nguyễn Văn Anh",
            unit_id=unit.id,
        )
    )

    assert response.email == "anh.nguyen2@fixit.vn"
    assert auth.created_email_users[0]["email"] == "anh.nguyen2@fixit.vn"


def test_manager_lists_resident_accounts_with_unit(db_session):
    unit = _seed_unit(db_session)
    user = UserProfile(user_id=uuid4(), phone_e164="+84901230000", full_name="Cu Dan A", role=UserRole.RESIDENT)
    db_session.add_all([user, ResidentProfile(user_id=user.user_id, unit_id=unit.id, is_primary=True)])
    db_session.commit()

    rows = ManagerAccountService(db_session, FakeAuthProvider()).list_residents()

    assert len(rows) == 1
    assert rows[0].user_id == user.user_id
    assert rows[0].unit_code == unit.unit_code
    # One building, so an apartment is identified by its floor and code.
    assert rows[0].floor_code == "12"


def test_manager_can_bind_multiple_residents_to_same_unit(db_session):
    unit = _seed_unit(db_session)
    existing = UserProfile(user_id=uuid4(), phone_e164="+84900000001", role=UserRole.RESIDENT)
    db_session.add_all([existing, ResidentProfile(user_id=existing.user_id, unit_id=unit.id, is_primary=True)])
    db_session.commit()

    response = ManagerAccountService(db_session, FakeAuthProvider()).create_resident(
        ManagerCreateResidentRequest(
            email="minhanh@fixit.vn",
            password="resident123",
            phone="0290329832",
            full_name="Minh Anh",
            unit_id=unit.id,
        )
    )

    resident = db_session.get(ResidentProfile, response.user_id)
    assert response.phone_e164 == "+84290329832"
    assert resident is not None
    assert resident.unit_id == unit.id
    assert resident.is_primary is False


def test_manager_creates_technician_account_with_skills(db_session):
    category = CategoryCatalog(
        code="WATER_LEAK",
        display_name="Rò nước",
        is_active=True,
    )
    db_session.add(category)
    db_session.commit()
    auth = FakeAuthProvider()

    response = ManagerAccountService(db_session, auth).create_technician(
        ManagerCreateTechnicianRequest(
            email="KTV@example.com",
            password="secret123",
            full_name="KTV Hung",
            phone_number="0901234567",
            skill_category_ids=[category.id],
        )
    )

    user = db_session.get(UserProfile, response.user_id)
    technician = db_session.get(TechnicianProfile, response.user_id)
    skills = db_session.query(TechnicianSkill).filter_by(technician_id=response.user_id).all()

    assert response.role == UserRole.TECHNICIAN
    assert response.email == "ktv@example.com"
    assert response.skill_category_ids == [category.id]
    assert user is not None
    assert user.role == UserRole.TECHNICIAN
    assert technician is not None
    assert technician.phone_number == "0901234567"
    # The identity phone is normalized so the roster can show one spelling.
    assert user.phone_e164 == "+84901234567"
    assert response.phone_e164 == "+84901234567"
    assert [skill.category_id for skill in skills] == [category.id]
    assert auth.created_email_users[0]["email"] == "ktv@example.com"


def test_manager_resets_resident_password(db_session):
    unit = _seed_unit(db_session)
    user = UserProfile(user_id=uuid4(), full_name="Cu Dan A", role=UserRole.RESIDENT)
    db_session.add_all([user, ResidentProfile(user_id=user.user_id, unit_id=unit.id, is_primary=True)])
    db_session.commit()
    auth = FakeAuthProvider()
    auth.user_emails[user.user_id] = "resident@example.com"

    response = ManagerAccountService(db_session, auth).reset_resident_password(user.user_id)

    assert response.user_id == user.user_id
    assert response.temporary_password is not None
    assert response.email == "resident@example.com"
    assert len(response.temporary_password) == 6
    assert auth.updated_users == [{"user_id": user.user_id, "payload": {"password": response.temporary_password}}]


def test_manager_resets_technician_password(db_session):
    user = UserProfile(user_id=uuid4(), full_name="KTV A", role=UserRole.TECHNICIAN)
    technician = TechnicianProfile(user_id=user.user_id, is_active=True, is_available=True)
    db_session.add_all([user, technician])
    db_session.commit()
    auth = FakeAuthProvider()
    auth.user_emails[user.user_id] = "technician@example.com"

    response = ManagerAccountService(db_session, auth).reset_technician_password(user.user_id)

    assert response.user_id == user.user_id
    assert response.temporary_password is not None
    assert response.email == "technician@example.com"
    assert auth.updated_users == [{"user_id": user.user_id, "payload": {"password": response.temporary_password}}]


def test_manager_deletes_unused_technician_account(db_session):
    user = UserProfile(user_id=uuid4(), full_name="KTV A", role=UserRole.TECHNICIAN)
    technician = TechnicianProfile(user_id=user.user_id, is_active=True, is_available=True)
    db_session.add_all([user, technician])
    db_session.commit()
    auth = FakeAuthProvider()

    response = ManagerAccountService(db_session, auth).delete_technician(user.user_id)

    assert response.user_id == user.user_id
    assert db_session.get(UserProfile, user.user_id) is None
    assert auth.deleted_users == [user.user_id]


def test_manager_locks_and_unlocks_resident(db_session):
    unit = _seed_unit(db_session)
    user = UserProfile(user_id=uuid4(), full_name="Cu Dan A", role=UserRole.RESIDENT, is_active=True)
    db_session.add_all([user, ResidentProfile(user_id=user.user_id, unit_id=unit.id, is_primary=True)])
    db_session.commit()

    locked = ManagerAccountService(db_session, FakeAuthProvider()).set_resident_active(user.user_id, False)

    assert locked.is_active is False
    assert db_session.get(UserProfile, user.user_id).is_active is False

    unlocked = ManagerAccountService(db_session, FakeAuthProvider()).set_resident_active(user.user_id, True)

    assert unlocked.is_active is True
    assert db_session.get(UserProfile, user.user_id).is_active is True


def test_email_resident_account_can_authenticate_without_phone(db_session):
    unit = _seed_unit(db_session)
    user_id = uuid4()
    user = UserProfile(user_id=user_id, role=UserRole.RESIDENT, full_name="Email Resident")
    db_session.add_all([user, ResidentProfile(user_id=user_id, unit_id=unit.id, is_primary=True)])
    db_session.commit()

    actor = get_current_actor(
        AuthenticatedPrincipal(
            auth_user_id=user_id,
            email="resident@fixit.vn",
            phone=None,
            issuer="test",
            audience="authenticated",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ),
        db_session,
    )

    assert actor.actor_type == "resident"
    assert actor.user.user_id == user_id
    assert actor.resident_profile is not None
