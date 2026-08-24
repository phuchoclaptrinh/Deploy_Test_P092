"""Backend-owned Agent tool operations for the v3 and v4 analysis rounds.

`search_related_tickets` answers two different questions depending on `purpose`
(contract §2.2), and getting them confused is the single most dangerous thing
this module could do:

* `GROUPING` — "is one physical problem spreading?" Water leak and electrical
  short only, same building, adjacent floors, at most three days back. This is
  the pre-v4 behaviour, unchanged, which is why v3 callers that pass no
  `purpose` keep working exactly as before.
* `DUPLICATE` — "is this the same live incident?" Any Category, active tickets
  only, same building **and the exact same `location_id`**, and no lookback
  window at all: a master still being worked on is still the master however long
  ago it was reported.

Everything the geography is derived from — building, floor, `location_id` — is
read off the ticket by Backend. The Agent supplies `purpose` and nothing that
could widen the radius; the `floor`/`location` parameters that survive from the
v3 signature are deliberately ignored.

Sanitization is not optional. A DUPLICATE hit is a ticket from *another* unit,
so what leaves this module is a shape the resident behind it cannot be
identified from: category ids, the location identity, status, status history
timestamps, the current due time, and a summary assembled from catalog data.
Never the description, the reporter, the unit, the reason text on a status
change, or an attachment.
"""

from datetime import UTC, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from src.database.models.ai_agent_session import AIAgentToolCall
from src.database.models.location import Location
from src.database.models.ticket import Ticket
from src.models.agent_schemas_v4 import MAX_SEARCH_RESULTS_V4, AgentSearchPurpose
from src.models.api.errors import (
    CATEGORY_REQUIRED,
    INVALID_STATUS_TRANSITION,
    DomainError,
)
from src.models.enums import TicketStatus
from src.services.agent_common import (
    GROUPING_CODES,
    AgentServiceBase,
)

# §1.5 item 4: a master is "still live" in any of these states. COMPLETED,
# CANCELLED, INVALID, UNRESOLVABLE and LINKED_DUPLICATE are not — the last
# because a duplicate is never itself a master (§1.5 item 7).
DUPLICATE_ACTIVE_STATUSES = (
    TicketStatus.NEW,
    TicketStatus.WAITING_RESIDENT_INFO,
    TicketStatus.APPROVED,
    TicketStatus.IN_PROGRESS,
)

# §2.2 GROUPING: a spreading case is a recent thing, and a finished ticket is
# not part of one.
GROUPING_EXCLUDED_STATUSES = (
    TicketStatus.COMPLETED,
    TicketStatus.CANCELLED,
    TicketStatus.INVALID,
    TicketStatus.UNRESOLVABLE,
    TicketStatus.LINKED_DUPLICATE,
)

MAX_DUPLICATE_CHAIN_DEPTH = 10


