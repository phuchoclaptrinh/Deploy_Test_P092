"""Coordinator ticket review and assignment routes."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.orm import Session

from src.agents.service import resume_after_p3_downgrade, run_analysis, run_case_grouping
from src.api.dependencies.auth import CurrentActor, require_coordinator
from src.api.dependencies.database import get_db
from src.models.agent_schemas import P3Decision, P3ReviewStatus
from src.models.api.common import ApiResponse
from src.models.api.coordinator import (
    AssignTicketRequest,
    ClassificationOverrideRequest,
    CoordinatorAgentQuestionSummary,
    CoordinatorAnalysisSummary,
    CoordinatorDuplicateCandidate,
    CoordinatorTicketListResponse,
    CoordinatorTicketReporter,
    CoordinatorTicketResponse,
    DuplicateDecisionRequest,
    DuplicateLinkRequest,
    ManualReviewRejectRequest,
    ManualReviewResolveRequest,
    OperationalTimeoutSweepResponse,
    P3ReviewRequest,
    RequestInformationRequest,
)
from src.models.api.dispatch import DispatchWorkerRunResponse
from src.models.api.tickets import TicketAttachmentResponse, TicketTimelineItem
from src.models.display import ticket_display_code
from src.models.enums import (
    AnalysisRunStatus,
    AssignmentStatus,
    ClassificationStatus,
    Priority,
    TicketStatus,
)
from src.services.agent_backend_service import AgentBackendService
from src.services.assignment_service import AssignmentService
from src.services.coordinator_service import CoordinatorService
from src.services.duplicate_workflow_service import DuplicateWorkflowService
from src.services.operational_timeout_service import OperationalTimeoutService

router = APIRouter()


def _ok(request: Request, data, meta: dict[str, object] | None = None):
    return {"data": data, "meta": meta or {}, "error": None, "request_id": request.state.request_id}


def _available_actions(ticket) -> list[str]:
    """What the coordinator UI may offer for this ticket.

    A UI hint, never the authorization -- every action listed here is checked
    again in the service that performs it. What this list has to get right is
    not offering something that can only fail.

    The P3 branch is why it is written in this order. A ticket held at the
    emergency gate is *also* in MANUAL_REVIEW, so a list keyed only on that
    would hand a coordinator the generic resolve/reject form for an emergency
    and record no decision, no reviewer and no reason when they used it.
    """
    actions: list[str] = []
    if ticket.status == TicketStatus.LINKED_DUPLICATE:
        return []
    if _p3_review_pending(ticket):
        # Exactly one action, and it is the only one the backend will accept:
        # confirm the emergency, or downgrade it with a reason.
        return ["REVIEW_P3"]
    if ticket.classification_status == ClassificationStatus.MANUAL_REVIEW:
        # Including a DUPLICATE_UNCERTAIN ticket, which keeps the generic
        # actions alongside its own duplicate-decision panel. That state is
        # unchanged; only the P3 one is carved out above.
        return ["RESOLVE_MANUAL_REVIEW", "REJECT_MANUAL_REVIEW"]
    if ticket.status == TicketStatus.NEW:
        # REQUEST_INFORMATION is retired: Building Management approves, overrides
        # or rejects a report, but never asks the resident for more information.
        if ticket.classification_status == ClassificationStatus.RESOLVED and ticket.category_id and ticket.priority:
            actions.append("APPROVE")
        actions.append("OVERRIDE_CLASSIFICATION")
    if ticket.status == TicketStatus.APPROVED and not any(assignment.is_active for assignment in ticket.assignments):
        actions.append("ASSIGN")
    return actions


def _p3_review_pending(ticket) -> bool:
    """Read off the loaded runs rather than re-querying.

    `coordinator_ticket_response` already has them for `latest_analysis`, and
    the authoritative version of this question lives in
    `src.services.p3_review_guard` -- this is the presentation-side echo of it.
    """
    succeeded = [
        run for run in ticket.ai_analysis_runs if run.status is AnalysisRunStatus.SUCCEEDED
    ]
    if not succeeded:
        return False
    # SUCCEEDED only, exactly as the guard does. A later FAILED retry concluded
    # nothing and must not read as "the gate is closed now" while the backend
    # still refuses every action.
    run = max(succeeded, key=lambda item: item.run_number)
    return run.p3_review_status == P3ReviewStatus.PENDING.value


_display_code = ticket_display_code


def _active_assignment(ticket):
    active = [assignment for assignment in ticket.assignments if assignment.is_active]
    if not active:
        return None
    return max(active, key=lambda assignment: assignment.assigned_at)


#: The candidate snapshot is stored exactly as the Agent was shown it, so the
#: response is projected onto the fields the panel renders rather than failing
#: on an older row that carries a key this contract no longer names.
_CANDIDATE_FIELDS = frozenset(CoordinatorDuplicateCandidate.model_fields)


def _latest_analysis_summary(ticket) -> CoordinatorAnalysisSummary | None:
    """What the last analysis concluded, with the evidence behind it.

    For a DUPLICATE_UNCERTAIN ticket this is the whole review packet: the final
    category and severity, why the ticket was classified that way, why the
    duplicate verdict is uncertain, and the exact candidate snapshot the Agent
    judged. Paired with `agent_questions`, it is everything management needs to
    confirm or reject the duplicate without re-running anything.
    """
    if not ticket.ai_analysis_runs:
        return None
    run = max(ticket.ai_analysis_runs, key=lambda item: item.run_number)
    return CoordinatorAnalysisSummary(
        run_number=run.run_number,
        exit_reason=run.exit_reason,
        final_category_id=run.final_category_id,
        text_category_id=run.text_category_id,
        image_category_id=run.image_category_id,
        severity=run.severity,
        severity_source=run.severity_source,
        red_flag=run.red_flag,
        ai_reason=run.ai_reason,
        duplicate_verdict=run.duplicate_verdict,
        duplicate_reason=run.duplicate_reason,
        duplicate_candidates=[
            CoordinatorDuplicateCandidate.model_validate(
                {key: value for key, value in item.items() if key in _CANDIDATE_FIELDS}
            )
            for item in (run.duplicate_candidates or [])
        ],
        grouping_status=run.grouping_status,
        p3_review_status=run.p3_review_status,
        p3_decision=run.p3_decision,
        p3_decision_reason=run.p3_decision_reason,
        p3_reviewed_by=run.p3_reviewed_by,
        p3_reviewed_at=run.p3_reviewed_at,
        ai_priority_before_review=run.ai_priority_before_review,
        effective_priority=run.effective_priority,
        model_version=run.model_version,
        error_code=run.error_code,
    )


def _agent_question_history(ticket) -> list[CoordinatorAgentQuestionSummary]:
    if not ticket.ai_analysis_runs:
        return []
    run = max(ticket.ai_analysis_runs, key=lambda item: item.run_number)
    session = run.analysis_session
    if session is None:
        return []
    return [
        CoordinatorAgentQuestionSummary(
            id=question.id,
            question_kind=question.question_kind,
            question_type=question.question_type,
            question_text=question.question_text,
            options=question.options,
            allow_free_text_fallback=question.allow_free_text_fallback,
            round_number=question.round_number,
            status=question.status,
            answer_type=question.answer_type,
            answer_text=question.answer_text,
            answer_payload=question.answer_payload,
            answer_upload_id=question.answer_upload_id,
            asked_at=question.asked_at,
            answered_at=question.answered_at,
            expires_at=question.expires_at,
        )
        for question in sorted(session.questions, key=lambda item: item.round_number)
    ]


def _reporter_summary(ticket) -> CoordinatorTicketReporter:
    """Reporter identity plus apartment and floor, so the panel does not have to
    fan out to the resident roster for every ticket it shows."""
    unit = ticket.source_unit
    return CoordinatorTicketReporter(
        user_id=ticket.reporter_user_id,
        full_name=ticket.reporter.full_name if ticket.reporter else None,
        phone_e164=ticket.reporter.phone_e164 if ticket.reporter else None,
        unit_code=unit.unit_code if unit else None,
        floor_label=(unit.floor.display_name if unit and unit.floor else None),
    )


def coordinator_ticket_response(ticket) -> CoordinatorTicketResponse:
    assignment = _active_assignment(ticket)
    completed_assignments = [row for row in ticket.assignments if row.status == AssignmentStatus.COMPLETED]
    completed_assignment = max(completed_assignments, key=lambda row: row.completed_at or row.updated_at) if completed_assignments else None
    return CoordinatorTicketResponse(
        id=ticket.id,
        reporter_user_id=ticket.reporter_user_id,
        reporter=_reporter_summary(ticket),
        source_unit_id=ticket.source_unit_id,
        location_id=ticket.location_id,
        location_label=ticket.location.label if ticket.location else None,
        description=ticket.description,
        status=ticket.status,
        classification_status=ticket.classification_status,
        display_code="P0" if ticket.classification_status == ClassificationStatus.MANUAL_REVIEW else None,
        category_id=ticket.category_id,
        category=ticket.category.code if ticket.category else None,
        priority=ticket.priority,
        severity=ticket.severity,
        red_flag_detected=ticket.red_flag_detected,
        score_total=float(ticket.score_total) if ticket.score_total is not None else None,
        sla_started_at=ticket.sla_started_at,
        sla_due_at=ticket.sla_due_at,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        version=ticket.version,
        available_actions=_available_actions(ticket),
        duplicate_of_ticket_id=ticket.duplicate_of_ticket_id,
        duplicate_master_display_code=_display_code(ticket.duplicate_of_ticket_id),
        invalid_reason=ticket.invalid_reason,
        reassignment_count=ticket.reassignment_count,
        auto_assignment_paused=ticket.auto_assignment_paused,
        auto_assignment_pause_reason=ticket.auto_assignment_pause_reason,
        active_assignment_id=assignment.id if assignment else None,
        active_assignment_status=assignment.status if assignment else None,
        active_assignment_source=assignment.assignment_source if assignment else None,
        active_technician_id=assignment.technician_id if assignment else None,
        active_technician_name=(
            assignment.technician.user.full_name
            if assignment and assignment.technician and assignment.technician.user
            else None
        ),
        active_assignment_updated_at=assignment.updated_at if assignment else None,
        # §4: the planned window is what the scheduler committed to. Both come
        # from the active assignment, so a ticket with neither is simply one
        # nobody is working yet.
        planned_start_at=assignment.planned_start_at if assignment else None,
        planned_finish_at=assignment.planned_finish_at if assignment else None,
        planned_order=assignment.planned_order if assignment else None,
        assignment_risk_state=assignment.risk_state if assignment else None,
        slack_seconds=assignment.slack_seconds if assignment else None,
        completion_note=completed_assignment.completion_note if completed_assignment else None,
        completed_technician_name=(
            completed_assignment.technician.user.full_name
            if completed_assignment and completed_assignment.technician and completed_assignment.technician.user
            else None
        ),
        latest_analysis=_latest_analysis_summary(ticket),
        agent_questions=_agent_question_history(ticket),
        attachments=[
            TicketAttachmentResponse(
                id=a.id,
                mime_type=a.mime_type,
                size_bytes=a.size_bytes,
                attachment_type=a.attachment_type.value,
                download_url_endpoint=f"/api/v1/coordinator/tickets/{ticket.id}/attachments/{a.id}/download-url",
            )
            for a in ticket.attachments
        ],
        timeline=[
            TicketTimelineItem(from_status=row.from_status, to_status=row.to_status, reason=row.reason, created_at=row.created_at)
            for row in sorted(ticket.status_history, key=lambda item: item.created_at)
        ],
    )


@router.get(
    "/tickets",
    response_model=ApiResponse[CoordinatorTicketListResponse],
    summary="Hang cho cua Ban quan ly",
    description=(
        "Chi tra ve phan anh da phan tich xong. Ticket dang o PENDING/PROCESSING chua duoc "
        "ban giao cho Ban quan ly, va bi loai truoc count/offset/limit."
    ),
)
def list_tickets(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
    category_id: UUID | None = Query(default=None),
    priority: Priority | None = Query(default=None),
    status_filter: TicketStatus | None = Query(default=None, alias="status"),
    classification_status: ClassificationStatus | None = Query(default=None),
    created_from: datetime | None = Query(default=None, alias="from"),
    created_to: datetime | None = Query(default=None, alias="to"),
    search: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    items, total = CoordinatorService(db).list_tickets(
        page, page_size, status=status_filter, category_id=category_id, priority=priority,
        classification_status=classification_status, created_from=created_from, created_to=created_to, search=search,
    )
    data = CoordinatorTicketListResponse(items=[coordinator_ticket_response(item) for item in items], page=page, page_size=page_size, total=total)
    return _ok(request, data, {"page": page, "page_size": page_size, "total": total})


@router.get("/tickets/{ticket_id}", response_model=ApiResponse[CoordinatorTicketResponse])
def get_ticket(request: Request, ticket_id: UUID, _actor: CurrentActor = Depends(require_coordinator), db: Session = Depends(get_db)):
    return _ok(request, coordinator_ticket_response(CoordinatorService(db).get_ticket(ticket_id)))


@router.post("/tickets/{ticket_id}/approve", response_model=ApiResponse[CoordinatorTicketResponse])
def approve_ticket(request: Request, ticket_id: UUID, actor: CurrentActor = Depends(require_coordinator), db: Session = Depends(get_db)):
    return _ok(request, coordinator_ticket_response(CoordinatorService(db).approve(actor.user.user_id, ticket_id)))


@router.post("/tickets/{ticket_id}/assign")
def assign_ticket(request: Request, ticket_id: UUID, body: AssignTicketRequest, actor: CurrentActor = Depends(require_coordinator), db: Session = Depends(get_db)):
    assignment = AssignmentService(db).assign(actor.user.user_id, ticket_id, body.technician_id)
    return _ok(request, {"assignment_id": assignment.id, "status": assignment.status})


@router.post("/tickets/{ticket_id}/duplicate-link", response_model=ApiResponse[CoordinatorTicketResponse])
def link_duplicate_ticket(request: Request, ticket_id: UUID, body: DuplicateLinkRequest, actor: CurrentActor = Depends(require_coordinator), db: Session = Depends(get_db)):
    ticket = DuplicateWorkflowService(db).link_duplicate(actor.user.user_id, ticket_id, body.master_ticket_id, body.reason)
    return _ok(request, coordinator_ticket_response(ticket))


@router.post(
    "/tickets/{ticket_id}/duplicate-decision",
    response_model=ApiResponse[CoordinatorTicketResponse],
    summary="Xác nhận phản ánh có trùng hay không",
    description=(
        "Chốt một phản ánh AI đánh giá là chưa chắc chắn. Xác nhận trùng thì liên kết ticket và "
        "không chạy gộp cụm. Xác nhận không trùng thì ticket được chấm điểm, công bố, và bước gộp "
        "cụm nền tự động chạy sau đó."
    ),
)
def decide_duplicate(
    request: Request,
    ticket_id: UUID,
    body: DuplicateDecisionRequest,
    background_tasks: BackgroundTasks,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    AgentBackendService(db).resolve_duplicate_uncertain(
        actor.user.user_id,
        ticket_id,
        is_duplicate=body.is_duplicate,
        master_ticket_id=body.master_ticket_id,
        reason=body.reason,
    )
    if not body.is_duplicate:
        # Duplicate processing is now final and the answer is "independent
        # ticket", which is exactly the point at which grouping is allowed to
        # look for a spreading case.
        background_tasks.add_task(run_case_grouping, ticket_id)
    return _ok(request, coordinator_ticket_response(CoordinatorService(db).get_ticket(ticket_id)))


@router.post(
    "/tickets/{ticket_id}/p3-review",
    response_model=ApiResponse[CoordinatorTicketResponse],
    summary="Duyệt phản ánh ở mức khẩn cấp P3",
    description=(
        "Bắt buộc với mọi phản ánh AI xếp mức P3. Xác nhận P3 thì phản ánh được công bố theo quy "
        "trình khẩn cấp và dừng ở đó: không tra trùng, không gộp cụm. Hạ mức xuống P1/P2 thì bắt "
        "buộc có lý do, và phản ánh tiếp tục quy trình từ bước tra cứu phản ánh trùng."
    ),
)
def review_p3(
    request: Request,
    ticket_id: UUID,
    body: P3ReviewRequest,
    background_tasks: BackgroundTasks,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    AgentBackendService(db).resolve_p3_review(
        actor.user.user_id,
        ticket_id,
        decision=body.decision,
        priority=body.priority,
        reason=body.reason,
    )
    if body.decision is P3Decision.DOWNGRADE_SEVERITY:
        # The only way back into the pipeline. Queued exactly once: the review
        # can no longer be PENDING, so a second call is refused before it gets
        # this far.
        background_tasks.add_task(resume_after_p3_downgrade, ticket_id)
    return _ok(request, coordinator_ticket_response(CoordinatorService(db).get_ticket(ticket_id)))


@router.post(
    "/tickets/{ticket_id}/analysis/retry",
    response_model=ApiResponse[CoordinatorTicketResponse],
    summary="Chạy lại phân tích AI sau lỗi kỹ thuật",
    description=(
        "Dùng khi lần phân tích trước dừng vì lỗi kỹ thuật (có error_code) chứ không phải vì một "
        "kết luận nghiệp vụ. Tạo một phiên phân tích mới cho ticket."
    ),
)
def retry_analysis(
    request: Request,
    ticket_id: UUID,
    background_tasks: BackgroundTasks,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    ticket = CoordinatorService(db).get_ticket(ticket_id)
    # `run_analysis` starts the grouping stage itself if the retried round ends
    # in a result that authorises one.
    background_tasks.add_task(run_analysis, ticket_id)
    return _ok(request, coordinator_ticket_response(ticket))


@router.post(
    "/operational-timeouts/run",
    response_model=ApiResponse[OperationalTimeoutSweepResponse],
    summary="Chạy quét hết hạn thủ công",
    description=(
        "Chỉ dùng cho vận hành và kiểm thử. Trong môi trường thật, việc này do "
        "`python -m src.workers.dispatch_worker` đảm nhiệm. Hiện chỉ quét hạn trả lời "
        "của cư dân: bước nhận việc đã bị bỏ và chưa có hạn bắt đầu nào thay thế."
    ),
)
def run_operational_timeouts(request: Request, _actor: CurrentActor = Depends(require_coordinator), db: Session = Depends(get_db)):
    return _ok(request, OperationalTimeoutSweepResponse(**OperationalTimeoutService(db).sweep()))


@router.post(
    "/dispatch/run-once",
    response_model=ApiResponse[DispatchWorkerRunResponse],
    summary="Chạy một micro-batch phân việc tự động",
    description=(
        "Chỉ dùng cho vận hành và kiểm thử. Trong môi trường thật, "
        "`python -m src.workers.dispatch_worker` chạy liên tục theo chu kỳ 0,5-1 giây."
    ),
)
def run_dispatch_once(request: Request, _actor: CurrentActor = Depends(require_coordinator), db: Session = Depends(get_db)):
    # Imported here rather than at module scope: this router is loaded by every
    # coordinator request, and the dispatch package pulls in the agent stack.
    from src.dispatch.service import DispatchService

    report = DispatchService(db, worker_id="api-manual-trigger").run_micro_batch()
    return _ok(request, DispatchWorkerRunResponse(**report.as_dict()))


@router.post("/tickets/{ticket_id}/manual-review/resolve", response_model=ApiResponse[CoordinatorTicketResponse])
def resolve_manual_review(
    request: Request,
    ticket_id: UUID,
    body: ManualReviewResolveRequest,
    background_tasks: BackgroundTasks,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    ticket = CoordinatorService(db).resolve_manual_review(actor.user.user_id, ticket_id, body)
    if AgentBackendService(db).grouping_is_pending(ticket_id):
        background_tasks.add_task(run_case_grouping, ticket_id)
    return _ok(request, coordinator_ticket_response(ticket))


@router.post("/tickets/{ticket_id}/manual-review/reject", response_model=ApiResponse[CoordinatorTicketResponse])
def reject_manual_review(request: Request, ticket_id: UUID, body: ManualReviewRejectRequest, actor: CurrentActor = Depends(require_coordinator), db: Session = Depends(get_db)):
    ticket = AgentBackendService(db).manual_review_reject(actor.user.user_id, ticket_id, body.reason)
    return _ok(request, coordinator_ticket_response(ticket))


@router.post(
    "/tickets/{ticket_id}/request-information",
    response_model=ApiResponse[CoordinatorTicketResponse],
    summary="[Ngừng sử dụng] Yêu cầu cư dân bổ sung thông tin",
    description=(
        "Luồng nghiệp vụ đã bị loại bỏ. Ban quản lý từ chối phản ánh bằng "
        "`manual-review/reject`; cư dân sẽ gửi phản ánh mới. Endpoint được giữ lại "
        "cho client cũ và không còn xuất hiện trong `available_actions`."
    ),
    deprecated=True,
)
def request_information(request: Request, ticket_id: UUID, body: RequestInformationRequest, actor: CurrentActor = Depends(require_coordinator), db: Session = Depends(get_db)):
    ticket = CoordinatorService(db).request_information(actor.user.user_id, ticket_id, body.message)
    return _ok(request, coordinator_ticket_response(ticket))


@router.patch("/tickets/{ticket_id}/classification", response_model=ApiResponse[CoordinatorTicketResponse])
def override_classification(
    request: Request,
    ticket_id: UUID,
    body: ClassificationOverrideRequest,
    background_tasks: BackgroundTasks,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    ticket = CoordinatorService(db).override_classification(actor.user.user_id, ticket_id, body)
    if AgentBackendService(db).grouping_is_pending(ticket_id):
        background_tasks.add_task(run_case_grouping, ticket_id)
    return _ok(request, coordinator_ticket_response(ticket))
