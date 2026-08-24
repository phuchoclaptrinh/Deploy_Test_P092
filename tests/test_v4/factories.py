"""Shared world-building for the v4 tests.

Every v4 scenario needs the same skeleton — a building with two floors, a
couple of distinct assets on the same floor, categories with real base scores,
residents in different units and technicians with skills — so it lives here
once instead of in each test module.

Two things the fixtures are deliberate about, because the contract turns on
them:

* **Assets are separate `Location` rows.** `elevator_a` and `elevator_b` share a
  building, a floor and a Category and differ only by `location_id`. That is the
  §1.5 item 5 / §11 assumption 2 distinction, and a test that used one location
  for both could not tell a correct duplicate from a wrong one.
* **Reporters live in different units.** Density is `COUNT(DISTINCT
  source_unit_id)` (§7.9), so tickets seeded from one unit would make every
  grouping test agree with a broken implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from src.database.models.building import Building
from src.database.models.category import CategoryCatalog
from src.database.models.floor import Floor
from src.database.models.location import Location
from src.database.models.location_type import LocationType
from src.database.models.resident_profile import ResidentProfile
from src.database.models.technician import TechnicianProfile, TechnicianSkill
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.database.models.ticket_status_history import TicketStatusHistory
from src.database.models.unit import Unit
from src.database.models.user_profile import UserProfile
from src.models.enums import (
    AssignmentSource,
    AssignmentStatus,
    Category,
    ClassificationStatus,
    Priority,
    Severity,
    TicketStatus,
    UserRole,
)


@dataclass
class World:
    db: Session
    building: Building
    floor_10: Floor
    floor_11: Floor
    elevator_a: Location
    elevator_b: Location
    corridor_10: Location
    corridor_11: Location
    water: CategoryCatalog
    electrical: CategoryCatalog
    elevator: CategoryCatalog
    coordinator: UserProfile
    residents: list[ResidentProfile] = field(default_factory=list)
    technicians: list[TechnicianProfile] = field(default_factory=list)

    def resident(self, index: int = 0) -> ResidentProfile:
        return self.residents[index]

    def technician(self, index: int = 0) -> TechnicianProfile:
        return self.technicians[index]


def build_world(db: Session, *, resident_count: int = 6, technician_count: int = 3) -> World:
    building = Building(code="A", name="Tower A")
    floor_10 = Floor(building=building, floor_code="10", display_name="Tầng 10", adjacency_index=10)
    floor_11 = Floor(building=building, floor_code="11", display_name="Tầng 11", adjacency_index=11)
    elevator_type = LocationType(code="ELEVATOR", display_name="Thang máy")
    corridor_type = LocationType(code="CORRIDOR", display_name="Hành lang")

    # Same building, same floor, same Category, different asset. This pair is
    # the whole point of location-based duplicate detection.
    elevator_a = Location(building=building, floor=floor_10, location_type=elevator_type, label="Thang máy A")
    elevator_b = Location(building=building, floor=floor_10, location_type=elevator_type, label="Thang máy B")
    corridor_10 = Location(building=building, floor=floor_10, location_type=corridor_type, label="Hành lang tầng 10")
    corridor_11 = Location(building=building, floor=floor_11, location_type=corridor_type, label="Hành lang tầng 11")

    water = CategoryCatalog(code=Category.WATER_LEAK, display_name="Rò rỉ nước", base_score=10)
    electrical = CategoryCatalog(code=Category.ELECTRICAL_SHORT, display_name="Chập điện", base_score=50)
    elevator = CategoryCatalog(code=Category.ELEVATOR, display_name="Thang máy", base_score=35)

    coordinator = UserProfile(user_id=uuid4(), role=UserRole.COORDINATOR, full_name="Điều phối viên")

    db.add_all(
        [
            building,
            floor_10,
            floor_11,
            elevator_type,
            corridor_type,
            elevator_a,
            elevator_b,
            corridor_10,
            corridor_11,
            water,
            electrical,
            elevator,
            coordinator,
        ]
    )

    residents: list[ResidentProfile] = []
    for index in range(resident_count):
        unit = Unit(building=building, floor=floor_10 if index % 2 == 0 else floor_11, unit_code=f"A-{1000 + index}")
        user = UserProfile(user_id=uuid4(), role=UserRole.RESIDENT, full_name=f"Cư dân {index}")
        profile = ResidentProfile(user=user, unit=unit, is_primary=True)
        db.add_all([unit, user, profile])
        residents.append(profile)

    technicians: list[TechnicianProfile] = []
    for index in range(technician_count):
        user = UserProfile(user_id=uuid4(), role=UserRole.TECHNICIAN, full_name=f"Kỹ thuật viên {index}")
        profile = TechnicianProfile(user=user, is_active=True, is_available=True)
        db.add_all([user, profile])
        technicians.append(profile)

    db.commit()

    # Every technician can handle every seeded Category unless a test narrows it.
    for profile in technicians:
        for category in (water, electrical, elevator):
            db.add(TechnicianSkill(technician_id=profile.user_id, category_id=category.id))
    db.commit()

    return World(
        db=db,
        building=building,
        floor_10=floor_10,
        floor_11=floor_11,
        elevator_a=elevator_a,
        elevator_b=elevator_b,
        corridor_10=corridor_10,
        corridor_11=corridor_11,
        water=water,
        electrical=electrical,
        elevator=elevator,
        coordinator=coordinator,
        residents=residents,
        technicians=technicians,
    )


def make_ticket(
    world: World,
    *,
    resident: ResidentProfile | None = None,
    location: Location | None = None,
    category: CategoryCatalog | None = None,
    status: TicketStatus = TicketStatus.NEW,
    classification_status: ClassificationStatus = ClassificationStatus.PROCESSING,
    description: str = "Sự cố cần xử lý.",
    created_at: datetime | None = None,
    priority: Priority | None = None,
    severity: Severity | None = Severity.MEDIUM,
    approved_at: datetime | None = None,
    sla_due_at: datetime | None = None,
    auto_assignment_paused: bool = False,
    reassignment_count: int = 0,
) -> Ticket:
    resident = resident or world.resident(0)
    location = location or world.corridor_10
    created_at = created_at or datetime.now(UTC)
    ticket = Ticket(
        reporter_user_id=resident.user_id,
        source_unit_id=resident.unit_id,
        location_id=location.id,
        description=description,
        status=status,
        classification_status=classification_status,
        category_id=category.id if category else None,
        priority=priority,
        severity=severity,
        score_total=Decimal("30.00") if priority else None,
        created_at=created_at,
        sla_started_at=created_at,
        sla_due_at=sla_due_at,
        approved_at=approved_at,
        auto_assignment_paused=auto_assignment_paused,
        reassignment_count=reassignment_count,
    )
    world.db.add(ticket)
    world.db.commit()
    return ticket


def approved_ticket(world: World, **kwargs) -> Ticket:
    """A ticket the assignment side considers eligible (§4.2)."""
    now = datetime.now(UTC)
    kwargs.setdefault("status", TicketStatus.APPROVED)
    kwargs.setdefault("classification_status", ClassificationStatus.RESOLVED)
    kwargs.setdefault("category", world.elevator)
    kwargs.setdefault("priority", Priority.P2)
    kwargs.setdefault("approved_at", now - timedelta(hours=6))
    kwargs.setdefault("sla_due_at", now + timedelta(hours=2))
    return make_ticket(world, **kwargs)


def add_status_history(world: World, ticket: Ticket, statuses: list[TicketStatus]) -> None:
    previous: TicketStatus | None = None
    base = ticket.created_at
    for index, status in enumerate(statuses):
        world.db.add(
            TicketStatusHistory(
                ticket_id=ticket.id,
                from_status=previous,
                to_status=status,
                changed_by=world.coordinator.user_id,
                # Free text a coordinator typed: the search response must never
                # carry it, which is what the sanitization tests assert.
                reason="Ghi chú nội bộ của BQL, chứa tên người báo.",
                created_at=base + timedelta(minutes=index),
            )
        )
        previous = status
    world.db.commit()


def make_assignment(
    world: World,
    ticket: Ticket,
    technician: TechnicianProfile,
    *,
    status: AssignmentStatus = AssignmentStatus.ASSIGNED,
    is_active: bool = True,
    source: str = AssignmentSource.COORDINATOR_MANUAL.value,
    assigned_by_user_id: UUID | None = None,
    end_reason: str | None = None,
    assigned_at: datetime | None = None,
) -> TicketAssignment:
    assigned_at = assigned_at or datetime.now(UTC)
    if assigned_by_user_id is None and source != AssignmentSource.AI_AUTO.value:
        assigned_by_user_id = world.coordinator.user_id
    assignment = TicketAssignment(
        ticket_id=ticket.id,
        technician_id=technician.user_id,
        assigned_by_user_id=assigned_by_user_id,
        assignment_source=source,
        status=status,
        is_active=is_active,
        assigned_at=assigned_at,
        cycle_started_at=assigned_at,
        end_reason=end_reason,
        ended_at=None if is_active else assigned_at,
    )
    world.db.add(assignment)
    world.db.commit()
    return assignment


def attach_image(world: World, ticket: Ticket) -> None:
    """Give a ticket one original photo.

    §1.7.6 ties the three image fields to whether the ticket actually has one,
    so a test about image/text reconciliation has to seed a real attachment.
    """
    from src.database.models.attachment import TicketAttachment
    from src.models.enums import AttachmentType, ImageQualityStatus

    world.db.add(
        TicketAttachment(
            ticket_id=ticket.id,
            attachment_type=AttachmentType.ISSUE_ORIGINAL,
            storage_bucket="ticket-attachments",
            object_path=f"tickets/{ticket.reporter_user_id}/{ticket.id}.jpg",
            mime_type="image/jpeg",
            size_bytes=2048,
            uploaded_by=ticket.reporter_user_id,
            image_quality_status=ImageQualityStatus.READABLE,
        )
    )
    world.db.commit()
