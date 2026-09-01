"""Coordinator and Technician assignment workflow operations."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.database.models.attachment import TicketAttachment
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.dispatch.planning import apply_placement, plan_single, reindex_technicians
from src.dispatch.shift import is_within_shift
from src.domain.assignment_guard import assert_ticket_assignment_allowed
from src.domain.assignment_transitions import ACTIVE_ASSIGNMENT_STATUSES, require_assignment_transition
from src.domain.ticket_transitions import require_assignable_ticket, require_ticket_status
from src.models.api.errors import (
    ACTIVE_ASSIGNMENT_EXISTS,
    ASSIGNMENT_NOT_AT_QUEUE_HEAD,
    ASSIGNMENT_NOT_FOUND,
    COMPLETION_EVIDENCE_REQUIRED,
    INVALID_ATTACHMENT,
    TECHNICIAN_NOT_ELIGIBLE,
    TECHNICIAN_NOT_FOUND,
    TICKET_NOT_FOUND,
    DomainError,
)
from src.models.enums import (
    AssignmentEndReason,
    AssignmentSource,
    AssignmentStatus,
    AttachmentType,
    ImageQualityStatus,
    TicketStatus,
)
from src.repositories.assignment_repository import AssignmentRepository
from src.repositories.technician_repository import TechnicianRepository
from src.repositories.ticket_repository import TicketRepository
from src.repositories.upload_session_repository import UploadSessionRepository
from src.services.assignment_support import (
    NEW_ASSIGNMENT_BODY_COORDINATOR,
    NEW_ASSIGNMENT_TITLE,
    AssignmentEvidenceVerifier,
    AssignmentSideEffects,
)
from src.services.dispatch_reassignment import requeue_after_release
from src.services.emergency_review_guard import assert_emergency_review_not_pending
from src.services.storage_service import StorageService

#: How each database names §3's one-live-job-per-technician index when it
#: refuses a write. PostgreSQL -- what production runs -- names the index;
#: SQLite names the column the index is on. Both are matched so a developer on
#: SQLite gets the same error the deployment would give them.
#:
#: Matched narrowly rather than by "is this an IntegrityError": the sibling
#: index `uq_ticket_assignments_one_active_per_ticket` fires on a completely
#: different mistake, and reporting that as "you are already working on
#: something else" would send a technician looking for a job they do not have.
_ONE_IN_PROGRESS_INDEX = "uq_ticket_assignments_one_in_progress_per_technician"
_ONE_IN_PROGRESS_SQLITE = "ticket_assignments.technician_id"


def _is_one_in_progress_violation(error: IntegrityError) -> bool:
    message = str(getattr(error, "orig", error))
    return _ONE_IN_PROGRESS_INDEX in message or _ONE_IN_PROGRESS_SQLITE in message


class AssignmentService:
    def __init__(self, db: Session, storage_service: StorageService | None = None) -> None:
        self.db = db
        self.assignments = AssignmentRepository(db)
        self.tickets = TicketRepository(db)
        self.uploads = UploadSessionRepository(db)
        self.technicians = TechnicianRepository(db)
        self.storage = storage_service or StorageService()
        self.evidence = AssignmentEvidenceVerifier(db, self.storage)
        self.side_effects = AssignmentSideEffects(db)

    def assign(
        self,
        coordinator_user_id: UUID,
        ticket_id: UUID,
        technician_id: UUID,
        *,
        now: datetime | None = None,
    ) -> TicketAssignment:
        try:
            now = now or datetime.now(UTC)
            ticket = self._locked_ticket(ticket_id)
            # Two different facts, both checked. The gate covers a ticket whose
            # priority is not settled yet; the guard covers one whose priority
            # is settled *at* P5. Neither implies the other, and a ticket that
            # was downgraded and then re-escalated satisfies only the second.
            assert_emergency_review_not_pending(self.db, ticket_id)
            assert_ticket_assignment_allowed(ticket)
            self._require_assignment_shift(now)
            technician = self.technicians.get_technician(technician_id, lock=True)
            if technician is None:
                raise DomainError(TECHNICIAN_NOT_FOUND, "KTV không tồn tại.", 404)
            if not technician.is_active or not technician.is_available:
                raise DomainError(TECHNICIAN_NOT_ELIGIBLE, "KTV không sẵn sàng nhận việc.", 409)
            require_assignable_ticket(ticket.status)
            if self.assignments.get_active_for_ticket(ticket.id, lock=True) is not None:
                raise DomainError(ACTIVE_ASSIGNMENT_EXISTS, "Ticket đã có assignment đang hoạt động.", 409)
            if ticket.category_id and not self.technicians.technician_has_skill(technician.user_id, ticket.category_id):
                raise DomainError(TECHNICIAN_NOT_ELIGIBLE, "KTV không có kỹ năng phù hợp.", 409)

            assignment = self.assignments.create_assignment(
                ticket_id=ticket.id,
                technician_id=technician.user_id,
                assigned_by_user_id=coordinator_user_id,
                assignment_source=AssignmentSource.COORDINATOR_MANUAL.value,
            )
            # §4: even a hand-picked assignment is scheduled. Without this the
            # resident has no expected start time and the technician's day has a
            # hole the next automatic pass would book straight over.
            self._schedule(ticket, assignment, technician.user_id, now=now)
            # A human just took this ticket, so any dispatch event still holding
            # it is superseded rather than left to overwrite this decision.
            self._supersede_dispatch(ticket.id)
            self.side_effects.audit(
                coordinator_user_id,
                "ASSIGN_TECHNICIAN",
                "TICKET_ASSIGNMENT",
                assignment.id,
                None,
                {"ticket_id": str(ticket.id), "technician_id": str(technician.user_id)},
                None,
                "COORDINATOR",
            )
            self.side_effects.notify_technician(
                assignment,
                "ASSIGNMENT_CREATED",
                NEW_ASSIGNMENT_TITLE,
                NEW_ASSIGNMENT_BODY_COORDINATOR,
            )
            self.side_effects.notify_unit(
                ticket,
                "TICKET_ASSIGNED",
                "Phản ánh đã được gán kỹ thuật viên",
                "Ban quản lý đã phân công kỹ thuật viên xử lý phản ánh.",
            )
            self.db.commit()
            return self.assignments.get_for_technician(assignment.id, technician.user_id) or assignment
        except Exception:
            self.db.rollback()
            raise

    def assign_case(
        self,
        coordinator_user_id: UUID,
        ticket_ids: list[UUID],
        technician_id: UUID,
        *,
        now: datetime | None = None,
    ) -> list[TicketAssignment]:
        """Assign every ticket of an incident case to one technician, atomically.

        A case is one unit of work (§7.9): handing some members to a
        technician while the rest are left behind because a later one in the
        loop failed eligibility would split a single incident across two
        people, which is exactly what a case exists to prevent. So every
        precondition -- for the technician and for *each* ticket -- is
        checked, under lock, before anything is written, and the whole case
        commits in one transaction. A rejection here always leaves every
        member exactly as it was; there is no `skipped_ticket_ids` because a
        partial outcome is not an allowed outcome.
        """
        try:
            now = now or datetime.now(UTC)
            # §4.5: lock every member in UUID order, the same rule the
            # contract gives PROPOSAL confirm, so a concurrent request
            # touching an overlapping set of tickets cannot deadlock against
            # this one.
            tickets = [self._locked_ticket(ticket_id) for ticket_id in sorted(set(ticket_ids), key=str)]
            # The emergency checks are more specific than the shift gate: an
            # emergency must remain visibly refused even when a coordinator
            # happens to try assigning it outside the working window.
            #
            # One P5 member refuses the whole case. A case is one unit of work,
            # and handing four of five members to a technician while the fifth
            # is an emergency somebody is walking to would split one incident
            # across two responses.
            for ticket in tickets:
                assert_emergency_review_not_pending(self.db, ticket.id)
                assert_ticket_assignment_allowed(ticket)
            self._require_assignment_shift(now)
            technician = self.technicians.get_technician(technician_id, lock=True)
            if technician is None:
                raise DomainError(TECHNICIAN_NOT_FOUND, "KTV không tồn tại.", 404)
            if not technician.is_active or not technician.is_available:
                raise DomainError(TECHNICIAN_NOT_ELIGIBLE, "KTV không sẵn sàng nhận việc.", 409)

            for ticket in tickets:
                # One member held at the emergency gate stops the whole case:
                # a case is one unit of work, and a partial outcome is not an
                # allowed outcome.
                require_assignable_ticket(ticket.status)
                if self.assignments.get_active_for_ticket(ticket.id, lock=True) is not None:
                    raise DomainError(ACTIVE_ASSIGNMENT_EXISTS, "Ticket đã có assignment đang hoạt động.", 409)
                if ticket.category_id and not self.technicians.technician_has_skill(
                    technician.user_id, ticket.category_id
                ):
                    raise DomainError(TECHNICIAN_NOT_ELIGIBLE, "KTV không có kỹ năng phù hợp.", 409)

            # Every precondition passed for every member -- now write. Restore
            # creation order for the assignments themselves and their
            # notifications, independent of the UUID lock order above.
            assignments: list[TicketAssignment] = []
            for ticket in sorted(tickets, key=lambda item: item.created_at):
                assignment = self.assignments.create_assignment(
                    ticket_id=ticket.id,
                    technician_id=technician.user_id,
                    assigned_by_user_id=coordinator_user_id,
                    assignment_source=AssignmentSource.COORDINATOR_MANUAL.value,
                )
                self._schedule(ticket, assignment, technician.user_id, now=now)
                self._supersede_dispatch(ticket.id)
                self.side_effects.audit(
                    coordinator_user_id,
                    "ASSIGN_TECHNICIAN",
                    "TICKET_ASSIGNMENT",
                    assignment.id,
                    None,
                    {"ticket_id": str(ticket.id), "technician_id": str(technician.user_id), "case_assignment": True},
                    None,
                    "COORDINATOR",
                )
                self.side_effects.notify_technician(
                    assignment,
                    "ASSIGNMENT_CREATED",
                    NEW_ASSIGNMENT_TITLE,
                    NEW_ASSIGNMENT_BODY_COORDINATOR,
                )
                self.side_effects.notify_unit(
                    ticket,
                    "TICKET_ASSIGNED",
                    "Phản ánh đã được gán kỹ thuật viên",
                    "Ban quản lý đã phân công kỹ thuật viên xử lý phản ánh.",
                )
                assignments.append(assignment)

            self.db.commit()
            return [
                self.assignments.get_for_technician(assignment.id, technician.user_id) or assignment
                for assignment in assignments
            ]
        except Exception:
            self.db.rollback()
            raise

    def start(self, technician_id: UUID, assignment_id: UUID) -> TicketAssignment:
        """The technician's first positive action on assigned work.

        There is no acknowledgement before this. ASSIGNED goes straight to
        IN_PROGRESS, and the checks that used to be spread between `accept` and
        `start` all happen here, under one lock, in one transaction:

        * the caller owns the assignment (`_locked_assignment`);
        * it is still ASSIGNED (`require_assignment_transition`);
        * the ticket is APPROVED;
        * the technician holds no other live job (§3);
        * **and it is the head of their queue.**

        The queue-head rule is why the backend re-simulates before it reads
        `planned_order`. The order shown on the technician's phone is a copy of
        a number this row holds, and a copy can be stale -- a job completed
        thirty seconds ago on another device already moved the queue. Reindexing
        first means the head is decided against the queue as it is now, not as
        the client last saw it. Building Management changes the order through
        its own auditable actions; a technician cannot reach past it.
        """
        try:
            # Lock the whole queue, in id order, *before* touching any of it.
            # `reindex_technicians` below renumbers every active assignment this
            # technician holds, so two concurrent starts each hold one row and
            # then reach for the other's -- a textbook deadlock, and one that
            # surfaced the first time these calls raced on PostgreSQL. Taking
            # the rows in a single deterministic order is the same rule
            # `assign_case` uses, and it turns the second caller into a waiter
            # rather than a casualty.
            self._lock_technician_queue(technician_id)
            assignment = self._locked_assignment(assignment_id, technician_id)
            require_assignment_transition(assignment.status, AssignmentStatus.IN_PROGRESS)
            require_ticket_status(
                assignment.ticket.status, TicketStatus.APPROVED, "Ticket chưa ở trạng thái sẵn sàng xử lý."
            )
            # §3 hard constraint: one IN_PROGRESS ticket at a time. Checked here
            # rather than at assignment, because a technician legitimately holds
            # a queue of ASSIGNED work -- that queue is what §4's "Do now" and
            # "Next" are made of. The partial unique index behind this catches
            # the concurrent case; this check is what turns it into a readable
            # error instead of an integrity violation.
            self._assert_no_other_in_progress(technician_id, assignment_id)
            now = datetime.now(UTC)
            reindex_technicians(self.db, {technician_id}, now)
            self.db.flush()
            self._assert_queue_head(assignment)

            old_assignment = assignment.status
            old_ticket = assignment.ticket.status
            assignment.status = AssignmentStatus.IN_PROGRESS
            assignment.started_at = now
            assignment.updated_at = now
            assignment.ticket.status = TicketStatus.IN_PROGRESS
            assignment.ticket.started_at = now
            assignment.ticket.version += 1
            self.tickets.append_status_history(
                assignment.ticket,
                from_status=old_ticket,
                to_status=TicketStatus.IN_PROGRESS,
                changed_by=technician_id,
                reason="Technician started work.",
            )
            self.side_effects.notify_unit(
                assignment.ticket, "TICKET_STARTED", "Phản ánh đang được xử lý", "Kỹ thuật viên đã bắt đầu xử lý."
            )
            self.side_effects.audit_assignment_transition(technician_id, assignment, old_assignment, "START_ASSIGNMENT")
            self.db.flush()
            # The live job is now pinned to the front and consumes the rest of
            # the day, so everything queued behind it starts later. Renumbering
            # in the same transaction is what keeps "Làm ngay" honest on the
            # next read rather than until the next unrelated write.
            self._reindex_and_warn(technician_id, now)
            self.db.commit()
            return self.assignments.get_for_technician(assignment.id, technician_id) or assignment
        except IntegrityError as exc:
            # The last line of defence, reached only by a genuine race. Two
            # concurrent calls each pass `_assert_no_other_in_progress` --
            # neither transaction can see the other's uncommitted row -- and
            # `uq_ticket_assignments_one_in_progress_per_technician` refuses the
            # second commit. The rollback comes first, so the loser leaves
            # nothing half-written; then the database's message is translated
            # into the same sentence the service check would have produced, so
            # the technician reads one explanation rather than two.
            self.db.rollback()
            if _is_one_in_progress_violation(exc):
                raise DomainError(
                    TECHNICIAN_NOT_ELIGIBLE,
                    "Bạn đang xử lý một công việc khác. Hãy hoàn thành công việc đó trước khi bắt đầu công việc mới.",
                    409,
                ) from exc
            raise
        except Exception:
            self.db.rollback()
            raise

    def unable_to_handle(self, technician_id: UUID, assignment_id: UUID, reason: str) -> TicketAssignment:
        if not reason.strip():
            raise DomainError(INVALID_ATTACHMENT, "Lý do không xử lý được là bắt buộc.", 400)
        try:
            assignment = self._locked_assignment(assignment_id, technician_id)
            require_assignment_transition(assignment.status, AssignmentStatus.UNABLE_TO_HANDLE)
            old = assignment.status
            now = datetime.now(UTC)
            assignment.status = AssignmentStatus.UNABLE_TO_HANDLE
            assignment.unable_reason = reason.strip()
            assignment.is_active = False
            assignment.ended_at = now
            assignment.end_reason = AssignmentEndReason.UNABLE_TO_HANDLE.value
            assignment.updated_at = now
            previous = assignment.ticket.status
            assignment.ticket.status = TicketStatus.UNRESOLVABLE
            assignment.ticket.version += 1
            self.tickets.append_status_history(
                assignment.ticket,
                from_status=previous,
                to_status=TicketStatus.UNRESOLVABLE,
                changed_by=technician_id,
                reason=reason.strip(),
            )
            self.side_effects.notify_unit(
                assignment.ticket,
                "TICKET_UNRESOLVABLE",
                "Phản ánh không xử lý được",
                "Kỹ thuật viên báo không thể xử lý phản ánh này.",
            )
            self.side_effects.audit_assignment_transition(technician_id, assignment, old, "UNABLE_TO_HANDLE", reason)
            self.db.flush()
            self._reindex_and_warn(technician_id, now)
            self.db.commit()
            return assignment
        except Exception:
            self.db.rollback()
            raise

    def reject(self, technician_id: UUID, assignment_id: UUID, reason: str) -> TicketAssignment:
        if not reason.strip():
            raise DomainError(INVALID_ATTACHMENT, "Lý do từ chối nhận việc là bắt buộc.", 400)
        try:
            assignment = self._locked_assignment(assignment_id, technician_id)
            require_assignment_transition(assignment.status, AssignmentStatus.REJECTED)
            old_assignment = assignment.status
            now = datetime.now(UTC)
            assignment.status = AssignmentStatus.REJECTED
            assignment.rejection_reason = reason.strip()
            assignment.is_active = False
            assignment.rejected_at = now
            assignment.ended_at = now
            assignment.end_reason = AssignmentEndReason.TECHNICIAN_REJECTED.value
            assignment.updated_at = now
            assignment.ticket.reassignment_count += 1
            if assignment.ticket.status == TicketStatus.IN_PROGRESS:
                assignment.ticket.status = TicketStatus.APPROVED
            # §6.1 step 4-5: the ticket is only paused when the AI path is over.
            # Pausing unconditionally would make every rejection manual and quietly
            # delete the reassignment flow.
            assignment.ticket.auto_assignment_paused = False
            assignment.ticket.auto_assignment_pause_reason = None
            assignment.ticket.version += 1
            self.side_effects.notify_unit(
                assignment.ticket,
                "ASSIGNMENT_REJECTED",
                "BQL sẽ phân lại kỹ thuật viên",
                "Kỹ thuật viên vừa từ chối nhận việc, Ban quản lý sẽ phân công lại.",
            )
            self.side_effects.audit_assignment_transition(
                technician_id,
                assignment,
                old_assignment,
                "REJECT_ASSIGNMENT",
                reason.strip(),
            )
            self.side_effects.notify_coordinators(
                assignment.ticket,
                "ASSIGNMENT_REJECTED",
                "Kỹ thuật viên từ chối nhận việc",
                "Một kỹ thuật viên vừa từ chối nhận việc kèm lý do.",
                {"assignment_id": str(assignment.id), "reason": reason.strip()[:200]},
            )
            self.db.flush()
            # The technician who said no is now excluded from this ticket, so a
            # fresh dispatch event routes it to someone else. With Automatic
            # Assignment off, or past the reassignment cap, nothing is enqueued
            # and the ticket waits for Building Management -- which is the same
            # two outcomes the old grace-window path had, without the grace
            # window nobody can act inside any more.
            requeue_after_release(self.db, assignment.ticket)
            self._reindex_and_warn(technician_id, datetime.now(UTC))
            self.db.commit()
            return assignment
        except Exception:
            self.db.rollback()
            raise

    def complete(self, technician_id: UUID, assignment_id: UUID, note: str, upload_ids: list[UUID]) -> TicketAssignment:
        if not note.strip() or not upload_ids:
            raise DomainError(COMPLETION_EVIDENCE_REQUIRED, "Cần ghi chú xử lý và ít nhất một ảnh hoàn thành.", 400)
        try:
            assignment = self._locked_assignment(assignment_id, technician_id)
            require_assignment_transition(assignment.status, AssignmentStatus.COMPLETED)
            sessions = self.evidence.lock_and_verify_completion_sessions(technician_id, upload_ids)
            old_assignment = assignment.status
            old_ticket = assignment.ticket.status
            now = datetime.now(UTC)
            for session in sessions:
                self.db.add(
                    TicketAttachment(
                        ticket_id=assignment.ticket_id,
                        attachment_type=AttachmentType.TECHNICIAN_COMPLETION,
                        storage_bucket="ticket-attachments",
                        object_path=session.storage_path,
                        mime_type=session.mime_type,
                        size_bytes=session.file_size,
                        uploaded_by=technician_id,
                        image_quality_status=ImageQualityStatus.READABLE,
                    )
                )
            self.uploads.mark_consumed(sessions)
            assignment.status = AssignmentStatus.COMPLETED
            assignment.completion_note = note.strip()
            assignment.is_active = False
            assignment.completed_at = now
            assignment.ended_at = now
            assignment.end_reason = AssignmentEndReason.COMPLETED.value
            assignment.updated_at = now
            assignment.ticket.status = TicketStatus.COMPLETED
            assignment.ticket.completed_at = now
            assignment.ticket.version += 1
            self.tickets.append_status_history(
                assignment.ticket,
                from_status=old_ticket,
                to_status=TicketStatus.COMPLETED,
                changed_by=technician_id,
                reason="Technician completed assignment.",
            )
            self.side_effects.notify_unit(
                assignment.ticket, "TICKET_COMPLETED", "Phản ánh đã hoàn thành", "Kỹ thuật viên đã hoàn thành xử lý."
            )
            self.side_effects.audit_assignment_transition(
                technician_id, assignment, old_assignment, "COMPLETE_ASSIGNMENT"
            )
            self.db.flush()
            # The day just got shorter for everything still queued behind this.
            self._reindex_and_warn(technician_id, now)
            self.db.commit()
            return self.assignments.get_for_technician(assignment.id, technician_id) or assignment
        except Exception:
            self.db.rollback()
            raise

    def _schedule(
        self,
        ticket: Ticket,
        assignment: TicketAssignment,
        technician_id: UUID,
        *,
        now: datetime,
    ) -> None:
        """Give a hand-picked assignment the same planned times an automatic one gets."""
        placement = plan_single(
            self.db,
            unit_key=assignment.id,
            ticket_ids=[ticket.id],
            category_codes=[ticket.category.code] if ticket.category else [],
            score=float(ticket.risk_score or 0),
            submitted_at=ticket.created_at,
            technician_id=technician_id,
            now=now,
            # The row already exists, so it would otherwise be loaded back as
            # existing work and booked twice.
            exclude_assignment_id=assignment.id,
        )
        apply_placement(assignment, placement)

    @staticmethod
    def _require_assignment_shift(now: datetime) -> None:
        """Manual placement follows the same 08:00–18:00 rule as the board."""
        if not is_within_shift(now):
            raise DomainError(
                TECHNICIAN_NOT_ELIGIBLE,
                "Chỉ có thể phân công KTV trong ca làm việc từ 08:00 đến 18:00.",
                409,
            )

    def _supersede_dispatch(self, ticket_id: UUID) -> None:
        """Close any open dispatch event for a ticket a human just took.

        Imported here rather than at module scope: `src.dispatch.service` pulls
        in the agent package, and the assignment lifecycle must stay usable in a
        deployment that never loads a model provider.
        """
        from src.services.dispatch_reassignment import supersede_open_event

        supersede_open_event(self.db, ticket_id)

    @staticmethod
    def _assert_queue_head(assignment: TicketAssignment) -> None:
        """Refuse to start anything but "Làm ngay".

        `planned_order` is the scheduler's, and it is the only order that
        counts: a technician who could start the third job in their queue would
        be re-planning the day from a phone, silently, with none of the audit
        trail a Building Management re-plan leaves behind. A null order means
        this row has never been simulated, which is not the head either -- a
        queue position nobody computed is not a promotion.
        """
        if assignment.planned_order != 0:
            raise DomainError(
                ASSIGNMENT_NOT_AT_QUEUE_HEAD,
                "Chỉ được bắt đầu công việc đang ở vị trí 'Làm ngay'. "
                "Hãy hoàn thành hoặc báo lại công việc đứng trước, hoặc đề nghị BQL xếp lại lịch.",
                409,
            )

    def _reindex_and_warn(self, technician_id: UUID, now: datetime) -> None:
        """Renumber the queue and tell Building Management what it just broke."""
        for assignment in reindex_technicians(self.db, {technician_id}, now):
            self.side_effects.notify_coordinators(
                assignment.ticket,
                "ASSIGNMENT_AT_RISK",
                "Lịch xử lý đang trễ",
                "Một công việc trong hàng chờ của kỹ thuật viên đã trễ so với lịch đã cam kết.",
                {
                    "assignment_id": str(assignment.id),
                    "technician_id": str(assignment.technician_id),
                    "slack_seconds": assignment.slack_seconds,
                },
            )

    def _lock_technician_queue(self, technician_id: UUID) -> None:
        """Take every live row of one technician's queue, in a fixed order.

        Ordered by id so that any two callers contending for the same queue
        queue up behind each other instead of deadlocking. Read-only here: the
        point is the lock, not the rows -- `reindex_technicians` reloads them
        through the ORM a moment later.
        """
        from sqlalchemy import select

        self.db.execute(
            select(TicketAssignment.id)
            .where(
                TicketAssignment.technician_id == technician_id,
                TicketAssignment.is_active.is_(True),
                TicketAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
            )
            .order_by(TicketAssignment.id)
            .with_for_update()
        ).all()

    def _assert_no_other_in_progress(self, technician_id: UUID, assignment_id: UUID) -> None:
        from sqlalchemy import select

        held = self.db.scalar(
            select(TicketAssignment.id).where(
                TicketAssignment.technician_id == technician_id,
                TicketAssignment.id != assignment_id,
                TicketAssignment.is_active.is_(True),
                TicketAssignment.status == AssignmentStatus.IN_PROGRESS,
            )
        )
        if held is not None:
            raise DomainError(
                TECHNICIAN_NOT_ELIGIBLE,
                "Bạn đang xử lý một công việc khác. Hãy hoàn thành công việc đó trước khi bắt đầu công việc mới.",
                409,
            )

    def _locked_ticket(self, ticket_id: UUID) -> Ticket:
        # Coordinator-initiated assignment only; a private AI-phase ticket is
        # not assignable and must not be discoverable through this path.
        ticket = self.tickets.get_coordinator_visible_ticket(ticket_id, lock=True)
        if ticket is None:
            raise DomainError(TICKET_NOT_FOUND, "Ticket không tồn tại.", 404)
        return ticket

    def _locked_assignment(self, assignment_id: UUID, technician_id: UUID) -> TicketAssignment:
        assignment = self.assignments.get_for_technician(assignment_id, technician_id, lock=True)
        if assignment is None:
            raise DomainError(ASSIGNMENT_NOT_FOUND, "Assignment không tồn tại.", 404)
        return assignment
