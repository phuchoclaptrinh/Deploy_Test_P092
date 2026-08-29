"""Durable automatic dispatch: the event queue and the at-risk decision log.

Two tables, and the split between them is the point.

* **`dispatch_events` is the queue.** A ticket becoming eligible for automatic
  assignment writes exactly one row here, and nothing about the automatic path
  lives in worker memory between passes. §8's peak-load requirement is met by
  making the event durable rather than by making the worker clever: a process
  killed mid-batch leaves `CLAIMED` rows that the claim timeout returns to
  `PENDING`, so no ticket is lost and none is assigned twice.

* **`at_risk_decisions` is the audit trail.** It is written only for the
  AT_RISK subset -- one row per ticket the agent (or its fallback) decided --
  and it holds what the decision was made from. A SAFE assignment produces no
  row here at all, which is exactly the fact "no model was called for this
  ticket" as data rather than as an inference from a missing log line.

Two invariants are enforced by the database rather than by convention:

* **One open event per ticket.** The partial unique index on `ticket_id WHERE
  is_open` is what makes enqueue idempotent. Two API workers racing to enqueue
  the same newly-eligible ticket produce one row and one integrity error, not
  two dispatch attempts.
* **An open event is claimable exactly once.** `claim_expires_at` is set in the
  same statement that sets `CLAIMED`, so a crashed worker's claim expires on a
  clock rather than on a heartbeat nothing would send.
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
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base
from src.models.enums import DispatchEventStatus

if TYPE_CHECKING:
    from src.database.models.technician import TechnicianProfile
    from src.database.models.ticket import Ticket

JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")

#: Statuses for which `is_open` must be true. Kept next to the constraint that
#: enforces it so the two cannot drift apart.
OPEN_STATUSES = (DispatchEventStatus.PENDING.value, DispatchEventStatus.CLAIMED.value)


class DispatchEvent(Base):
    """One ticket's journey through the automatic path, as a durable row."""

    __tablename__ = "dispatch_events"
    __table_args__ = (
        # The idempotency guarantee §8 asks for. A partial unique index rather
        # than a plain one, because a ticket legitimately accumulates many
        # *closed* events over its life (assigned, technician rejected,
        # re-enqueued) and only the open one is exclusive.
        Index(
            "uq_dispatch_events_open_ticket",
            "ticket_id",
            unique=True,
            postgresql_where=text("is_open"),
            sqlite_where=text("is_open = 1"),
        ),
        # The worker's claim query.
        Index("ix_dispatch_events_claimable", "status", "available_at"),
        Index("ix_dispatch_events_batch", "batch_id"),
        Index("ix_dispatch_events_ticket_created", "ticket_id", "created_at"),
        # `is_open` is derived from `status`, and a row where the two disagree
        # would either be invisible to the worker or block re-enqueue forever.
        CheckConstraint(
            "(is_open = true) = (status IN ('PENDING', 'CLAIMED'))",
            name="ck_dispatch_events_open_matches_status",
        ),
        # §2: P3 must never enter the automatic workflow. Enforced here as well
        # as at enqueue, because this is the table that would carry it.
        CheckConstraint("priority <> 'P3'", name="ck_dispatch_events_no_p3"),
        # A claimed row without an expiry can never be reclaimed after a crash.
        CheckConstraint(
            "status <> 'CLAIMED' OR claim_expires_at IS NOT NULL",
            name="ck_dispatch_events_claim_has_expiry",
        ),
        # An assignment is the only outcome that may name a technician, and an
        # escalation is the only one that may carry a reason. A row missing
        # either means the worker lost track of what it decided.
        CheckConstraint(
            "status <> 'ASSIGNED' OR (assignment_id IS NOT NULL AND selected_technician_id IS NOT NULL)",
            name="ck_dispatch_events_assigned_shape",
        ),
        CheckConstraint(
            "status <> 'ESCALATED' OR escalation_reason IS NOT NULL",
            name="ck_dispatch_events_escalated_shape",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DispatchEventStatus.PENDING.value, server_default="PENDING"
    )
    #: Redundant with `status` and deliberately so: a partial unique index needs
    #: a stable boolean predicate, and `status IN (...)` in the index would have
    #: to be rewritten every time a status is added.
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    #: Snapshotted at enqueue. The scheduler orders on these, and reading them
    #: live would let a reclassification mid-batch reorder work already planned.
    priority: Mapped[str] = mapped_column(String(4), nullable=False)
    category_id: Mapped[UUID] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False)
    #: When the resident submitted. Tie-break 3 of the §6 ordering, and it must
    #: survive the ticket being touched later.
    ticket_submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score_total: Mapped[float | None] = mapped_column(Float, nullable=True)

    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    #: Earliest moment a worker may claim this. Equal to `enqueued_at` on the
    #: normal path; pushed forward only by a retry backoff.
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Which worker holds the claim. A free-text process identity, for operators
    #: reading a stuck queue -- never used to decide anything.
    claimed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    #: The micro-batch that processed this event. Shared by every event in the
    #: same pass, which is what makes "one agent call per micro-batch" auditable.
    batch_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    risk_state: Mapped[str | None] = mapped_column(String(10), nullable=True)
    decision_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    selected_technician_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("technician_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    #: The assignment this event produced. Deliberately **not** a foreign key.
    #: `ticket_assignments.dispatch_event_id` already carries that reference,
    #: and declaring both directions makes the two tables mutually dependent --
    #: which SQLAlchemy can only create by emitting a separate ALTER, something
    #: SQLite (used by the whole unit-test suite) cannot do. One direction is
    #: enforced; this one is the denormalised outcome, read straight from the
    #: event log without a join.
    assignment_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    #: The planned window the scheduler committed to when it placed this ticket.
    #: Copied onto the assignment; kept here too so an escalated or superseded
    #: event still shows what the scheduler had in mind.
    planned_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    planned_finish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    slack_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    escalation_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    ticket: Mapped[Ticket] = relationship()
    selected_technician: Mapped[TechnicianProfile | None] = relationship()
    at_risk_decision: Mapped[AtRiskDecision | None] = relationship(
        back_populates="event", cascade="all, delete-orphan", uselist=False
    )


class AtRiskDecision(Base):
    """One AT_RISK ticket's decision, and what it was decided from.

    Written for the at-risk subset only. §7 requires the result to be
    auditable, which means three things have to survive the request: the
    candidate set the backend allowed, what came back, and whether a model
    actually answered. `decision_source` carries the last one -- a
    `SCHEDULER_FALLBACK` row is a decision made *without* the agent, and an
    auditor must be able to find those without reading timestamps.

    `tool_snapshot` holds the aggregated operational payload the tool returned.
    It is safe to persist precisely because the tool refuses to emit resident
    descriptions, phone numbers, emails, addresses or raw ticket text.
    """

    __tablename__ = "at_risk_decisions"
    __table_args__ = (
        Index("ix_at_risk_decisions_batch", "batch_id"),
        Index("ix_at_risk_decisions_created", "created_at"),
        CheckConstraint(
            "decision_source IN ('AGENT', 'SCHEDULER_FALLBACK')",
            name="ck_at_risk_decisions_source",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    #: Unique: an event is decided once. A re-enqueued ticket gets a new event,
    #: and therefore a new decision row, rather than overwriting this one.
    dispatch_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("dispatch_events.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    technician_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("technician_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    decision_source: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Exactly the technicians the backend allowed the agent to choose from.
    #: The validator rejects any answer outside this list, and this is the copy
    #: that proves what the list was.
    candidate_technician_ids: Mapped[list[str] | None] = mapped_column(JSON_TYPE, nullable=True)
    slack_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE, nullable=True)
    raw_model_output: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    event: Mapped[DispatchEvent] = relationship(back_populates="at_risk_decision")
    technician: Mapped[TechnicianProfile | None] = relationship()


__all__ = ["OPEN_STATUSES", "AtRiskDecision", "DispatchEvent"]
