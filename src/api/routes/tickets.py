"""Resident ticket APIs aligned with Self Dev v3."""

from datetime import datetime, time, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Path, Query, Request, status
from sqlalchemy.orm import Session

from src.agents.service import resume_analysis, run_analysis
from src.api.dependencies.auth import CurrentActor, require_resident
from src.api.dependencies.database import get_db
from src.api.routes.storage import get_storage_service
from src.database.models.attachment import TicketAttachment
from src.database.models.ticket import Ticket
from src.models.agent_schemas import P3ReviewStatus
from src.models.api.common import ApiResponse
from src.models.api.tickets import (
    AgentQuestionAnswerRequest,
    AgentQuestionResponse,
    AttachmentDownloadUrlResponse,
    ResidentTechnicianSummary,
    ResidentTicketResponse,
    ResidentTimelineItem,
    TicketAttachmentResponse,
    TicketCreatedResponse,
    TicketCreateRequest,
    TicketListResponse,
    TicketSupplementRequest,
)
from src.models.display import resident_progress_text
from src.models.enums import AssignmentStatus, TicketLifecycleGroup, TicketStatus
from src.services.agent_backend_service import AgentBackendService
from src.services.resident_lifecycle import (
    lifecycle_group,
    resident_invalid_reason_text,
    resident_timeline_reason,
)
from src.services.scoring_service import (
    priority_description,
    resident_status_text,
)
from src.services.storage_service import StorageService
from src.services.ticket_service import TicketService
from src.services.ticket_visibility import is_reporter

router = APIRouter()


def bounded_page_size(page_size: int = Query(default=20, ge=1, le=100)) -> int:
    return page_size


#: Residents pick dates on a Vietnam wall clock, so "đến ngày 23/08" must cover
#: 23/08 00:00 to 23/08 23:59:59.999999 Vietnam time, not UTC.
VIETNAM_TZ = timezone(timedelta(hours=7))


def _inclusive_day_end(value: datetime | None) -> datetime | None:
    """Extend a date-only bound to the end of that day in Vietnam time.

    A value that already carries a time of day is passed through untouched, so
    an explicit timestamp filter still means exactly what it says.
    """
    if value is None:
        return None
    if value.timetz() != time(0, 0, tzinfo=value.tzinfo):
        return value
    local = value if value.tzinfo else value.replace(tzinfo=VIETNAM_TZ)
    return local.astimezone(VIETNAM_TZ).replace(
        hour=23, minute=59, second=59, microsecond=999999
    )


