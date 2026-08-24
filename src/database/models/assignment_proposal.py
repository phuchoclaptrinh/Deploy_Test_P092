"""Durable AI assignment jobs and the coordinator proposal table (§7.4, §7.5).

Three things this schema is deliberately strict about:

* **Mode is a shape, not a label.** A `DIRECT` job answers for exactly one work
  item and carries a `decision_id`; a `PROPOSAL` job represents one model call
  over a whole batch and carries a `batch_decision_id`. The check constraints
  make the wrong combination unwritable rather than merely discouraged.
* **Only DIRECT locks tickets.** `ai_assignment_job_members.is_active` with a
  partial unique index is what stops one ticket sitting in two unfinished DIRECT
  jobs. PROPOSAL never sets it, because manual assignment has to stay possible
  while the preview table is open (§5.1).
* **A ticket appears once per batch.** `assignment_proposal_item_members` carries
  `batch_id` and a composite foreign key back to `(item_id, batch_id)`, so the
  `UNIQUE (batch_id, ticket_id)` guarantee cannot be bypassed by pointing a
  member at an item from another batch.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base
from src.models.enums import AssignmentJobStatus, ProposalBatchStatus, ProposalItemStatus

if TYPE_CHECKING:
    from src.database.models.incident_case import IncidentCase
    from src.database.models.technician import TechnicianProfile
    from src.database.models.ticket import Ticket
    from src.database.models.ticket_assignment import TicketAssignment
    from src.database.models.user_profile import UserProfile

JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")


class AssignmentProposalBatch(Base):
    __tablename__ = "assignment_proposal_batches"
    __table_args__ = (
        # §4.6 item 3. A READY batch without an expiry would be confirmable
        # forever against a stale candidate snapshot.
        CheckConstraint(
            "status <> 'READY' OR expires_at IS NOT NULL",
            name="ck_assignment_proposal_batches_ready_has_expiry",
        ),
        CheckConstraint(
            "created_by_type IN ('COORDINATOR', 'SYSTEM')",
            name="ck_assignment_proposal_batches_created_by_type",
        ),
        CheckConstraint(
            "followup_schedule IS NULL OR followup_schedule IN ('NONE', '2_HOURS', '1_DAY', '3_DAYS')",
            name="ck_assignment_proposal_batches_followup_schedule",
        ),
        Index("ix_assignment_proposal_batches_status_expires", "status", "expires_at"),
        Index("ix_assignment_proposal_batches_status_ready", "status", "ready_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ProposalBatchStatus.BUILDING.value, server_default="BUILDING"
    )
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    # §4.6 item 6: null until confirm decides. Opening, cancelling or expiring a
    # batch must never be read as "the coordinator asked for auto-assignment".
    continue_auto_assignment: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    activation_delay: Mapped[str | None] = mapped_column(String(20), nullable=True)
    batch_decision_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, unique=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    # Who opened the batch. A batch the recurring schedule opened has no
    # coordinator behind it, and §8.1 keeps SYSTEM and a named actor apart.
    created_by_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="COORDINATOR", server_default="COORDINATOR"
    )
    # The confirmed state, frozen. History reads this and nothing live: a
    # category renamed, a technician deactivated or a resident moved out must
    # not rewrite what a coordinator actually approved months ago. Written once,
    # inside the confirm transaction, and never updated after.
    confirmation_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE, nullable=True)
    # The repeat interval the coordinator picked in the result modal *after*
    # this batch was confirmed, or 'NONE'. Not part of the confirmation --
    # it is the next thing they asked for -- so it is its own write-once column
    # rather than an edit to the snapshot above.
    followup_schedule: Mapped[str | None] = mapped_column(String(20), nullable=True)
    followup_schedule_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # §4.6 item 5: optimistic concurrency for confirm.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list[AssignmentProposalItem]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="AssignmentProposalItem.created_at",
        foreign_keys="AssignmentProposalItem.batch_id",
    )
    # §8.1: a confirmed batch is the coordinator's act, so the history has to be
    # able to name them. The foreign key already exists; this only reads it.
    confirmed_by: Mapped[UserProfile | None] = relationship(foreign_keys=[confirmed_by_user_id])


class AIAssignmentJob(Base):
    __tablename__ = "ai_assignment_jobs"
    __table_args__ = (
        # §7.4 DIRECT shape: one decision, one work item, no batch identity.
        CheckConstraint(
            "mode <> 'DIRECT' OR ("
            " decision_id IS NOT NULL"
            " AND batch_decision_id IS NULL"
            " AND proposal_batch_id IS NULL"
            " AND work_item_type IS NOT NULL"
            " AND work_item_id IS NOT NULL"
            " AND ((work_item_type = 'TICKET' AND ticket_id IS NOT NULL AND incident_case_id IS NULL)"
            "   OR (work_item_type = 'INCIDENT_CASE' AND incident_case_id IS NOT NULL AND ticket_id IS NULL))"
            ")",
            name="ck_ai_assignment_jobs_direct_shape",
        ),
        # §7.4 PROPOSAL shape: one job stands for one model call over the batch,
        # so it must not claim a single work item.
        CheckConstraint(
            "mode <> 'PROPOSAL' OR ("
            " batch_decision_id IS NOT NULL"
            " AND proposal_batch_id IS NOT NULL"
            " AND decision_id IS NULL"
            " AND work_item_type IS NULL"
            " AND work_item_id IS NULL"
            " AND ticket_id IS NULL"
            " AND incident_case_id IS NULL"
            ")",
            name="ck_ai_assignment_jobs_proposal_shape",
        ),
        CheckConstraint("mode IN ('DIRECT', 'PROPOSAL')", name="ck_ai_assignment_jobs_mode"),
        Index("ix_ai_assignment_jobs_status_execute", "status", "execute_after"),
        Index("ix_ai_assignment_jobs_mode_status", "mode", "status"),
        Index("ix_ai_assignment_jobs_ticket_id", "ticket_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default=AssignmentJobStatus.SCHEDULED_GRACE.value, server_default="SCHEDULED_GRACE"
    )
    trigger: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # §4.3: the idempotency key that survives primary -> fallback -> write.
    decision_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, unique=True)
    batch_decision_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, unique=True)
    # Several independent DIRECT jobs may be sent in one model request; this is
    # what ties them back together for audit without merging their decisions.
    model_request_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)

    work_item_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    work_item_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    ticket_id: Mapped[UUID | None] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=True)
    incident_case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("incident_cases.id", ondelete="CASCADE"), nullable=True
    )
    proposal_batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("assignment_proposal_batches.id", ondelete="CASCADE", name="fk_ai_assignment_jobs_proposal_batch"),
        nullable=True,
    )

    previous_assignment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ticket_assignments.id", ondelete="SET NULL"), nullable=True
    )
    reassignment_count_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # §5.1 / §6.2: SCHEDULED_GRACE jobs wait until execute_after; the two
    # deadlines are the hard 300-second windows from §5.2.
    execute_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    primary_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fallback_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # §7.4 / §8.1: exactly what the model was shown, and what it said.
    candidate_snapshot: Mapped[list[dict[str, object]] | None] = mapped_column(JSON_TYPE, nullable=True)
    excluded_technician_ids: Mapped[list[str] | None] = mapped_column(JSON_TYPE, nullable=True)
    raw_model_output: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE, nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    primary_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fallback_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    completed_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    selected_technician_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("technician_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    decision_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="SET NULL"), nullable=True
    )

    # Set by the worker when it claims the job, so a crashed claim is visible.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ticket: Mapped[Ticket | None] = relationship(foreign_keys=[ticket_id])
    incident_case: Mapped[IncidentCase | None] = relationship()
    selected_technician: Mapped[TechnicianProfile | None] = relationship()
    previous_assignment: Mapped[TicketAssignment | None] = relationship(foreign_keys=[previous_assignment_id])
    members: Mapped[list[AIAssignmentJobMember]] = relationship(back_populates="job", cascade="all, delete-orphan")


class AIAssignmentJobMember(Base):
    __tablename__ = "ai_assignment_job_members"
    __table_args__ = (
        # §5.1: the concurrency rule expressed in persistence. Only DIRECT jobs
        # set is_active, so one ticket can never sit in two unfinished DIRECT
        # jobs -- including when one of them represents a whole incident case.
        Index(
            "uq_ai_assignment_job_members_active_ticket",
            "ticket_id",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active = 1"),
        ),
        Index("ix_ai_assignment_job_members_ticket", "ticket_id", "is_active"),
    )

    job_id: Mapped[UUID] = mapped_column(ForeignKey("ai_assignment_jobs.id", ondelete="CASCADE"), primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    job: Mapped[AIAssignmentJob] = relationship(back_populates="members")
    ticket: Mapped[Ticket] = relationship()


class AssignmentProposalItem(Base):
    __tablename__ = "assignment_proposal_items"
    __table_args__ = (
        # Needed so item members can carry a composite FK on (item_id, batch_id).
        UniqueConstraint("id", "batch_id", name="uq_assignment_proposal_items_id_batch"),
        CheckConstraint(
            "(work_item_type = 'TICKET' AND ticket_id IS NOT NULL AND incident_case_id IS NULL)"
            " OR (work_item_type = 'INCIDENT_CASE' AND incident_case_id IS NOT NULL AND ticket_id IS NULL)",
            name="ck_assignment_proposal_items_work_item_shape",
        ),
        Index("ix_assignment_proposal_items_batch_status", "batch_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("assignment_proposal_batches.id", ondelete="CASCADE"), nullable=False
    )
    # §7.5: the per-row idempotency key, the same one the model answered under.
    decision_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, unique=True, default=uuid4)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ProposalItemStatus.PENDING.value, server_default="PENDING"
    )
    work_item_type: Mapped[str] = mapped_column(String(30), nullable=False)
    work_item_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    ticket_id: Mapped[UUID | None] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=True)
    incident_case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("incident_cases.id", ondelete="CASCADE"), nullable=True
    )
    # §4.6 item 4: what the model proposed, and what the coordinator actually
    # settled on, are two different facts and both are auditable.
    proposed_technician_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("technician_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    final_technician_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("technician_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    completed_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    decision_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_assignment_jobs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    batch: Mapped[AssignmentProposalBatch] = relationship(back_populates="items", foreign_keys=[batch_id])
    ticket: Mapped[Ticket | None] = relationship(foreign_keys=[ticket_id])
    incident_case: Mapped[IncidentCase | None] = relationship()
    proposed_technician: Mapped[TechnicianProfile | None] = relationship(foreign_keys=[proposed_technician_id])
    final_technician: Mapped[TechnicianProfile | None] = relationship(foreign_keys=[final_technician_id])
    decision_job: Mapped[AIAssignmentJob | None] = relationship()
    members: Mapped[list[AssignmentProposalItemMember]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        foreign_keys="AssignmentProposalItemMember.item_id",
    )


class AssignmentProposalItemMember(Base):
    __tablename__ = "assignment_proposal_item_members"
    __table_args__ = (
        # §7.5: one ticket per batch, guaranteed by the database rather than by
        # a JSON array nobody can constrain.
        UniqueConstraint("batch_id", "ticket_id", name="uq_assignment_proposal_item_members_batch_ticket"),
        ForeignKeyConstraint(
            ["item_id", "batch_id"],
            ["assignment_proposal_items.id", "assignment_proposal_items.batch_id"],
            name="fk_assignment_proposal_item_members_item_batch",
            ondelete="CASCADE",
        ),
    )

    item_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    item: Mapped[AssignmentProposalItem] = relationship(back_populates="members", foreign_keys=[item_id])
    ticket: Mapped[Ticket] = relationship()


__all__ = [
    "AIAssignmentJob",
    "AIAssignmentJobMember",
    "AssignmentProposalBatch",
    "AssignmentProposalItem",
    "AssignmentProposalItemMember",
]
