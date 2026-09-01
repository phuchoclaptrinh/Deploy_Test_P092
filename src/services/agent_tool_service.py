"""Backend-owned candidate retrieval for the ticket-analysis agent.

`search_related_tickets` answers two different questions depending on `purpose`,
and confusing them is the single most dangerous thing this module could do:

* `DUPLICATE` -- "is this the same incident?" Exact same `location_id`, exact
  same `category_id`, tickets still being handled, plus anything that finished
  inside the last hour. A candidate that is itself linked as a duplicate is
  normalized onto its root master before it is returned, so the Agent never
  sees a master that is not the end of the chain.
* `GROUPING` -- "is one physical problem spreading?" Exact same `category_id`,
  same floor first and then adjacent floors, inside the existing three-day
  window, active tickets only. This one runs in the background *after* the
  round is finalized, so it works on a COMPLETED session and deliberately does
  not spend the resident-facing tool budget: the resident is not waiting on it,
  and charging it against the five interactive calls would take budget away
  from the questions that are.

Everything the scope is derived from -- location, floor, category, time -- is
read off the ticket by Backend. The Agent supplies `purpose` and nothing that
could widen the radius.

Sanitization is not optional. A candidate is a ticket from *another* apartment,
so what leaves this module is a shape its reporter cannot be identified from:
ids, a display code, category and location labels, status, timestamps, and a
short redacted phenomenon excerpt. Never the description in full, the reporter,
the unit, the reason text on a status change, or an attachment.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload, selectinload

from src.database.models.ai_agent_session import AIAgentToolCall
from src.database.models.location import Location
from src.database.models.ticket import Ticket
from src.domain.grouping_guard import ticket_may_join_case
from src.domain.risk_scoring import EMERGENCY_PRIORITY
from src.models.agent_schemas import (
    MAX_DUPLICATE_CANDIDATES,
    MAX_GROUPING_CANDIDATES,
    RECENT_COMPLETION_WINDOW_MINUTES,
    AgentSearchPurpose,
    CandidateTicket,
)
from src.models.api.errors import (
    CATEGORY_REQUIRED,
    INVALID_STATUS_TRANSITION,
    DomainError,
)
from src.models.enums import TicketStatus
from src.services.agent_common import (
    GROUPING_CODES,
    AgentServiceBase,
    reference_code,
)

#: "Still being handled". COMPLETED is handled separately by the one-hour
#: recency rule; CANCELLED, INVALID, UNRESOLVABLE and LINKED_DUPLICATE never
#: come back at all (the last because a duplicate is never itself a master).
DUPLICATE_ACTIVE_STATUSES = (
    TicketStatus.NEW,
    TicketStatus.WAITING_RESIDENT_INFO,
    TicketStatus.APPROVED,
    TicketStatus.IN_PROGRESS,
)

#: A spreading case is a recent thing, and a finished ticket is not part of one.
GROUPING_EXCLUDED_STATUSES = (
    TicketStatus.COMPLETED,
    TicketStatus.CANCELLED,
    TicketStatus.INVALID,
    TicketStatus.UNRESOLVABLE,
    TicketStatus.LINKED_DUPLICATE,
)

GROUPING_LOOKBACK_DAYS = 3
MAX_DUPLICATE_CHAIN_DEPTH = 10


class AgentToolService(AgentServiceBase):
    def search_related_tickets(
        self,
        session_id: UUID,
        *,
        ticket_id: UUID,
        category_id: UUID,
        purpose: str = AgentSearchPurpose.DUPLICATE.value,
        limit: int | None = None,
    ) -> dict[str, object]:
        search_purpose = self._parse_purpose(purpose)

        session = self._session(session_id, lock=True)
        self._validate_session_ticket(session, ticket_id)

        if self.emergency_gate_is_open(ticket_id):
            # Both purposes are blocked. Duplicate work is what the gate exists
            # to defer, and grouping is downstream of it.
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Phản ánh đang chờ duyệt mức khẩn cấp nên chưa tra cứu phản ánh liên quan.",
                409,
            )

        if search_purpose is AgentSearchPurpose.DUPLICATE:
            # Foreground work: it happens inside the resident-facing round and
            # is paid for out of that round's tool budget.
            if session.status != "RUNNING":
                raise DomainError(INVALID_STATUS_TRANSITION, "Analysis session is not running.", 409)
            self._increment_tool(session)
        elif session.status not in {"RUNNING", "COMPLETED"}:
            # Grouping runs after the round is finalized, so its session is
            # COMPLETED by design. A FAILED or TIMED_OUT one never reached a
            # result worth grouping.
            raise DomainError(INVALID_STATUS_TRANSITION, "Analysis session is not usable.", 409)

        if str(category_id) not in self._snapshot_by_id(session):
            raise DomainError(
                CATEGORY_REQUIRED,
                "Search category must come from the session catalog snapshot.",
                400,
            )

        current = self._ticket(ticket_id)
        now = datetime.now(UTC)

        if search_purpose is AgentSearchPurpose.DUPLICATE:
            cap = min(int(limit or MAX_DUPLICATE_CANDIDATES), MAX_DUPLICATE_CANDIDATES)
            rows = self._duplicate_candidates(current, category_id, cap, now)
        else:
            cap = min(int(limit or MAX_GROUPING_CANDIDATES), MAX_GROUPING_CANDIDATES)
            rows = self._grouping_candidates(current, category_id, cap)

        candidates = [self._candidate_payload(row, now).model_dump(mode="json") for row in rows]
        response = {"purpose": search_purpose.value, "candidates": candidates}

        # finalize reads this log back: a duplicate master is only accepted if
        # this session actually saw it, and a coordinator reviewing an uncertain
        # duplicate is shown exactly the candidates the Agent judged.
        self._log_tool(
            session,
            "search_related_tickets",
            {
                "ticket_id": str(ticket_id),
                "purpose": search_purpose.value,
                "category_id": str(category_id),
                "location_id": str(current.location_id) if current.location_id else None,
            },
            response,
        )

        self.db.commit()
        return response

    # ------------------------------------------------------------------
    # Purpose-specific candidate selection.
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_purpose(purpose: str | AgentSearchPurpose) -> AgentSearchPurpose:
        try:
            return AgentSearchPurpose(getattr(purpose, "value", purpose))
        except ValueError as exc:
            raise DomainError(
                CATEGORY_REQUIRED,
                "search_related_tickets purpose must be DUPLICATE or GROUPING.",
                400,
            ) from exc

    def _duplicate_candidates(
        self,
        current: Ticket,
        category_id: UUID,
        limit: int,
        now: datetime,
    ) -> list[Ticket]:
        """Same asset, same Category, still open or only just closed.

        `location_id` is the asset identity: elevator A and elevator B share a
        Category and a building but are different rows, so matching on the
        label would merge two unrelated faults. A ticket with no resolvable
        location returns nothing rather than falling back to something looser.
        """
        if current.location_id is None:
            return []

        recent_cutoff = now - timedelta(minutes=RECENT_COMPLETION_WINDOW_MINUTES)
        query = (
            select(Ticket)
            .where(
                Ticket.id != current.id,
                Ticket.location_id == current.location_id,
                Ticket.category_id == category_id,
                or_(
                    Ticket.status.in_(DUPLICATE_ACTIVE_STATUSES),
                    # "Completed less than an hour ago" is deliberately part of
                    # the candidate set: a resident re-reporting a problem that
                    # was just closed is the exact case the recurrence question
                    # exists for, and it cannot be asked about a ticket the
                    # search never returned.
                    (Ticket.status == TicketStatus.COMPLETED) & (Ticket.completed_at >= recent_cutoff),
                ),
            )
            .options(*self._candidate_load_options())
            .order_by(Ticket.created_at.desc())
            # Over-fetch: normalizing duplicates onto their master collapses
            # rows, and the cap must apply to the answer, not the raw hits.
            .limit(limit * 3)
        )

        resolved: dict[UUID, Ticket] = {}
        for row in self.db.scalars(query):
            master = self._canonical_master(row)
            if master is None or master.id == current.id or master.id in resolved:
                continue
            if master.location_id != current.location_id:
                # The chain led out of this asset; it is not evidence here.
                continue
            resolved[master.id] = master
            if len(resolved) >= limit:
                break
        return list(resolved.values())

    def _canonical_master(self, ticket: Ticket) -> Ticket | None:
        """Never hand the Agent a candidate that is itself a duplicate.

        The depth cap is belt and braces: `ck_tickets_duplicate_not_self` blocks
        the one-step cycle, but a longer loop created before that constraint
        existed must not spin here.
        """
        seen: set[UUID] = set()
        current = ticket
        for _ in range(MAX_DUPLICATE_CHAIN_DEPTH):
            if current.duplicate_of_ticket_id is None:
                return current
            if current.id in seen:
                return None
            seen.add(current.id)
            master = self.db.scalar(
                select(Ticket)
                .where(Ticket.id == current.duplicate_of_ticket_id)
                .options(*self._candidate_load_options())
            )
            if master is None:
                return None
            current = master
        return None

    def _grouping_candidates(self, current: Ticket, category_id: UUID, limit: int) -> list[Ticket]:
        """Same Category, same floor first, then the floors next to it.

        Only the four categories that can physically spread through the
        building are eligible; anything else returns nothing, so a grouping
        question is never even put to the model.
        """
        if current.location is None or current.location.floor is None:
            return []

        # The three-day window is symmetric around this ticket, not a lookback.
        # Grouping runs after duplicate processing is final, which can be well
        # after submission -- by then a neighbour may have reported the same
        # spreading problem *after* this one, and a backwards-only bound would
        # make the case invisible to whichever ticket happened to be resolved
        # first.
        window = timedelta(days=GROUPING_LOOKBACK_DAYS)
        rows = self.db.scalars(
            select(Ticket)
            .where(
                Ticket.id != current.id,
                Ticket.category_id == category_id,
                Ticket.created_at >= current.created_at - window,
                Ticket.created_at <= current.created_at + window,
                Ticket.status.not_in(GROUPING_EXCLUDED_STATUSES),
                # An emergency is not a case member. See
                # `src/domain/grouping_guard.py` -- filtering on status alone
                # let a P5 into the count and pushed a P4 neighbour over 80.
                # `is_distinct_from` rather than `!=` so an unscored ticket,
                # whose priority is NULL, still reaches the model.
                Ticket.priority.is_distinct_from(EMERGENCY_PRIORITY),
            )
            .options(*self._candidate_load_options())
            .order_by(Ticket.created_at.desc())
            .limit(limit * 5)
        )

        same_floor: list[Ticket] = []
        adjacent: list[Ticket] = []
        for row in rows:
            distance = self._floor_distance(current, row)
            if distance == 0:
                same_floor.append(row)
            elif distance == 1:
                adjacent.append(row)
        # Same floor is the stronger signal, so it fills the five slots first.
        return [*same_floor, *adjacent][:limit]

    @staticmethod
    def _floor_distance(current: Ticket, candidate: Ticket) -> int | None:
        if current.location is None or candidate.location is None:
            return None
        if current.location.floor is None or candidate.location.floor is None:
            return None
        return abs(current.location.floor.adjacency_index - candidate.location.floor.adjacency_index)

    # ------------------------------------------------------------------
    # Sanitized response shape.
    # ------------------------------------------------------------------

    @staticmethod
    def _candidate_load_options():
        return (
            joinedload(Ticket.category),
            joinedload(Ticket.location).joinedload(Location.floor),
            selectinload(Ticket.status_history),
        )

    def _candidate_payload(self, row: Ticket, now: datetime) -> CandidateTicket:
        completed_at = self._as_utc(row.completed_at)
        recently_completed = (
            row.status is TicketStatus.COMPLETED
            and completed_at is not None
            and (now - completed_at) < timedelta(minutes=RECENT_COMPLETION_WINDOW_MINUTES)
        )
        return CandidateTicket(
            ticket_id=row.id,
            display_code=reference_code(row.id),
            category_id=row.category_id,
            category_name=row.category.display_name if row.category else "",
            location_id=row.location_id,
            location_label=row.location.label if row.location else "",
            floor_label=(row.location.floor.floor_code if row.location and row.location.floor else ""),
            status=row.status.value,
            summary=self._safe_summary(row),
            created_at=self._as_utc(row.created_at),
            completed_at=completed_at,
            recently_completed=recently_completed,
        )

    # ------------------------------------------------------------------
    # Grouping proposal.
    # ------------------------------------------------------------------

    def propose_case_grouping(
        self,
        session_id: UUID,
        *,
        ticket_id: UUID,
        related_ticket_ids: list[UUID],
        reason: str,
    ) -> dict[str, object]:
        """Validate a proposal against what this session actually searched.

        This only validates and records. The IncidentCase itself is written by
        `AgentResultService.apply_grouping()`, because the ticket's Category is
        not authoritative until the foreground round has been finalized.
        """
        session = self._session(session_id, lock=True)
        self._validate_session_ticket(session, ticket_id)

        if self.emergency_gate_is_open(ticket_id):
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Phản ánh đang chờ duyệt mức khẩn cấp nên chưa gộp cụm sự cố.",
                409,
            )

        if session.status not in {"RUNNING", "COMPLETED"}:
            raise DomainError(INVALID_STATUS_TRANSITION, "Analysis session is not usable.", 409)

        ticket = self._ticket(ticket_id)
        snapshot = self._snapshot_by_id(session)

        accepted = False
        rejected_reason: str | None = None
        grouping_category_id: UUID | None = None
        density = 1

        allowed = self._grouping_candidates_from_log(session)

        if not related_ticket_ids:
            rejected_reason = "NO_RELATED_TICKETS"
        elif not set(related_ticket_ids) <= set(allowed):
            rejected_reason = "RELATED_TICKET_NOT_FROM_SESSION_SEARCH"
        else:
            candidate_categories = {allowed[item] for item in related_ticket_ids}
            if len(candidate_categories) != 1:
                rejected_reason = "RELATED_TICKETS_MIX_CATEGORIES"
            else:
                grouping_category_id = next(iter(candidate_categories))
                category = snapshot.get(str(grouping_category_id))
                if category is None or category["code"] not in GROUPING_CODES:
                    rejected_reason = "CATEGORY_NOT_GROUPING_ELIGIBLE"
                else:
                    valid_related = self._valid_grouping_related(ticket, related_ticket_ids, grouping_category_id)
                    density = self._density(ticket, valid_related)
                    accepted = len(valid_related) == len(set(related_ticket_ids))
                    if not accepted:
                        rejected_reason = "NO_VALID_RELATED_TICKETS"

        response: dict[str, object] = {
            "accepted": accepted,
            "density": density,
            "category_id": str(grouping_category_id) if grouping_category_id else None,
            "related_ticket_ids": [str(item) for item in related_ticket_ids] if accepted else [],
            "rejected_reason": rejected_reason,
        }

        self._log_tool(
            session,
            "propose_case_grouping",
            {
                "ticket_id": str(ticket_id),
                "related_ticket_ids": [str(item) for item in related_ticket_ids],
                "reason": reason,
            },
            response,
            accepted,
        )
        self.db.commit()
        return response

    def _grouping_candidates_from_log(self, session) -> dict[UUID, UUID]:
        """Tickets a grouping proposal may point at.

        Filtered by `purpose`: a DUPLICATE hit shares the Category and the
        location, so it would sail through the re-check below even though the
        Agent never grouping-searched for it. Keeping the two searches apart
        here is what stops duplicate evidence turning into grouping evidence.
        """
        candidates: dict[UUID, UUID] = {}
        calls = self.db.scalars(
            select(AIAgentToolCall).where(
                AIAgentToolCall.session_id == session.id,
                AIAgentToolCall.tool_name == "search_related_tickets",
            )
        )
        for call in calls:
            if (call.sanitized_request or {}).get("purpose") != AgentSearchPurpose.GROUPING.value:
                continue
            for item in (call.sanitized_response or {}).get("candidates", []):
                category_id = item.get("category_id")
                if category_id:
                    candidates[UUID(str(item["ticket_id"]))] = UUID(str(category_id))
        return candidates

    def _valid_grouping_related(
        self,
        ticket: Ticket,
        related_ticket_ids: list[UUID],
        category_id: UUID,
    ) -> list[Ticket]:
        if not related_ticket_ids:
            return []
        rows = self.db.scalars(
            select(Ticket)
            .where(Ticket.id.in_(related_ticket_ids), Ticket.category_id == category_id)
            .options(joinedload(Ticket.location).joinedload(Location.floor))
        )
        # Same symmetric window `_grouping_candidates` selects with, so the
        # re-check cannot reject a candidate the search legitimately returned.
        window = timedelta(days=GROUPING_LOOKBACK_DAYS)
        return [
            row
            for row in rows
            if (
                # Re-checked rather than trusted from the search: a ticket that
                # was P4 when the candidates were fetched can be P5 by the time
                # the proposal arrives, and a replayed proposal carries ids that
                # were valid under a priority nobody holds any more.
                ticket_may_join_case(row)
                and self._same_building_adjacent_floor(ticket, row)
                and abs(row.created_at - ticket.created_at) <= window
            )
        ]


__all__ = [
    "DUPLICATE_ACTIVE_STATUSES",
    "GROUPING_EXCLUDED_STATUSES",
    "GROUPING_LOOKBACK_DAYS",
    "AgentToolService",
]
