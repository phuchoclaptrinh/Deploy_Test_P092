"""Per-decision validation of assignment model output (contract §4.4, §5.2).

The unit of validation is one decision, never the batch. A batch that is mostly
right must stay mostly usable: valid decisions from the primary model are kept
and applied, and only the missing or contract-breaking ones are handed to the
fallback (§5.2 item 3–4).

This is layer two of the parse. The envelope has already been checked for being
an object with a `decisions` list; each element arrives here as a raw dict and
is validated on its own, so one bad element cannot invalidate its neighbours.
`StrictAssignmentDecision` uses `extra="forbid"`: a field the contract does not
define is a violation of that decision, not something to silently drop.

What counts as a technical model error, and therefore as fallback material:

* a decision that never arrived for a requested work item;
* an element that is not an object, or carries fields outside the contract;
* a missing or malformed `decision_id`, or one that is not in the request;
* the same `decision_id` answered twice;
* a `work_item_id` that does not belong to that `decision_id`;
* an unknown decision value, or a malformed technician id;
* `SELECTED` naming a technician outside that work item candidate snapshot;
* `SELECTED` with no technician, or `NO_SUITABLE_CANDIDATE` with one;
* a missing reason, or one longer than the contract allows.

What does *not*: `NO_SUITABLE_CANDIDATE` itself. It is a valid business answer
and never triggers the fallback (§5.2 item 7).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from src.assignment_agent.model_client import AssignmentModelEnvelope
from src.assignment_agent.schemas import (
    AssignmentDecisionType,
    AssignmentDecisionV4,
    DirectWorkItemRequestV4,
    ProposalWorkItemRequestV4,
)

logger = logging.getLogger(__name__)

WorkItemRequest = DirectWorkItemRequestV4 | ProposalWorkItemRequestV4

MAX_REASON_LENGTH = 500

MISSING_DECISION = "MISSING_DECISION"
DUPLICATE_DECISION = "DUPLICATE_DECISION"
WORK_ITEM_MISMATCH = "WORK_ITEM_MISMATCH"
UNKNOWN_DECISION_TYPE = "UNKNOWN_DECISION_TYPE"
MALFORMED_ID = "MALFORMED_ID"
TECHNICIAN_NOT_IN_SNAPSHOT = "TECHNICIAN_NOT_IN_SNAPSHOT"
TECHNICIAN_REQUIRED = "TECHNICIAN_REQUIRED"
TECHNICIAN_MUST_BE_NULL = "TECHNICIAN_MUST_BE_NULL"
MISSING_REASON = "MISSING_REASON"
REASON_TOO_LONG = "REASON_TOO_LONG"
SCHEMA_ERROR = "SCHEMA_ERROR"
UNKNOWN_DECISION_ID = "UNKNOWN_DECISION_ID"
ENVELOPE_CONTAMINATED = "ENVELOPE_CONTAMINATED"


class StrictAssignmentDecision(BaseModel):
    """One decision exactly as the contract defines it — nothing more.

    Ids and the decision value are plain strings so a malformed UUID or an
    invented enum value arrives intact and fails this one decision, instead of
    exploding the parse and taking the whole batch with it. Length limits are
    checked in the validator rather than here, so the failure can name what
    went wrong instead of reporting a generic schema error.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision_id: str
    work_item_id: str
    selected_technician_id: str | None = None
    decision: str
    reason: str = ""


@dataclass(frozen=True)
class DecisionFailure:
    """One work item the model did not answer acceptably."""

    decision_id: UUID
    work_item_id: UUID
    error_code: str
    error_detail: str


@dataclass
class ValidationOutcome:
    decisions: dict[UUID, AssignmentDecisionV4] = field(default_factory=dict)
    failures: list[DecisionFailure] = field(default_factory=list)
    unattributed_decisions: list[str] = field(default_factory=list)
    # Set when the envelope cannot be trusted as a whole. Nothing from it is
    # kept, and the entire request goes to the fallback.
    envelope_failure: str | None = None

    @property
    def failed_decision_ids(self) -> set[UUID]:
        return {item.decision_id for item in self.failures}

    @property
    def envelope_trusted(self) -> bool:
        return self.envelope_failure is None


