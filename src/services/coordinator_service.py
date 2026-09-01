"""Coordinator review workflow for Self Dev v3."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.category import CategoryCatalog
from src.database.models.information_request import InformationRequest
from src.database.models.ticket import Ticket
from src.models.api.coordinator import ClassificationOverrideRequest, ManualReviewResolveRequest
from src.models.api.errors import (
    CATEGORY_REQUIRED,
    INVALID_STATUS_TRANSITION,
    P0_REVIEW_REQUIRED,
    RISK_ASSESSMENT_REQUIRED,
    TICKET_NOT_FOUND,
    DomainError,
)
from src.models.enums import (
    AnalysisRunStatus,
    ClassificationStatus,
    InformationRequestStatus,
    Priority,
    ResolutionSource,
    RiskAssessmentSource,
    TicketStatus,
)
from src.repositories.catalog_repository import CatalogRepository
from src.repositories.ticket_repository import TicketRepository
from src.services.agent_common import GROUPING_CODES
from src.services.coordinator_support import CoordinatorReadService, CoordinatorScoringSupport, CoordinatorSideEffects
from src.services.emergency_review_guard import assert_emergency_review_not_pending
from src.services.risk_assessment_service import RiskAssessmentService, evidence_payload

# Analysis contracts that stored Category *codes* in the run predictions. v3 and
# v4 both store Category UUIDs (§1.3), so anything newer is compared by id and
# only historical v2 rows take the code path.
LEGACY_CATEGORY_CODE_CONTRACT_VERSIONS = frozenset({"v2"})
GROUPING_PENDING = "PENDING"
GROUPING_NOT_ELIGIBLE = "NOT_ELIGIBLE"


class CoordinatorService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tickets = TicketRepository(db)
        self.catalog = CatalogRepository(db)
        self.risk = RiskAssessmentService(db)
        self.scoring_support = CoordinatorScoringSupport(db)
        self.side_effects = CoordinatorSideEffects(db)
        self.reads = CoordinatorReadService(db)

    def list_tickets(self, page: int, page_size: int, **filters):
        return self.tickets.list_coordinator_tickets(page, page_size, **filters)

    def get_ticket(self, ticket_id: UUID) -> Ticket:
        """The human coordinator read.

        A report still in the private AI phase has not been handed to Building
        Management yet, so it reads as not-found here and in every mutation
        below — guessing a ticket ID during analysis reveals nothing.
        """
        ticket = self.tickets.get_coordinator_visible_ticket(ticket_id)
        if ticket is None:
            raise DomainError(TICKET_NOT_FOUND, "Ticket không tồn tại.", 404)
        return ticket

    def resolve_manual_review(
        self,
        coordinator_user_id: UUID,
        ticket_id: UUID,
        request: ManualReviewResolveRequest,
    ) -> Ticket:
        """§8.3: the Coordinator settles a ticket the analysis could not classify.

        This only ends the classification: the ticket becomes RESOLVED and still
        has to go through the normal APPROVE action before it is ready for
        assignment. Nothing here approves anything.
        """
        try:
            ticket = self._locked(ticket_id)
            # MANUAL_REVIEW is where two different things wait: a report the
            # analysis could not classify, and one it classified as an
            # emergency. Only the first is settled by this form.
            assert_emergency_review_not_pending(self.db, ticket_id)
            if ticket.classification_status != ClassificationStatus.MANUAL_REVIEW:
                raise DomainError(P0_REVIEW_REQUIRED, "Ticket không ở trạng thái P0/manual review.", 409)
            category = self.catalog.get_category(request.category_id)
            if category is None:
                raise DomainError(CATEGORY_REQUIRED, "Category không hợp lệ.", 400)
            self._assert_category_matches_source(ticket, category, request.resolution_source)

            # A ticket that was never scored -- the session failed, or the run
            # stopped before the criteria were established -- cannot be
            # prioritized on its own. The Coordinator supplies the five
            # judgements; an existing assessment is kept as it is and is not
            # re-asked for.
            stored = self.risk.current(ticket)
            manual_criteria = request.criteria if stored is None else None
            if stored is None and manual_criteria is None:
                raise DomainError(
                    RISK_ASSESSMENT_REQUIRED,
                    "Phản ánh chưa được chấm điểm rủi ro. Điều phối viên phải chấm năm tiêu chí 0-4 để tính Priority.",
                    400,
                )

            before = self._classification_snapshot(ticket)
            ticket.category_id = category.id
            ticket.classification_status = ClassificationStatus.RESOLVED
            if manual_criteria is not None:
                self.risk.record(
                    ticket,
                    criteria=manual_criteria.to_domain(),
                    source=RiskAssessmentSource.HUMAN_REVIEW,
                    blocker_codes=request.blockers,
                    evidence=evidence_payload(request.evidence),
                    reviewed_by=coordinator_user_id,
                )
            self._sync_grouping_after_category_change(ticket, category)
            ticket.version += 1
            after = self._classification_snapshot(ticket)
            after["resolution_source"] = request.resolution_source.value
            self.side_effects.audit(
                coordinator_user_id,
                "RESOLVE_MANUAL_REVIEW",
                ticket,
                before,
                after,
                request.reason,
            )
            self.db.commit()
            return self.get_ticket(ticket.id)
        except Exception:
            self.db.rollback()
            raise

    def _assert_category_matches_source(
        self,
        ticket: Ticket,
        category: CategoryCatalog,
        resolution_source: ResolutionSource,
    ) -> None:
        """When the Coordinator names the image or the text as the trusted source,
        the chosen Category must really be in that source's latest prediction.

        Naming a source the analysis never produced would record a decision as
        AI-backed when nothing backed it, so an absent run or an empty prediction
        list is refused rather than waved through — the Coordinator is pointed at
        OTHER, which is what an independent human choice actually is (§8.3) and
        is deliberately not constrained to what the analysis proposed.
        """
        if resolution_source is ResolutionSource.OTHER:
            return
        source_label = "ảnh" if resolution_source is ResolutionSource.IMAGE else "text"
        no_prediction = DomainError(
            CATEGORY_REQUIRED,
            f"Phản ánh này không có kết quả phân loại từ {source_label} để đối chiếu. "
            'Hãy chọn "Danh mục khác" nếu điều phối viên tự quyết định Category.',
            400,
        )
        if not ticket.ai_analysis_runs:
            raise no_prediction

        latest_run = max(ticket.ai_analysis_runs, key=lambda item: item.run_number)
        if resolution_source is ResolutionSource.IMAGE:
            predicted = latest_run.image_categories
        else:
            predicted = latest_run.text_categories
        if not predicted:
            raise no_prediction

        stored = {str(value) for value in predicted}
        if latest_run.contract_version in LEGACY_CATEGORY_CODE_CONTRACT_VERSIONS:
            selected = _category_code(category)
            stored = {value.strip().upper() for value in stored}
        else:
            selected = str(category.id)
        if selected not in stored:
            raise DomainError(
                CATEGORY_REQUIRED,
                f"Category đã chọn không thuộc kết quả phân loại từ {source_label}.",
                400,
            )

    def request_information(
        self,
        coordinator_user_id: UUID,
        ticket_id: UUID,
        message: str,
    ) -> Ticket:
        try:
            ticket = self._locked(ticket_id)
            # Retired, but still routable by an old client, and it moves the
            # ticket's status like anything else.
            assert_emergency_review_not_pending(self.db, ticket_id)
            if ticket.status != TicketStatus.NEW:
                raise DomainError(INVALID_STATUS_TRANSITION, "Chỉ ticket Mới mới có thể yêu cầu bổ sung thông tin.", 409)
            old = ticket.status
            row = InformationRequest(
                ticket_id=ticket.id,
                requested_by=coordinator_user_id,
                request_message=message,
                status=InformationRequestStatus.OPEN,
            )
            self.db.add(row)
            self.db.flush()
            ticket.status = TicketStatus.WAITING_RESIDENT_INFO
            ticket.version += 1
            self.tickets.append_status_history(
                ticket,
                from_status=old,
                to_status=TicketStatus.WAITING_RESIDENT_INFO,
                changed_by=coordinator_user_id,
                reason="Coordinator requested resident information.",
            )
            self.side_effects.notify_unit(
                ticket,
                "INFORMATION_REQUESTED",
                "BQL yêu cầu bổ sung thông tin",
                message,
            )
            self.side_effects.audit(
                coordinator_user_id,
                "REQUEST_INFORMATION",
                ticket,
                {"status": old.value},
                {"status": ticket.status.value, "information_request_id": str(row.id)},
                message,
            )
            self.db.commit()
            return self.get_ticket(ticket.id)
        except Exception:
            self.db.rollback()
            raise

    def approve(self, coordinator_user_id: UUID, ticket_id: UUID) -> Ticket:
        try:
            ticket = self._locked(ticket_id)
            assert_emergency_review_not_pending(self.db, ticket_id)
            if ticket.status != TicketStatus.NEW:
                raise DomainError(INVALID_STATUS_TRANSITION, "Chỉ ticket Mới mới có thể được duyệt.", 409)
            if ticket.classification_status == ClassificationStatus.MANUAL_REVIEW:
                raise DomainError(P0_REVIEW_REQUIRED, "Phải xử lý P0 trước khi duyệt ticket.", 409)
            if (
                ticket.classification_status != ClassificationStatus.RESOLVED
                or ticket.category_id is None
                or ticket.priority is None
            ):
                raise DomainError(CATEGORY_REQUIRED, "Ticket chưa có Category/Priority hợp lệ.", 409)
            self.side_effects.transition(
                coordinator_user_id,
                ticket,
                TicketStatus.APPROVED,
                action="APPROVE_TICKET",
                notification_title="Phản ánh đã được duyệt",
                notification_body="Ban quản lý đã duyệt phản ánh của bạn.",
            )
            self.db.commit()
            return self.get_ticket(ticket.id)
        except Exception:
            self.db.rollback()
            raise

    def override_classification(
        self,
        coordinator_user_id: UUID,
        ticket_id: UUID,
        request: ClassificationOverrideRequest,
    ) -> Ticket:
        try:
            ticket = self._locked(ticket_id)
            # A P3 ticket already has a category and a priority. Changing them
            # here would be answering the gate's question through a door that
            # records no decision, no reviewer and no reason.
            assert_emergency_review_not_pending(self.db, ticket_id)
            if ticket.status != TicketStatus.NEW:
                raise DomainError(INVALID_STATUS_TRANSITION, "Chỉ ticket Mới mới có thể điều chỉnh phân loại.", 409)
            if ticket.classification_status == ClassificationStatus.MANUAL_REVIEW:
                raise DomainError(P0_REVIEW_REQUIRED, "Hãy xử lý hàng chờ duyệt thủ công trước.", 409)
            before = self._classification_snapshot(ticket)
            category: CategoryCatalog | None = None
            if request.category_id is not None:
                category = self.catalog.get_category(request.category_id)
                if category is None:
                    raise DomainError(CATEGORY_REQUIRED, "Category không hợp lệ.", 400)
                ticket.category_id = category.id
                ticket.classification_status = ClassificationStatus.RESOLVED
            if request.priority is not None:
                # A category change alone no longer moves a priority: the
                # category is not an input to the score. Only an explicit
                # priority does, and it is recorded as a human override rather
                # than written straight onto the ticket, so the reason travels
                # with it.
                self.risk.record_priority_override(
                    ticket,
                    priority=request.priority,
                    reason=request.reason,
                    reviewed_by=coordinator_user_id,
                )
            if category is not None:
                self._sync_grouping_after_category_change(ticket, category)
            ticket.version += 1
            self.side_effects.audit(
                coordinator_user_id,
                "OVERRIDE_CLASSIFICATION",
                ticket,
                before,
                self._classification_snapshot(ticket),
                request.reason,
            )
            self.db.commit()
            return self.get_ticket(ticket.id)
        except Exception:
            self.db.rollback()
            raise

    def list_audit_logs(self,
        *,
        actor_user_id: UUID | None = None,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        created_from=None,
        created_to=None,
        limit: int = 100,
    ):
        return self.reads.list_audit_logs(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
        )

    def tickets_summary(self) -> dict[str, object]:
        return self.reads.tickets_summary()
    def sla_performance(self) -> dict[str, object]:
        return self.reads.sla_performance()

    def _locked(self, ticket_id: UUID) -> Ticket:
        ticket = self.tickets.get_coordinator_visible_ticket(ticket_id, lock=True)
        if ticket is None:
            raise DomainError(TICKET_NOT_FOUND, "Ticket không tồn tại.", 404)
        return ticket

    def _classification_snapshot(self, ticket: Ticket) -> dict[str, object]:
        return {
            "category_id": str(ticket.category_id) if ticket.category_id else None,
            "priority": ticket.priority.value if ticket.priority else None,
            "classification_status": ticket.classification_status.value,
            "risk_score": float(ticket.risk_score) if ticket.risk_score is not None else None,
        }

    def _sync_grouping_after_category_change(self, ticket: Ticket, category: CategoryCatalog) -> None:
        """Re-open or close grouping when a human changes the category."""
        latest_run = self._latest_successful_analysis_run(ticket.id)
        if latest_run is None:
            return
        # The emergency band never groups. It is P5 now, not P3: the scale
        # inverted, and a check written against the old top band would let an
        # emergency into a case while excluding routine work.
        if ticket.priority is Priority.P5:
            latest_run.grouping_status = GROUPING_NOT_ELIGIBLE
            return
        latest_run.grouping_status = (
            GROUPING_PENDING if _category_code(category) in GROUPING_CODES else GROUPING_NOT_ELIGIBLE
        )

    def _latest_successful_analysis_run(self, ticket_id: UUID) -> AIAnalysisRun | None:
        return self.db.scalar(
            select(AIAnalysisRun)
            .where(
                AIAnalysisRun.ticket_id == ticket_id,
                AIAnalysisRun.status == AnalysisRunStatus.SUCCEEDED,
            )
            .order_by(AIAnalysisRun.run_number.desc(), AIAnalysisRun.started_at.desc())
            .limit(1)
        )


def _category_code(category: CategoryCatalog) -> str:
    """The catalog stores `code` as the Category enum; legacy rows kept the string."""
    code = category.code
    return str(code.value if hasattr(code, "value") else code).strip().upper()
