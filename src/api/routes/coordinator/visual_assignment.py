"""Visual Assignment board routes (§1, §10).

Two endpoints and nothing else. There is deliberately no "create a board", no
"save a draft" and no per-placement PATCH: §1 asks for a pool, drag and drop,
and **one** confirming action. Anything that let the board be half-committed
server-side would put the transaction boundary in the wrong place.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.api.dependencies.auth import CurrentActor, require_coordinator
from src.api.dependencies.database import get_db
from src.api.routes.coordinator._common import ok
from src.models.api.common import ApiResponse
from src.models.api.visual_assignment import (
    BoardPlacementPreviewResponse,
    BoardPlannedSlotResponse,
    BoardTechnicianResponse,
    BoardUnitResponse,
    VisualBoardResponse,
    VisualConfirmRequest,
    VisualConfirmResponse,
)
from src.services.visual_assignment_service import Board, VisualAssignmentService

router = APIRouter()


def board_response(board: Board) -> VisualBoardResponse:
    return VisualBoardResponse(
        generated_at=board.generated_at,
        within_working_shift=board.within_working_shift,
        units=[
            BoardUnitResponse(
                unit_id=unit.unit_id,
                unit_type=unit.unit_type,
                ticket_ids=unit.ticket_ids,
                display_codes=unit.display_codes,
                category_id=unit.category_id,
                category_code=unit.category_code,
                category_display_name=unit.category_display_name,
                priority=unit.priority.value if unit.priority else None,
                score=float(unit.score),
                submitted_at=unit.submitted_at,
                location_labels=unit.location_labels,
                p80_seconds=unit.p80_seconds,
                member_count=unit.member_count,
                eligible_technician_ids=unit.eligible_technician_ids,
                previews=[
                    BoardPlacementPreviewResponse(
                        technician_id=preview.technician_id,
                        blocked=preview.blocked,
                        warnings=[code.value for code in preview.warnings],
                        planned_start_at=preview.planned_start_at,
                        planned_finish_at=preview.planned_finish_at,
                        worst_slack_seconds=preview.worst_slack_seconds,
                    )
                    for preview in unit.previews
                ],
            )
            for unit in board.units
        ],
        technicians=[
            BoardTechnicianResponse(
                technician_id=column.technician_id,
                display_name=column.display_name,
                is_active=column.is_active,
                is_available=column.is_available,
                skill_category_ids=column.skill_category_ids,
                active_assignment_count=column.active_assignment_count,
                in_progress_count=column.in_progress_count,
                planned_slots=[BoardPlannedSlotResponse(**slot) for slot in column.planned_slots],
                day_ends_at=column.day_ends_at,
            )
            for column in board.technicians
        ],
    )


@router.get(
    "/visual-assignment/board",
    response_model=ApiResponse[VisualBoardResponse],
    summary="Bảng phân việc trực quan",
    description=(
        "Toàn bộ dữ liệu bảng phân việc: nhóm việc chưa gán (ticket lẻ và cụm sự cố giữ nguyên "
        "thành một khối), cột kỹ thuật viên kèm khối lượng hiện tại, và cảnh báo cho từng cặp "
        "việc/kỹ thuật viên. Bảng được tính tại thời điểm gọi, không lưu trữ."
    ),
)
def get_board(
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
    _actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    return ok(request, board_response(VisualAssignmentService(db).board(limit=limit)))


@router.post(
    "/visual-assignment/confirm",
    response_model=ApiResponse[VisualConfirmResponse],
    summary="Xác nhận toàn bộ phân việc thủ công",
    description=(
        "Nhận tất cả vị trí kéo-thả trong một lần và ghi trong một transaction duy nhất. "
        "Nếu bất kỳ vị trí nào vi phạm ràng buộc cứng (kỹ năng, trạng thái sẵn sàng, ca làm việc, "
        "ticket đã có người xử lý) thì toàn bộ lần xác nhận bị từ chối và không có thay đổi nào "
        "được lưu; chi tiết các vị trí lỗi nằm trong `error.details.failures`."
    ),
)
def confirm_board(
    request: Request,
    body: VisualConfirmRequest,
    actor: CurrentActor = Depends(require_coordinator),
    db: Session = Depends(get_db),
):
    result = VisualAssignmentService(db).confirm(
        actor.user.user_id,
        [(item.unit_id, item.technician_id) for item in body.placements],
    )
    return ok(
        request,
        VisualConfirmResponse(
            assigned_unit_count=result.assigned_unit_count,
            assigned_ticket_count=result.assigned_ticket_count,
            assignment_ids=result.assignment_ids,
        ),
    )