def _as_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def _summarize(errors: list[dict[str, Any]]) -> str:
    parts = []
    for item in errors[:3]:
        location = ".".join(str(piece) for piece in item.get("loc", ()))
        parts.append(f"{location or '<root>'}: {item.get('msg')}")
    return "; ".join(parts)


def _validate_one(
    raw: dict[str, Any],
    item: WorkItemRequest,
    *,
    model_version: str,
    decided_at: datetime,
) -> AssignmentDecisionV4 | DecisionFailure:
    work_item_id = item.work_item.work_item_id

    try:
        parsed = StrictAssignmentDecision.model_validate(raw)
    except ValidationError as exc:
        # Covers unknown fields (extra="forbid"), wrong types and missing keys.
        return DecisionFailure(item.decision_id, work_item_id, SCHEMA_ERROR, _summarize(exc.errors()))

    if _as_uuid(parsed.work_item_id) != work_item_id:
        # The model rewrote the work item it was answering about. Nothing about
        # this decision can be trusted onto a specific ticket.
        return DecisionFailure(
            item.decision_id,
            work_item_id,
            WORK_ITEM_MISMATCH,
            f"Model answered work_item_id={parsed.work_item_id!r} for decision {item.decision_id}.",
        )

    try:
        decision_type = AssignmentDecisionType(parsed.decision.strip().upper())
    except ValueError:
        return DecisionFailure(
            item.decision_id,
            work_item_id,
            UNKNOWN_DECISION_TYPE,
            f"Unknown decision value {parsed.decision!r}.",
        )

    technician_id = _as_uuid(parsed.selected_technician_id) if parsed.selected_technician_id else None
    if parsed.selected_technician_id and technician_id is None:
        return DecisionFailure(
            item.decision_id,
            work_item_id,
            MALFORMED_ID,
            f"selected_technician_id {parsed.selected_technician_id!r} is not a UUID.",
        )

    if decision_type is AssignmentDecisionType.SELECTED:
        if technician_id is None:
            return DecisionFailure(item.decision_id, work_item_id, TECHNICIAN_REQUIRED, "SELECTED without a technician.")
        if technician_id not in item.candidate_ids:
            # §4.1: the only technicians that exist for this decision are the
            # ones Backend put in this work item snapshot.
            return DecisionFailure(
                item.decision_id,
                work_item_id,
                TECHNICIAN_NOT_IN_SNAPSHOT,
                f"Technician {technician_id} is not in the candidate snapshot for this work item.",
            )
    elif technician_id is not None:
        return DecisionFailure(
            item.decision_id,
            work_item_id,
            TECHNICIAN_MUST_BE_NULL,
            "NO_SUITABLE_CANDIDATE came back with a technician.",
        )

    reason = parsed.reason.strip()
    if not reason:
        # Substituting a placeholder here would put words in the model mouth on
        # a record Backend keeps for audit, so an empty reason fails instead.
        return DecisionFailure(item.decision_id, work_item_id, MISSING_REASON, "Decision carries no reason.")
    if len(reason) > MAX_REASON_LENGTH:
        # Truncating would silently rewrite the recorded rationale.
        return DecisionFailure(
            item.decision_id,
            work_item_id,
            REASON_TOO_LONG,
            f"Reason is {len(reason)} characters; the contract allows {MAX_REASON_LENGTH}.",
        )

    try:
        return AssignmentDecisionV4(
            decision_id=item.decision_id,
            work_item_id=work_item_id,
            selected_technician_id=technician_id,
            decision=decision_type,
            reason=reason,
            model_version=model_version,
            decided_at=decided_at,
        )
    except ValidationError as exc:
        return DecisionFailure(item.decision_id, work_item_id, SCHEMA_ERROR, _summarize(exc.errors()))


