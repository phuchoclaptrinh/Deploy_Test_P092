"""A scripted assignment model client for the orchestration tests.

The real `AssignmentModelClient` gets a rendered prompt and returns an
envelope, so the stub reads back the two facts the prompt states plainly — the
`decision_id` of each work item, and the technician ids it is allowed to pick
from — and answers with a policy the test chooses.

Reading them out of the prompt rather than being handed the request object is
deliberate: it means the tests fail if the prompt ever stops telling the model
which candidates it may choose, which is the one thing the model must not have
to guess.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from src.assignment_agent.model_client import AssignmentModelEnvelope, AssignmentModelError

DECISION_RE = re.compile(r"decision_id:\s*([0-9a-fA-F-]{36})")
CANDIDATE_RE = re.compile(r"technician_id:\s*([0-9a-fA-F-]{36})")


@dataclass
class ParsedWorkItem:
    decision_id: str
    work_item_id: str
    candidate_ids: list[str]


def parse_prompt(user_prompt: str) -> list[ParsedWorkItem]:
    items: list[ParsedWorkItem] = []
    current: ParsedWorkItem | None = None
    for raw in user_prompt.splitlines():
        line = raw.strip()
        decision = DECISION_RE.search(line)
        # The renderer writes the first field of each work item as
        # "- decision_id: <uuid>"; every other decision_id mention is inside a
        # sentence, so anchoring on the bullet is what separates items.
        if decision and line.startswith("- decision_id"):
            current = ParsedWorkItem(decision_id=decision.group(1), work_item_id="", candidate_ids=[])
            items.append(current)
            continue
        if current is None:
            continue
        if line.startswith("work_item_id:"):
            current.work_item_id = line.split(":", 1)[1].strip()
        elif line.startswith("* technician_id:"):
            found = CANDIDATE_RE.search(line)
            if found:
                current.candidate_ids.append(found.group(1))
    return items


@dataclass
class ScriptedAssignmentModel:
    """One configured model side (primary or fallback).

    `policy` decides what to answer for one work item. The default picks the
    first candidate, which is the first row of the snapshot and therefore the
    least loaded technician Backend offered.
    """

    model_version: str = "scripted-primary"
    policy: Callable[[ParsedWorkItem], dict | None] | None = None
    raise_error: Exception | None = None
    envelope_override: object | None = None
    calls: list[str] = field(default_factory=list)

    def decide(self, *, system_prompt: str, user_prompt: str) -> AssignmentModelEnvelope:
        self.calls.append(user_prompt)
        if self.raise_error is not None:
            raise AssignmentModelError(str(self.raise_error))
        if self.envelope_override is not None:
            return AssignmentModelEnvelope.model_validate(self.envelope_override)

        decisions = []
        for item in parse_prompt(user_prompt):
            answer = (self.policy or _select_first)(item)
            if answer is not None:
                decisions.append(answer)
        return AssignmentModelEnvelope(decisions=decisions)

    @property
    def call_count(self) -> int:
        return len(self.calls)


def _select_first(item: ParsedWorkItem) -> dict:
    return {
        "decision_id": item.decision_id,
        "work_item_id": item.work_item_id,
        "selected_technician_id": item.candidate_ids[0],
        "decision": "SELECTED",
        "reason": "Ky thuat vien dang it viec nhat va co ky nang phu hop.",
    }


def select_index(index: int) -> Callable[[ParsedWorkItem], dict]:
    def policy(item: ParsedWorkItem) -> dict:
        chosen = item.candidate_ids[min(index, len(item.candidate_ids) - 1)]
        return {
            "decision_id": item.decision_id,
            "work_item_id": item.work_item_id,
            "selected_technician_id": chosen,
            "decision": "SELECTED",
            "reason": "Lua chon theo kich ban kiem thu.",
        }

    return policy


def no_suitable_candidate(item: ParsedWorkItem) -> dict:
    """A valid business answer, which must never trigger the fallback (§5.2.7)."""
    return {
        "decision_id": item.decision_id,
        "work_item_id": item.work_item_id,
        "selected_technician_id": None,
        "decision": "NO_SUITABLE_CANDIDATE",
        "reason": "Khong ung vien nao du dieu kien xu ly ngay.",
    }


def broken_decision(item: ParsedWorkItem) -> dict:
    """Answers the right work item with a technician outside its snapshot.

    A contract violation on one decision, which is exactly what the partial
    fallback is for.
    """
    return {
        "decision_id": item.decision_id,
        "work_item_id": item.work_item_id,
        "selected_technician_id": "00000000-0000-4000-8000-000000000000",
        "decision": "SELECTED",
        "reason": "Chon nguoi ngoai snapshot.",
    }


def first_item_broken(item: ParsedWorkItem, *, seen: list[str]) -> dict:
    if item.decision_id not in seen:
        seen.append(item.decision_id)
        if len(seen) == 1:
            return broken_decision(item)
    return _select_first(item)
