"""Agent v3 resident-question operations."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from src.database.models.ai_agent_session import (
    AIAgentQuestion,
    AIAnalysisSession,
)
from src.database.models.attachment import TicketAttachment
from src.database.models.resident_profile import ResidentProfile
from src.models.api.errors import (
    AGENT_BUDGET_EXHAUSTED,
    INVALID_ATTACHMENT,
    INVALID_STATUS_TRANSITION,
    TICKET_NOT_FOUND,
    DomainError,
)
from src.models.enums import AttachmentType, ImageQualityStatus
from src.services.agent_common import (
    MAX_ASK_ROUNDS,
    MAX_TOOL_CALLS,
    MAX_WAIT_SECONDS,
    AgentServiceBase,
)
from src.services.ticket_service import TicketService


class AgentQuestionService(AgentServiceBase):
    def create_question(
        self,
        session_id: UUID,
        *,
        ticket_id: UUID,
        question_type: str,
        question_text: str,
        options: list[str] | None = None,
        allow_free_text_fallback: bool = False,
    ) -> AIAgentQuestion:
        session = self._session(session_id, lock=True)
        self._validate_session_ticket(session, ticket_id)

        if session.status != "RUNNING":
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Analysis session is not running.",
                409,
            )

        if question_type not in {"MULTIPLE_CHOICE", "FREE_TEXT"}:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Invalid Agent question type.",
                400,
            )

        if question_type == "MULTIPLE_CHOICE" and not options:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Multiple-choice question requires options.",
                400,
            )

        if (
            session.total_tool_calls >= MAX_TOOL_CALLS
            or session.ask_resident_rounds >= MAX_ASK_ROUNDS
            or session.ask_resident_elapsed_seconds >= MAX_WAIT_SECONDS
        ):
            raise DomainError(
                AGENT_BUDGET_EXHAUSTED,
                "Agent question limit reached.",
                409,
            )

        self._increment_tool(session)

        now = datetime.now(UTC)
        remaining = MAX_WAIT_SECONDS - session.ask_resident_elapsed_seconds
        question = AIAgentQuestion(
            session_id=session.id,
            ticket_id=ticket_id,
            question_type=question_type,
            question_text=question_text,
            options=options,
            allow_free_text_fallback=allow_free_text_fallback,
            round_number=session.ask_resident_rounds + 1,
            expires_at=now + timedelta(seconds=remaining),
        )

        session.ask_resident_rounds += 1
        session.waiting_deadline_at = question.expires_at

        self.db.add(question)
        self.db.flush()

        self._log_tool(
            session,
            "ask_resident",
            {
                "ticket_id": str(ticket_id),
                "question_type": question_type,
            },
            {
                "question_id": str(question.id),
                "round_number": question.round_number,
            },
        )

        self.db.commit()
        self.db.refresh(question)
        return question

    def pending_resident_question(
        self,
        resident_profile: ResidentProfile | None,
        ticket_id: UUID,
        viewer_user_id: UUID,
    ) -> AIAgentQuestion | None:
        ticket = self._reporter_ticket(resident_profile, viewer_user_id, ticket_id)
        return self.db.scalar(
            select(AIAgentQuestion)
            .where(
                AIAgentQuestion.ticket_id == ticket.id,
                AIAgentQuestion.status == "PENDING",
            )
            .join(
                AIAnalysisSession,
                AIAnalysisSession.id == AIAgentQuestion.session_id,
            )
            .where(AIAnalysisSession.status == "RUNNING")
            .order_by(AIAgentQuestion.asked_at.desc())
        )

    def answer_question(
        self,
        resident_profile: ResidentProfile | None,
        ticket_id: UUID,
        question_id: UUID,
        viewer_user_id: UUID,
        *,
        answer_type: str,
        answer_text: str | None = None,
        upload_id: UUID | None = None,
    ) -> AIAgentQuestion:
        ticket = self._reporter_ticket(resident_profile, viewer_user_id, ticket_id)

        question = self.db.scalar(
            select(AIAgentQuestion)
            .where(
                AIAgentQuestion.id == question_id,
                AIAgentQuestion.ticket_id == ticket.id,
            )
            .options(joinedload(AIAgentQuestion.session))
            .with_for_update(of=AIAgentQuestion)
        )

        if question is None or question.status != "PENDING":
            raise DomainError(
                TICKET_NOT_FOUND,
                "Agent question not found.",
                404,
            )

        if question.session.status != "RUNNING":
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Analysis session is not running.",
                409,
            )

        now = datetime.now(UTC)
        expires_at = self._as_utc(question.expires_at)

        # A late answer is rejected, but the timeout side-effect must still be
        # persisted immediately; otherwise the ticket could remain PROCESSING.
        if expires_at and expires_at <= now:
            question.status = "EXPIRED"
            question.session.status = "TIMED_OUT"
            question.session.completed_at = now
            question.session.waiting_deadline_at = None
            question.session.ask_resident_elapsed_seconds = MAX_WAIT_SECONDS

            self._invalidate_ticket(
                ticket,
                "Agent resident response timeout.",
            )
            ticket.invalid_reason = "RESIDENT_RESPONSE_TIMEOUT"
            self.db.commit()

            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Agent question expired.",
                409,
            )

        self._validate_answer(
            question,
            answer_type,
            answer_text,
        )

        if answer_type == "NEW_PHOTO":
            if upload_id is None:
                raise DomainError(
                    INVALID_ATTACHMENT,
                    "Upload session is required.",
                    400,
                )

            upload = self._verify_photo_upload(
                ticket.reporter_user_id,
                upload_id,
            )

            self.db.add(
                TicketAttachment(
                    ticket_id=ticket.id,
                    attachment_type=AttachmentType.RESIDENT_SUPPLEMENT,
                    storage_bucket="ticket-attachments",
                    object_path=upload.storage_path,
                    mime_type=upload.mime_type,
                    size_bytes=upload.file_size,
                    uploaded_by=ticket.reporter_user_id,
                    image_quality_status=ImageQualityStatus.READABLE,
                )
            )
            self.uploads.mark_consumed([upload])
            question.answer_upload_id = upload_id
        else:
            question.answer_text = (answer_text or "").strip()

        asked_at = self._as_utc(question.asked_at) or now
        elapsed = int((now - asked_at).total_seconds())

        question.session.ask_resident_elapsed_seconds = min(
            MAX_WAIT_SECONDS,
            question.session.ask_resident_elapsed_seconds
            + max(0, elapsed),
        )
        question.answer_type = answer_type
        question.status = "ANSWERED"
        question.answered_at = now
        question.session.updated_at = now
        question.session.waiting_deadline_at = None

        self.db.commit()
        self.db.refresh(question)
        return question

    def handle_timeouts(
        self,
        now: datetime | None = None,
    ) -> int:
        now = now or datetime.now(UTC)

        sessions = list(
            self.db.scalars(
                select(AIAnalysisSession)
                .where(
                    AIAnalysisSession.status == "RUNNING",
                    AIAnalysisSession.waiting_deadline_at <= now,
                )
                .options(
                    joinedload(AIAnalysisSession.ticket),
                    selectinload(AIAnalysisSession.questions),
                )
            )
        )

        timed_out = 0

        for session in sessions:
            pending = [
                question
                for question in session.questions
                if (
                    question.status == "PENDING"
                    and question.expires_at
                    and self._as_utc(question.expires_at) <= now
                )
            ]

            if not pending:
                session.waiting_deadline_at = None
                continue

            if any(
                run.red_flag_text or run.red_flag_signal
                for run in session.ticket.ai_analysis_runs
            ):
                continue

            session.status = "TIMED_OUT"
            session.completed_at = now
            session.waiting_deadline_at = None
            session.ask_resident_elapsed_seconds = MAX_WAIT_SECONDS

            for question in pending:
                question.status = "EXPIRED"

            self._invalidate_ticket(
                session.ticket,
                "Agent resident response timeout.",
            )
            session.ticket.invalid_reason = "RESIDENT_RESPONSE_TIMEOUT"
            timed_out += 1

        self.db.commit()
        return timed_out

    @staticmethod
    def _validate_answer(
        question: AIAgentQuestion,
        answer_type: str,
        answer_text: str | None,
    ) -> None:
        if answer_type == "OPTION":
            if (
                question.question_type != "MULTIPLE_CHOICE"
                or answer_text not in (question.options or [])
            ):
                raise DomainError(
                    INVALID_STATUS_TRANSITION,
                    "Answer option is not valid for this question.",
                    400,
                )

        elif answer_type == "FREE_TEXT":
            if (
                question.question_type == "MULTIPLE_CHOICE"
                and not question.allow_free_text_fallback
            ):
                raise DomainError(
                    INVALID_STATUS_TRANSITION,
                    "Free-text fallback is not allowed for this question.",
                    400,
                )

            if not (answer_text or "").strip():
                raise DomainError(
                    INVALID_STATUS_TRANSITION,
                    "Free-text answer is required.",
                    400,
                )

        elif answer_type != "NEW_PHOTO":
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Invalid Agent answer type.",
                400,
            )

    def _verify_photo_upload(
        self,
        owner_user_id: UUID,
        upload_id: UUID,
    ):
        sessions = TicketService(
            self.db,
            self.storage,
        )._lock_and_verify_upload_sessions(
            owner_user_id,
            [upload_id],
        )

        if len(sessions) != 1:
            raise DomainError(
                INVALID_ATTACHMENT,
                "Upload session not found.",
                400,
            )

        return sessions[0]
