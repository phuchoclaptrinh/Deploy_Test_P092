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
    from src.database.models.dispatch import DispatchEvent
    from src.database.models.technician import TechnicianProfile
    from src.database.models.ticket import Ticket


def enum_values(enum_class):
    return [member.value for member in enum_class]


class TicketAssignment(Base):
    __tablename__ = "ticket_assignments"
    __table_args__ = (
        Index("ix_ticket_assignments_technician_active", "technician_id", "is_active"),
        Index("ix_ticket_assignments_technician_planned", "technician_id", "planned_order"),
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
        # A human source must name the human. The three automatic sources are
        # the only ones allowed to leave it null, because their audit actor is
        # SYSTEM -- and they are enumerated rather than pattern-matched so that
        # inventing a new source cannot accidentally opt out of naming a human.
        CheckConstraint(
            "assignment_source IN ('AUTO_SCHEDULER', 'AUTO_AGENT', 'AUTO_FALLBACK')"
            " OR assigned_by_user_id IS NOT NULL",
            name="ck_ticket_assignments_human_source_has_actor",
        ),
        # §3 hard constraint: a technician may hold only one IN_PROGRESS ticket
        # at a time. In the database because it is the constraint two concurrent
        # `start` calls would otherwise race past -- the service check alone
        # cannot see the other transaction.
        Index(
            "uq_ticket_assignments_one_in_progress_per_technician",
            "technician_id",
            unique=True,
            postgresql_where=text("status = 'IN_PROGRESS' AND is_active"),
            sqlite_where=text("status = 'IN_PROGRESS' AND is_active = 1"),
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
    #: The durable dispatch event that produced this assignment, for the three
    #: automatic sources. Null for both coordinator sources -- a manual or
    #: visual placement never goes through the dispatch queue.
    dispatch_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("dispatch_events.id", ondelete="SET NULL"), nullable=True
    )
    assignment_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    unable_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # §7.3 names this `rejection_reason`. The technician API keeps emitting it
    # under the older `reject_reason` key so the frontend contract is unchanged.
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completion_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    #: §4: when the technician is expected to *start*. Resident-visible, and the
    #: only forward-looking time the resident is ever shown.
    #:
    #: An estimate, not a deadline. Nothing enforces it: no start SLA has been
    #: approved, so there is no `start_due_at` beside it and no sweep that ends
    #: an assignment for missing it. See `docs/assignment_lifecycle.md`.
    planned_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: §4: internal scheduling only. Feeds capacity and slack maths and must
    #: never be rendered to a resident as a completion promise. The resident
    #: serializers do not carry it at all, which is the enforcement.
    planned_finish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 0 for "Do now", 1 for "Next", and so on within one technician's queue.
    #: Recomputed whenever that technician's schedule is re-simulated.
    planned_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: What the scheduler concluded when this assignment was written: SAFE, or
    #: AT_RISK because every valid placement produced negative slack.
    risk_state: Mapped[str | None] = mapped_column(String(10), nullable=True)
    #: Remaining slack in seconds at placement time. Negative on an AT_RISK
    #: assignment; that sign is the whole signal.
    slack_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    # dispatch_events points back here too (its `assignment_id`), so the join
    # has to name its column explicitly.
    dispatch_event: Mapped[DispatchEvent | None] = relationship(foreign_keys=[dispatch_event_id])
