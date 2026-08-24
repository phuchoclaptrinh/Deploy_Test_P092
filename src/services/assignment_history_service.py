"""Assignment history, read from frozen snapshots and nothing else.

The rule this module exists to enforce: a confirmed batch is a record of what a
human approved, so rendering it must not touch a live ticket, category,
location, technician profile or user name. Those all change. A category renamed
in September must not rewrite what a coordinator signed off in August, and a
technician who has since left must still appear as the person the work went to.

So the query here loads one column -- `confirmation_snapshot` -- and reads
everything out of it. There is deliberately no `joinedload` in this file; adding
one would be the bug returning.

Batches confirmed before snapshots existed carry `confirmation_snapshot IS
NULL`. They are returned with `has_snapshot=False` and no items rather than
reconstructed from live rows, because reconstructing them is exactly what this
module refuses to do.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models.assignment_proposal import AssignmentProposalBatch
from src.models.api.coordinator import (
    AssignmentHistoryItemResponse,
    AssignmentHistoryMemberResponse,
    AssignmentHistoryRecordResponse,
)
from src.models.enums import ProposalBatchStatus, ProposalItemStatus


class AssignmentHistoryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_records(self, limit: int = 50) -> list[AssignmentHistoryRecordResponse]:
        rows = self.db.scalars(
            select(AssignmentProposalBatch)
            .where(AssignmentProposalBatch.status == ProposalBatchStatus.CONFIRMED.value)
            .order_by(AssignmentProposalBatch.confirmed_at.desc())
            .limit(limit)
        )
        return [self._record(row) for row in rows]

    def get_record(self, batch_id: UUID) -> AssignmentHistoryRecordResponse | None:
        row = self.db.scalar(
            select(AssignmentProposalBatch).where(
                AssignmentProposalBatch.id == batch_id,
                AssignmentProposalBatch.status == ProposalBatchStatus.CONFIRMED.value,
            )
        )
        return self._record(row) if row is not None else None

    # ------------------------------------------------------------------

    def _record(self, row: AssignmentProposalBatch) -> AssignmentHistoryRecordResponse:
        snapshot: dict[str, Any] = row.confirmation_snapshot or {}
        # Only rows that actually became assignments belong in the summary
        # counts; an EMPTY or deselected row was excluded from the round.
        items = [
            self._item(entry)
            for entry in snapshot.get("items", [])
            if entry.get("status") == ProposalItemStatus.ASSIGNED.value
        ]
        technicians = {item.final_technician_id for item in items if item.final_technician_id}
        return AssignmentHistoryRecordResponse(
            batch_id=row.id,
            # `confirmed_at` is a column, not a join, so it is safe for the
            # pre-snapshot rows too.
            confirmed_at=row.confirmed_at,
            confirmed_by_user_id=snapshot.get("confirmed_by_user_id"),
            # Deliberately from the snapshot: reading `row.confirmed_by.full_name`
            # would show the name the coordinator has *today*.
            confirmed_by_name=snapshot.get("confirmed_by_name"),
            created_by_type=snapshot.get("created_by_type") or row.created_by_type,
            ticket_count=sum(len(item.members) for item in items),
            technician_count=len(technicians),
            items=items,
            followup_schedule=row.followup_schedule,
            has_snapshot=bool(snapshot),
        )

    @staticmethod
    def _item(entry: dict[str, Any]) -> AssignmentHistoryItemResponse:
        return AssignmentHistoryItemResponse(
            item_id=entry.get("item_id"),
            status=entry.get("status"),
            work_item_type=entry.get("work_item_type"),
            proposed_technician_id=entry.get("proposed_technician_id"),
            proposed_technician_name=entry.get("proposed_technician_name"),
            final_technician_id=entry.get("final_technician_id"),
            final_technician_name=entry.get("final_technician_name"),
            coordinator_override=bool(entry.get("coordinator_override")),
            reason=entry.get("reason"),
            members=[
                AssignmentHistoryMemberResponse(
                    ticket_id=member.get("ticket_id"),
                    display_code=member.get("display_code"),
                    category=member.get("category"),
                    location_label=member.get("location_label"),
                    priority=member.get("priority"),
                    created_at=member.get("created_at"),
                    sla_due_at=member.get("sla_due_at"),
                )
                for member in entry.get("members", [])
            ],
        )


__all__ = ["AssignmentHistoryService"]
