"""Technician assignment state for a ticket."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base
from src.models.enums import AssignmentStatus

if TYPE_CHECKING:
    from src.database.models.assignment_proposal import AIAssignmentJob
    from src.database.models.technician import TechnicianProfile
    from src.database.models.ticket import Ticket


def enum_values(enum_class):
    return [member.value for member in enum_class]


class TicketAssignment(Base):
    __tablename__ = "ticket_assignments"
    __table_args__ = (
        Index("ix_ticket_assignments_technician_active", "technician_id", "is_active"),
        Index("ix_ticket_assignments_acceptance_warning_at", "acceptance_warning_at"),
        Index("ix_ticket_assignments_acceptance_reassign_at", "acceptance_reassign_at"),
        Index("ix_ticket_assignments_ticket_assigned_at", "ticket_id", "assigned_at"),
        Index(
            "uq_ticket_assignments_one_active_per_ticket",
            "ticket_id",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active = 1"),
        ),
        # §7.3: 1.00 for a single ticket, +0.25 per extra case member, capped at
        # double. Anything outside that band means the SLA maths went wrong.
        CheckConstraint(
            "completion_sla_extension_factor >= 1.00 AND completion_sla_extension_factor <= 2.00",
            name="ck_ticket_assignments_sla_factor_range",
        ),
        CheckConstraint(
            "case_member_count_snapshot IS NULL OR (case_member_count_snapshot >= 1 AND case_member_count_snapshot <= 5)",
            name="ck_ticket_assignments_case_member_count",
        ),
        # §7.3: a human source must name the human. AI_AUTO is the only source
        # allowed to leave it null, because its audit actor is SYSTEM.
        CheckConstraint(
            "assignment_source = 'AI_AUTO' OR assigned_by_user_id IS NOT NULL",
            name="ck_ticket_assignments_human_source_has_actor",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    technician_id: Mapped[UUID] = mapped_column(
        ForeignKey("technician_profiles.user_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assigned_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[AssignmentStatus] = mapped_column(
        SQLEnum(AssignmentStatus, name="assignment_status_enum", native_enum=True, values_callable=enum_values),
        nullable=False,
        default=AssignmentStatus.ASSIGNED,
        server_default=AssignmentStatus.ASSIGNED.value,
    )
    assignment_source: Mapped[str] = mapped_column(String(40), nullable=False, default="MANUAL", server_default="MANUAL")
    assignment_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_assignment_jobs.id", ondelete="SET NULL"), nullable=True
    )
    assignment_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    unable_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # §7.3 names this `rejection_reason`. The technician API keeps emitting it
    # under the older `reject_reason` key so the frontend contract is unchanged.
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completion_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # §6.4: the clock this assignment's acceptance deadlines are measured from.
    # First assignment of an ordinary ticket uses ticket.sla_started_at; every
    # reassignment starts a fresh cycle at its own assigned_at, so the previous
    # technician's expired clock is never inherited.
    cycle_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acceptance_warning_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acceptance_reassign_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warning_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # §4.5 item 5 / §7.3: how many case members this one decision covered, and
    # the completion-SLA factor that follows from it. Recorded per assignment so
    # a later membership change cannot rewrite what this technician was given.
    case_member_count_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_sla_extension_factor: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, default=Decimal("1.00"), server_default="1.00"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    ticket: Mapped[Ticket] = relationship(back_populates="assignments")
    technician: Mapped[TechnicianProfile] = relationship(back_populates="assignments")
    # ai_assignment_jobs points back here twice (this column, and the job's
    # previous_assignment_id), so the join has to be named explicitly.
    assignment_job: Mapped[AIAssignmentJob | None] = relationship(foreign_keys=[assignment_job_id])
