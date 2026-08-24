"""Backend-owned AI Agent v3 session audit tables."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.ticket import Ticket

JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")


class AIAnalysisSession(Base):
    __tablename__ = "ai_analysis_sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="RUNNING", server_default="RUNNING")
    category_catalog_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category_catalog_snapshot: Mapped[list[dict[str, object]] | None] = mapped_column(JSON_TYPE, nullable=True)
    total_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ask_resident_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ask_resident_elapsed_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    waiting_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ticket: Mapped[Ticket] = relationship()
    tool_calls: Mapped[list[AIAgentToolCall]] = relationship(back_populates="session", cascade="all, delete-orphan")
    questions: Mapped[list[AIAgentQuestion]] = relationship(back_populates="session", cascade="all, delete-orphan")


class AIAgentToolCall(Base):
    __tablename__ = "ai_agent_tool_calls"
    __table_args__ = (UniqueConstraint("session_id", "sequence", name="uq_ai_agent_tool_calls_session_sequence"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("ai_analysis_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    sanitized_request: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    sanitized_response: Mapped[dict[str, object]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    session: Mapped[AIAnalysisSession] = relationship(back_populates="tool_calls")


class AIAgentQuestion(Base):
    __tablename__ = "ai_agent_questions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("ai_analysis_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    question_type: Mapped[str] = mapped_column(String(30), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[str] | None] = mapped_column(JSON_TYPE, nullable=True)
    allow_free_text_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", server_default="PENDING")
    answer_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_upload_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[AIAnalysisSession] = relationship(back_populates="questions")
