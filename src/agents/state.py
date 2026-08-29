"""LangGraph state for the one ticket-analysis pipeline.

Plain JSON-serializable primitives only -- no ORM objects, no UUID instances --
because the graph is checkpointed across the pause between `ask_resident` and
the resident answering, possibly minutes later in a different HTTP request.

## What the state is organised around

The pipeline is a straight line with one loop:

    classify -> (maybe ask) -> duplicate candidates -> duplicate judgement -> exit

so the state holds exactly three things: the evidence package handed to the
model, what the single classification call returned, and the duplicate stage's
working set. Grouping is deliberately absent from the foreground state -- it
runs in the background after the resident has already been told the outcome,
with its own short-lived state built by `service.run_case_grouping`.

## The evidence fingerprint

A resident answer can change the facts or merely reword them. Recomputing the
candidate search for a reworded sentence spends a database round trip on a
question whose answer cannot have moved. So the facts that actually decide
which candidates come back -- the final Category, the `location_id`, and the
material incident facts the model extracted -- are hashed into one fingerprint.
The duplicate lookup reruns when the fingerprint moves and is reused when it
does not. Nothing else is in the hash: not the description, not the wording of
an answer, not the severity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, TypedDict

Severity = Literal["LOW", "MEDIUM", "HIGH"]
SeveritySource = Literal["IMAGE", "TEXT"]
DuplicateVerdict = Literal["SAME_INCIDENT", "DIFFERENT_INCIDENT", "UNCERTAIN"]

#: Sentinel for "this lookup has never run". Revision 0 is a real revision.
NEVER_RAN = -1

#: Mirrors of the Backend session budget. The ask/reclassify loop needs no
#: separate iteration cap: every trip through it costs one resident question,
#: and `create_question` refuses the fourth against the database counters. A
#: second in-graph cap could only fire if that enforcement were already broken,
#: and it would have to invent an exit reason to do it.
BUDGET_MAX_TOOL_CALLS = 5
BUDGET_MAX_ASK_ROUNDS = 3
BUDGET_MAX_WAIT_SECONDS = 300


class AgentState(TypedDict, total=False):
    # --- The unified evidence package, assembled once by `service`. ---
    ticket_id: str
    session_id: str
    description: str
    image_urls: list[str]
    image_paths: list[str]
    location_id: str | None
    location_label: str
    #: Backend-internal code for the kind of place this is (ELEVATOR,
    #: FIRE_EXIT, ...). Never shown to the model -- it is here only because the
    #: scoring bonus keys on it, and the P3 check has to score the ticket before
    #: the duplicate stage runs.
    location_type_code: str | None
    floor_label: str
    unit_code: str | None
    model_version: str

    #: Category catalog pinned to this session (id / display_name / ceiling /
    #: base_score). A name outside it is dropped, never coerced into a UUID.
    catalog: list[dict[str, object]]
    catalog_version: str

    #: The same categories as `catalog`, keyed by id, with the Backend-internal
    #: `code` the scoring rules key on. Kept separate precisely so `code` never
    #: reaches a prompt: `catalog` is what the model is shown, this is what the
    #: P3 check scores with.
    scoring_catalog: dict[str, dict[str, object]]

    #: Every question asked so far and what the resident replied, in order.
    #: Part of the evidence package on every reclassification.
    conversation: list[dict[str, object]]

    # --- Output of the single multimodal classification call. ---
    category_id: str | None
    text_category_id: str | None
    image_category_id: str | None
    severity: Severity | None
    severity_source: SeveritySource | None
    red_flag: bool
    ai_reason: str | None
    understandable: bool
    image_relevant: bool | None
    #: Short observable facts. Part of the fingerprint, so a clarification that
    #: changes what the problem *is* invalidates the candidate lookup.
    incident_facts: list[str]

    #: The question the classification asked for, if any: kind / text / options.
    requested_question: dict[str, object] | None

    # --- The resident's own answer to "which problem is this ticket for?". ---
    #: Set once a CATEGORY_CONFIRMATION is answered with an option, and then
    #: fixed for the rest of the round. Later classification passes are told
    #: what it is and may not move it; a model that names something else is
    #: overruled, not obeyed. One ticket, one problem, chosen by the person who
    #: reported it.
    confirmed_category_id: str | None
    confirmed_category_name: str | None

    # --- Evidence revisioning. ---
    evidence_fingerprint: str
    evidence_revision: int

    # --- Duplicate stage. ---
    duplicate_candidates: list[dict[str, object]]
    duplicate_candidates_revision: int
    duplicate_searched_revision: int
    duplicate_verdict: DuplicateVerdict | None
    duplicate_master_ticket_id: str | None
    duplicate_reason: str | None
    #: Set when a matching candidate finished less than an hour ago and the
    #: resident still has to say whether the problem actually came back.
    recent_completion_master_id: str | None
    recent_completion_answer: str | None

    # --- Resident question bookkeeping. ---
    pending_question_id: str | None
    pending_question_kind: str | None
    ask_prepare_failed: bool
    iterations: int

    # --- Budget mirrors of the Backend session counters. Read, never written. ---
    tool_calls_used: int
    ask_rounds_used: int
    ask_elapsed_seconds: int

    # --- Outcome. Exactly one of `result` / `technical_failure` is ever set. ---
    exit_reason: str | None
    result: dict[str, object] | None
    technical_failure: dict[str, object] | None


def ask_budget_available(state: AgentState) -> bool:
    """True when one more resident question is still allowed."""
    return (
        state.get("tool_calls_used", 0) < BUDGET_MAX_TOOL_CALLS
        and state.get("ask_rounds_used", 0) < BUDGET_MAX_ASK_ROUNDS
        and state.get("ask_elapsed_seconds", 0) < BUDGET_MAX_WAIT_SECONDS
    )


def budget_actually_spent(state: AgentState) -> bool:
    """LIMIT_REACHED is only truthful when a real budget ran out."""
    return (
        state.get("tool_calls_used", 0) >= BUDGET_MAX_TOOL_CALLS
        or state.get("ask_rounds_used", 0) >= BUDGET_MAX_ASK_ROUNDS
        or state.get("ask_elapsed_seconds", 0) >= BUDGET_MAX_WAIT_SECONDS
    )


def classification_settled(state: AgentState) -> bool:
    """Category, severity and location are all resolved well enough to search.

    Duplicate retrieval keys on the exact Category and the exact location, so
    running it before either is settled would look for the wrong thing.
    """
    return bool(state.get("category_id")) and state.get("severity") in {"LOW", "MEDIUM", "HIGH"} and bool(state.get("location_id"))


def prospective_priority(state: AgentState) -> str | None:
    """Score the ticket the way `finalize` will, before the duplicate stage.

    Priority is normally decided at persistence time, but the P3 gate has to
    fire *before* any duplicate lookup, so the same calculation is run here on
    the same inputs: the pinned catalog row, the severity, the location type
    and a density of one (no case exists yet).

    Returns None when the ticket cannot be scored yet, which the caller treats
    as "not P3" rather than guessing.
    """
    from src.models.enums import Priority, Severity
    from src.services.scoring_service import ScoringService

    if state.get("red_flag"):
        # Danger is P3 by definition and carries no score at all.
        return Priority.P3.value

    category_id = state.get("category_id")
    severity = state.get("severity")
    if not category_id or severity not in {"LOW", "MEDIUM", "HIGH"}:
        return None
    snapshot = (state.get("scoring_catalog") or {}).get(str(category_id))
    if snapshot is None:
        return None

    ceiling = snapshot.get("priority_ceiling")
    outcome = ScoringService().calculate_dynamic(
        category_code=str(snapshot.get("code") or ""),
        base_score=int(snapshot.get("base_score") or 0),
        severity=Severity(severity),
        location_type_code=state.get("location_type_code"),
        # One apartment. Grouping runs later and by design does not rescore.
        density_count=1,
        red_flag_detected=False,
        priority_ceiling=Priority(ceiling) if ceiling in {"P1", "P2", "P3"} else None,
    )
    return outcome.priority_final.value


def p3_review_required(state: AgentState) -> bool:
    """True when this round must stop for a human before doing anything else.

    P3 is the five-minute-SLA priority. Everything after classification --
    duplicate retrieval, duplicate judgement, grouping, publication -- is
    deferred until a coordinator confirms the emergency or downgrades it.
    """
    return prospective_priority(state) == "P3"


def category_is_confirmed(state: AgentState) -> bool:
    """True once the resident has settled the Category themselves."""
    return bool(state.get("confirmed_category_id"))


def input_insufficient(state: AgentState) -> bool:
    """The report cannot be understood safely.

    An attached photo with nothing to do with the building is enough on its
    own; unintelligible text only counts when no usable photo rescues it.
    """
    has_image = bool(state.get("image_urls"))
    if has_image and state.get("image_relevant") is False:
        return True
    usable_image = has_image and state.get("image_relevant") is True
    return not state.get("understandable", True) and not usable_image


def has_technical_failure(state: AgentState) -> bool:
    return state.get("technical_failure") is not None


# ---------------------------------------------------------------------------
# Evidence fingerprint.
# ---------------------------------------------------------------------------


def _evidence_material(state: AgentState) -> dict[str, object]:
    """Exactly what decides which candidates a search can return.

    Category and location select the rows; the incident facts decide whether
    the ticket is still about the same problem. A reworded description is
    deliberately not in here.
    """
    return {
        "category_id": state.get("category_id"),
        "location_id": state.get("location_id"),
        "incident_facts": sorted(state.get("incident_facts") or []),
    }


def evidence_fingerprint(state: AgentState) -> str:
    payload = json.dumps(_evidence_material(state), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def advance_evidence_revision(state: AgentState) -> dict[str, object]:
    """Recompute the fingerprint and bump the revision only if it moved.

    The first pass establishes revision 0 without counting as a change.
    """
    fingerprint = evidence_fingerprint(state)
    previous = state.get("evidence_fingerprint")
    revision = state.get("evidence_revision", 0)
    if previous is not None and fingerprint != previous:
        revision += 1
    return {"evidence_fingerprint": fingerprint, "evidence_revision": revision}


def needs_duplicate_search(state: AgentState) -> bool:
    """True when the current evidence has never been searched against."""
    return state.get("duplicate_searched_revision", NEVER_RAN) != state.get("evidence_revision", 0)


def duplicate_candidates_valid(state: AgentState) -> bool:
    """Candidates only count for the evidence revision that produced them."""
    if not state.get("duplicate_candidates"):
        return False
    return state.get("duplicate_candidates_revision", NEVER_RAN) == state.get("evidence_revision", 0)


__all__ = [
    "BUDGET_MAX_ASK_ROUNDS",
    "BUDGET_MAX_TOOL_CALLS",
    "BUDGET_MAX_WAIT_SECONDS",
    "NEVER_RAN",
    "AgentState",
    "advance_evidence_revision",
    "ask_budget_available",
    "category_is_confirmed",
    "budget_actually_spent",
    "classification_settled",
    "duplicate_candidates_valid",
    "evidence_fingerprint",
    "has_technical_failure",
    "input_insufficient",
    "needs_duplicate_search",
    "p3_review_required",
    "prospective_priority",
]