def _start_of_day(value: datetime | None) -> datetime | None:
    """Read a naive date-only lower bound as Vietnam time rather than UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=VIETNAM_TZ)


def _ok(request: Request, data, meta: dict[str, object] | None = None):
    return {"data": data, "meta": meta or {}, "error": None, "request_id": request.state.request_id}


def _resident_actions(ticket: Ticket, viewer_user_id: UUID | None) -> list[str]:
    """Actions the Resident UI may offer *this* account.

    A UI hint, never the authorization: `CANCEL` is offered only to the sender,
    and `TicketService.cancel_ticket` checks the same thing again. A housemate
    reading a published report gets an empty list here and a 403 if they post
    the cancel anyway.

    `SUPPLEMENT_INFORMATION` is deliberately never returned: Building Management
    no longer asks for more information after a report arrives. A rejected report
    ends as INVALID and the resident sends a new one. There is no duplicate
    appeal action either - a linked duplicate is informational.
    """
    actions: list[str] = []
    if _awaiting_p3_review(ticket):
        # A report held at the emergency gate is mid-decision. Cancelling it
        # from the app would change the outcome a coordinator is looking at.
        return actions
    if ticket.status == TicketStatus.NEW and viewer_user_id is not None and is_reporter(ticket, viewer_user_id):
        actions.append("CANCEL")
    return actions


def _awaiting_p3_review(ticket: Ticket) -> bool:
    """Whether this report is waiting on the urgent-review gate.

    The resident is told, because "we are looking at this right now" is more
    useful than a report that appears to have stalled, but they are told
    without any of the reasoning behind it.
    """
    if not ticket.ai_analysis_runs:
        return False
    run = max(ticket.ai_analysis_runs, key=lambda item: item.run_number)
    return run.p3_review_status == P3ReviewStatus.PENDING.value


def _attachment_response(ticket_id: UUID, attachment: TicketAttachment) -> TicketAttachmentResponse:
    return TicketAttachmentResponse(
        id=attachment.id,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
        attachment_type=attachment.attachment_type.value,
        download_url_endpoint=f"/api/v1/tickets/{ticket_id}/attachments/{attachment.id}/download-url",
    )


def _active_assignment(ticket: Ticket):
    active = [assignment for assignment in ticket.assignments if assignment.is_active]
    if not active:
        return None
    return max(active, key=lambda assignment: assignment.assigned_at)


def _latest_completed_assignment(ticket: Ticket):
    completed = [assignment for assignment in ticket.assignments if assignment.status == AssignmentStatus.COMPLETED]
    if not completed:
        return None
    return max(completed, key=lambda assignment: assignment.completed_at or assignment.updated_at)


def _display_code(ticket_id: UUID | None) -> str | None:
    if ticket_id is None:
        return None
    return f"PA-{str(ticket_id).replace('-', '')[:6].upper()}"


def _expected_start_at(ticket: Ticket, assignment) -> datetime | None:
    """§4: when the technician is expected to start, or nothing.

    Shown from the moment a technician is assigned. There is no acceptance step
    left to wait for: the assignment is real work on a real queue the instant it
    is written, and the scheduler's `planned_start_at` is the honest answer to
    "when will somebody come?" from that moment on.

    It disappears once work has begun -- a start estimate is history, not news,
    and the resident is shown "Kỹ thuật viên đang xử lý" instead.

    Never a completion time. `planned_finish_at` exists on the assignment and is
    deliberately not read here.
    """
    if assignment is None:
        return None
    if lifecycle_group(ticket) is TicketLifecycleGroup.FINISHED:
        return None
    if ticket.status is TicketStatus.IN_PROGRESS:
        return None
    return assignment.planned_start_at


def resident_ticket_response(ticket: Ticket, current_user_id: UUID | None = None) -> ResidentTicketResponse:
    """Serialize one report for a member of the reporting apartment.

    `current_user_id` decides only whether the caller is the sender. Nothing
    identifying a *different* apartment is read here: the duplicate master
    contributes a display code and its lifecycle group, never its reporter,
    location, text or photos.
    """
    assignment = _active_assignment(ticket)
    completed_assignment = _latest_completed_assignment(ticket)
    if ticket.status == TicketStatus.INVALID:
        display_status = resident_status_text(ticket.status)
    elif ticket.status == TicketStatus.LINKED_DUPLICATE:
        display_status = "Đã gộp phản ánh"
    elif _awaiting_p3_review(ticket):
        display_status = "Ban quản lý đang xử lý khẩn cấp"
    elif ticket.classification_status.value in {"PENDING", "PROCESSING"}:
        display_status = "Đang phân tích..."
    else:
        display_status = resident_status_text(ticket.status)
    technician = None
    if ticket.status in {TicketStatus.APPROVED, TicketStatus.IN_PROGRESS} and assignment is not None:
        if ticket.status == TicketStatus.APPROVED:
            display_status = "Đã gán kỹ thuật viên"
        technician = ResidentTechnicianSummary(
            id=assignment.technician_id,
            full_name=assignment.technician.user.full_name if assignment.technician and assignment.technician.user else None,
        )
    return ResidentTicketResponse(
        id=ticket.id,
        display_code=_display_code(ticket.id) or "",
        description=ticket.description,
        display_status=display_status,
        category_display_name=ticket.category.display_name if ticket.category else None,
        priority_description=priority_description(ticket.priority),
        progress_text=resident_progress_text(
            ticket.status,
            ticket.classification_status,
            assignment.status if assignment else None,
        ),
        expected_start_at=_expected_start_at(ticket, assignment),
        location_label=ticket.location.label if ticket.location else "Chưa cập nhật vị trí",
        reporter_name=ticket.reporter.full_name if ticket.reporter else None,
        is_reporter=current_user_id is not None and ticket.reporter_user_id == current_user_id,
        lifecycle_group=lifecycle_group(ticket),
        invalid_reason_text=resident_invalid_reason_text(ticket),
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        available_actions=_resident_actions(ticket, current_user_id),
        duplicate_of_ticket_id=ticket.duplicate_of_ticket_id,
        duplicate_master_display_code=_display_code(ticket.duplicate_of_ticket_id),
        technician=technician,
        completion_note=completed_assignment.completion_note if completed_assignment else None,
        attachments=[_attachment_response(ticket.id, a) for a in ticket.attachments],
        timeline=[
            ResidentTimelineItem(
                display_status=resident_status_text(row.to_status),
                # Only approved public copy. Coordinator notes and agent-authored
                # duplicate reasons are dropped, not translated.
                reason=resident_timeline_reason(row.reason),
                created_at=row.created_at,
            )
            for row in sorted(ticket.status_history, key=lambda item: item.created_at)
        ],
    )


def _agent_question_response(question, ticket: Ticket | None) -> AgentQuestionResponse:
    """One shape for both the read and the answer endpoints.

    `current_location_*` rides along so a LOCATION_CONFIRMATION can render
    "keep the location you chose" without the app having to fetch the ticket
    again just to name it.
    """
    location = ticket.location if ticket is not None else None
    return AgentQuestionResponse(
        id=question.id,
        question_kind=question.question_kind,
        question_type=question.question_type,
        question_text=question.question_text,
        options=question.options,
        allow_free_text_fallback=question.allow_free_text_fallback,
        round_number=question.round_number,
        expires_at=question.expires_at,
        current_location_id=ticket.location_id if ticket is not None else None,
        current_location_label=location.label if location is not None else None,
    )


TicketCreateBody = Annotated[TicketCreateRequest, Body()]


@router.post(
    "",
    response_model=ApiResponse[TicketCreatedResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Tạo phản ánh mới",
    description=(
        "Cư dân gửi vị trí và ít nhất text hoặc ảnh. source_unit_id được suy ra từ hồ sơ đã bind; "
        "frontend không được tự gửi ownership ID. Ảnh dùng signed-upload session một lần."
    ),
    operation_id="create_resident_ticket",
)
def create_ticket(
    http_request: Request,
    body: TicketCreateBody,
    background_tasks: BackgroundTasks,
    actor: CurrentActor = Depends(require_resident),
    db: Session = Depends(get_db),
    storage_service: StorageService = Depends(get_storage_service),
):
    ticket = TicketService(db, storage_service).create_ticket(actor.user.user_id, actor.resident_profile, body)
    # Grouping is not queued here. Nothing is known about this ticket yet, so
    # scheduling it now would be scheduling it unconditionally -- including for
    # a ticket that turns out to be an uncertain duplicate waiting on a human.
    # `run_analysis` starts the background grouping stage itself, once the
    # result it wrote actually authorises one.
    background_tasks.add_task(run_analysis, ticket.id)
    return _ok(
        http_request,
        TicketCreatedResponse(
            ticket_id=ticket.id,
            status=ticket.status,
            classification_status=ticket.classification_status,
            display_status="Đang phân tích...",
        ),
    )


@router.get(
    "",
    response_model=ApiResponse[TicketListResponse],
    summary="Danh sách phản ánh của căn hộ hiện tại",
    description=(
        "Trả về phản ánh của căn hộ mà tài khoản hiện tại được phép xem: phản ánh do chính "
        "họ gửi, và phản ánh của thành viên khác đã phân tích xong. Phản ánh đang trong giai "
        "đoạn AI phân tích (PENDING/PROCESSING) chỉ người gửi mới thấy - bộ lọc chạy trong "
        "SQL trước count/offset/limit nên `total` và số dòng mỗi trang đều đúng theo người gọi."
    ),
    operation_id="list_resident_tickets",
)
def list_tickets(
    http_request: Request,
    actor: CurrentActor = Depends(require_resident),
    db: Session = Depends(get_db),
    status_filter: TicketStatus | None = Query(default=None, alias="status"),
    status_group: TicketLifecycleGroup | None = Query(
        default=None,
        description="ACTIVE = đang theo dõi, FINISHED = đã kết thúc. Bỏ trống để lấy tất cả.",
    ),
    category_id: UUID | None = Query(default=None),
    created_from: datetime | None = Query(default=None, alias="from"),
    created_to: datetime | None = Query(default=None, alias="to"),
    search: str | None = Query(
        default=None,
        max_length=200,
        description="Tìm theo mã phản ánh hiển thị hoặc nội dung mô tả.",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Depends(bounded_page_size),
):
    items, total = TicketService(db).list_my_tickets(
        actor.resident_profile,
        actor.user.user_id,
        page=page,
        page_size=page_size,
        status=status_filter,
        status_group=status_group,
        category_id=category_id,
        created_from=_start_of_day(created_from),
        created_to=_inclusive_day_end(created_to),
        search=search,
    )
    data = TicketListResponse(
        items=[resident_ticket_response(item, actor.user.user_id) for item in items],
        page=page,
        page_size=page_size,
        total=total,
    )
    return _ok(http_request, data, {"page": page, "page_size": page_size, "total": total})


@router.get(
    "/{ticket_id}",
    response_model=ApiResponse[ResidentTicketResponse],
    summary="Chi tiết phản ánh của căn hộ",
    description=(
        "Cùng quy tắc hiển thị với danh sách. Thành viên khác trong căn hộ mở thẳng URL của "
        "một phản ánh đang phân tích sẽ nhận 404 giống như ticket không tồn tại."
    ),
    operation_id="get_resident_ticket",
)
def ticket_detail(
    http_request: Request,
    ticket_id: UUID = Path(),
    actor: CurrentActor = Depends(require_resident),
    db: Session = Depends(get_db),
):
    ticket = TicketService(db).get_ticket(actor.resident_profile, ticket_id, actor.user.user_id)
    return _ok(http_request, resident_ticket_response(ticket, actor.user.user_id))


@router.post(
    "/{ticket_id}/cancel",
    response_model=ApiResponse[ResidentTicketResponse],
    summary="Hủy ticket khi còn NEW",
    description="Chỉ người gửi phản ánh được hủy; backend kiểm tra, không dựa vào UI.",
    operation_id="cancel_resident_ticket",
)
def cancel_ticket(
    http_request: Request,
    ticket_id: UUID,
    actor: CurrentActor = Depends(require_resident),
    db: Session = Depends(get_db),
):
    ticket = TicketService(db).cancel_ticket(actor.user.user_id, actor.resident_profile, ticket_id)
    return _ok(http_request, resident_ticket_response(ticket, actor.user.user_id))


@router.post(
    "/{ticket_id}/supplements",
    response_model=ApiResponse[ResidentTicketResponse],
    summary="[Ngừng sử dụng] Bổ sung thông tin theo yêu cầu BQL",
    description=(
        "Luồng nghiệp vụ đã bị loại bỏ: Ban quản lý không còn yêu cầu cư dân bổ sung "
        "thông tin sau khi tiếp nhận phản ánh. Phản ánh bị từ chối sẽ kết thúc ở trạng "
        "thái INVALID và cư dân gửi phản ánh mới. "
        "Endpoint chỉ còn phục vụ các phản ánh cũ đang mắc ở WAITING_RESIDENT_INFO và "
        "client phiên bản cũ. UI hiện tại không bao giờ gọi endpoint này, và "
        "`available_actions` không còn trả về `SUPPLEMENT_INFORMATION`."
    ),
    operation_id="supplement_resident_ticket",
    deprecated=True,
)
def supplement_ticket(
    http_request: Request,
    ticket_id: UUID,
    body: TicketSupplementRequest,
    background_tasks: BackgroundTasks,
    actor: CurrentActor = Depends(require_resident),
    db: Session = Depends(get_db),
    storage_service: StorageService = Depends(get_storage_service),
):
    ticket = TicketService(db, storage_service).supplement(actor.user.user_id, actor.resident_profile, ticket_id, body)
    background_tasks.add_task(run_analysis, ticket.id)
    return _ok(http_request, resident_ticket_response(ticket, actor.user.user_id))


@router.get(
    "/{ticket_id}/attachments/{attachment_id}/download-url",
    response_model=ApiResponse[AttachmentDownloadUrlResponse],
    summary="Tạo signed URL cho ảnh thuộc ticket của căn hộ",
    description=(
        "Quyền trên ticket cha được kiểm tra trước khi tra cứu và ký URL cho ảnh, nên không "
        "thể lấy ảnh của một phản ánh đang trong giai đoạn AI phân tích của thành viên khác."
    ),
)
def attachment_download_url(
    http_request: Request,
    ticket_id: UUID,
    attachment_id: UUID,
    actor: CurrentActor = Depends(require_resident),
    db: Session = Depends(get_db),
    storage_service: StorageService = Depends(get_storage_service),
):
    attachment, signed_url, expires_in = TicketService(db, storage_service).get_attachment_download_url(
        actor.resident_profile, ticket_id, attachment_id, actor.user.user_id
    )
    data = AttachmentDownloadUrlResponse(
        attachment_id=attachment.id,
        signed_download_url=signed_url,
        expires_in=expires_in,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
    )
    return _ok(http_request, data)


@router.get(
    "/{ticket_id}/agent-question",
    response_model=ApiResponse[AgentQuestionResponse | None],
    summary="Câu hỏi AI đang chờ trả lời (chỉ người gửi)",
    description="Chỉ người gửi phản ánh đọc được câu hỏi này; thành viên khác nhận 404.",
)
def get_agent_question(
    http_request: Request,
    ticket_id: UUID,
    actor: CurrentActor = Depends(require_resident),
    db: Session = Depends(get_db),
):
    service = AgentBackendService(db)
    question = service.pending_resident_question(actor.resident_profile, ticket_id, actor.user.user_id)
    data = None
    if question is not None:
        ticket = db.get(Ticket, ticket_id)
        data = _agent_question_response(question, ticket)
    return _ok(http_request, data)


@router.post(
    "/{ticket_id}/agent-question/{question_id}/answer",
    response_model=ApiResponse[AgentQuestionResponse],
    summary="Trả lời câu hỏi AI (chỉ người gửi)",
    description=(
        "Chỉ người gửi phản ánh được trả lời. Câu hỏi phải thuộc đúng ticket trong URL, và "
        "ảnh bổ sung vẫn dùng upload session một lần của chính người gửi."
    ),
)
def answer_agent_question(
    http_request: Request,
    ticket_id: UUID,
    question_id: UUID,
    body: AgentQuestionAnswerRequest,
    background_tasks: BackgroundTasks,
    actor: CurrentActor = Depends(require_resident),
    db: Session = Depends(get_db),
    storage_service: StorageService = Depends(get_storage_service),
):
    question = AgentBackendService(db, storage_service).answer_question(
        actor.resident_profile,
        ticket_id,
        question_id,
        actor.user.user_id,
        answer_type=body.answer_type,
        answer_text=body.answer_text,
        upload_id=body.upload_id,
        selected_location_id=body.selected_location_id,
    )
    # A changed location means the candidate searches have to be recalculated,
    # which the resumed round does on its own: the evidence fingerprint moves,
    # so the previous snapshot is retired rather than reused. Grouping, again,
    # is left to the resumed round to start only if its result allows one.
    background_tasks.add_task(resume_analysis, question.session_id)
    return _ok(http_request, _agent_question_response(question, db.get(Ticket, ticket_id)))
