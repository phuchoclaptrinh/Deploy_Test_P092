"""Ticket persistence model aligned with Self Dev v3."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base
from src.models.enums import ClassificationStatus, Priority, TicketStatus

if TYPE_CHECKING:
    from src.database.models.ai_analysis import AIAnalysisRun
    from src.database.models.attachment import TicketAttachment
    from src.database.models.category import CategoryCatalog
    from src.database.models.information_request import InformationRequest
    from src.database.models.location import Location
    from src.database.models.notification import Notification
    from src.database.models.ticket_assignment import TicketAssignment
    from src.database.models.ticket_risk_assessment import TicketRiskAssessment
    from src.database.models.ticket_status_history import TicketStatusHistory
    from src.database.models.unit import Unit
    from src.database.models.user_profile import UserProfile


def enum_values(enum_class):
    return [member.value for member in enum_class]


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        # §7.1: a ticket can never be its own master. Enforced in the database
        # because a self-link would make the duplicate chain a cycle of one and
        # every "walk to the canonical master" query would spin.
        CheckConstraint("duplicate_of_ticket_id IS NULL OR duplicate_of_ticket_id <> id", name="ck_tickets_duplicate_not_self"),
        # §7.1: LINKED_DUPLICATE without a master is a ticket that has been taken
        # out of the queue and points at nothing.
        CheckConstraint(
            "status <> 'LINKED_DUPLICATE' OR duplicate_of_ticket_id IS NOT NULL",
            name="ck_tickets_linked_duplicate_needs_master",
        ),
        CheckConstraint(
            "invalid_reason IS NULL OR invalid_reason IN "
            "('CONTENT_INSUFFICIENT', 'RESIDENT_RESPONSE_TIMEOUT', 'COORDINATOR_REJECTED')",
            name="ck_tickets_invalid_reason_enum",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    reporter_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_unit_id: Mapped[UUID] = mapped_column(ForeignKey("units.id", ondelete="RESTRICT"), nullable=False, index=True)
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TicketStatus] = mapped_column(
        SQLEnum(TicketStatus, name="ticket_status_v2_enum", native_enum=True, values_callable=enum_values),
        nullable=False,
        default=TicketStatus.NEW,
        server_default=TicketStatus.NEW.value,
    )
    classification_status: Mapped[ClassificationStatus] = mapped_column(
        SQLEnum(
            ClassificationStatus,
            name="classification_status_enum",
            native_enum=True,
            values_callable=enum_values,
        ),
        nullable=False,
        default=ClassificationStatus.PENDING,
        server_default=ClassificationStatus.PENDING.value,
    )
    category_id: Mapped[UUID | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True)
    duplicate_of_ticket_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    duplicate_linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duplicate_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duplicate_analysis_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    priority: Mapped[Priority | None] = mapped_column(
        SQLEnum(Priority, name="priority_level_enum", native_enum=True, values_callable=enum_values), nullable=True
    )
    # --- cache of the current risk assessment ------------------------------
    # `ticket_risk_assessments` is the record; these three are the denormalized
    # copy every list screen and every dispatch query reads. They are only ever
    # written together with a new assessment row, and they can be rebuilt from
    # it -- which is why nothing else may write them.
    current_risk_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ticket_risk_assessments.id", ondelete="SET NULL"), nullable=True
    )
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    invalid_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reassignment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    auto_assignment_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    auto_assignment_pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    sla_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    reporter: Mapped[UserProfile] = relationship()
    source_unit: Mapped[Unit] = relationship(back_populates="tickets")
    duplicate_master: Mapped[Ticket | None] = relationship(remote_side=[id])
    location: Mapped[Location] = relationship(back_populates="tickets")
    category: Mapped[CategoryCatalog | None] = relationship(back_populates="tickets")
    attachments: Mapped[list[TicketAttachment]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    ai_analysis_runs: Mapped[list[AIAnalysisRun]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        foreign_keys="AIAnalysisRun.ticket_id",
    )
    status_history: Mapped[list[TicketStatusHistory]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    information_requests: Mapped[list[InformationRequest]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    notifications: Mapped[list[Notification]] = relationship(back_populates="ticket")
    assignments: Mapped[list[TicketAssignment]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    risk_assessments: Mapped[list[TicketRiskAssessment]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        foreign_keys="TicketRiskAssessment.ticket_id",
        order_by="TicketRiskAssessment.revision_no",
    )
