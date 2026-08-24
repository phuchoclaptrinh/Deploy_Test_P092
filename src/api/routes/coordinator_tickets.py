"""Coordinator ticket review and assignment routes."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from src.api.dependencies.auth import CurrentActor, require_coordinator
from src.api.dependencies.database import get_db
from src.database.models.assignment_proposal import AIAssignmentJob
from src.database.models.technician import TechnicianProfile
from src.models.api.common import ApiResponse
from src.models.api.coordinator import (
    AssignmentHistoryRecordResponse,
    AssignmentJobResponse,
    AssignmentProposalBatchResponse,
    AssignmentProposalCancelRequest,
    AssignmentProposalConfirmRequest,
    AssignmentProposalCreateRequest,
    AssignmentProposalItemMemberResponse,
    AssignmentProposalItemResponse,
    AssignmentProposalItemUpdateRequest,
    AssignmentScheduleResponse,
    AssignmentScheduleUpdateRequest,
    AssignmentWorkerRunResponse,
    AssignTicketRequest,
    AutoAssignmentSettingsResponse,
    AutoAssignmentSettingsUpdateRequest,
    ClassificationOverrideRequest,
    CoordinatorAgentQuestionSummary,
    CoordinatorAnalysisSummary,
    CoordinatorTicketListResponse,
    CoordinatorTicketReporter,
    CoordinatorTicketResponse,
    DuplicateLinkRequest,
    ManualReviewRejectRequest,
    ManualReviewResolveRequest,
    OperationalTimeoutSweepResponse,
    RequestInformationRequest,
)
from src.models.api.errors import TICKET_NOT_FOUND, DomainError
from src.models.api.tickets import TicketAttachmentResponse, TicketTimelineItem
from src.models.display import ticket_display_code
from src.models.enums import (
    AssignmentJobMode,
    AssignmentJobStatus,
    AssignmentJobTrigger,
    ClassificationStatus,
    Priority,
    TicketStatus,
)
from src.services.agent_backend_service import AgentBackendService
from src.services.assignment_history_service import AssignmentHistoryService
from src.services.assignment_job_service import AssignmentJobService
from src.services.assignment_proposal_service import AssignmentProposalService
from src.services.assignment_schedule_service import AssignmentScheduleService
from src.services.assignment_service import AssignmentService
from src.services.coordinator_service import CoordinatorService
from src.services.operational_timeout_service import OperationalTimeoutService
from src.services.v4_workflow_service import AutoAssignmentSettingsService, DuplicateWorkflowService

router = APIRouter()


def _ok(request: Request, data, meta: dict[str, object] | None = None):
    return {"data": data, "meta": meta or {}, "error": None, "request_id": request.state.request_id}


def _available_actions(ticket) -> list[str]:
    actions: list[str] = []
    if ticket.status == TicketStatus.LINKED_DUPLICATE:
        return []
    if ticket.classification_status == ClassificationStatus.MANUAL_REVIEW:
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


_display_code = ticket_display_code


def _active_assignment(ticket):
    active = [assignment for assignment in ticket.assignments if assignment.is_active]
    if not active:
        return None
    return max(active, key=lambda assignment: assignment.assigned_at)


def _latest_analysis_summary(ticket) -> CoordinatorAnalysisSummary | None:
    if not ticket.ai_analysis_runs:
        return None
    run = max(ticket.ai_analysis_runs, key=lambda item: item.run_number)
    return CoordinatorAnalysisSummary(
        run_number=run.run_number,
        exit_reason=run.exit_reason,
        text_categories=[str(value) for value in run.text_categories],
        image_categories=[str(value) for value in run.image_categories] if run.image_categories else None,
        red_flag_text=run.red_flag_text,
        red_flag_signal=run.red_flag_signal,
        severity=run.severity,
        severity_source=run.severity_source,
        is_confident=run.is_confident,
        confidence_notes=run.confidence_notes,
        text_model_version=run.text_model_version,
        vision_model_version=run.vision_model_version,
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
            question_type=question.question_type,
            question_text=question.question_text,
            options=question.options,
            allow_free_text_fallback=question.allow_free_text_fallback,
            round_number=question.round_number,
            status=question.status,
            answer_type=question.answer_type,
            answer_text=question.answer_text,
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
        building_code=unit.building.code if unit and unit.building else None,
        floor_label=(unit.floor.display_name if unit and unit.floor else None),
    )


def coordinator_ticket_response(ticket) -> CoordinatorTicketResponse:
    assignment = _active_assignment(ticket)
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
        latest_analysis=_latest_analysis_summary(ticket),
        agent_questions=_agent_question_history(ticket),
        attachments=[
            TicketAttachmentResponse(
                id=a.id,
                mime_type=a.mime_type,
                size_bytes=a.size_bytes,
                download_url_endpoint=f"/api/v1/coordinator/tickets/{ticket.id}/attachments/{a.id}/download-url",
            )
            for a in ticket.attachments
        ],
        timeline=[
            TicketTimelineItem(from_status=row.from_status, to_status=row.to_status, reason=row.reason, created_at=row.created_at)
            for row in sorted(ticket.status_history, key=lambda item: item.created_at)
        ],
    )


def auto_assignment_response(row) -> AutoAssignmentSettingsResponse:
    return AutoAssignmentSettingsResponse(
        enabled=row.enabled,
        activation_delay=row.activation_delay,
        version=row.version,
        updated_at=row.updated_at,
        activated_by_batch_id=row.activated_by_batch_id,
        activated_by_user_id=row.activated_by_user_id,
        activated_at=row.activated_at,
    )


def _technician_name(profile) -> str | None:
    return profile.user.full_name if profile and profile.user else None


def _proposal_member_response(member) -> AssignmentProposalItemMemberResponse:
    """A case row covers up to five tickets; the board renders every one."""
    ticket = member.ticket
    return AssignmentProposalItemMemberResponse(
        ticket_id=member.ticket_id,
        display_code=_display_code(member.ticket_id),
        location_label=ticket.location.label if ticket and ticket.location else None,
        category=ticket.category.code if ticket and ticket.category else None,
        priority=ticket.priority if ticket else None,
        created_at=ticket.created_at if ticket else None,
        sla_due_at=ticket.sla_due_at if ticket else None,
    )


def assignment_proposal_item_response(item) -> AssignmentProposalItemResponse:
    return AssignmentProposalItemResponse(
        id=item.id,
        decision_id=item.decision_id,
        status=item.status,
        work_item_type=item.work_item_type,
        work_item_id=item.work_item_id,
        ticket_id=item.ticket_id,
        ticket_display_code=_display_code(item.ticket_id),
        ticket_description=item.ticket.description if item.ticket else None,
        ticket_location_label=item.ticket.location.label if item.ticket and item.ticket.location else None,
        ticket_category=item.ticket.category.code if item.ticket and item.ticket.category else None,
        ticket_priority=item.ticket.priority if item.ticket else None,
        proposed_technician_id=item.proposed_technician_id,
        proposed_technician_name=_technician_name(item.proposed_technician),
        final_technician_id=item.final_technician_id,
        final_technician_name=_technician_name(item.final_technician),
        # Compatibility aliases for the existing frontend field names.
        selected_technician_id=item.final_technician_id,
        selected_technician_name=_technician_name(item.final_technician),
        completed_model=item.completed_model,
        decided_at=item.decided_at,
        ticket_ids=[member.ticket_id for member in item.members],
        members=[_proposal_member_response(member) for member in item.members],
        reason=item.reason,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def assignment_proposal_response(row) -> AssignmentProposalBatchResponse:
    return AssignmentProposalBatchResponse(
        id=row.id,
        status=row.status,
        ready_at=row.ready_at,
        expires_at=row.expires_at,
        continue_auto_assignment=row.continue_auto_assignment,
        activation_delay=row.activation_delay,
        version=row.version,
        created_at=row.created_at,
        confirmed_at=row.confirmed_at,
        cancelled_at=row.cancelled_at,
        confirmed_by_user_id=row.confirmed_by_user_id,
        confirmed_by_name=row.confirmed_by.full_name if row.confirmed_by else None,
        items=[assignment_proposal_item_response(item) for item in row.items],
    )


def assignment_schedule_response(row) -> AssignmentScheduleResponse:
    """The recurring *draft* schedule. Not the switch that assigns."""
    return AssignmentScheduleResponse(
        enabled=row.enabled,
        interval=row.interval_code,
        next_run_at=row.next_run_at,
        last_run_at=row.last_run_at,
        version=row.version,
        updated_at=row.updated_at,
    )


def job_is_cancellable(job) -> bool:
    """§6.2: only the P1/P2 intervention window after a rejection.

    A P3 reassignment runs immediately and never has this window, an
    initial-delay job is the configured switch doing its job, and a running job
    is already mid-decision. Everything else is taken back by assigning by hand,
    which still wins the race (§4.5).
    """
    return (
        job.mode == AssignmentJobMode.DIRECT.value
        and job.trigger == AssignmentJobTrigger.REASSIGN_REJECTED.value
        and job.status == AssignmentJobStatus.SCHEDULED_GRACE.value
    )


def assignment_job_response(job) -> AssignmentJobResponse:
    return AssignmentJobResponse(
        id=job.id,
        mode=job.mode,
        status=job.status,
        trigger=job.trigger,
        work_item_type=job.work_item_type,
        work_item_id=job.work_item_id,
        ticket_ids=[member.ticket_id for member in job.members],
        execute_after=job.execute_after,
        selected_technician_id=job.selected_technician_id,
        selected_technician_name=_technician_name(job.selected_technician),
        completed_model=job.completed_model,
        # §9: the sanitized business reason only. `error_detail` and
        # `raw_model_output` stay in the audit tables.
        decision_reason=job.decision_reason,
        error_code=job.error_code,
        created_at=job.created_at,
        completed_at=job.completed_at,
        cancellable=job_is_cancellable(job),
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


@router.get("/auto-assignment-settings", response_model=ApiResponse[AutoAssignmentSettingsResponse])
def get_auto_assignment_settings(request: Request, _actor: CurrentActor = Depends(require_coordinator), db: Session = Depends(get_db)):
    return _ok(request, auto_assignment_response(AutoAssignmentSettingsService(db).get()))


@router.patch(
    "/auto-assignment-settings",
    response_model=ApiResponse[AutoAssignmentSettingsResponse],
    summary="Tat phan viec tu dong truc tiep, hoac doi do tre khi dang bat",
    description=(
        "Khong bat duoc phan viec tu dong tu endpoint nay. Chuyen tu TAT sang BAT "
        "chi xay ra khi mot dieu phoi vien xac nhan mot de xuat phan viec that; "
        "moi yeu cau khac tra ve 409 AUTO_ASSIGNMENT_PROPOSAL_REQUIRED."
    ),
)
def update_auto_assignment_settings(request: Request, body: AutoAssignmentSettingsUpdateRequest, actor: CurrentActor = Depends(require_coordinator), db: Session = Depends(get_db)):
    row = AutoAssignmentSettingsService(db).update(actor.user.user_id, enabled=body.enabled, activation_delay=body.activation_delay)
    return _ok(request, auto_assignment_response(row))


@router.get("/assignment-proposals", response_model=ApiResponse[list[AssignmentProposalBatchResponse]])
def list_assignment_proposals(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
):
    rows = AssignmentProposalService(db).list_batches(limit)
    return _ok(request, [assignment_proposal_response(row) for row in rows])


@router.post("/assignment-proposals", response_model=ApiResponse[AssignmentProposalBatchResponse])
def create_assignment_proposal(
    request: Request,
    body: AssignmentProposalCreateRequest,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    service = AssignmentProposalService(db)
    row = service.create_batch(
        actor.user.user_id,
        body.limit,
        # A rule calculation is in-process and deterministic, so returning a
        # BUILDING shell merely waits for the worker's poll interval.  Keep AI
        # queued because its model timeout/failover path remains asynchronous.
        build_immediately=service.settings.assignment_decision_engine == "RULE",
    )
    return _ok(request, assignment_proposal_response(row))


@router.get("/assignment-proposals/{batch_id}", response_model=ApiResponse[AssignmentProposalBatchResponse])
def get_assignment_proposal(
    request: Request,
    batch_id: UUID,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    row = AssignmentProposalService(db).get_batch(batch_id)
    return _ok(request, assignment_proposal_response(row))


@router.post(
    "/assignment-proposals/{batch_id}/confirm",
    response_model=ApiResponse[AssignmentProposalBatchResponse],
    summary="Xac nhan de xuat va phan viec",
    description=(
        "Phan cong cac dong da dat vao ky thuat vien. Neu phan viec tu dong truc tiep "
        "dang tat va lan xac nhan nay tao ra assignment that, no se duoc bat trong "
        "cung mot transaction: day la con duong duy nhat."
    ),
)
def confirm_assignment_proposal(
    request: Request,
    batch_id: UUID,
    body: AssignmentProposalConfirmRequest,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    row = AssignmentProposalService(db).confirm_batch(
        actor.user.user_id,
        batch_id,
        activation_delay=body.activation_delay,
        expected_version=body.expected_version,
    )
    return _ok(request, assignment_proposal_response(row))


@router.patch(
    "/assignment-proposals/{batch_id}/items/{item_id}",
    response_model=ApiResponse[AssignmentProposalBatchResponse],
    summary="Bo chon dong hoac doi ky thuat vien trong bang de xuat",
)
def update_assignment_proposal_item(
    request: Request,
    batch_id: UUID,
    item_id: UUID,
    body: AssignmentProposalItemUpdateRequest,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    """4.6 item 4: deselect a row, or put a different technician on it."""
    row = AssignmentProposalService(db).update_item(
        actor.user.user_id,
        batch_id,
        item_id,
        selected=body.selected,
        technician_id=body.technician_id,
    )
    return _ok(request, assignment_proposal_response(row))


@router.post("/assignment-proposals/{batch_id}/cancel", response_model=ApiResponse[AssignmentProposalBatchResponse])
def cancel_assignment_proposal(
    request: Request,
    batch_id: UUID,
    body: AssignmentProposalCancelRequest,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    row = AssignmentProposalService(db).cancel_batch(actor.user.user_id, batch_id, body.reason)
    return _ok(request, assignment_proposal_response(row))


@router.get("/assignment-schedule", response_model=ApiResponse[AssignmentScheduleResponse])
def get_assignment_schedule(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    """How often a new draft proposal is opened for review."""
    return _ok(request, assignment_schedule_response(AssignmentScheduleService(db).get()))


@router.patch(
    "/assignment-schedule",
    response_model=ApiResponse[AssignmentScheduleResponse],
    summary="Dat lich lap lai tao bang de xuat phan viec",
    description=(
        "Lich nay chi TAO BANG DE XUAT de BQL duyet, khong tu dong phan viec. "
        "Cong tac tu dong phan viec truc tiep nam o /auto-assignment-settings."
    ),
)
def update_assignment_schedule(
    request: Request,
    body: AssignmentScheduleUpdateRequest,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    row = AssignmentScheduleService(db).update(
        actor.user.user_id,
        enabled=body.enabled,
        interval=body.interval,
        expected_version=body.expected_version,
        after_batch_id=body.after_batch_id,
    )
    return _ok(request, assignment_schedule_response(row))


@router.get(
    "/assignment-history",
    response_model=ApiResponse[list[AssignmentHistoryRecordResponse]],
    summary="Lich su cac dot phan viec da xac nhan",
    description=(
        "Doc tu ban chup dong bang tai thoi diem xac nhan. Doi ten danh muc, "
        "doi vi tri hay ngung hoat dong ky thuat vien deu khong lam thay doi "
        "mot ban ghi da chot."
    ),
)
def list_assignment_history(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
):
    return _ok(request, AssignmentHistoryService(db).list_records(limit))


@router.get(
    "/assignment-history/{batch_id}",
    response_model=ApiResponse[AssignmentHistoryRecordResponse],
)
def get_assignment_history_record(
    request: Request,
    batch_id: UUID,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    record = AssignmentHistoryService(db).get_record(batch_id)
    if record is None:
        raise DomainError(TICKET_NOT_FOUND, "Khong tim thay dot phan viec da xac nhan.", 404)
    return _ok(request, record)


@router.post(
    "/operational-timeouts/run",
    response_model=ApiResponse[OperationalTimeoutSweepResponse],
    summary="Chay mot luot quet timeout (chan doan)",
    description=(
        "Endpoint chan doan. Lich chay that do worker ben vung "
        "`python -m src.workers.assignment_worker` dam nhiem (contract 5)."
    ),
)
def run_operational_timeouts(request: Request, _actor: CurrentActor = Depends(require_coordinator), db: Session = Depends(get_db)):
    return _ok(request, OperationalTimeoutSweepResponse(**OperationalTimeoutService(db).sweep()))


@router.get("/assignment-jobs", response_model=ApiResponse[list[AssignmentJobResponse]])
def list_assignment_jobs(
    request: Request,
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
    job_status: str | None = Query(
        default=None,
        alias="status",
        description="Mot hoac nhieu trang thai, ngan cach bang dau phay.",
    ),
    limit: int = Query(default=50, ge=1, le=200),
):
    query = (
        select(AIAssignmentJob)
        .options(
            selectinload(AIAssignmentJob.members),
            joinedload(AIAssignmentJob.selected_technician).joinedload(TechnicianProfile.user),
        )
        .order_by(AIAssignmentJob.created_at.desc())
        .limit(limit)
    )
    if job_status:
        # The workspace asks for the three live states in one call, so the
        # filter takes a list rather than forcing three round trips.
        wanted = [part.strip().upper() for part in job_status.split(",") if part.strip()]
        if wanted:
            query = query.where(AIAssignmentJob.status.in_(wanted))
    return _ok(request, [assignment_job_response(row) for row in db.scalars(query)])


@router.post(
    "/assignment-jobs/{job_id}/cancel",
    response_model=ApiResponse[AssignmentJobResponse],
    summary="Huy job phan viec AI de phan cong thu cong",
)
def cancel_assignment_job(
    request: Request,
    job_id: UUID,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    """6.2: inside the grace window the coordinator may take the work item back."""
    job = db.scalar(
        select(AIAssignmentJob)
        .where(AIAssignmentJob.id == job_id)
        .options(
            selectinload(AIAssignmentJob.members),
            joinedload(AIAssignmentJob.selected_technician).joinedload(TechnicianProfile.user),
        )
        .with_for_update(of=AIAssignmentJob)
    )
    if job is None:
        raise DomainError(TICKET_NOT_FOUND, "Job phan viec khong ton tai.", 404)
    # The DIRECT + REASSIGN_REJECTED + SCHEDULED_GRACE rule lives in the
    # service, so it holds for every caller and not only this route.
    AssignmentJobService(db).cancel_by_coordinator(job, actor.user.user_id)
    db.commit()
    db.refresh(job)
    return _ok(request, assignment_job_response(job))


@router.post(
    "/assignment-worker/run",
    response_model=ApiResponse[AssignmentWorkerRunResponse],
    summary="Chay mot luot worker phan viec (chan doan)",
    description=(
        "Chay dung mot vong cua worker: timeout, tao job, DIRECT, PROPOSAL. "
        "Day la cong cu chan doan; production phai chay tien trinh worker rieng "
        "`python -m src.workers.assignment_worker` (contract 5)."
    ),
)
def run_assignment_worker(request: Request, _actor: CurrentActor = Depends(require_coordinator)):
    from src.workers.assignment_worker import run_once

    return _ok(request, AssignmentWorkerRunResponse(**run_once().as_dict()))


@router.post("/tickets/{ticket_id}/manual-review/resolve", response_model=ApiResponse[CoordinatorTicketResponse])
def resolve_manual_review(request: Request, ticket_id: UUID, body: ManualReviewResolveRequest, actor: CurrentActor = Depends(require_coordinator), db: Session = Depends(get_db)):
    ticket = CoordinatorService(db).resolve_manual_review(actor.user.user_id, ticket_id, body)
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
def override_classification(request: Request, ticket_id: UUID, body: ClassificationOverrideRequest, actor: CurrentActor = Depends(require_coordinator), db: Session = Depends(get_db)):
    ticket = CoordinatorService(db).override_classification(actor.user.user_id, ticket_id, body)
    return _ok(request, coordinator_ticket_response(ticket))
