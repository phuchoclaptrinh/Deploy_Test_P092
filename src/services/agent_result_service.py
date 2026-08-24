"""Agent v3 result finalization and scoring operations."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from src.database.models.ai_agent_session import (
    AIAgentToolCall,
    AIAnalysisSession,
)
from src.database.models.ai_analysis import AIAnalysisRun
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.location import Location
from src.database.models.ticket import Ticket
from src.models.agent_schemas import (
    AgentAnalysisResultV3,
    AgentExitReason,
    AgentToolUsage,
)
from src.models.api.errors import (
    CATEGORY_REQUIRED,
    INVALID_STATUS_TRANSITION,
    DomainError,
)
from src.models.enums import (
    AnalysisRunStatus,
    AssignmentStatus,
    ClassificationStatus,
    Priority,
    SeveritySource,
    TicketStatus,
)
from src.services.agent_common import GROUPING_CODES, AgentServiceBase
from src.services.coordinator_support import CoordinatorScoringSupport
from src.services.scoring_service import ScoringService
from src.services.ticket_service import TicketService


class AgentResultService(AgentServiceBase):
    TERMINAL_TICKET_STATUSES = {
        TicketStatus.COMPLETED,
        TicketStatus.CANCELLED,
        TicketStatus.INVALID,
        TicketStatus.UNRESOLVABLE,
    }

    def finalize(
        self,
        result: AgentAnalysisResultV3,
    ) -> AIAnalysisRun:
        session = self._session(
            result.analysis_session_id,
            lock=True,
        )
        self._validate_result(session, result)

        backend_usage = self._backend_tool_usage(session)
        ticket = self._ticket(result.ticket_id)
        now = datetime.now(UTC)

        red_flag = (
            result.red_flag_text
            or bool(result.red_flag_signal)
        )
        exit_reason = (
            AgentExitReason.RED_FLAG
            if red_flag
            else result.exit_reason
        )

        # Red flag is a hard safety override. Bad/stale grouping must never block
        # P3 finalization.
        official_grouping = (
            None
            if red_flag
            else self._validated_grouping(session, result)
        )

        run = self._create_run(
            ticket,
            session,
            result,
            exit_reason,
            official_grouping,
            backend_usage,
        )

        version_already_incremented = False

        if red_flag:
            ticket.priority = Priority.P3
            ticket.score_total = None
            ticket.classification_status = ClassificationStatus.RESOLVED
            ticket.red_flag_detected = True
            ticket.severity = result.severity

            CoordinatorScoringSupport(
                self.db,
                ScoringService(),
            ).recalculate_sla(ticket)

        elif exit_reason in {
            AgentExitReason.LIMIT_REACHED,
            AgentExitReason.CATEGORY_MISMATCH,
        }:
            ticket.severity = result.severity
            ticket.classification_status = (
                ClassificationStatus.MANUAL_REVIEW
            )

        elif exit_reason == AgentExitReason.INSUFFICIENT_INPUT:
            self._invalidate_ticket(
                ticket,
                "Agent reported insufficient input.",
            )
            ticket.invalid_reason = "CONTENT_INSUFFICIENT"
            TicketService(self.db).register_ai_insufficient_input_rejection(ticket.reporter_user_id)
            version_already_incremented = True

        else:
            category_id = self._resolve_confident_category(
                session,
                result,
            )

            if category_id is None:
                # The final Backend policy cannot deterministically resolve one
                # Category, therefore it falls back to P0/manual review.
                ticket.severity = result.severity
                ticket.classification_status = (
                    ClassificationStatus.MANUAL_REVIEW
                )

            else:
                snapshot = self._snapshot_by_id(session).get(
                    str(category_id)
                )
                if snapshot is None:
                    raise DomainError(
                        CATEGORY_REQUIRED,
                        "Category not found in session snapshot.",
                        400,
                    )

                if (
                    official_grouping is not None
                    and official_grouping.get("category_id")
                    != str(category_id)
                ):
                    raise DomainError(
                        INVALID_STATUS_TRANSITION,
                        (
                            "Grouping category does not match "
                            "final resolved Category."
                        ),
                        409,
                    )

                ticket.category_id = category_id
                ticket.severity = result.severity

                grouping_to_persist = official_grouping or self._auto_grouping(
                    ticket,
                    category_id,
                    snapshot,
                )

                self._apply_snapshot_scoring(
                    ticket,
                    snapshot,
                    (
                        int(grouping_to_persist["density"])
                        if grouping_to_persist
                        else 1
                    ),
                )
                ticket.classification_status = ClassificationStatus.RESOLVED

                # Materialize an IncidentCase only after Category finalization
                # confirms that the grouping proposal belongs to the same
                # authoritative Category.
                if grouping_to_persist is not None:
                    self._persist_incident_grouping(
                        ticket,
                        category_id,
                        grouping_to_persist,
                    )

        session.status = "COMPLETED"
        session.completed_at = now
        session.waiting_deadline_at = None
        session.model_version = result.model_version

        if not version_already_incremented:
            ticket.version += 1

        self.db.add(run)
        self._audit(
            None,
            "FINALIZE_AGENT_RESULT",
            ticket.id,
            {
                "session_id": str(session.id),
                "exit_reason": exit_reason.value,
            },
        )

        self.db.commit()
        self.db.refresh(run)
        return run

    def _validate_result(
        self,
        session: AIAnalysisSession,
        result: AgentAnalysisResultV3,
    ) -> None:
        self._validate_session_ticket(
            session,
            result.ticket_id,
        )

        if session.status != "RUNNING":
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Analysis session already finalized.",
                409,
            )

        if (
            session.category_catalog_version
            != result.category_catalog_version
        ):
            raise DomainError(
                CATEGORY_REQUIRED,
                "Category catalog version mismatch.",
                409,
            )

        snapshot_ids = set(self._snapshot_by_id(session))
        supplied = {
            str(item)
            for item in (result.text_categories or [])
        }
        supplied |= {
            str(item)
            for item in (result.image_categories or [])
        }

        if not supplied <= snapshot_ids:
            raise DomainError(
                CATEGORY_REQUIRED,
                "Agent returned Category outside session snapshot.",
                400,
            )

        backend_usage = self._backend_tool_usage(session)
        if (
            result.tool_usage.model_dump()
            != backend_usage.model_dump()
        ):
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Agent tool usage does not match backend records.",
                409,
            )

    def _resolve_confident_category(
        self,
        session: AIAnalysisSession,
        result: AgentAnalysisResultV3,
    ) -> UUID | None:
        valid = set(self._snapshot_by_id(session))
        text = {
            str(item)
            for item in (result.text_categories or [])
        } & valid

        if result.image_categories is None:
            return (
                UUID(next(iter(text)))
                if len(text) == 1
                else None
            )

        image = {
            str(item)
            for item in result.image_categories
        } & valid
        matched = text & image

        return (
            UUID(next(iter(matched)))
            if len(matched) == 1
            else None
        )

    def _create_run(
        self,
        ticket: Ticket,
        session: AIAnalysisSession,
        result: AgentAnalysisResultV3,
        exit_reason: AgentExitReason,
        grouping: dict[str, object] | None,
        tool_usage: AgentToolUsage,
    ) -> AIAnalysisRun:
        run_number = (
            int(
                self.db.scalar(
                    select(func.count(AIAnalysisRun.id)).where(
                        AIAnalysisRun.ticket_id == ticket.id
                    )
                )
                or 0
            )
            + 1
        )

        return AIAnalysisRun(
            ticket_id=ticket.id,
            run_number=run_number,
            text_categories=[
                str(item)
                for item in (result.text_categories or [])
            ],
            image_categories=(
                [
                    str(item)
                    for item in result.image_categories
                ]
                if result.image_categories is not None
                else None
            ),
            red_flag_text=result.red_flag_text,
            red_flag_signal=bool(result.red_flag_signal),
            severity=result.severity,
            severity_source=(
                SeveritySource.VISION
                if (
                    result.severity_source
                    and result.severity_source.value == "IMAGE"
                )
                else SeveritySource.TEXT_FALLBACK
                if result.severity_source
                else None
            ),
            status=AnalysisRunStatus.SUCCEEDED,
            completed_at=datetime.now(UTC),
            contract_version="v3",
            analysis_session_id=session.id,
            exit_reason=exit_reason.value,
            is_relevant=result.is_relevant,
            is_confident=result.is_confident,
            confidence_notes=result.confidence_notes,
            grouping=grouping,
            # Backend-owned canonical usage; do not persist Agent-declared flags.
            tool_usage=tool_usage.model_dump(),
            category_catalog_version=session.category_catalog_version,
            model_version=result.model_version,
            analyzed_at=result.analyzed_at,
        )

    def _validated_grouping(
        self,
        session: AIAnalysisSession,
        result: AgentAnalysisResultV3,
    ) -> dict[str, object] | None:
        if result.grouping is None:
            return None

        accepted = list(
            self.db.scalars(
                select(AIAgentToolCall)
                .where(
                    AIAgentToolCall.session_id == session.id,
                    AIAgentToolCall.tool_name
                    == "propose_case_grouping",
                    AIAgentToolCall.success.is_(True),
                )
                .order_by(AIAgentToolCall.sequence.desc())
            )
        )

        if not accepted:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                (
                    "Grouping result requires an accepted "
                    "backend grouping tool call."
                ),
                409,
            )

        latest = accepted[0].sanitized_response

        expected_ids = {
            str(item)
            for item in latest.get("related_ticket_ids", [])
        }
        actual_ids = {
            str(item)
            for item in result.grouping.related_ticket_ids
        }

        if (
            bool(result.grouping.grouped)
            is not bool(latest.get("accepted"))
            or expected_ids != actual_ids
        ):
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                (
                    "Agent grouping result does not match "
                    "backend grouping tool result."
                ),
                409,
            )

        if (
            int(result.grouping.density)
            != int(latest.get("density", 1))
        ):
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                (
                    "Agent grouping density does not match "
                    "backend grouping tool result."
                ),
                409,
            )

        return {
            "grouped": bool(latest["accepted"]),
            "density": int(latest["density"]),
            "related_ticket_ids": sorted(expected_ids),
            "category_id": latest.get("category_id"),
            "reason": result.grouping.reason,
        }

    def _apply_snapshot_scoring(
        self,
        ticket: Ticket,
        snapshot: dict[str, object],
        density_count: int,
    ) -> None:
        if ticket.severity is None:
            raise DomainError(
                CATEGORY_REQUIRED,
                "Agent did not provide Severity for scoring.",
                409,
            )

        ceiling = snapshot.get("priority_ceiling")

        scoring = ScoringService()
        scoring_result = scoring.calculate_dynamic(
            category_code=str(snapshot["code"]),
            base_score=int(snapshot["base_score"]),
            severity=ticket.severity,
            location_type_code=(
                ticket.location.location_type.code
                if ticket.location
                else None
            ),
            density_count=density_count,
            red_flag_detected=ticket.red_flag_detected,
            priority_ceiling=(
                Priority(ceiling)
                if ceiling in {"P1", "P2", "P3"}
                else None
            ),
        )

        ticket.score_total = scoring_result.score_total
        ticket.priority = scoring_result.priority_final

        CoordinatorScoringSupport(
            self.db,
            scoring,
        ).recalculate_sla(ticket)

    def _auto_grouping(
        self,
        ticket: Ticket,
        category_id: UUID,
        snapshot: dict[str, object],
    ) -> dict[str, object] | None:
        if str(snapshot.get("code")) not in GROUPING_CODES:
            return None
        if ticket.location is None:
            return None
        if not self._can_join_new_case(ticket):
            return None
        if self.db.scalar(
            select(IncidentCaseMember.ticket_id).where(
                IncidentCaseMember.ticket_id == ticket.id
            )
        ):
            return None

        candidates = list(
            self.db.scalars(
                select(Ticket)
                .where(
                    Ticket.id != ticket.id,
                    Ticket.category_id == category_id,
                    Ticket.created_at >= ticket.created_at - timedelta(days=3),
                    Ticket.created_at <= ticket.created_at,
                )
                .options(
                    joinedload(Ticket.location).joinedload(Location.floor),
                    joinedload(Ticket.location).joinedload(Location.building),
                )
                .order_by(Ticket.created_at.desc())
                .limit(50)
            )
        )
        related = [
            candidate
            for candidate in candidates
            if (
                self._can_join_new_case(candidate)
                and self._same_building_adjacent_floor(ticket, candidate)
            )
        ]
        if not related:
            return None
        return {
            "accepted": True,
            "category_id": str(category_id),
            "density": self._density(ticket, related),
            "related_ticket_ids": [str(item.id) for item in related],
            "reason": (
                "Backend auto-grouped related tickets after classification."
            ),
        }

    def _can_join_new_case(self, ticket: Ticket) -> bool:
        if ticket.status in self.TERMINAL_TICKET_STATUSES:
            return False
        return not any(
            assignment.is_active
            and assignment.status
            in {
                AssignmentStatus.ASSIGNED,
                AssignmentStatus.ACCEPTED,
                AssignmentStatus.IN_PROGRESS,
            }
            for assignment in ticket.assignments
        )

    def _backend_tool_usage(
        self,
        session: AIAnalysisSession,
    ) -> AgentToolUsage:
        tool_names = set(
            self.db.scalars(
                select(AIAgentToolCall.tool_name).where(
                    AIAgentToolCall.session_id == session.id
                )
            )
        )

        return AgentToolUsage(
            total_tool_calls=session.total_tool_calls,
            ask_resident_rounds=session.ask_resident_rounds,
            ask_resident_elapsed_seconds=(
                session.ask_resident_elapsed_seconds
            ),
            search_related_tickets_called=(
                "search_related_tickets" in tool_names
            ),
            propose_case_grouping_called=(
                "propose_case_grouping" in tool_names
            ),
        )

    def _persist_incident_grouping(
        self,
        ticket: Ticket,
        category_id: UUID,
        grouping: dict[str, object],
    ) -> None:
        if ticket.location is None:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Grouping requires a valid ticket location.",
                409,
            )

        related_ids = [
            UUID(str(item))
            for item in grouping.get("related_ticket_ids", [])
        ]
        if not related_ids:
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Accepted grouping requires related tickets.",
                409,
            )

        related = list(
            self.db.scalars(
                select(Ticket)
                .where(
                    Ticket.id.in_(related_ids),
                    Ticket.category_id == category_id,
                )
                .options(
                    joinedload(Ticket.location).joinedload(Location.floor),
                    selectinload(Ticket.assignments),
                )
            )
        )

        unique_ids = set(related_ids)
        valid = [
            row
            for row in related
            if (
                row.id in unique_ids
                and self._can_join_new_case(row)
                and self._same_building_adjacent_floor(ticket, row)
                and row.created_at
                >= ticket.created_at - timedelta(days=3)
            )
        ]

        if len({row.id for row in valid}) != len(unique_ids):
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Grouping tickets are no longer valid.",
                409,
            )

        official_density = self._density(ticket, valid)
        if official_density != int(grouping["density"]):
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Grouping density changed before finalization.",
                409,
            )

        case = IncidentCase(
            category_id=category_id,
            building_id=ticket.location.building_id,
            window_start=ticket.created_at - timedelta(days=3),
            window_end=ticket.created_at + timedelta(seconds=1),
            density_value=official_density,
        )
        self.db.add(case)
        self.db.flush()

        for item in [ticket, *valid]:
            exists = self.db.scalar(
                select(IncidentCaseMember.ticket_id).where(
                    IncidentCaseMember.ticket_id == item.id
                )
            )
            if exists is None:
                self.db.add(
                    IncidentCaseMember(
                        case_id=case.id,
                        ticket_id=item.id,
                        source_unit_id=item.source_unit_id,
                    )
                )
