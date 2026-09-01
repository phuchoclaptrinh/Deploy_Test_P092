"""Human-facing identifiers derived from internal ones.

A ticket's UUID is what the database joins on; `PA-5DBB6A` is what a resident
reads out over the phone and what a coordinator types into a search box. The
derivation lives here rather than in a route module because the confirmation
snapshot has to freeze the same code the API renders — two implementations that
drifted would make a history record disagree with the ticket it names.
"""

from __future__ import annotations

from uuid import UUID


def ticket_display_code(ticket_id: UUID | str | None) -> str | None:
    """The short code shown to people. Derived, never stored, never reused."""
    if ticket_id is None:
        return None
    return f"PA-{str(ticket_id).replace('-', '')[:6].upper()}"


#: §4's resident-facing progression, in order. A resident is told what is
#: happening now, never when it will be finished:
#:
#:   assigned    -> a technician is on it, with the expected start time
#:                  alongside in `expected_start_at`
#:   in progress -> the technician is handling it
#:
#: There is no "waiting for the technician to accept" step any more, in the
#: wording or in the model behind it: the technician's first action is to start.
#:
#: There is deliberately no branch that produces a completion time. The old
#: `estimated_resolution_text` did, and §4 removes the promise it was making --
#: so the function is gone rather than softened, because a caller reaching for
#: it would be reaching for exactly the thing that is no longer true.
def resident_progress_text(
    ticket_status,
    classification_status,
    assignment_status=None,
) -> str:
    """One sentence describing where this report actually stands."""
    classification = getattr(classification_status, "value", classification_status)
    status = getattr(ticket_status, "value", ticket_status)
    assignment = getattr(assignment_status, "value", assignment_status)

    if classification in {"PENDING", "PROCESSING"}:
        return "Đang phân tích phản ánh..."
    if classification == "MANUAL_REVIEW":
        return "Đang chờ Ban quản lý xem xét"
    if classification == "FAILED":
        return "Phản ánh không hợp lệ, vui lòng gửi lại"

    if status == "COMPLETED":
        return "Đã hoàn thành"
    if status == "UNRESOLVABLE":
        return "Không thể xử lý, Ban quản lý sẽ liên hệ"
    if status == "LINKED_DUPLICATE":
        return "Đã gộp với một phản ánh khác đang được xử lý"
    if status == "CANCELLED":
        return "Đã hủy"
    if status == "INVALID":
        return "Phản ánh không hợp lệ"
    if status == "IN_PROGRESS":
        return "Kỹ thuật viên đang xử lý"

    if assignment == "ASSIGNED":
        return "Đã có kỹ thuật viên, chờ tới lịch xử lý"
    if status == "APPROVED":
        return "Đã duyệt, đang phân công kỹ thuật viên"
    return "Đã tiếp nhận, đang chờ xử lý"


__all__ = ["resident_progress_text", "ticket_display_code"]
