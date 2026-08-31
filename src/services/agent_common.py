"""Shared policy and persistence helpers for the Backend-owned Agent services."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from src.database.models.ai_agent_session import AIAgentToolCall, AIAnalysisSession
from src.database.models.audit_log import AuditLog
from src.database.models.location import Location
from src.database.models.notification import Notification
from src.database.models.resident_profile import ResidentProfile
from src.database.models.ticket import Ticket
from src.models.api.errors import NO_ACTIVE_UNIT, TICKET_NOT_FOUND, DomainError
from src.models.enums import (
    ClassificationStatus,
    InvalidReason,
    NotificationChannel,
    NotificationStatus,
    TicketStatus,
)
from src.repositories.attachment_repository import AttachmentRepository
from src.repositories.catalog_repository import CatalogRepository
from src.repositories.ticket_repository import TicketRepository
from src.repositories.upload_session_repository import UploadSessionRepository
from src.request_context import request_id_context
from src.services.storage_service import StorageService

MAX_TOOL_CALLS = 5
MAX_ASK_ROUNDS = 3
MAX_WAIT_SECONDS = 300

#: The only Categories a spreading incident can be built from. Everything else
#: is a fault on one asset: two broken locks on two floors are two jobs, not one
#: case. Water and damp spread through the structure, and a power or
#: internet/TV outage spreads along the riser feeding several apartments.
GROUPING_CODES = {"WATER", "WALL_DAMP", "POWER_OUTAGE", "INTERNET_TV"}


def reference_code(ticket_id: UUID) -> str:
    """The short code a ticket is known by outside the database.

    Shared rather than duplicated per module: a candidate snapshot, a duplicate
    notification and the coordinator panel all show it, and three independent
    formatters would eventually disagree about the same ticket.
    """
    return f"PA-{str(ticket_id).replace('-', '')[:6].upper()}"


# Digit runs shaped like a phone number (7+ digits, optional separators) and
# tokens shaped like this codebase's unit codes (e.g. "A-1203") -- two of the
# identifying patterns contract §2.2 names (reporter, their unit). A leading
# Vietnamese self-introduction clause ("Tôi là ...", "Em tên là ...") is the
# common third way a name enters a description, so it is stripped too; this
# is a bounded heuristic, not full name detection, which is why the excerpt
# stays short and is never the raw, unredacted description.
_PHONE_LIKE_PATTERN = re.compile(r"\+?\d(?:[\s().-]?\d){6,}")
_UNIT_CODE_LIKE_PATTERN = re.compile(r"\b[A-Za-z]{1,3}-\d{2,5}\b")
_SELF_INTRO_PATTERN = re.compile(
    r"^\s*(?:t[ôo]i|em|anh|ch[ịi])\s+(?:l[àa]|t[êe]n\s+l[àa])\s+[^,.;]+[,.;]\s*",
    re.IGNORECASE,
)
_EMAIL_LIKE_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
MAX_PHENOMENON_EXCERPT_CHARS = 160


def redact_phenomenon_excerpt(description: str | None) -> str:
    """A short, identity-stripped slice of a ticket's own words.

    Used to give a duplicate/grouping judge model enough to confirm "same
    phenomenon" (contract §4.3b criterion 4) instead of only Category,
    location label, status and Priority, which never states the phenomenon at
    all and so can never clear that bar (see `_safe_summary`). Bounded and
    redacted rather than the raw description: a leading self-introduction
    clause, phone-number-shaped digit runs, email-shaped tokens and
    unit-code-shaped tokens are stripped, and the result is capped well short
    of a full sentence, so it is evidence about the fault, not a copy of the
    report. Not a substitute for real name-entity redaction -- a name
    mentioned outside these patterns can still slip through -- so this stays
    a short excerpt, never the full text.
    """
    text = (description or "").strip()
    if not text:
        return ""
    text = _SELF_INTRO_PATTERN.sub("", text)
    text = _PHONE_LIKE_PATTERN.sub("[số]", text)
    text = _EMAIL_LIKE_PATTERN.sub("[email]", text)
    text = _UNIT_CODE_LIKE_PATTERN.sub("[căn hộ]", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= MAX_PHENOMENON_EXCERPT_CHARS:
        return text
    head = text[:MAX_PHENOMENON_EXCERPT_CHARS].rsplit(" ", 1)[0]
    return f"{head}…" if head else text[:MAX_PHENOMENON_EXCERPT_CHARS]


class AgentServiceBase:
    def __init__(self, db: Session, storage: StorageService | None = None) -> None:
        self.db = db
        self.storage = storage or StorageService()
        self.catalog = CatalogRepository(db)
        self.tickets = TicketRepository(db)
        self.uploads = UploadSessionRepository(db)
        self.attachments = AttachmentRepository(db)

    def _session(
        self,
        session_id: UUID,
        *,
        lock: bool = False,
    ) -> AIAnalysisSession:
        query = select(AIAnalysisSession).where(AIAnalysisSession.id == session_id)
        if lock:
            query = query.with_for_update()
        session = self.db.scalar(query)
        if session is None:
            raise DomainError(
                TICKET_NOT_FOUND,
                "Analysis session not found.",
                404,
            )
        return session

    def _ticket(self, ticket_id: UUID) -> Ticket:
        ticket = self.db.scalar(
            select(Ticket)
            .where(Ticket.id == ticket_id)
            .options(
                joinedload(Ticket.category),
                joinedload(Ticket.location).joinedload(Location.floor),
                selectinload(Ticket.ai_analysis_runs),
            )
        )
        if ticket is None:
            raise DomainError(TICKET_NOT_FOUND, "Ticket not found.", 404)
        return ticket

    def _reporter_ticket(
        self,
        resident_profile: ResidentProfile | None,
        viewer_user_id: UUID,
        ticket_id: UUID,
    ) -> Ticket:
        """The AI conversation belongs to the person who started it.

        Reading the pending question and answering it are both reporter-only:
        a housemate must not be able to steer the analysis of a report they did
        not send, and during the private phase they must not even learn it
        exists. Both failures return the same 404 as a missing ticket.
        """
        if resident_profile is None:
            raise DomainError(
                NO_ACTIVE_UNIT,
                "Resident has no active unit.",
                400,
            )
        ticket = self.tickets.get_resident_ticket(
            resident_profile.unit_id,
            ticket_id,
        )
        if ticket is None or ticket.reporter_user_id != viewer_user_id:
            raise DomainError(TICKET_NOT_FOUND, "Ticket not found.", 404)
        return ticket

    @staticmethod
    def _validate_session_ticket(
        session: AIAnalysisSession,
        ticket_id: UUID,
    ) -> None:
        if session.ticket_id != ticket_id:
            raise DomainError(
                TICKET_NOT_FOUND,
                "Ticket does not belong to analysis session.",
                404,
            )

    def _increment_tool(self, session: AIAnalysisSession) -> None:
        from src.models.api.errors import INVALID_STATUS_TRANSITION

        if session.status != "RUNNING":
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Analysis session is not running.",
                409,
            )
        if session.total_tool_calls >= MAX_TOOL_CALLS:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Agent tool budget exceeded.",
                409,
            )
        session.total_tool_calls += 1
        session.updated_at = datetime.now(UTC)
        self.db.flush()

    def _log_tool(
        self,
        session: AIAnalysisSession,
        tool_name: str,
        request: dict[str, object],
        response: dict[str, object],
        success: bool = True,
    ) -> None:
        """Append one call to the session log.

        The sequence comes from the log itself rather than from
        `total_tool_calls`, because the two are not the same thing: the
        background grouping stage records calls that deliberately do not spend
        the resident-facing budget, and numbering those off the counter would
        collide with an earlier row under
        `uq_ai_agent_tool_calls_session_sequence`.
        """
        highest = self.db.scalar(
            select(func.max(AIAgentToolCall.sequence)).where(AIAgentToolCall.session_id == session.id)
        )
        self.db.add(
            AIAgentToolCall(
                session_id=session.id,
                sequence=int(highest or 0) + 1,
                tool_name=tool_name,
                sanitized_request=request,
                sanitized_response=response,
                success=success,
            )
        )

    @staticmethod
    def _safe_summary(ticket: Ticket) -> str:
        """A one-line description of another unit's ticket, built mostly from
        Backend-owned structured data plus one redacted phenomenon excerpt.

        Contract §2.2 forbids returning the reporter, their unit, the full text
        or any photo. It permits a "sanitized summary": the Category, the
        shared asset label, the current state and the Priority always were
        enough to judge "same asset, still being worked on" (§1.5 items 4-6),
        but never enough to judge "same phenomenon" (§4.3b criterion 4) --
        without it, a duplicate/grouping judge can never clear that bar and
        systematically hedges to UNCERTAIN. `redact_phenomenon_excerpt` adds a
        short, capped slice of the resident's own words with phone numbers,
        emails and unit codes stripped, which is evidence about the fault
        rather than a copy of the report.
        """
        parts = [ticket.category.display_name if ticket.category else "Chưa phân loại"]
        if ticket.location is not None and ticket.location.label:
            parts.append(ticket.location.label)
        parts.append(f"trạng thái {ticket.status.value}")
        if ticket.priority is not None:
            parts.append(ticket.priority.value)
        excerpt = redact_phenomenon_excerpt(ticket.description)
        if excerpt:
            parts.append(f'hiện tượng: "{excerpt}"')
        return " · ".join(parts)

    def emergency_gate_is_open(self, ticket_id) -> bool:
        """True while a ticket is held at the emergency review gate.

        Delegates to `emergency_review_guard`, which is where the question is answered
        for the whole codebase -- coordinator services ask it too, and two
        implementations that could drift is exactly how a gate develops a hole.
        """
        from src.services.emergency_review_guard import emergency_review_is_pending

        return emergency_review_is_pending(self.db, ticket_id)

    @staticmethod
    def _snapshot_by_id(
        session: AIAnalysisSession,
    ) -> dict[str, dict[str, object]]:
        return {
            str(item["category_id"]): item
            for item in (session.category_catalog_snapshot or [])
        }

    @staticmethod
    def _density(ticket: Ticket, related: list[Ticket]) -> int:
        return len(
            {
                ticket.source_unit_id,
                *(item.source_unit_id for item in related),
            }
        )

    @staticmethod
    def _same_building_adjacent_floor(
        current: Ticket,
        candidate: Ticket,
    ) -> bool:
        if current.location is None or candidate.location is None:
            return False
        if current.location.floor is None or candidate.location.floor is None:
            return False
        return (
            abs(
                current.location.floor.adjacency_index
                - candidate.location.floor.adjacency_index
            )
            <= 1
        )

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    def _invalidate_ticket(
        self,
        ticket: Ticket,
        reason: str,
        *,
        changed_by: UUID | None = None,
        invalid_reason: InvalidReason | None = None,
        notification_body: str = "Phản ánh hiện tại không đủ điều kiện xử lý.",
    ) -> None:
        """End a report as INVALID.

        `reason` is the internal audit string and stays in the status history;
        residents only ever see `notification_body` and the friendly text derived
        from `invalid_reason`.
        """
        old = ticket.status
        ticket.status = TicketStatus.INVALID
        ticket.classification_status = ClassificationStatus.FAILED
        ticket.priority = None
        ticket.risk_score = None
        ticket.sla_due_at = None
        if invalid_reason is not None:
            ticket.invalid_reason = invalid_reason.value
        ticket.version += 1

        self.tickets.append_status_history(
            ticket,
            from_status=old,
            to_status=TicketStatus.INVALID,
            changed_by=changed_by,
            reason=reason,
        )
        self._notify_unit(
            ticket,
            "TICKET_INVALID",
            "Vui lòng gửi lại phản ánh",
            notification_body,
        )

    def _notify_unit(
        self,
        ticket: Ticket,
        event: str,
        title: str,
        body: str,
        payload_extra: dict[str, object] | None = None,
    ) -> None:
        """§8.1: every account in the reporting apartment, one notification each.

        `payload_extra` is for reduced facts about *another* ticket — a master
        reference code, its status, its due time. Anything identifying the other
        reporter must never be passed here.
        """
        recipients = list(
            self.db.scalars(
                select(ResidentProfile.user_id).where(
                    ResidentProfile.unit_id == ticket.source_unit_id
                )
            )
        )
        for user_id in recipients:
            self.db.add(
                Notification(
                    recipient_user_id=user_id,
                    ticket_id=ticket.id,
                    notification_type=event,
                    channel=NotificationChannel.IN_APP,
                    title=title,
                    body=body,
                    payload={
                        "ticket_id": str(ticket.id),
                        "status": ticket.status.value,
                        **(payload_extra or {}),
                    },
                    status=NotificationStatus.PENDING,
                )
            )

    def _notify_coordinators(
        self,
        ticket: Ticket,
        event: str,
        title: str,
        body: str,
        payload_extra: dict[str, object] | None = None,
    ) -> None:
        """Management is told about anything that needs a human decision.

        `payload_extra` carries operational facts -- a candidate count, a short
        reason -- and never another resident's identity or a raw model response.
        """
        from src.database.models.user_profile import UserProfile
        from src.models.enums import UserRole

        recipients = list(
            self.db.scalars(
                select(UserProfile.user_id).where(
                    UserProfile.role == UserRole.COORDINATOR,
                    UserProfile.is_active.is_(True),
                )
            )
        )
        for user_id in recipients:
            self.db.add(
                Notification(
                    recipient_user_id=user_id,
                    ticket_id=ticket.id,
                    notification_type=event,
                    channel=NotificationChannel.IN_APP,
                    title=title,
                    body=body,
                    payload={"ticket_id": str(ticket.id), **(payload_extra or {})},
                    status=NotificationStatus.PENDING,
                )
            )

    def duplicate_candidates_from_log(self, session: AIAnalysisSession) -> list[dict[str, object]]:
        """The sanitized duplicate candidate set one session saw.

        Lives on the base class because three callers need the same list from
        three directions: the search that produced it, the finalize whitelist a
        master must appear in, and the coordinator panel that renders it.
        """
        seen: dict[str, dict[str, object]] = {}
        calls = self.db.scalars(
            select(AIAgentToolCall)
            .where(
                AIAgentToolCall.session_id == session.id,
                AIAgentToolCall.tool_name == "search_related_tickets",
            )
            .order_by(AIAgentToolCall.sequence)
        )
        for call in calls:
            if (call.sanitized_request or {}).get("purpose") != "DUPLICATE":
                continue
            for row in (call.sanitized_response or {}).get("candidates", []):
                seen[str(row.get("ticket_id"))] = row
        return list(seen.values())

    def _audit(
        self,
        actor_user_id: UUID | None,
        action: str,
        entity_id: UUID,
        data: dict[str, object],
    ) -> None:
        self.db.add(
            AuditLog(
                actor_user_id=actor_user_id,
                actor_role=(
                    "SYSTEM"
                    if actor_user_id is None
                    else "COORDINATOR"
                ),
                action=action,
                entity_type="TICKET",
                entity_id=entity_id,
                after_data=data,
                request_id=(
                    UUID(request_id)
                    if (request_id := request_id_context.get())
                    else None
                ),
            )
        )
