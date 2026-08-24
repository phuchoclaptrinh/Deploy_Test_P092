"""Tool interface the Agent v4 analysis graph calls (contract §2).

The port is the shape the contract specifies. In particular
`search_related_tickets` carries `purpose`, because DUPLICATE and GROUPING are
two different questions with two different Backend filters:

* `DUPLICATE` — is this the same live incident? Any Category, active tickets
  only, same building and the *exact same* `location_id`, no 3-day cutoff while
  the master is still active.
* `GROUPING` — is this one physical problem spreading? Water leak and
  electrical short only, same building, adjacent floors, at most 3 days back.

`BackendAnalysisToolAdapterV4` bridges the port onto `AgentToolService`, which
implements both filters, so it declares both purposes as supported.
`supported_purposes` stays part of the protocol rather than being deleted: a
caller may still inject a narrower port — an evaluation harness, or a Backend
that has not run the v4 migration — and the graph must then skip the step and
report a dependency gap. What it must never do is relabel GROUPING-shaped rows
as duplicate evidence, because that is exactly how a ticket gets auto-linked to
a master nobody checked was the same asset.

Failures are typed so the graph can keep a technical problem from turning into
a business exit:

* `ToolPurposeUnsupportedError` — a declared capability gap. Surfaced to the caller
  as a dependency gap; the affected step is skipped, not faked.
* `ToolBudgetExhaustedError` — the Backend budget ran out. A business signal.
* `ToolExecutionError` — the call genuinely failed. Aborts the run.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable
from uuid import UUID

from src.models.agent_schemas_v4 import (
    AgentSearchPurpose,
    AskResidentRequestV4,
    ProposeCaseGroupingRequestV4,
    ProposeCaseGroupingResponseV4,
    RelatedTicketStatusChangeV4,
    RelatedTicketV4,
    SearchRelatedTicketsRequestV4,
    SearchRelatedTicketsResponseV4,
)
from src.models.api.errors import AGENT_BUDGET_EXHAUSTED, DomainError
from src.observability import annotate, span
from src.services.agent_backend_service import AgentBackendService

logger = logging.getLogger(__name__)


class ToolPortError(RuntimeError):
    """Base for everything that goes wrong at the tool boundary."""


class ToolPurposeUnsupportedError(ToolPortError):
    """The port cannot serve this purpose at all.

    A missing Backend capability, not a failed call and not a business answer.
    The graph records it as a dependency gap and skips the step.
    """

    def __init__(self, purpose: AgentSearchPurpose, detail: str) -> None:
        super().__init__(detail)
        self.purpose = purpose
        self.detail = detail


class ToolBudgetExhaustedError(ToolPortError):
    """Backend refused because the session budget is spent (§1.1).

    The one tool failure that *is* a business signal: it maps to LIMIT_REACHED.
    """


class ToolExecutionError(ToolPortError):
    """The tool call failed for a technical reason.

    Never convertible into DUPLICATE_UNCERTAIN, LIMIT_REACHED,
    INSUFFICIENT_INPUT or ANALYSIS_COMPLETE.
    """

    def __init__(self, tool_name: str, detail: str) -> None:
        super().__init__(f"{tool_name}: {detail}")
        self.tool_name = tool_name
        self.detail = detail


@runtime_checkable
class AnalysisToolPortV4(Protocol):
    """What the graph is allowed to ask Backend to do.

    `supported_purposes` is part of the contract, not a hint: the graph checks
    it before spending a tool call, and a port that omits DUPLICATE simply never
    produces duplicate evidence.
    """

    supported_purposes: frozenset[AgentSearchPurpose]

    def search_related_tickets(self, request: SearchRelatedTicketsRequestV4) -> SearchRelatedTicketsResponseV4: ...

    def propose_case_grouping(self, request: ProposeCaseGroupingRequestV4) -> ProposeCaseGroupingResponseV4: ...

    def ask_resident(self, request: AskResidentRequestV4) -> UUID: ...


def supports(port: AnalysisToolPortV4, purpose: AgentSearchPurpose) -> bool:
    return purpose in getattr(port, "supported_purposes", frozenset())


# Reported to the caller so a gap is visible in logs and hand-off notes instead
# of only in a design document. These are only raised by a port that actually
# declares the capability missing — the Backend adapter below serves both
# purposes, so on this Backend the search gaps do not occur.
DUPLICATE_SEARCH_GAP = (
    "search_related_tickets(purpose=DUPLICATE) is not available on this tool port, so same-incident "
    "duplicate detection cannot run."
)
DUPLICATE_PAYLOAD_GAP = (
    "The search response carries no location_id, so the same-asset condition required by contract "
    "§1.5 item 5 cannot be established."
)
GROUPING_SEARCH_GAP = (
    "search_related_tickets(purpose=GROUPING) is unavailable on this tool port, so spreading-case detection cannot run."
)
# Backend now implements every capability the v4 analysis round depends on:
# both search purposes and `AgentResultV4Service.finalize_v4()`. The tuple stays
# so a caller can still report gaps raised by a narrower injected port.
BACKEND_DEPENDENCY_NOTES: tuple[str, ...] = ()


class BackendAnalysisToolAdapterV4:
    """Adapts the v4 tool port onto `AgentToolService`.

    Both purposes are served: `AgentToolService.search_related_tickets` applies
    either the DUPLICATE filter (exact same `location_id`, live tickets only, no
    lookback, candidates normalized onto their canonical master) or the GROUPING
    one (spreading categories, adjacent floors, three days), and returns the
    sanitized evidence §2.2 specifies.
    """

    supported_purposes = frozenset({AgentSearchPurpose.DUPLICATE, AgentSearchPurpose.GROUPING})

    def __init__(self, backend: AgentBackendService) -> None:
        self.backend = backend

    def search_related_tickets(self, request: SearchRelatedTicketsRequestV4) -> SearchRelatedTicketsResponseV4:
        if request.purpose not in self.supported_purposes:
            raise ToolPurposeUnsupportedError(request.purpose, DUPLICATE_SEARCH_GAP)

        # A retrieval step, nested under the run's root span. The candidates
        # themselves are not logged: they are another unit's tickets, and the
        # count plus the purpose is what makes a trace readable anyway.
        with span(
            "tool.search_related_tickets",
            purpose=request.purpose.value,
            ticket_id=str(request.ticket_id),
            category_count=len(request.category_ids),
            limit=request.limit,
        ) as active:
            return self._search(request, active)

    def _search(
        self, request: SearchRelatedTicketsRequestV4, active: object
    ) -> SearchRelatedTicketsResponseV4:
        try:
            raw = self.backend.search_related_tickets(
                request.session_id,
                ticket_id=request.ticket_id,
                category_ids=list(request.category_ids),
                purpose=request.purpose.value,
                # Backend reads building/floor/location off the ticket itself; the
                # Agent is not allowed to widen the radius, so nothing is passed.
                floor=None,
                location=None,
                limit=request.limit,
            )
        except DomainError as exc:
            raise _translate(exc, "search_related_tickets") from exc

        related = [self._to_related_ticket(item) for item in raw.get("related_tickets", [])][: request.limit]
        annotate(active, output={"candidate_count": len(related)})
        return SearchRelatedTicketsResponseV4(purpose=request.purpose, related_tickets=related)

    @staticmethod
    def _to_related_ticket(item: dict[str, object]) -> RelatedTicketV4:
        location_id = item.get("location_id")
        return RelatedTicketV4(
            ticket_id=UUID(str(item["ticket_id"])),
            category_ids=[UUID(str(value)) for value in (item.get("category_ids") or [])],
            # Nullable on purpose: a Backend that cannot resolve a distinct asset
            # has to be visible as such, and the graph refuses to auto-link a
            # duplicate whose candidate carries no location identity (§1.5 item 5).
            location_id=UUID(str(location_id)) if location_id else None,
            location_label=str(item.get("location_label") or item.get("location") or ""),
            status=str(item.get("status") or ""),
            summary=str(item.get("summary") or ""),
            status_history=[
                RelatedTicketStatusChangeV4(status=str(entry["status"]), changed_at=entry["changed_at"])
                for entry in (item.get("status_history") or [])
                if entry.get("status") and entry.get("changed_at")
            ],
            current_due_at=item.get("current_due_at"),
            created_at=item.get("created_at"),
        )

    def propose_case_grouping(self, request: ProposeCaseGroupingRequestV4) -> ProposeCaseGroupingResponseV4:
        with span(
            "tool.propose_case_grouping",
            ticket_id=str(request.ticket_id),
            related_ticket_count=len(request.related_ticket_ids),
        ) as active:
            return self._propose(request, active)

    def _propose(
        self, request: ProposeCaseGroupingRequestV4, active: object
    ) -> ProposeCaseGroupingResponseV4:
        try:
            raw = self.backend.propose_case_grouping(
                request.session_id,
                ticket_id=request.ticket_id,
                related_ticket_ids=list(request.related_ticket_ids),
                reason=request.reason,
            )
        except DomainError as exc:
            raise _translate(exc, "propose_case_grouping") from exc

        annotate(
            active,
            output={
                "accepted": bool(raw.get("accepted")),
                # Backend-owned, and the number a grouping decision turns on.
                "density": int(raw.get("density") or 1),
                "rejected_reason": raw.get("rejected_reason"),
            },
        )
        return ProposeCaseGroupingResponseV4(
            accepted=bool(raw.get("accepted")),
            # Backend owns Density. The Agent reads it here for context only and
            # must never copy it into AgentAnalysisResultV4 (§1.4).
            density=int(raw.get("density") or 1),
            category_id=UUID(str(raw["category_id"])) if raw.get("category_id") else None,
            related_ticket_ids=[UUID(str(value)) for value in (raw.get("related_ticket_ids") or [])],
            rejected_reason=raw.get("rejected_reason"),
        )

    def ask_resident(self, request: AskResidentRequestV4) -> UUID:
        # The question text is the resident's business and stays out of the
        # trace; that a question was asked, and of what type, is the fact a
        # trace needs to explain why the run paused.
        with span(
            "tool.ask_resident",
            ticket_id=str(request.ticket_id),
            question_type=request.question_type,
            option_count=len(request.options or []),
        ) as active:
            return self._ask(request, active)

    def _ask(self, request: AskResidentRequestV4, active: object) -> UUID:
        try:
            question = self.backend.create_question(
                request.session_id,
                ticket_id=request.ticket_id,
                question_type=request.question_type,
                question_text=request.question_text,
                options=request.options,
                allow_free_text_fallback=request.allow_free_text_fallback,
            )
        except DomainError as exc:
            raise _translate(exc, "ask_resident") from exc
        annotate(active, output={"question_id": str(question.id)})
        return question.id


def _translate(exc: DomainError, tool_name: str) -> ToolPortError:
    """Split the one Backend error type into business vs technical."""
    if exc.code == AGENT_BUDGET_EXHAUSTED:
        return ToolBudgetExhaustedError(str(exc))
    return ToolExecutionError(tool_name, f"{exc.code}: {exc}")


def duplicate_search_request(
    *,
    session_id: UUID,
    ticket_id: UUID,
    category_ids: list[UUID],
) -> SearchRelatedTicketsRequestV4:
    """Build the DUPLICATE-purpose search request (§2.2).

    No Category narrowing beyond what was extracted, and no 3-day window: a
    duplicate is about one asset still being worked on, however long ago the
    master was reported.
    """
    return SearchRelatedTicketsRequestV4(
        session_id=session_id,
        ticket_id=ticket_id,
        purpose=AgentSearchPurpose.DUPLICATE,
        category_ids=category_ids,
    )


def grouping_search_request(
    *,
    session_id: UUID,
    ticket_id: UUID,
    category_ids: list[UUID],
) -> SearchRelatedTicketsRequestV4:
    """Build the GROUPING-purpose search request (§2.2).

    Backend applies the water-leak/electrical-short, adjacent-floor and 3-day
    limits; the Agent only states the purpose.
    """
    return SearchRelatedTicketsRequestV4(
        session_id=session_id,
        ticket_id=ticket_id,
        purpose=AgentSearchPurpose.GROUPING,
        category_ids=category_ids,
    )