def validate_envelope(
    envelope: AssignmentModelEnvelope,
    items: Sequence[WorkItemRequest],
    *,
    model_version: str,
    decided_at: datetime | None = None,
) -> ValidationOutcome:
    """Validate every decision independently against the item it answers.

    A decision the request never asked for is not just noise. Depending on what
    it points at, it is either one work item the model got wrong, or evidence
    that the whole reply is about some other request:

    * unknown `decision_id` but a `work_item_id` that *is* in the request — that
      work item is failed and re-asked; any decision the model also sent under
      the right id for it is dropped, because two answers about one work item
      with one of them mislabelled is not a decision anyone can apply.
    * unknown `decision_id` and an unrecognisable `work_item_id`, or an element
      that is not an object at all — the envelope is contaminated. Nothing from
      it is kept and the entire request goes to the fallback.
    """
    decided_at = decided_at or datetime.now(UTC)
    by_decision_id = {item.decision_id: item for item in items}
    by_work_item_id = {item.work_item.work_item_id: item for item in items}
    outcome = ValidationOutcome()

    raw_by_id: dict[UUID, dict[str, Any]] = {}
    duplicated: set[UUID] = set()
    mislabelled: dict[UUID, str] = {}
    for raw in envelope.decisions:
        if not isinstance(raw, dict):
            outcome.unattributed_decisions.append(repr(raw)[:120])
            continue
        decision_id = _as_uuid(raw.get("decision_id"))
        if decision_id is None or decision_id not in by_decision_id:
            work_item_id = _as_uuid(raw.get("work_item_id"))
            owner = by_work_item_id.get(work_item_id) if work_item_id else None
            if owner is None:
                outcome.unattributed_decisions.append(str(raw.get("decision_id")))
                continue
            mislabelled[owner.decision_id] = str(raw.get("decision_id"))
            continue
        if decision_id in raw_by_id:
            # Two answers for one work item: there is no rule for picking a
            # winner, so both are discarded and the item goes to the fallback.
            duplicated.add(decision_id)
            continue
        raw_by_id[decision_id] = raw

    if outcome.unattributed_decisions:
        outcome.envelope_failure = (
            f"Envelope carried {len(outcome.unattributed_decisions)} decision(s) that belong to no requested work item: "
            f"{outcome.unattributed_decisions[:3]}"
        )
        logger.warning("Assignment envelope contaminated: %s", outcome.envelope_failure)
        outcome.decisions = {}
        outcome.failures = [
            DecisionFailure(item.decision_id, item.work_item.work_item_id, ENVELOPE_CONTAMINATED, outcome.envelope_failure)
            for item in items
        ]
        return outcome

    for decision_id, item in by_decision_id.items():
        if decision_id in mislabelled:
            outcome.failures.append(
                DecisionFailure(
                    decision_id,
                    item.work_item.work_item_id,
                    UNKNOWN_DECISION_ID,
                    f"Model answered this work item under decision_id {mislabelled[decision_id]!r}.",
                )
            )
            continue
        if decision_id in duplicated:
            outcome.failures.append(
                DecisionFailure(
                    decision_id,
                    item.work_item.work_item_id,
                    DUPLICATE_DECISION,
                    "Model returned more than one decision for this decision_id.",
                )
            )
            continue
        raw = raw_by_id.get(decision_id)
        if raw is None:
            outcome.failures.append(
                DecisionFailure(decision_id, item.work_item.work_item_id, MISSING_DECISION, "No decision returned.")
            )
            continue
        validated = _validate_one(raw, item, model_version=model_version, decided_at=decided_at)
        if isinstance(validated, DecisionFailure):
            outcome.failures.append(validated)
        else:
            outcome.decisions[decision_id] = validated

    return outcome
