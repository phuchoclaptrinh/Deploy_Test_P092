"""Coordinator request for resident supplemental information."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from src.database.base import Base
from src.models.enums import InformationRequestStatus

if TYPE_CHECKING:
    from src.database.models.ticket import Ticket


def enum_values(enum_class):
    return [member.value for member in enum_class]


class InformationRequest(Base):
    __tablename__ = "information_requests"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(
        ForeignKey("user_profiles.user_id", ondelete="RESTRICT"), nullable=False
    )
    request_message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[InformationRequestStatus] = mapped_column(
        SQLEnum(
            InformationRequestStatus,
            name="information_request_status_enum",
            native_enum=True,
            values_callable=enum_values,
        ),
        nullable=False,
        default=InformationRequestStatus.OPEN,
        server_default=InformationRequestStatus.OPEN.value,
    )
    resident_response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    ticket: Mapped[Ticket] = relationship(back_populates="information_requests")
