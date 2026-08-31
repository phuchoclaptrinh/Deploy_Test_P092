"""Every priority a ticket ever had, and the evidence behind each one.

Append-only. A row is never updated and never deleted; a new judgement writes a
new row that points at the one it replaces through ``supersedes_id`` and takes
the next ``revision_no``. The reason is `docs/risk_scoring_v2.md` §7.3: a case
that grows and then loses a member would otherwise rewrite history into
something that never happened, and the P5 a ticket held for four minutes would
disappear along with the assignment it cancelled.

The ticket table keeps only a cache of the current row --
``current_risk_assessment_id``, ``risk_score``, ``priority``, ``sla_started_at``,
``sla_due_at``. The cache is rebuildable from here; this is not rebuildable from
the cache.

Three scope columns rather than one, because they answer three different
questions and a reviewer needs all three:

* ``ai_scope_score`` -- what the Agent estimated from one report.
* ``backend_scope_score`` -- what a case actually counted. NULL until one has.
* ``effective_scope_score`` -- which of the two the formula used.

An estimate that was overruled is exactly the thing you want to be able to see
later, so it is stored rather than replaced.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base
from src.domain.risk_scoring import MAX_AFFECTED_UNITS, MAX_CRITERION_SCORE, MIN_CRITERION_SCORE
from src.models.enums import Priority, RiskAssessmentSource

if TYPE_CHECKING:
    from src.database.models.ticket import Ticket

JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")


def enum_values(enum_class):
    return [member.value for member in enum_class]


#: The five criterion columns, plus the two scope columns that are also on the
#: 0-4 scale. Every one gets the same range constraint, written once.
_CRITERION_COLUMNS = (
    "human_safety_score",
    "property_spread_score",
    "essential_function_score",
    "ai_scope_score",
    "backend_scope_score",
    "effective_scope_score",
    "deterioration_speed_score",
)


class TicketRiskAssessment(Base):
    __tablename__ = "ticket_risk_assessments"
    __table_args__ = (
        # One revision number per ticket. Two concurrent rescores racing to
        # write revision 4 is exactly the case where the database has to be the
        # one saying no, because both of them read revision 3.
        UniqueConstraint("ticket_id", "revision_no", name="uq_ticket_risk_assessments_ticket_revision"),
        CheckConstraint("revision_no >= 1", name="ck_ticket_risk_assessments_revision_positive"),
        *(
            CheckConstraint(
                f"{column} IS NULL OR ({column} >= {MIN_CRITERION_SCORE} AND {column} <= {MAX_CRITERION_SCORE})",
                name=f"ck_ticket_risk_assessments_{column}_range",
            )
            for column in _CRITERION_COLUMNS
        ),
        CheckConstraint("risk_score >= 0 AND risk_score <= 100", name="ck_ticket_risk_assessments_score_range"),
        # A case holds at most five apartments, so a confirmed count outside
        # 1-5 means whoever wrote it counted something other than case members.
        CheckConstraint(
            "confirmed_affected_unit_count IS NULL OR "
            f"(confirmed_affected_unit_count >= 1 AND confirmed_affected_unit_count <= {MAX_AFFECTED_UNITS})",
            name="ck_ticket_risk_assessments_unit_count_range",
        ),
        CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="ck_ticket_risk_assessments_not_self"),
        Index("ix_ticket_risk_assessments_ticket_revision", "ticket_id", "revision_no"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    source: Mapped[RiskAssessmentSource] = mapped_column(
        SQLEnum(
            RiskAssessmentSource,
            name="risk_assessment_source_enum",
            native_enum=True,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    ai_analysis_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    supersedes_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ticket_risk_assessments.id", ondelete="SET NULL"), nullable=True
    )

    # --- the five criteria, exactly as scored ------------------------------
    human_safety_score: Mapped[int] = mapped_column(Integer, nullable=False)
    property_spread_score: Mapped[int] = mapped_column(Integer, nullable=False)
    essential_function_score: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_scope_score: Mapped[int] = mapped_column(Integer, nullable=False)
    backend_scope_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_scope_score: Mapped[int] = mapped_column(Integer, nullable=False)
    deterioration_speed_score: Mapped[int] = mapped_column(Integer, nullable=False)

    #: `case.density_value` at the moment of scoring. NULL when no case had
    #: counted anything yet, which is not the same as "one apartment".
    confirmed_affected_unit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    blocker_codes: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    #: Per-criterion evidence the Agent cited, keyed by criterion name.
    evidence: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    #: What the Agent could not establish. Kept because "we did not know" is a
    #: different reason for a low score than "we checked and it was fine".
    unknown_facts: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)

    risk_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    score_priority: Mapped[Priority] = mapped_column(
        SQLEnum(Priority, name="priority_level_enum", native_enum=True, values_callable=enum_values), nullable=False
    )
    #: The floor a blocker imposed, or NULL when no blocker applied. Kept apart
    #: from `final_priority` so "a blocker decided this" stays visible.
    blocker_floor: Mapped[Priority | None] = mapped_column(
        SQLEnum(Priority, name="priority_level_enum", native_enum=True, values_callable=enum_values), nullable=True
    )
    final_priority: Mapped[Priority] = mapped_column(
        SQLEnum(Priority, name="priority_level_enum", native_enum=True, values_callable=enum_values), nullable=False
    )
    rubric_version: Mapped[str] = mapped_column(String(32), nullable=False)

    # --- what the case looked like when this row was written ---------------
    # Written *before* a P5 member is detached, which is the whole point: after
    # the detach there is no case left to ask, and without the snapshot the row
    # would read as though the ticket had always been on its own.
    case_id_snapshot: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    case_density_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Required when a human overruled the calculator; NULL otherwise.
    override_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    reviewed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    ticket: Mapped[Ticket] = relationship(back_populates="risk_assessments", foreign_keys=[ticket_id])
