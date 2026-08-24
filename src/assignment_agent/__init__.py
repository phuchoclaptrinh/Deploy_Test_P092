"""FixIt Assignment Agent v4.

The second, independent way this system calls AI (`agent_backend_contract_v4.md`
§4–§5). It picks a technician out of a candidate snapshot Backend already
filtered, in two modes:

* `DIRECT` — first assignment, reassignment after a rejection, or reassignment
  after an acceptance timeout. Decisions are applied immediately.
* `PROPOSAL` — a preview batch built when a coordinator turns auto-assignment
  on while a queue exists. Nothing is written until a human confirms.

It shares nothing with the analysis agent in `src.agents`: no session, no
tools, no LangGraph state, no 5-call budget. It produces decisions only —
creating assignments, persisting jobs, expiring proposal batches and resolving
the manual-wins race are all Backend responsibilities.
"""

from src.assignment_agent.schemas import (
    AssignmentDecisionType,
    AssignmentDecisionV4,
    AssignmentMode,
    AssignmentProposalBatchRequestV4,
    AssignmentProposalBatchResultV4,
    AssignmentTrigger,
    CandidateSnapshotV4,
    DirectAssignmentBatchRequestV4,
    DirectAssignmentBatchResultV4,
    DirectWorkItemRequestV4,
    ProposalWorkItemRequestV4,
    WorkItemType,
    WorkItemV4,
)
from src.assignment_agent.service import (
    AssignmentAgentService,
    DirectAssignmentOutcome,
    ProposalAssignmentOutcome,
)

__all__ = [
    "AssignmentAgentService",
    "AssignmentDecisionType",
    "AssignmentDecisionV4",
    "AssignmentMode",
    "AssignmentProposalBatchRequestV4",
    "AssignmentProposalBatchResultV4",
    "AssignmentTrigger",
    "CandidateSnapshotV4",
    "DirectAssignmentBatchRequestV4",
    "DirectAssignmentBatchResultV4",
    "DirectAssignmentOutcome",
    "DirectWorkItemRequestV4",
    "ProposalAssignmentOutcome",
    "ProposalWorkItemRequestV4",
    "WorkItemType",
    "WorkItemV4",
]
