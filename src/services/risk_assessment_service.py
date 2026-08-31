"""Writing a risk assessment, and the one place a ticket's priority changes.

Everything that can move a ticket's priority goes through `record()`: the Agent
finishing an analysis, a case gaining or losing a member, a coordinator
downgrading an emergency, a duplicate pulling its master up to P5. Each one
appends a revision and refreshes the ticket's cache in the same call, so the
cache cannot drift from the record it summarizes.

Nothing here updates a previous revision, and nothing deletes one. The
append-only rule is what makes `docs/risk_scoring_v2.md` §7.3 possible: a
member that grouping pushed to P5 is detached from its case immediately
afterwards, and without a row written *before* the detach there would be no
surviving evidence that the case ever justified the escalation.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database.models.ticket import Ticket
from src.database.models.ticket_risk_assessment import TicketRiskAssessment
from src.domain.risk_scoring import (
    CRITERION_NAMES,
    BlockerCode,
    RiskCriterionScores,
    RiskScoringResult,
    calculate_risk_score,
)
from src.domain.sla_clock import CURRENT_POLICY, add_sla_duration, sla_duration
from src.models.enums import Priority, RiskAssessmentSource


class RiskAssessmentService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # -- reading ------------------------------------------------------------

    def current(self, ticket: Ticket) -> TicketRiskAssessment | None:
        """The revision the ticket's cache was written from."""
        if ticket.current_risk_assessment_id is None:
            return None
        return self.db.get(TicketRiskAssessment, ticket.current_risk_assessment_id)

    def _next_revision_no(self, ticket_id: UUID) -> int:
        highest = self.db.scalar(
            select(func.max(TicketRiskAssessment.revision_no)).where(TicketRiskAssessment.ticket_id == ticket_id)
        )
        return int(highest or 0) + 1

    # -- writing ------------------------------------------------------------

    def record(
        self,
        ticket: Ticket,
        *,
        criteria: RiskCriterionScores,
        source: RiskAssessmentSource,
        blocker_codes: Iterable[BlockerCode | str] = (),
        evidence: dict[str, object] | None = None,
        unknown_facts: list[str] | None = None,
        backend_scope_score: int | None = None,
        confirmed_affected_unit_count: int | None = None,
        ai_analysis_run_id: UUID | None = None,
        case_id_snapshot: UUID | None = None,
        case_density_snapshot: int | None = None,
        override_reason: str | None = None,
        reviewed_by: UUID | None = None,
        final_priority: Priority | None = None,
    ) -> TicketRiskAssessment:
        """Append one revision and refresh the ticket cache from it.

        `final_priority` is the single override hatch, and it exists for one
        case only: a coordinator downgrading an emergency by hand. It requires
        `override_reason`, because a priority that disagrees with its own score
        and carries no explanation is unreadable six weeks later.
        """
        if final_priority is not None and not (override_reason or "").strip():
            raise ValueError("Overriding the calculated priority requires an override_reason.")

        result = calculate_risk_score(
            criteria,
            blocker_codes=blocker_codes,
            backend_scope_score=backend_scope_score,
        )
        previous = self.current(ticket)
        assessment = TicketRiskAssessment(
            ticket_id=ticket.id,
            revision_no=self._next_revision_no(ticket.id),
            source=source,
            ai_analysis_run_id=ai_analysis_run_id,
            supersedes_id=previous.id if previous is not None else None,
            human_safety_score=result.criteria.human_safety,
            property_spread_score=result.criteria.property_spread,
            essential_function_score=result.criteria.essential_function,
            ai_scope_score=result.ai_scope_score,
            backend_scope_score=result.backend_scope_score,
            effective_scope_score=result.effective_scope_score,
            deterioration_speed_score=result.criteria.deterioration_speed,
            confirmed_affected_unit_count=confirmed_affected_unit_count,
            blocker_codes=[code.value for code in result.blocker_codes],
            evidence=evidence or {},
            unknown_facts=unknown_facts or [],
            risk_score=result.risk_score,
            score_priority=result.score_priority,
            blocker_floor=result.blocker_floor,
            final_priority=final_priority or result.final_priority,
            rubric_version=result.rubric_version,
            case_id_snapshot=case_id_snapshot,
            case_density_snapshot=case_density_snapshot,
            override_reason=override_reason,
            reviewed_by=reviewed_by,
        )
        self.db.add(assessment)
        self.db.flush()
        self.apply(ticket, assessment)
        return assessment

    def rescore(
        self,
        ticket: Ticket,
        *,
        source: RiskAssessmentSource,
        backend_scope_score: int | None,
        confirmed_affected_unit_count: int | None = None,
        case_id_snapshot: UUID | None = None,
        case_density_snapshot: int | None = None,
    ) -> TicketRiskAssessment | None:
        """Re-run the formula over the criteria already on record.

        Used when nothing about the ticket changed except how many apartments
        the backend can now confirm. The Agent is not called again -- its four
        other judgements are still the best evidence there is, and re-asking a
        model the same question is how two members of one case end up with
        different human-safety scores for the same flood.

        Returns None for a ticket that has never been assessed: there are no
        criteria to rescore, and inventing zeroes would publish a P1.

        **A standing human override survives.** If the current revision carries
        an `override_reason`, a coordinator has already looked at this ticket and
        disagreed with the calculator. Re-deriving the priority from the same
        criteria would land on the same band they overruled and silently undo
        the decision -- which is exactly what the emergency gate exists to
        prevent. The new scope is still recorded; only the band is carried
        forward, with the reason that set it.
        """
        previous = self.current(ticket)
        if previous is None:
            return None
        override = (previous.override_reason or "").strip() or None
        criteria = RiskCriterionScores(
            human_safety=previous.human_safety_score,
            property_spread=previous.property_spread_score,
            essential_function=previous.essential_function_score,
            # The Agent's own estimate, not the effective value: re-deriving
            # from the effective score would let one confirmed count become the
            # floor for the next one.
            affected_scope=previous.ai_scope_score,
            deterioration_speed=previous.deterioration_speed_score,
        )
        return self.record(
            ticket,
            criteria=criteria,
            source=source,
            blocker_codes=[BlockerCode(code) for code in (previous.blocker_codes or [])],
            evidence=dict(previous.evidence or {}),
            unknown_facts=list(previous.unknown_facts or []),
            backend_scope_score=backend_scope_score,
            confirmed_affected_unit_count=confirmed_affected_unit_count,
            case_id_snapshot=case_id_snapshot,
            case_density_snapshot=case_density_snapshot,
            final_priority=previous.final_priority if override else None,
            override_reason=override,
            reviewed_by=previous.reviewed_by if override else None,
        )

    def record_priority_override(
        self,
        ticket: Ticket,
        *,
        priority: Priority,
        reason: str,
        reviewed_by: UUID | None = None,
        source: RiskAssessmentSource = RiskAssessmentSource.HUMAN_REVIEW,
    ) -> TicketRiskAssessment:
        """A human sets the band directly, keeping the criteria on record.

        The five judgements are not rewritten to justify the new priority --
        that would forge evidence. The revision says what the rubric computed,
        what the human chose instead, and why, and the three sit side by side.

        A ticket that was never assessed -- the analysis failed, or a
        coordinator is classifying a report by hand -- still gets a revision.
        Its criteria are zero and every criterion name is in `unknown_facts`,
        which is the honest reading: the rubric never ran, so the score says P1
        because nothing was scored, and the priority next to it is a human's.
        Writing plausible-looking scores to justify the chosen band would be
        fabricating evidence.
        """
        previous = self.current(ticket)
        if previous is None:
            return self.record(
                ticket,
                criteria=RiskCriterionScores(**dict.fromkeys(CRITERION_NAMES, 0)),
                source=source,
                unknown_facts=list(CRITERION_NAMES),
                final_priority=priority,
                override_reason=reason,
                reviewed_by=reviewed_by,
            )
        criteria = RiskCriterionScores(
            human_safety=previous.human_safety_score,
            property_spread=previous.property_spread_score,
            essential_function=previous.essential_function_score,
            affected_scope=previous.ai_scope_score,
            deterioration_speed=previous.deterioration_speed_score,
        )
        return self.record(
            ticket,
            criteria=criteria,
            source=source,
            blocker_codes=[BlockerCode(code) for code in (previous.blocker_codes or [])],
            evidence=dict(previous.evidence or {}),
            unknown_facts=list(previous.unknown_facts or []),
            backend_scope_score=previous.backend_scope_score,
            confirmed_affected_unit_count=previous.confirmed_affected_unit_count,
            final_priority=priority,
            override_reason=reason,
            reviewed_by=reviewed_by,
        )

    def apply(self, ticket: Ticket, assessment: TicketRiskAssessment) -> None:
        """Point the ticket's cache at one revision and recompute its deadline.

        The one place a ticket's priority changes, which is why the emergency
        release hangs off it. A ticket that has just become a P5 while somebody
        was assigned to it is exactly the case the assignment guards cannot
        catch: they refuse *new* assignments, and this one already exists.
        """
        became_emergency = assessment.final_priority is Priority.P5 and ticket.priority is not Priority.P5
        ticket.current_risk_assessment_id = assessment.id
        ticket.risk_score = assessment.risk_score
        ticket.priority = assessment.final_priority
        self.recalculate_sla(ticket)
        if became_emergency:
            # Imported here rather than at module scope: the release path
            # reaches the dispatch planner, and this module is imported by the
            # agent pipeline, which must stay free of it.
            from src.services.emergency_release import release_assignments_for_emergency

            release_assignments_for_emergency(self.db, ticket)

    def recalculate_sla(self, ticket: Ticket) -> None:
        """When somebody has to have *started* on this ticket.

        Through `src.domain.sla_clock`, which is also what the simulator uses,
        so a projection and a live deadline cannot disagree about where 18:00
        is. P1-P4 consume service minutes and pause overnight; P5 runs on the
        wall clock, because a five-minute emergency promise that paused
        overnight would not be an emergency promise.
        """
        if ticket.priority is None:
            ticket.sla_due_at = None
            return
        started = ticket.sla_started_at or ticket.created_at or datetime.now(UTC)
        ticket.sla_started_at = started
        ticket.sla_due_at = add_sla_duration(
            started,
            sla_duration(ticket.priority, CURRENT_POLICY),
            ticket.priority,
            CURRENT_POLICY,
        )


def evidence_payload(evidence) -> dict[str, object]:
    """Normalize an Agent evidence object into the JSON column's shape.

    The five criterion keys hold lists; `blockers` holds a `{code: [lines]}`
    mapping, so it is copied as a mapping. Flattening it would lose which line
    justified which floor, which is the whole reason it is keyed.
    """
    if evidence is None:
        return {}
    if isinstance(evidence, dict):
        source = {key: value for key, value in evidence.items() if key in {*CRITERION_NAMES, "blockers"}}
    else:
        source = {name: getattr(evidence, name, None) for name in (*CRITERION_NAMES, "blockers")}
    payload: dict[str, object] = {name: list(source.get(name) or []) for name in CRITERION_NAMES}
    blockers = source.get("blockers") or {}
    if isinstance(blockers, dict):
        payload["blockers"] = {code: list(lines or []) for code, lines in blockers.items()}
    else:
        # A caller still passing the old flat list. Kept readable rather than
        # dropped, under a key that says nobody attributed it.
        payload["blockers"] = {"UNATTRIBUTED": list(blockers)} if blockers else {}
    return payload


__all__ = ["RiskAssessmentService", "RiskScoringResult", "evidence_payload"]
