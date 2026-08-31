"""Resident-question operations: asking, answering, and timing out.

Only eight kinds of question exist (`AgentQuestionKind`), and each is a
confirmation of something the resident already told us -- a Category, the
location they picked, whether a just-closed problem came back, or one of the
five risk criteria the Agent could not establish on its own. Nothing here ever
asks a resident to adjudicate a duplicate or a grouping: those are judgements
about other people's tickets, which the resident cannot see.

The five criterion questions replaced a single `SEVERITY_CONFIRMATION`. Asking
"how serious is it?" gets an answer about how upset the resident is; asking "is
water still coming out right now?" gets an answer that moves exactly one score.

`LOCATION_CONFIRMATION` is the one kind with a structured answer, and the
contract is enforced here rather than in the UI, because a client can call the
API directly:

* the answer must be one of the two fixed options -- keep, or choose another;
* "keep" must arrive with **no** `selected_location_id`;
* "choose another" must arrive **with** one, and it must still exist in the
  active location catalog;
* the ticket moves only on "choose another".

A location is never parsed out of free text, and `selected_location_id` is
refused outright on any other kind of question.

`CATEGORY_CONFIRMATION` is the other answer with a consequence beyond the
transcript. When the resident picks which problem the ticket is for, that
choice becomes the ticket's Category here and now. It is recorded in
`answer_payload` so the rest of the round -- including the next classification
pass -- treats it as settled rather than as one more opinion to weigh.
"""

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
from src.models.agent_schemas import (
    LOCATION_CHANGE_OPTION,
    LOCATION_KEEP_OPTION,
    AgentQuestionKind,
)
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
        question_kind: str,
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

        if self.emergency_gate_is_open(ticket_id):
            # A ticket waiting on an emergency review is waiting on a
            # coordinator, not on the resident.
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Phản ánh đang chờ duyệt mức khẩn cấp nên không hỏi thêm cư dân.",
                409,
            )

        try:
            kind = AgentQuestionKind(question_kind)
        except ValueError as exc:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Invalid Agent question kind.",
                400,
            ) from exc

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
            question_kind=kind.value,
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
                "question_kind": kind.value,
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
        selected_location_id: UUID | None = None,
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

        if question.question_kind == AgentQuestionKind.LOCATION_CONFIRMATION.value:
            # The one structured answer. Backend resolves and applies the id;
            # the free-text answer is never inspected for a location name.
            self._apply_location_answer(question, ticket, answer_type, answer_text, selected_location_id)
        elif selected_location_id is not None:
            # A location id on a Category or severity answer is a client bug at
            # best. Ignoring it silently would let the caller believe it moved
            # the ticket.
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "selected_location_id chỉ dùng cho câu hỏi xác nhận vị trí.",
                400,
            )
        elif question.question_kind == AgentQuestionKind.CATEGORY_CONFIRMATION.value:
            self._apply_category_answer(question, ticket, answer_type, answer_text)

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

    def _apply_location_answer(
        self,
        question: AIAgentQuestion,
        ticket,
        answer_type: str,
        answer_text: str | None,
        selected_location_id: UUID | None,
    ) -> None:
        """Move the ticket to the location the resident re-picked, or keep it.

        Two answers exist and both are explicit, so both are checked. Keeping
        the current location must not carry a replacement id -- a caller that
        sends one is contradicting itself, and guessing which half it meant is
        exactly the inference this design rules out. Choosing another location
        must carry one, from the fixed selector, validated against the active
        catalog so a stale or deactivated id is refused rather than quietly
        written onto the ticket.

        The resulting location is recorded on the answer either way, so the
        history management reads shows what the ticket ended up on and not
        merely which button was pressed.
        """
        if answer_type != "OPTION":
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Câu hỏi xác nhận vị trí phải được trả lời bằng một trong hai lựa chọn.",
                400,
            )

        choice = (answer_text or "").strip()
        payload: dict[str, object] = {"choice": choice}

        if choice == LOCATION_KEEP_OPTION:
            if selected_location_id is not None:
                raise DomainError(
                    INVALID_STATUS_TRANSITION,
                    "Giữ nguyên vị trí thì không được gửi kèm vị trí thay thế.",
                    400,
                )
            current = ticket.location
            question.answer_payload = {
                **payload,
                "selected_location_id": None,
                "final_location_id": str(ticket.location_id) if ticket.location_id else None,
                "final_location_label": current.label if current else None,
                "location_changed": False,
            }
            return

        if choice != LOCATION_CHANGE_OPTION:
            # `_validate_answer` already refused anything outside the offered
            # options; this catches a question built with the wrong option list
            # rather than trusting it.
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Lựa chọn không hợp lệ cho câu hỏi xác nhận vị trí.",
                400,
            )

        if selected_location_id is None:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Chọn vị trí khác thì phải kèm theo vị trí mới.",
                400,
            )

        location = self.catalog.get_location(selected_location_id)
        if location is None:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Vị trí đã chọn không còn hợp lệ.",
                400,
            )

        changed = ticket.location_id != location.id
        if changed:
            ticket.location_id = location.id
            ticket.version += 1
        question.answer_payload = {
            **payload,
            "selected_location_id": str(location.id),
            "selected_location_label": location.label,
            "final_location_id": str(location.id),
            "final_location_label": location.label,
            "location_changed": changed,
        }

    def _apply_category_answer(
        self,
        question: AIAgentQuestion,
        ticket,
        answer_type: str,
        answer_text: str | None,
    ) -> None:
        """Make the resident's choice the ticket's Category, here and now.

        This answers "which problem is this ticket for?" when the description
        and the photos pointed at different things. One ticket has one
        Category, the resident has just named it, and a later model pass must
        not talk them out of it -- so it is written to the ticket immediately
        and recorded on the answer, which is what the graph reads back to pin
        the Category for the rest of the round.

        The free-text fallback allowed on this question stays purely
        conversational: only an option chosen from the offered list settles a
        Category.
        """
        if answer_type != "OPTION":
            return

        chosen = (answer_text or "").strip().casefold()
        match = next(
            (
                item
                for item in (question.session.category_catalog_snapshot or [])
                if str(item.get("display_name", "")).strip().casefold() == chosen
            ),
            None,
        )
        if match is None:
            # The option came from this session's snapshot, so a miss means the
            # question and the snapshot disagree. Recording the answer without
            # acting on it is the honest outcome.
            question.answer_payload = {"choice": answer_text, "confirmed_category_id": None}
            return

        category_id = UUID(str(match["category_id"]))
        if ticket.category_id != category_id:
            ticket.category_id = category_id
            ticket.version += 1
        question.answer_payload = {
            "choice": answer_text,
            "confirmed_category_id": str(category_id),
            "confirmed_category_name": str(match.get("display_name") or ""),
        }

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
