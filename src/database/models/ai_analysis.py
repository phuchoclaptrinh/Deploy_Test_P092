"""One row per finished analysis round, and the audit copy of what it decided."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base
from src.models.enums import AnalysisRunStatus, EmergencyDecision, EmergencyReviewStatus, Priority

if TYPE_CHECKING:
    from src.database.models.ai_agent_session import AIAnalysisSession
    from src.database.models.ticket import Ticket
    from src.database.models.ticket_risk_assessment import TicketRiskAssessment

JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")


def enum_values(enum_class):
    return [member.value for member in enum_class]


class AIAnalysisRun(Base):
    __tablename__ = "ai_analysis_runs"
    __table_args__ = (
        # §1.7.9: one successful finalization per analysis session. The database
        # is the last line of defence behind the row lock, so two concurrent
        # finalize calls cannot both write a run.
        Index(
            "uq_ai_analysis_runs_one_success_per_session",
            "analysis_session_id",
            unique=True,
            postgresql_where=text("status = 'SUCCEEDED' AND analysis_session_id IS NOT NULL"),
            sqlite_where=text("status = 'SUCCEEDED' AND analysis_session_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    text_model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vision_model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The answer: one Category for the ticket, plus the two evidence fields
    # that explain how it was reached. `text_category_id` and
    # `image_category_id` are never combined into `final_category_id` by
    # anything -- when they disagree the resident is asked which problem the
    # ticket is for, and their answer is what lands in `final_category_id`.
    final_category_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    text_category_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    image_category_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    # Why the ticket was classified the way it was. Shown to management, so it
    # is a full sentence rather than the 500-char note `confidence_notes` was.
    ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SAME_INCIDENT / DIFFERENT_INCIDENT / UNCERTAIN, and the sentence behind
    # it. Management reads `duplicate_reason` to understand an uncertain
    # verdict, which is a different question from `ai_reason` above.
    duplicate_verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)
    duplicate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # --- The mandatory human gate in front of the emergency priority. ---
    # P5 is the five-minute-SLA priority, so a P5 classification is not
    # published automatically: the run parks here and a coordinator either
    # confirms the emergency or downgrades it.
    #
    # Renamed from `p3_review_*` by `a1b2c3d4e5f7`. Only the band moved -- v2
    # inverted the scale, so the emergency that used to be P3 is now P5 -- and
    # keeping the old names would have left every screen reading "P3 review" on
    # a ticket showing P5.
    emergency_review_status: Mapped[EmergencyReviewStatus | None] = mapped_column(
        SQLEnum(
            EmergencyReviewStatus,
            name="emergency_review_status_enum",
            native_enum=True,
            values_callable=enum_values,
        ),
        nullable=True,
    )
    emergency_reviewed_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="SET NULL"), nullable=True
    )
    emergency_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    emergency_decision: Mapped[EmergencyDecision | None] = mapped_column(
        SQLEnum(
            EmergencyDecision,
            name="emergency_decision_enum",
            native_enum=True,
            values_callable=enum_values,
        ),
        nullable=True,
    )
    emergency_decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: What the pipeline scored before a human looked at it, kept so a
    #: downgrade never erases what the AI actually said.
    ai_priority_before_review: Mapped[Priority | None] = mapped_column(
        SQLEnum(Priority, name="priority_level_enum", native_enum=True, values_callable=enum_values), nullable=True
    )
    #: What downstream processing must use. Equal to the AI priority unless a
    #: coordinator downgraded it.
    effective_priority: Mapped[Priority | None] = mapped_column(
        SQLEnum(Priority, name="priority_level_enum", native_enum=True, values_callable=enum_values), nullable=True
    )
    # How the background grouping stage ended for this run.
    #: Widened to 50 in `d4e5f6a7b9ca`. WAITING_EMERGENCY_MANAGEMENT_REVIEW is
    #: 35 characters, and at String(30) PostgreSQL rejected the whole finalize
    #: transaction -- which silently rolled back a genuine P5 classification.
    #: SQLite does not enforce the limit, so only production ever saw it.
    grouping_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    grouping_candidates: Mapped[list[dict[str, object]] | None] = mapped_column(JSON_TYPE, nullable=True)
    # Superseded by the three *_category_id columns above and kept nullable for
    # audit only: rows written before the pipelines were merged still carry the
    # list-shaped text/image extraction they were reconciled from. Nothing
    # writes them any more.
    text_categories: Mapped[list[str] | None] = mapped_column(JSON_TYPE, nullable=True)
    image_categories: Mapped[list[str] | None] = mapped_column(JSON_TYPE, nullable=True)
    category_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: The assessment this run produced. Everything about how the priority was
    #: arrived at -- the five criteria, the blockers, the evidence, the score --
    #: lives on that row, and none of it is restated here.
    risk_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ticket_risk_assessments.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[AnalysisRunStatus] = mapped_column(
        SQLEnum(AnalysisRunStatus, name="analysis_run_status_enum", native_enum=True, values_callable=enum_values),
        nullable=False,
        default=AnalysisRunStatus.RUNNING,
        server_default=AnalysisRunStatus.RUNNING.value,
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contract_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v2", server_default="v2")
    analysis_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_analysis_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    exit_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_relevant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_confident: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    grouping: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE, nullable=True)
    # §7.2: the validated duplicate evidence exactly as finalize accepted it.
    # Kept as the audit copy of what the ticket columns were set from, so a
    # later coordinator split does not erase what the Agent claimed.
    duplicate: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE, nullable=True)
    # §1.7.9: replaying the same finalize returns the stored run; a different
    # payload under the same key is a 409. The hash covers the whole validated
    # payload, so "same" means the same decision, not the same key.
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # §1.7.4 / §3.3: the sanitized candidate set a coordinator needs to review a
    # DUPLICATE_UNCERTAIN or LIMIT_REACHED ticket by hand.
    duplicate_candidates: Mapped[list[dict[str, object]] | None] = mapped_column(JSON_TYPE, nullable=True)
    tool_usage: Mapped[dict[str, object] | None] = mapped_column(JSON_TYPE, nullable=True)
    category_catalog_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ticket: Mapped[Ticket] = relationship(back_populates="ai_analysis_runs", foreign_keys=[ticket_id])
    analysis_session: Mapped[AIAnalysisSession | None] = relationship()
    risk_assessment: Mapped[TicketRiskAssessment | None] = relationship(foreign_keys=[risk_assessment_id])
