"""Additive Self Dev v4 workflow pieces that do not require Agent changes."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.database.models.ticket import Ticket
from src.models.api.errors import (
    AUTO_ASSIGNMENT_PROPOSAL_REQUIRED,
    INVALID_STATUS_TRANSITION,
    TICKET_NOT_FOUND,
    DomainError,
)
from src.models.enums import TicketStatus
from src.repositories.ticket_repository import TicketRepository
from src.services.assignment_support import AssignmentSideEffects

#: `audit_logs.entity_id` is a non-null UUID and the DIRECT switch is a
#: singleton with an integer key, so it gets one stable derived id rather
#: than a magic literal. An activation audits against the batch that earned
#: it instead; a disable has no batch, only this row.
SETTING_ENTITY_ID = uuid5(NAMESPACE_URL, "fixit:auto-assignment-setting")

TERMINAL_TICKET_STATUSES = {
    TicketStatus.COMPLETED,
    TicketStatus.CANCELLED,
    TicketStatus.INVALID,
    TicketStatus.UNRESOLVABLE,
    TicketStatus.LINKED_DUPLICATE,
}


class DuplicateWorkflowService:
    """Coordinator-initiated duplicate linking.

    Linking only. There is no resident appeal and no Building Management
    dispute-resolution step: if the link is wrong, a coordinator corrects the
    ticket through the normal review actions.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.tickets = TicketRepository(db)
        self.side_effects = AssignmentSideEffects(db)

    def link_duplicate(self, actor_user_id: UUID, ticket_id: UUID, master_ticket_id: UUID, reason: str) -> Ticket:
        if ticket_id == master_ticket_id:
            raise DomainError(INVALID_STATUS_TRANSITION, "Không thể đánh dấu trùng với chính ticket này.", 409)
        try:
            ticket = self._locked_ticket(ticket_id)
            master = self._locked_ticket(master_ticket_id)
            if ticket.status in TERMINAL_TICKET_STATUSES:
                raise DomainError(INVALID_STATUS_TRANSITION, "Ticket này không thể liên kết duplicate.", 409)
            if master.status in {TicketStatus.CANCELLED, TicketStatus.INVALID, TicketStatus.UNRESOLVABLE}:
                raise DomainError(INVALID_STATUS_TRANSITION, "Ticket gốc không còn hợp lệ để liên kết.", 409)

            old = ticket.status
            resolved_master_id = master.duplicate_of_ticket_id or master.id
            now = datetime.now(UTC)
            ticket.duplicate_of_ticket_id = resolved_master_id
            ticket.duplicate_linked_at = now
            ticket.duplicate_reason = reason.strip() or "Coordinator linked duplicate ticket."
            ticket.status = TicketStatus.LINKED_DUPLICATE
            ticket.auto_assignment_paused = True
            ticket.auto_assignment_pause_reason = "Linked duplicate"
            ticket.version += 1
            self.tickets.append_status_history(
                ticket,
                from_status=old,
                to_status=TicketStatus.LINKED_DUPLICATE,
                changed_by=actor_user_id,
                reason=reason.strip() or "Coordinator linked duplicate ticket.",
            )
            self.side_effects.notify_unit(
                ticket,
                "TICKET_LINKED_DUPLICATE",
                "Phản ánh đã được gộp với ticket đang xử lý",
                "Ban quản lý xác định phản ánh này trùng với một phản ánh khác và sẽ theo dõi theo ticket gốc.",
            )
            self.side_effects.audit(
                actor_user_id,
                "LINK_DUPLICATE",
                "TICKET",
                ticket.id,
                {"status": old.value, "duplicate_of_ticket_id": None},
                {"status": ticket.status.value, "duplicate_of_ticket_id": str(resolved_master_id)},
                reason,
                "COORDINATOR",
            )
            self.db.commit()
            return self.tickets.get_coordinator_visible_ticket(ticket.id) or ticket
        except Exception:
            self.db.rollback()
            raise

    def _locked_ticket(self, ticket_id: UUID) -> Ticket:
        # Human coordinator action: a ticket still under AI analysis is not
        # visible here and cannot be linked by guessing its ID.
        ticket = self.tickets.get_coordinator_visible_ticket(ticket_id, lock=True)
        if ticket is None:
            raise DomainError(TICKET_NOT_FOUND, "Ticket không tồn tại.", 404)
        return ticket


class AutoAssignmentSettingsService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.side_effects = AssignmentSideEffects(db)

    def get(self) -> AutoAssignmentSetting:
        row = self.db.get(AutoAssignmentSetting, 1)
        if row is not None:
            return row
        row = AutoAssignmentSetting(id=1, enabled=False, activation_delay="IMMEDIATE", version=1)
        self.db.add(row)
        self.db.commit()
        return row

    def update(self, actor_user_id: UUID, *, enabled: bool, activation_delay: str) -> AutoAssignmentSetting:
        """Coordinator edits of the DIRECT switch.

        Asymmetric, and the asymmetry is the rule rather than an accident of
        implementation:

        * **Off is always available.** Stopping autonomous assignment is a
          safety action, so it goes through immediately and needs no ceremony
          beyond the confirmation the UI asks for.
        * **On is not reachable from here at all.** Turning DIRECT on means
          future eligible tickets get assigned with nobody looking, so it may
          only happen as a consequence of a named coordinator confirming a real
          proposal batch -- `AssignmentProposalService.confirm_batch` is the
          single code path allowed to do it. A request that tries to skip that
          is refused here rather than in the router, because the guard has to
          hold for every caller: another service, a script, a replayed request.

        Changing the delay while DIRECT is already on is an ordinary edit and
        stays allowed; it is not a transition.
        """
        try:
            row = self.db.get(AutoAssignmentSetting, 1, with_for_update=True)
            if enabled and (row is None or not row.enabled):
                # Nothing is written: not the flag, not the delay, not version.
                raise DomainError(
                    AUTO_ASSIGNMENT_PROPOSAL_REQUIRED,
                    "Bật phân việc tự động phải đi qua một đề xuất: tạo đề xuất phân việc, "
                    "xem lại rồi bấm Xác nhận và phân việc.",
                    409,
                )
            if row is None:
                row = AutoAssignmentSetting(id=1)
                self.db.add(row)
                self.db.flush()
            was_enabled = bool(row.enabled)
            row.enabled = enabled
            row.activation_delay = activation_delay
            row.updated_by_user_id = actor_user_id
            if not enabled:
                # The provenance explained why DIRECT was on. With it off, that
                # explanation is history: leaving it would make the next reader
                # think the old batch still authorises something.
                row.activated_by_batch_id = None
                row.activated_by_user_id = None
                row.activated_at = None
            row.version += 1
            row.updated_at = datetime.now(UTC)
            if was_enabled and not enabled:
                self.side_effects.audit(
                    actor_user_id,
                    "DISABLE_DIRECT_AUTO_ASSIGNMENT",
                    "AUTO_ASSIGNMENT_SETTING",
                    SETTING_ENTITY_ID,
                    {"enabled": True},
                    {"enabled": False, "activation_delay": activation_delay},
                    None,
                    "COORDINATOR",
                )
            self.db.commit()
            return row
        except Exception:
            self.db.rollback()
            raise
