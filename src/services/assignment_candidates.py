"""Backend-authoritative candidate snapshots for the Assignment Agent (§4.1, §4.3).

The model never discovers a technician; it only ranks the ones Backend put in
front of it. That makes this module the security boundary of the assignment
side, and it applies exactly the three filters §4.1 names — active profile,
availability on, skill matching the ticket Category — plus the per-work-item
exclusion list from §4.3.

Two rules that are easy to get subtly wrong and are therefore explicit here:

* **Exclusions are historical, not "the last person".** §4.3 rule 1 asks for
  every technician who ever left this work item with `TECHNICIAN_REJECTED` or
  `ACCEPTANCE_TIMEOUT`, so someone who declined two rounds ago does not come
  back around. For an incident case it is the union across all its members.
* **Exclusions are scoped to this work item.** They are not a blacklist: the
  same technician stays a normal candidate on every other ticket, and a
  coordinator may still assign them by hand (§4.3 rule 5).

Work items with no candidate left never reach a model call (§5.2 item 1). This
module returns them as `WorkItemDraft(candidates=[])` so the caller can send
DIRECT straight to manual and mark the PROPOSAL row EMPTY, which are two
different outcomes for the same fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from src.database.models.category import CategoryCatalog
from src.database.models.incident_case import IncidentCase
from src.database.models.incident_case_member import IncidentCaseMember
from src.database.models.location import Location
from src.database.models.technician import TechnicianProfile, TechnicianSkill
from src.database.models.ticket import Ticket
from src.database.models.ticket_assignment import TicketAssignment
from src.database.models.user_profile import UserProfile
from src.config import get_settings
from src.models.enums import (
    AssignmentEndReason,
    AssignmentStatus,
    ClassificationStatus,
    Priority,
    TicketStatus,
)

ACTIVE_ASSIGNMENT_STATUSES = (
    AssignmentStatus.ASSIGNED,
    AssignmentStatus.ACCEPTED,
    AssignmentStatus.IN_PROGRESS,
)

# §7.9: a case member that will never become eligible again (closed, cancelled,
# ruled invalid, or ruled unresolvable) does not count against a case's
# completeness -- otherwise one dead member would block that case's proposal
# forever. Kept as its own module-level set rather than imported, matching the
# existing convention of a local terminal-status set per module (see also
# `src.api.routes.coordinator.clusters.TERMINAL_STATUSES` and
# `src.services.v4_workflow_service.TERMINAL_TICKET_STATUSES`).
TERMINAL_CASE_MEMBER_STATUSES = (
    TicketStatus.COMPLETED,
    TicketStatus.CANCELLED,
    TicketStatus.INVALID,
    TicketStatus.UNRESOLVABLE,
)

EXCLUDING_END_REASONS = (
    AssignmentEndReason.TECHNICIAN_REJECTED.value,
    AssignmentEndReason.ACCEPTANCE_TIMEOUT.value,
)


def as_utc(value: datetime | None) -> datetime | None:
    """Normalize a stored timestamp before any Python-side comparison.

    PostgreSQL returns timezone-aware values and SQLite returns naive ones, and
    a case whose members mix a freshly assigned aware value with a reloaded
    naive one would otherwise raise when the earliest deadline is picked.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass
class WorkItemDraft:
    """One unit of work plus the snapshot the model will be shown.

    Kept as a plain dataclass rather than the Pydantic request model because a
    draft with no candidates is a legitimate outcome that the contract model
    (min_length=1 on `candidates`) refuses to represent.
    """

    decision_id: UUID
    work_item_type: str
    work_item_id: UUID
    ticket_ids: list[UUID]
    category_id: UUID
    priority: Priority
    location_labels: list[str]
    issue_summary: str
    required_skills: list[str]
    current_due_at: datetime | None
    excluded_technician_ids: list[UUID]
    candidates: list[dict[str, object]] = field(default_factory=list)
    # When this work item entered the queue: the ticket's `created_at`, or the
    # earliest member's for a case. `RULE_ENGINE_V1` orders a batch by priority
    # and then by this, so an older report is never overtaken by a newer one of
    # the same priority.
    created_at: datetime | None = None

    @property
    def ticket_count(self) -> int:
        return len(self.ticket_ids)

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)


