"""Resident ticket business operations aligned with Self Dev v3."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models.attachment import TicketAttachment
from src.database.models.resident_profile import ResidentProfile
from src.database.models.resident_ticket_rate_limit import ResidentTicketRateLimit
from src.database.models.ticket import Ticket
from src.database.models.ticket_attachment_upload_session import TicketAttachmentUploadSession
from src.models.api.errors import (
    ATTACHMENT_NOT_FOUND,
    INFORMATION_REQUEST_NOT_FOUND,
    INVALID_ATTACHMENT,
    INVALID_LOCATION,
    INVALID_STATUS_TRANSITION,
    NO_ACTIVE_UNIT,
    STORAGE_NOT_CONFIGURED,
    TICKET_CREATE_RATE_LIMITED,
    TICKET_NOT_FOUND,
    TICKET_NOT_OWNED,
    DomainError,
)
from src.models.api.tickets import TicketCreateRequest, TicketSupplementRequest
from src.models.enums import (
    AttachmentType,
    ClassificationStatus,
    InformationRequestStatus,
    TicketLifecycleGroup,
    TicketStatus,
)
from src.repositories.attachment_repository import AttachmentRepository
from src.repositories.catalog_repository import CatalogRepository
from src.repositories.ticket_repository import TicketRepository
from src.repositories.upload_session_repository import UploadSessionRepository
from src.services.p3_review_guard import assert_p3_review_not_pending
from src.services.storage_service import StorageService
from src.services.ticket_visibility import is_reporter

TICKET_CREATE_SPAM_LIMIT = 10
TICKET_CREATE_SPAM_WINDOW = timedelta(hours=1)
TICKET_CREATE_SPAM_BLOCK_DURATION = timedelta(hours=12)
AI_REJECTION_SPAM_LIMIT = 3
AI_REJECTION_SPAM_WINDOW = timedelta(days=1)


class TicketService:
    def __init__(self, db: Session, storage_service: StorageService | None = None) -> None:
        self.db = db
        self.attachments = AttachmentRepository(db)
        self.catalog = CatalogRepository(db)
        self.tickets = TicketRepository(db)
        self.uploads = UploadSessionRepository(db)
        self.storage = storage_service or StorageService()

    def create_ticket(self, user_id: UUID, resident_profile: ResidentProfile | None, request: TicketCreateRequest) -> Ticket:
        if resident_profile is None:
            raise DomainError(NO_ACTIVE_UNIT, "Tài khoản chưa liên kết căn hộ.", 400)
        location = self.catalog.get_location(request.location_id)
        if location is None:
            raise DomainError(INVALID_LOCATION, "Vị trí không hợp lệ.", 400)

        try:
            self._register_ticket_creation_or_raise(user_id)
            sessions = self._lock_and_verify_upload_sessions(user_id, request.attachment_upload_ids)
            ticket = self.tickets.create_ticket(
                reporter_user_id=user_id,
                source_unit_id=resident_profile.unit_id,
                location_id=location.id,
                description=request.description,
            )
            self.tickets.append_status_history(
                ticket,
                from_status=None,
                to_status=TicketStatus.NEW,
                changed_by=user_id,
                reason="Resident created ticket.",
            )
            self.attachments.create_from_upload_sessions(ticket.id, user_id, sessions)
            self.uploads.mark_consumed(sessions)
            self.db.commit()
            return self.tickets.get_resident_ticket(resident_profile.unit_id, ticket.id) or ticket
        except Exception:
            self.db.rollback()
            raise

    def _register_ticket_creation_or_raise(self, user_id: UUID) -> None:
        now = datetime.now(UTC)
        window_started_at = now - TICKET_CREATE_SPAM_WINDOW
        row = self.db.scalar(
            select(ResidentTicketRateLimit)
            .where(ResidentTicketRateLimit.reporter_user_id == user_id)
            .with_for_update()
        )

        if row and row.blocked_until and row.blocked_until > now:
            self._raise_ticket_create_rate_limited(row.blocked_until)

        recent_count = self.tickets.count_created_by_reporter_since(user_id, window_started_at)
        if recent_count >= TICKET_CREATE_SPAM_LIMIT:
            blocked_until = now + TICKET_CREATE_SPAM_BLOCK_DURATION
            block_reason = "Resident created too many tickets in one hour."
            if row is None:
                self.db.add(
                    ResidentTicketRateLimit(
                        reporter_user_id=user_id,
                        window_started_at=window_started_at,
                        window_ticket_count=recent_count,
                        blocked_until=blocked_until,
                        block_reason=block_reason,
                    )
                )
            else:
                row.window_started_at = window_started_at
                row.window_ticket_count = recent_count
                row.blocked_until = blocked_until
                row.block_reason = block_reason
            self.db.flush()
            self.db.commit()
            self._raise_ticket_create_rate_limited(blocked_until)

        next_count = recent_count + 1
        blocked_until = None
        block_reason = None
        if next_count >= TICKET_CREATE_SPAM_LIMIT:
            blocked_until = now + TICKET_CREATE_SPAM_BLOCK_DURATION
            block_reason = "Resident created too many tickets in one hour."

        if row is None:
            self.db.add(
                ResidentTicketRateLimit(
                    reporter_user_id=user_id,
                    window_started_at=window_started_at,
                    window_ticket_count=next_count,
                    blocked_until=blocked_until,
                    block_reason=block_reason,
                )
            )
        else:
            row.window_started_at = window_started_at
            row.window_ticket_count = next_count
            row.blocked_until = blocked_until
            row.block_reason = block_reason

    def _raise_ticket_create_rate_limited(self, blocked_until: datetime) -> None:
        now = datetime.now(UTC)
        retry_after_seconds = max(1, int((blocked_until - now).total_seconds()))
        raise DomainError(
            TICKET_CREATE_RATE_LIMITED,
            "Bạn đã gửi quá nhiều phản ánh trong thời gian ngắn. Vui lòng thử lại sau.",
            429,
            {
                "blocked_until": blocked_until.isoformat(),
                "retry_after_seconds": retry_after_seconds,
                "limit": TICKET_CREATE_SPAM_LIMIT,
                "window_seconds": int(TICKET_CREATE_SPAM_WINDOW.total_seconds()),
            },
        )

    def register_ai_insufficient_input_rejection(self, user_id: UUID) -> None:
        now = datetime.now(UTC)
        window_started_at = now - AI_REJECTION_SPAM_WINDOW
        row = self.db.scalar(
            select(ResidentTicketRateLimit)
            .where(ResidentTicketRateLimit.reporter_user_id == user_id)
            .with_for_update()
        )
        if row is None:
            row = ResidentTicketRateLimit(
                reporter_user_id=user_id,
                window_started_at=now - TICKET_CREATE_SPAM_WINDOW,
                window_ticket_count=0,
            )
            self.db.add(row)

        started_at = row.ai_rejection_window_started_at
        if started_at and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        if started_at is None or started_at < window_started_at:
            row.ai_rejection_window_started_at = now
            row.ai_rejection_count = 1
        else:
            row.ai_rejection_count += 1

        if row.ai_rejection_count >= AI_REJECTION_SPAM_LIMIT:
            row.blocked_until = now + TICKET_CREATE_SPAM_BLOCK_DURATION
            row.block_reason = "Resident reached AI insufficient-input rejection limit."

    def list_my_tickets(
        self,
        resident_profile: ResidentProfile | None,
        viewer_user_id: UUID,
        *,
        page: int = 1,
        page_size: int = 20,
        status: TicketStatus | None = None,
        status_group: TicketLifecycleGroup | None = None,
        category_id: UUID | None = None,
        created_from=None,
        created_to=None,
        search: str | None = None,
    ):
        """Keyword-only: the positional form let `page` be passed twice.

        `viewer_user_id` is required, not optional: the apartment alone no
        longer decides what a resident sees, because a report still in the
        private AI phase belongs to its sender only.
        """
        if resident_profile is None:
            raise DomainError(NO_ACTIVE_UNIT, "Tài khoản chưa liên kết căn hộ.", 400)
        return self.tickets.list_resident_tickets(
            resident_profile.unit_id,
            viewer_user_id,
            page,
            page_size,
            status=status,
            status_group=status_group,
            category_id=category_id,
            created_from=created_from,
            created_to=created_to,
            search=search,
        )

    def get_ticket(
        self, resident_profile: ResidentProfile | None, ticket_id: UUID, viewer_user_id: UUID
    ) -> Ticket:
        """One report, as far as this account is allowed to see it.

        A housemate asking for a report that is still being analysed gets the
        same 404 as for an ID that does not exist, so the detail URL cannot be
        used to learn that a private ticket is there.
        """
        if resident_profile is None:
            raise DomainError(NO_ACTIVE_UNIT, "Tài khoản chưa liên kết căn hộ.", 400)
        ticket = self.tickets.get_resident_visible_ticket(resident_profile.unit_id, viewer_user_id, ticket_id)
        if ticket is None:
            raise DomainError(TICKET_NOT_FOUND, "Ticket không tồn tại.", 404)
        return ticket

    def cancel_ticket(self, user_id: UUID, resident_profile: ResidentProfile | None, ticket_id: UUID) -> Ticket:
        """Cancel is the sender's alone, enforced here rather than in the UI.

        `available_actions` hides the button from a housemate, but that is a
        hint; this check is the authorization.
        """
        if resident_profile is None:
            raise DomainError(NO_ACTIVE_UNIT, "Tài khoản chưa liên kết căn hộ.", 400)
        try:
            ticket = self.tickets.get_resident_visible_ticket(
                resident_profile.unit_id, user_id, ticket_id, lock=True
            )
            if ticket is None:
                raise DomainError(TICKET_NOT_FOUND, "Ticket không tồn tại.", 404)
            if not is_reporter(ticket, user_id):
                raise DomainError(TICKET_NOT_OWNED, "Chỉ người gửi phản ánh mới được hủy.", 403)
            # An emergency being reviewed right now is not the resident's to
            # withdraw. `_resident_actions` hides the button; this is the
            # authorization behind it.
            assert_p3_review_not_pending(self.db, ticket_id)
            if ticket.status != TicketStatus.NEW:
                raise DomainError(INVALID_STATUS_TRANSITION, "Chỉ có thể hủy ticket ở trạng thái Mới.", 409)
            old = ticket.status
            ticket.status = TicketStatus.CANCELLED
            ticket.cancelled_at = datetime.now(UTC)
            ticket.version += 1
            self.tickets.append_status_history(
                ticket,
                from_status=old,
                to_status=TicketStatus.CANCELLED,
                changed_by=user_id,
                reason="Resident cancelled ticket.",
            )
            self.db.commit()
            return self.tickets.get_resident_ticket(resident_profile.unit_id, ticket.id) or ticket
        except Exception:
            self.db.rollback()
            raise

    def supplement(
        self,
        user_id: UUID,
        resident_profile: ResidentProfile | None,
        ticket_id: UUID,
        request: TicketSupplementRequest,
    ) -> Ticket:
        if resident_profile is None:
            raise DomainError(NO_ACTIVE_UNIT, "Tài khoản chưa liên kết căn hộ.", 400)
        try:
            ticket = self.tickets.get_resident_visible_ticket(
                resident_profile.unit_id, user_id, ticket_id, lock=True
            )
            if ticket is None:
                raise DomainError(TICKET_NOT_FOUND, "Ticket không tồn tại.", 404)
            if ticket.status != TicketStatus.WAITING_RESIDENT_INFO:
                raise DomainError(INVALID_STATUS_TRANSITION, "Ticket không ở trạng thái chờ bổ sung thông tin.", 409)
            info_request = self.tickets.get_information_request(ticket.id, request.information_request_id, lock=True)
            if info_request is None or info_request.status != InformationRequestStatus.OPEN:
                raise DomainError(INFORMATION_REQUEST_NOT_FOUND, "Yêu cầu bổ sung không còn hiệu lực.", 404)
            sessions = self._lock_and_verify_upload_sessions(user_id, request.attachment_upload_ids)
            if request.description and request.description.strip():
                info_request.resident_response_text = request.description.strip()
            info_request.status = InformationRequestStatus.RESPONDED
            info_request.responded_at = datetime.now(UTC)
            self.attachments.create_from_upload_sessions(
                ticket.id,
                user_id,
                sessions,
                attachment_type=AttachmentType.RESIDENT_SUPPLEMENT,
            )
            self.uploads.mark_consumed(sessions)
            old = ticket.status
            ticket.status = TicketStatus.NEW
            ticket.classification_status = ClassificationStatus.PROCESSING
            ticket.category_id = None
            ticket.priority = None
            ticket.severity = None
            ticket.red_flag_detected = False
            ticket.score_total = None
            ticket.sla_due_at = None
            ticket.version += 1
            self.tickets.append_status_history(
                ticket,
                from_status=old,
                to_status=TicketStatus.NEW,
                changed_by=user_id,
                reason="Resident supplied requested information.",
            )
            self.db.commit()
            return self.tickets.get_resident_ticket(resident_profile.unit_id, ticket.id) or ticket
        except Exception:
            self.db.rollback()
            raise

    def get_attachment_download_url(
        self,
        resident_profile: ResidentProfile | None,
        ticket_id: UUID,
        attachment_id: UUID,
        viewer_user_id: UUID,
    ) -> tuple[TicketAttachment, str, int]:
        """Authorize the parent ticket *before* the attachment is looked up.

        Doing it the other way round would sign a URL for a photo on a report
        the caller may not see yet.
        """
        ticket = self.get_ticket(resident_profile, ticket_id, viewer_user_id)
        attachment = self.attachments.get_attachment(ticket.id, attachment_id)
        if attachment is None:
            raise DomainError(ATTACHMENT_NOT_FOUND, "Attachment không tồn tại.", 404)
        signed_url = self.storage.create_signed_download_url(attachment.object_path)
        return attachment, signed_url, self.storage.settings.supabase_signed_download_ttl_seconds

    def _lock_and_verify_upload_sessions(
        self, owner_user_id: UUID, upload_ids: list[UUID]
    ) -> list[TicketAttachmentUploadSession]:
        if not upload_ids:
            return []
        sessions = self.uploads.lock_upload_sessions(upload_ids)
        by_id = {row.id: row for row in sessions}
        if set(by_id) != set(upload_ids):
            raise DomainError(INVALID_ATTACHMENT, "Upload session không hợp lệ.", 400)
        ordered = [by_id[upload_id] for upload_id in upload_ids]
        now = datetime.now(UTC)
        verified_at = now
        for session in ordered:
            if session.owner_user_id != owner_user_id:
                raise DomainError(INVALID_ATTACHMENT, "Upload session không hợp lệ.", 400)
            if session.status != "pending" or session.consumed_at is not None:
                raise DomainError(INVALID_ATTACHMENT, "Upload session đã được sử dụng.", 400)
            expires_at = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                raise DomainError(INVALID_ATTACHMENT, "Upload session đã hết hạn.", 400)
            if not self.storage.is_owned_ticket_attachment_path(session.storage_path, owner_user_id):
                raise DomainError(INVALID_ATTACHMENT, "Upload session không hợp lệ.", 400)
            try:
                verified = self.storage.verify_uploaded_object(
                    session.storage_path,
                    expected_mime_type=session.mime_type,
                    expected_file_size=session.file_size,
                )
            except DomainError as exc:
                if exc.code == STORAGE_NOT_CONFIGURED:
                    raise
                raise
            verified_at = verified.verified_at
        self.uploads.mark_verified(ordered, verified_at)
        return ordered