class AgentToolService(AgentServiceBase):
    def search_related_tickets(
        self,
        session_id: UUID,
        *,
        ticket_id: UUID,
        category_ids: list[UUID],
        purpose: str = AgentSearchPurpose.GROUPING.value,
        floor: str | None = None,
        location: str | None = None,
        include_resolved: bool = False,
        lookback_days: int = 3,
        limit: int = MAX_SEARCH_RESULTS_V4,
    ) -> dict[str, object]:
        # §2.2: the Agent states a purpose. It does not get to say where to look.
        _ = floor, location

        search_purpose = self._parse_purpose(purpose)

        session = self._session(session_id, lock=True)
        self._validate_session_ticket(session, ticket_id)

        if session.status != "RUNNING":
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Analysis session is not running.",
                409,
            )

        self._increment_tool(session)

        snapshot_ids = set(self._snapshot_by_id(session))
        requested_ids = {str(item) for item in category_ids}
        if not category_ids or not requested_ids <= snapshot_ids:
            raise DomainError(
                CATEGORY_REQUIRED,
                "Search categories must come from the session catalog snapshot.",
                400,
            )

        current = self._ticket(ticket_id)
        limit = max(1, min(int(limit), MAX_SEARCH_RESULTS_V4))

        if search_purpose is AgentSearchPurpose.DUPLICATE:
            rows = self._duplicate_candidates(current, limit)
        else:
            rows = self._grouping_candidates(
                current,
                category_ids,
                include_resolved=include_resolved,
                lookback_days=lookback_days,
                limit=limit,
            )

        related = [self._related_ticket_payload(row) for row in rows]
        response = {"purpose": search_purpose.value, "related_tickets": related}

        # §1.5 item 3 and §1.7.4 both read back from this log: finalize only
        # accepts a master, and only shows a coordinator candidate evidence,
        # that this session actually saw.
        self._log_tool(
            session,
            "search_related_tickets",
            {
                "ticket_id": str(ticket_id),
                "purpose": search_purpose.value,
                "category_ids": [str(item) for item in category_ids],
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

    def _duplicate_candidates(self, current: Ticket, limit: int) -> list[Ticket]:
        """§2.2 DUPLICATE: same asset, still live, any Category, no time limit.

        `location_id` is the asset identity (§11 assumption 2): elevator A and
        elevator B are the same building and the same Category but different
        rows, so matching on the label would merge two unrelated faults. If the
        ticket has no resolvable location the search returns nothing rather than
        falling back to something looser.
        """
        if current.location_id is None:
            return []

        query = (
            select(Ticket)
            .where(
                Ticket.id != current.id,
                Ticket.location_id == current.location_id,
                Ticket.status.in_(DUPLICATE_ACTIVE_STATUSES),
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
            if master is None or master.id == current.id:
                continue
            if master.location_id != current.location_id:
                # The chain led out of this asset; it is not evidence here.
                continue
            if master.status not in DUPLICATE_ACTIVE_STATUSES:
                continue
            resolved.setdefault(master.id, master)
            if len(resolved) >= limit:
                break
        return list(resolved.values())

    def _canonical_master(self, ticket: Ticket) -> Ticket | None:
        """§1.5 item 7: never hand the Agent a candidate that is itself a
        duplicate. Walk to the end of the chain first.

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

    def _grouping_candidates(
        self,
        current: Ticket,
        category_ids: list[UUID],
        *,
        include_resolved: bool,
        lookback_days: int,
        limit: int,
    ) -> list[Ticket]:
        """§2.2 GROUPING: the v3 filter, unchanged."""
        lookback_days = max(1, min(lookback_days, 3))
        since = current.created_at - timedelta(days=lookback_days)

        query = (
            select(Ticket)
            .where(
                Ticket.id != current.id,
                Ticket.created_at >= since,
                Ticket.category_id.in_(category_ids),
            )
            .options(*self._candidate_load_options())
            .order_by(Ticket.created_at.desc())
            .limit(limit)
        )
        if not include_resolved:
            query = query.where(Ticket.status.not_in(GROUPING_EXCLUDED_STATUSES))

        return [row for row in self.db.scalars(query) if self._same_building_adjacent_floor(current, row)]

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

    def _related_ticket_payload(self, row: Ticket) -> dict[str, object]:
        """One search hit, carrying evidence and no identity.

        `floor` and `location` are kept for the v3 Agent, which still reads
        them; `location_id`, `status_history` and `current_due_at` are what v4
        needs to decide "same asset, still active" (§1.5 items 4-5).
        """
        return {
            "ticket_id": str(row.id),
            "category_ids": [str(row.category_id)] if row.category_id else [],
            "location_id": str(row.location_id) if row.location_id else None,
            "location_label": row.location.label if row.location else "",
            # v3 field names, retained so the v3 graph keeps working.
            "floor": (row.location.floor.floor_code if row.location and row.location.floor else ""),
            "location": (row.location.label if row.location else ""),
            "status": row.status.value,
            "summary": self._safe_summary(row),
            "status_history": self._sanitized_status_history(row),
            "current_due_at": self._iso(row.sla_due_at),
            "created_at": self._iso(row.created_at),
        }

    @staticmethod
    def _sanitized_status_history(row: Ticket) -> list[dict[str, str]]:
        """Transitions and when they happened — never who made them or why.

        The `reason` on a status change is free text a coordinator typed and can
        name people, so it never leaves Backend.
        """
        history = sorted(row.status_history or [], key=lambda item: item.created_at)
        return [
            {
                "status": entry.to_status.value,
                "changed_at": AgentToolService._iso(entry.created_at) or "",
            }
            for entry in history[-10:]
        ]

    @staticmethod
    def _iso(value) -> str | None:
        if value is None:
            return None
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()

    def propose_case_grouping(
        self,
        session_id: UUID,
        *,
        ticket_id: UUID,
        related_ticket_ids: list[UUID],
        reason: str,
    ) -> dict[str, object]:
        session = self._session(session_id, lock=True)
        self._validate_session_ticket(session, ticket_id)

        if session.status != "RUNNING":
            raise DomainError(
                INVALID_STATUS_TRANSITION,
                "Analysis session is not running.",
                409,
            )

        self._increment_tool(session)

        ticket = self._ticket(ticket_id)
        snapshot = self._snapshot_by_id(session)

        accepted = False
        rejected_reason: str | None = None
        grouping_category_id: UUID | None = None
        density = 1

        allowed = self._previous_related_candidates(session)

        if not related_ticket_ids:
            rejected_reason = "NO_RELATED_TICKETS"

        elif not set(related_ticket_ids) <= set(allowed):
            rejected_reason = "RELATED_TICKET_NOT_FROM_SESSION_SEARCH"

        else:
            candidate_ids = {
                allowed[item]
                for item in related_ticket_ids
            }

            if len(candidate_ids) != 1:
                rejected_reason = "RELATED_TICKETS_MIX_CATEGORIES"

            else:
                grouping_category_id = next(iter(candidate_ids))
                category = snapshot.get(str(grouping_category_id))

                if (
                    category is None
                    or category["code"] not in GROUPING_CODES
                ):
                    rejected_reason = "CATEGORY_NOT_GROUPING_ELIGIBLE"

                else:
                    valid_related = self._valid_grouping_related(
                        ticket,
                        related_ticket_ids,
                        grouping_category_id,
                    )
                    density = self._density(ticket, valid_related)
                    accepted = (
                        len(valid_related)
                        == len(set(related_ticket_ids))
                    )

                    if not accepted:
                        rejected_reason = "NO_VALID_RELATED_TICKETS"

        # IMPORTANT: this tool only validates/proposes grouping. It must not
        # create IncidentCase/IncidentCaseMember yet because the ticket's final
        # Category is not authoritative until AgentResultService.finalize().
        response = {
            "accepted": accepted,
            "density": density,
            "category_id": (
                str(grouping_category_id)
                if grouping_category_id
                else None
            ),
            "related_ticket_ids": (
                [str(item) for item in related_ticket_ids]
                if accepted
                else []
            ),
            "rejected_reason": rejected_reason,
        }

        self._log_tool(
            session,
            "propose_case_grouping",
            {
                "ticket_id": str(ticket_id),
                "related_ticket_ids": [
                    str(item)
                    for item in related_ticket_ids
                ],
                "reason": reason,
            },
            response,
            accepted,
        )

        self.db.commit()
        return response

    def _previous_related_candidates(
        self,
        session,
    ) -> dict[UUID, UUID]:
        candidates: dict[UUID, UUID] = {}

        calls = self.db.scalars(
            select(AIAgentToolCall).where(
                AIAgentToolCall.session_id == session.id,
                AIAgentToolCall.tool_name == "search_related_tickets",
            )
        )

        for call in calls:
            for item in call.sanitized_response.get(
                "related_tickets",
                [],
            ):
                category_ids = item.get("category_ids") or []
                if len(category_ids) == 1:
                    candidates[UUID(str(item["ticket_id"]))] = UUID(
                        str(category_ids[0])
                    )

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
            .where(
                Ticket.id.in_(related_ticket_ids),
                Ticket.category_id == category_id,
            )
            .options(
                joinedload(Ticket.location).joinedload(Location.floor)
            )
        )

        return [
            row
            for row in rows
            if (
                self._same_building_adjacent_floor(ticket, row)
                and row.created_at
                >= ticket.created_at - timedelta(days=3)
            )
        ]