class AssignmentCandidateService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    # ------------------------------------------------------------------
    # Eligibility of the work itself.
    # ------------------------------------------------------------------

    def eligible_ticket_query(self, *, include_paused: bool = False):
        """§4.2: what "ready to be assigned" means, as one query.

        A ticket that is unapproved, unclassified, already assigned or linked as
        a duplicate is never eligible, in either mode.

        `auto_assignment_paused` is different: DIRECT reads it literally (a
        failed AI round, a breached reassignment cap, or the switch being off
        all take a ticket out of the automatic path without touching the global
        switch), but PROPOSAL is *the* recovery path for the two pause reasons
        that are not the reassignment cap -- opening a batch while the switch is
        off is precisely how a coordinator clears the `AUTO_ASSIGNMENT_DISABLED`
        backlog (§4.6 item 1), and a `NO_CANDIDATES` pause deserves a fresh look
        too, since a technician may have gained the skill since. Nothing else
        ever clears those two pauses, so excluding them here would make a
        PROPOSAL batch permanently blind to the exact tickets it exists to
        surface. The reassignment cap (§11 assumption 4 / §14.3) is a mandatory
        manual routing rule independent of the switch, so it stays enforced
        directly on `reassignment_count` even with `include_paused=True`.
        """
        active_assignment_exists = (
            select(TicketAssignment.id)
            .where(TicketAssignment.ticket_id == Ticket.id, TicketAssignment.is_active.is_(True))
            .exists()
        )
        conditions = [
            Ticket.status == TicketStatus.APPROVED,
            Ticket.classification_status == ClassificationStatus.RESOLVED,
            Ticket.category_id.is_not(None),
            Ticket.priority.is_not(None),
            Ticket.duplicate_of_ticket_id.is_(None),
            ~active_assignment_exists,
        ]
        if include_paused:
            conditions.append(Ticket.reassignment_count <= self.settings.assignment_reassignment_cap)
        else:
            conditions.append(Ticket.auto_assignment_paused.is_(False))
        return (
            select(Ticket)
            .where(*conditions)
            .options(
                joinedload(Ticket.category),
                joinedload(Ticket.location).joinedload(Location.building),
                joinedload(Ticket.location).joinedload(Location.floor),
                selectinload(Ticket.assignments),
            )
        )

    def is_ticket_eligible(self, ticket: Ticket, *, include_paused: bool = False) -> bool:
        """Re-checked immediately before an assignment is written (§4.5 step 2).

        See `eligible_ticket_query` for why `include_paused` exists: PROPOSAL
        passes it so a batch built (or confirmed) against an
        `AUTO_ASSIGNMENT_DISABLED`/`NO_CANDIDATES` backlog is not re-blocked by
        the very pause it was opened to clear.
        """
        if ticket.status is not TicketStatus.APPROVED:
            return False
        if ticket.classification_status is not ClassificationStatus.RESOLVED:
            return False
        if ticket.category_id is None or ticket.priority is None:
            return False
        if ticket.duplicate_of_ticket_id is not None:
            return False
        if include_paused:
            if ticket.reassignment_count > self.settings.assignment_reassignment_cap:
                return False
        elif ticket.auto_assignment_paused:
            return False
        return not self.has_active_assignment(ticket.id)

    def has_active_assignment(self, ticket_id: UUID) -> bool:
        return (
            self.db.scalar(
                select(TicketAssignment.id).where(
                    TicketAssignment.ticket_id == ticket_id,
                    TicketAssignment.is_active.is_(True),
                )
            )
            is not None
        )

    # ------------------------------------------------------------------
    # Building work items.
    # ------------------------------------------------------------------

    def ticket_draft(self, ticket: Ticket) -> WorkItemDraft:
        excluded = self.excluded_technician_ids([ticket.id])
        draft = WorkItemDraft(
            decision_id=uuid4(),
            work_item_type="TICKET",
            work_item_id=ticket.id,
            ticket_ids=[ticket.id],
            category_id=ticket.category_id,
            priority=ticket.priority,
            location_labels=[ticket.location.label] if ticket.location else [],
            issue_summary=self.sanitized_summary([ticket]),
            required_skills=[ticket.category.display_name] if ticket.category else [],
            current_due_at=as_utc(ticket.sla_due_at),
            excluded_technician_ids=excluded,
            created_at=as_utc(ticket.created_at),
        )
        draft.candidates = self.candidate_snapshot(ticket.category_id, excluded)
        return draft

    def case_draft(self, case: IncidentCase, *, max_members: int, include_paused: bool = False) -> WorkItemDraft | None:
        """§4.2 / §7.9: a case work item covering every pending member, or nothing.

        "Pending" excludes two kinds of member on purpose, because neither one
        is evidence the case is not ready:

        * a **terminal** member (cancelled, invalid, unresolvable, completed)
          will never become eligible and must not block the case forever;
        * a member that already has an **active assignment** was taken by a
          coordinator directly (§4.5: "a manual win shrinks the case") -- it
          is *handled*, not *unready*, and the case correctly drafts around it
          with the members that remain.

        Among what is left, this is all-or-nothing: if even one pending
        member is not yet eligible (still `NEW`, still unclassified, ...) the
        whole case is deferred rather than drafting the members that happen
        to be ready and letting the rest surface later as their own,
        separately-decided work item. That would split one incident across
        two different technicians on two different decisions, which is
        exactly what a case exists to prevent -- and it is a different
        situation from a manual win, because nothing here has been decided
        for that member yet.

        `current_due_at` is the earliest member deadline, because the case is
        finished when its slowest promise is kept, not its easiest.
        """
        pending_ticket_ids = {
            member.ticket_id
            for member in case.members
            if member.ticket is not None
            and member.ticket.status not in TERMINAL_CASE_MEMBER_STATUSES
            and not self.has_active_assignment(member.ticket_id)
        }
        if not pending_ticket_ids:
            return None

        eligible = self.eligible_case_members(case, include_paused=include_paused)
        if {ticket.id for ticket in eligible} != pending_ticket_ids:
            return None
        members = eligible[:max_members]

        ticket_ids = [member.id for member in members]
        excluded = self.excluded_technician_ids(ticket_ids)
        created_dates = [as_utc(member.created_at) for member in members if member.created_at is not None]
        due_dates = [as_utc(member.sla_due_at) for member in members if member.sla_due_at is not None]
        priorities = [member.priority for member in members if member.priority is not None]
        draft = WorkItemDraft(
            decision_id=uuid4(),
            work_item_type="INCIDENT_CASE",
            work_item_id=case.id,
            ticket_ids=ticket_ids,
            category_id=case.category_id,
            priority=min(priorities, key=_priority_rank) if priorities else Priority.P2,
            location_labels=sorted(
                {member.location.label for member in members if member.location and member.location.label}
            ),
            issue_summary=self.sanitized_summary(members),
            required_skills=[case.category.display_name] if case.category else [],
            current_due_at=min(due_dates) if due_dates else None,
            excluded_technician_ids=excluded,
            # The case queues from its oldest report: gathering later reports
            # into it must not push the original complaint back down the queue.
            created_at=min(created_dates) if created_dates else None,
        )
        draft.candidates = self.candidate_snapshot(case.category_id, excluded)
        return draft

    def eligible_case_members(self, case: IncidentCase, *, include_paused: bool = False) -> list[Ticket]:
        """§4.2: every eligible member of the case, ordered by creation.

        Untruncated on purpose: `case_draft` needs the full eligible set to
        compare against every *live* member before it may build a draft at
        all, and only slices to `max_members` once that completeness check
        has passed. Members are ordered by creation so that slice always
        keeps the oldest reports rather than an arbitrary subset.
        """
        rows = self.db.scalars(
            self.eligible_ticket_query(include_paused=include_paused)
            .join(IncidentCaseMember, IncidentCaseMember.ticket_id == Ticket.id)
            .where(IncidentCaseMember.case_id == case.id)
            .order_by(Ticket.created_at.asc())
        )
        return list(rows)

    # ------------------------------------------------------------------
    # Candidates and exclusions.
    # ------------------------------------------------------------------

    def _active_count_subquery(self, priority: Priority | None = None):
        """How much live work one technician is holding, optionally per priority.

        Correlated on `TechnicianProfile.user_id`, so it evaluates once per row
        of the candidate query rather than once per technician in the system.
        """
        query = select(func.count(TicketAssignment.id)).where(
            TicketAssignment.technician_id == TechnicianProfile.user_id,
            TicketAssignment.is_active.is_(True),
            TicketAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
        )
        if priority is not None:
            query = query.join(Ticket, Ticket.id == TicketAssignment.ticket_id).where(Ticket.priority == priority)
        return query.scalar_subquery()

    def candidate_snapshot(self, category_id: UUID, excluded: list[UUID]) -> list[dict[str, object]]:
        """§4.1: active, available, skilled — and never someone excluded here.

        The counts are the projected-load context the decision engine reasons
        about (§4.3a). They are point-in-time, which is exactly why both engines
        add their own decisions on top as they work through a batch.

        `last_assigned_at` covers *every* assignment ever made to that
        technician, active or closed, because the question the tie-break asks is
        "who has gone longest without being given work" — someone who finished a
        job an hour ago has not been idle, even though they hold nothing now.
        """
        active_count = self._active_count_subquery()
        p1_count = self._active_count_subquery(Priority.P1)
        p2_count = self._active_count_subquery(Priority.P2)
        p3_count = self._active_count_subquery(Priority.P3)
        last_assigned = (
            select(func.max(TicketAssignment.assigned_at))
            .where(TicketAssignment.technician_id == TechnicianProfile.user_id)
            .scalar_subquery()
        )

        query = (
            select(
                TechnicianProfile,
                active_count.label("active_total"),
                p1_count.label("active_p1"),
                p2_count.label("active_p2"),
                p3_count.label("active_p3"),
                last_assigned.label("last_assigned_at"),
            )
            .join(TechnicianSkill, TechnicianSkill.technician_id == TechnicianProfile.user_id)
            .join(UserProfile, UserProfile.user_id == TechnicianProfile.user_id)
            .where(
                TechnicianProfile.is_active.is_(True),
                TechnicianProfile.is_available.is_(True),
                UserProfile.is_active.is_(True),
                TechnicianSkill.category_id == category_id,
            )
            .options(joinedload(TechnicianProfile.user), selectinload(TechnicianProfile.skills))
            .order_by(active_count.asc(), TechnicianProfile.user_id)
        )
        if excluded:
            query = query.where(TechnicianProfile.user_id.not_in(excluded))

        category = self.db.get(CategoryCatalog, category_id)
        matched_skill = [category.display_name] if category else []

        snapshot: list[dict[str, object]] = []
        for profile, active_total, active_p1, active_p2, active_p3, assigned_at in self.db.execute(query).unique():
            last = as_utc(assigned_at)
            snapshot.append(
                {
                    "technician_id": str(profile.user_id),
                    # A display name only. No phone, no email, no address: §4.1
                    # limits the engine to skills and load.
                    "display_name": (profile.user.full_name if profile.user else "") or "",
                    "matched_skills": matched_skill,
                    "active_assignment_count": int(active_total or 0),
                    "active_p3_count": int(active_p3 or 0),
                    "is_available_snapshot": True,
                    "active_p1_count": int(active_p1 or 0),
                    "active_p2_count": int(active_p2 or 0),
                    # ISO text, not a datetime: this dict is persisted verbatim
                    # into the JSON `ai_assignment_jobs.candidate_snapshot`.
                    "last_assigned_at": last.isoformat() if last else None,
                }
            )
        return snapshot

    def excluded_technician_ids(self, ticket_ids: list[UUID]) -> list[UUID]:
        """§4.3 rule 1-2: everyone who ever rejected or timed out on this work
        item, unioned across an incident case's members."""
        if not ticket_ids:
            return []
        rows = self.db.scalars(
            select(TicketAssignment.technician_id)
            .where(
                TicketAssignment.ticket_id.in_(ticket_ids),
                TicketAssignment.is_active.is_(False),
                TicketAssignment.end_reason.in_(EXCLUDING_END_REASONS),
            )
            .distinct()
        )
        return sorted(set(rows), key=str)

    # ------------------------------------------------------------------
    # Prompt-safe summary.
    # ------------------------------------------------------------------

    @staticmethod
    def sanitized_summary(tickets: list[Ticket]) -> str:
        """§4.3: cleaned, and treated by the model as data rather than
        instructions.

        Built from catalog and location fields only. The resident description is
        left out for the same reason as in the analysis search: it is free text
        that routinely contains names, phone numbers and apartment numbers, and
        it is also the obvious prompt-injection surface on this path.
        """
        if not tickets:
            return ""
        first = tickets[0]
        category = first.category.display_name if first.category else "Chưa phân loại"
        locations = sorted({ticket.location.label for ticket in tickets if ticket.location and ticket.location.label})
        where = ", ".join(locations) if locations else "chưa xác định vị trí"
        if len(tickets) == 1:
            return f"{category} tại {where}."
        return f"{category} tại {where} ({len(tickets)} phản ánh trong cùng cụm sự cố)."


def _priority_rank(priority: Priority) -> int:
    return {Priority.P3: 0, Priority.P2: 1, Priority.P1: 2}[priority]


__all__ = ["ACTIVE_ASSIGNMENT_STATUSES", "AssignmentCandidateService", "WorkItemDraft", "as_utc"]
