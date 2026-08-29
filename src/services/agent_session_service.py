"""Analysis session lifecycle and the pinned category catalog.

`fail_session` is where a *technical* failure lands, and it is deliberately not
one of the six business exit reasons. It writes a FAILED analysis run carrying
an explicit `error_code`, closes the session, and leaves the ticket in manual
review so a coordinator can see it and retry -- never `DUPLICATE_UNCERTAIN`,
which is a real conclusion about the ticket rather than "we do not know what
happened".
"""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select

from src.database.models.ai_agent_session import AIAnalysisSession
from src.database.models.ai_analysis import AIAnalysisRun
from src.models.agent_schemas import ANALYSIS_CONTRACT_VERSION, CategoryCatalogToolResponse
from src.models.api.errors import (
    CATEGORY_REQUIRED,
    INVALID_STATUS_TRANSITION,
    TICKET_NOT_FOUND,
    DomainError,
)
from src.models.enums import AnalysisRunStatus, ClassificationStatus, InvalidReason
from src.services.agent_common import AgentServiceBase
from src.services.p3_review_guard import assert_p3_review_not_pending


class AgentSessionService(AgentServiceBase):
    def start_session(self, ticket_id: UUID, *, model_version: str | None = None) -> AIAnalysisSession:
        # Re-analysing a ticket held at the emergency gate would let the retry
        # action decide the question the gate is asking. A downgrade clears the
        # gate before it resumes, so the legitimate continuation still passes.
        assert_p3_review_not_pending(self.db, ticket_id)
        ticket = self._ticket(ticket_id)
        session = AIAnalysisSession(
            ticket_id=ticket.id,
            model_version=model_version,
            status="RUNNING",
        )
        self.db.add(session)
        self.db.flush()
        self.get_category_catalog(session.id)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_category_catalog(self, session_id: UUID) -> CategoryCatalogToolResponse:
        session = self._session(session_id)

        # A running Agent session is pinned to the first catalog snapshot it saw.
        # Later BQL catalog edits must not mutate the contract of an in-flight session.
        if (
            session.category_catalog_snapshot is not None
            and session.category_catalog_version is not None
        ):
            return CategoryCatalogToolResponse(
                catalog_version=session.category_catalog_version,
                categories=[
                    {
                        "category_id": item["category_id"],
                        "display_name": item["display_name"],
                        "priority_ceiling": item["priority_ceiling"],
                        "base_score": item["base_score"],
                    }
                    for item in session.category_catalog_snapshot
                ],
            )

        rows = self.catalog.list_categories()
        snapshot: list[dict[str, object]] = []
        for row in sorted(rows, key=lambda item: str(item.id)):
            if row.base_score is None:
                raise DomainError(
                    CATEGORY_REQUIRED,
                    "Active Category requires base_score.",
                    409,
                )
            snapshot.append(
                {
                    "category_id": str(row.id),
                    # code is Backend-internal only; it is intentionally omitted
                    # from CategoryCatalogToolResponse.
                    "code": row.code.value if hasattr(row.code, "value") else row.code,
                    "display_name": row.display_name,
                    "priority_ceiling": (
                        row.priority_ceiling.value
                        if row.priority_ceiling
                        else "UNLIMITED"
                    ),
                    "base_score": int(row.base_score),
                }
            )

        version = hashlib.sha256(
            json.dumps(
                snapshot,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

        session.category_catalog_version = version
        session.category_catalog_snapshot = snapshot
        session.updated_at = datetime.now(UTC)
        self.db.flush()

        return CategoryCatalogToolResponse(
            catalog_version=version,
            categories=[
                {
                    "category_id": item["category_id"],
                    "display_name": item["display_name"],
                    "priority_ceiling": item["priority_ceiling"],
                    "base_score": item["base_score"],
                }
                for item in snapshot
            ],
        )

    def fail_session(self, session_id: UUID, reason: str, *, error_code: str = "AGENT_RUNTIME_ERROR") -> bool:
        """Close a session that hit a technical fault, and record why.

        The FAILED run row is what makes the failure explicit and retryable: it
        carries the error code, so the coordinator panel can show "the analysis
        errored" rather than a business conclusion nobody reached, and
        `retry_analysis` has something to supersede.
        """
        session = self._session(session_id, lock=True)
        if session.status != "RUNNING":
            return False

        now = datetime.now(UTC)
        session.status = "FAILED"
        session.completed_at = now
        session.waiting_deadline_at = None

        for question in session.questions:
            if question.status == "PENDING":
                question.status = "EXPIRED"

        ticket = session.ticket
        run_number = (
            int(self.db.scalar(select(func.count(AIAnalysisRun.id)).where(AIAnalysisRun.ticket_id == ticket.id)) or 0) + 1
        )
        self.db.add(
            AIAnalysisRun(
                ticket_id=ticket.id,
                run_number=run_number,
                analysis_session_id=session.id,
                status=AnalysisRunStatus.FAILED,
                error_code=error_code,
                ai_reason=reason,
                contract_version=ANALYSIS_CONTRACT_VERSION,
                model_version=session.model_version,
                category_catalog_version=session.category_catalog_version,
                completed_at=now,
            )
        )

        if ticket.classification_status in {
            ClassificationStatus.PENDING,
            ClassificationStatus.PROCESSING,
        }:
            ticket.classification_status = ClassificationStatus.MANUAL_REVIEW

        self._audit(
            None,
            "AGENT_SESSION_FAILED",
            ticket.id,
            {"session_id": str(session.id), "reason": reason, "error_code": error_code},
        )
        self.db.commit()
        return True

    def manual_review_reject(
        self,
        coordinator_user_id: UUID,
        ticket_id: UUID,
        reason: str,
    ):
        # Human coordinator action. A report still in the private AI phase is
        # not visible to Building Management, and must read as not-found rather
        # than as a status conflict — a 409 would confirm the ticket exists.
        if self.tickets.get_coordinator_visible_ticket(ticket_id) is None:
            raise DomainError(TICKET_NOT_FOUND, "Ticket not found.", 404)
        # An emergency waiting at the P3 gate is also in MANUAL_REVIEW, and
        # rejecting one outright is the most destructive thing this form can
        # do to it. Confirming or downgrading is the decision to take.
        assert_p3_review_not_pending(self.db, ticket_id)
        ticket = self._ticket(ticket_id)
        if ticket.classification_status != ClassificationStatus.MANUAL_REVIEW:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Ticket is not in manual review.",
                409,
            )
        # Building Management rejecting a report ends it. There is no supplement
        # step: the resident is told to send a new report instead. `reason` is
        # the coordinator's internal note and never reaches the resident.
        self._invalidate_ticket(
            ticket,
            reason,
            changed_by=coordinator_user_id,
            invalid_reason=InvalidReason.COORDINATOR_REJECTED,
            notification_body="Phản ánh chưa được tiếp nhận. Vui lòng tạo phản ánh mới với thông tin rõ hơn.",
        )
        self._audit(
            coordinator_user_id,
            "REJECT_MANUAL_REVIEW",
            ticket.id,
            {"reason": reason},
        )
        self.db.commit()
        self.db.refresh(ticket)
        return ticket
