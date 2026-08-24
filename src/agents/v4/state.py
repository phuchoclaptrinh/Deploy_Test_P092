"""LangGraph state for the Agent v4 analysis pipeline.

Separate from `src.agents.state` (V3) on purpose: V4 adds duplicate detection
and drops the Agent-side Category match verdict, so the two graphs do not share
a state shape and a checkpoint written by one is never replayed by the other.

Everything stays JSON-serializable primitives (no ORM objects, no UUID
instances) because the graph is checkpointed across the pause between
`ask_resident` and the resident answering minutes later in another request.

`text_understandable` and `symptom_facts` are kept here and only here. They are
what make the `INSUFFICIENT_INPUT` branch and the revision logic decidable, and
contract §1.3 forbids internal fields in the final payload — the terminal nodes
never copy them out.

## Three revisions, not one

A resident answer can change different things, and each kind of change
invalidates different work. One fingerprint for all of it would either re-run
paid lookups for a reworded sentence, or reuse a verdict that is no longer
about the same incident. So the facts are hashed three times, in a strict
hierarchy — each level contains the one above it:

* **search** — Category from text, Category from image, `location_id`. These
  are the only things that decide *which candidates a search returns*. A
  resident restating the same symptom does not move this, so no second lookup
  is spent.
* **incident** — search facts plus the symptom facts and image relevance:
  *what the problem actually is*. Grouping hangs off this, because a case
  proposal claims "these tickets are one spreading incident".
* **judgement** — incident facts plus the description, the resident answers,
  severity and the red-flag evidence: everything a duplicate verdict reads.
  Re-judging is a model call, not a billable tool call, so it is affordable
  whenever the evidence behind the verdict moved at all.

Because the levels nest, a search change implies an incident and judgement
change, and an incident change implies a judgement change. Budget counters are
owned by Backend and are never reset by any of this.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, TypedDict

Severity = Literal["LOW", "MEDIUM", "HIGH"]
SeveritySource = Literal["IMAGE", "TEXT"]
DuplicateVerdict = Literal["SAME_INCIDENT", "DIFFERENT_INCIDENT", "UNCERTAIN"]

# Sentinel for "this lookup has never run". Revision 0 is a real revision.
NEVER_RAN = -1


class AgentStateV4(TypedDict, total=False):
    # --- Cleaned ticket input for this session (Backend-owned facts).
    ticket_id: str
    session_id: str
    description: str
    building_label: str
    floor_label: str
    location_label: str
    location_id: str | None
    image_paths: list[str]
    image_urls: list[str]
    model_version: str

    # --- Category catalog pinned to the session (§1.1 / §4.5 of the logic doc).
    catalog: list[dict[str, object]]
    catalog_version: str

    # --- Text extraction, evaluated without looking at the image.
    text_categories: list[str]
    red_flag_text: bool
    text_understandable: bool  # internal only; never reaches AgentAnalysisResultV4
    text_severity: Severity | None
    text_notes: str | None
    text_symptom_facts: list[str]
    # Union of the text and image symptom facts, internal only. This is what
    # lets "the resident clarified the symptom" be detected as a real change
    # instead of hashing their wording.
    symptom_facts: list[str]

    # --- Image extraction, evaluated without looking at the text.
    image_categories: list[str] | None
    red_flag_signal: bool | None
    is_relevant: bool | None
    image_severity: Severity | None
    image_notes: str | None
    image_symptom_facts: list[str]

    # --- Merged severity (§9.5: image wins when usable, otherwise text).
    # `None` means "not established yet" and is never silently turned into LOW.
    severity: Severity | None
    severity_source: SeveritySource | None
    severity_gap_note: str | None
    is_confident: bool
    confidence_notes: str | None

    # --- Red-flag evidence, kept so the exit node can explain itself.
    red_flag_evidence: list[str]

    # --- Evidence revisioning (see the module docstring).
    search_fingerprint: str
    search_revision: int
    incident_fingerprint: str
    incident_revision: int
    judgement_fingerprint: str
    judgement_revision: int

    # --- Duplicate detection (§1.5 / §7.2.1).
    duplicate_candidates: list[dict[str, object]]
    duplicate_candidates_revision: int  # the search revision they belong to
    duplicate_searched_revision: int
    duplicate_judged_revision: int  # the judgement revision that was judged
    duplicate_verdict: DuplicateVerdict | None
    duplicate_master_ticket_id: str | None
    duplicate_reason: str | None

    # --- Grouping (§1.4 / §7.3), water leak and electrical short only.
    grouping_candidates: list[dict[str, object]]
    grouping_candidates_revision: int  # search revision
    grouping_searched_revision: int  # search revision
    grouping: dict[str, object] | None
    grouping_result_revision: int  # incident revision the accepted proposal is about
    # Revision-scoped refusal: a rejected proposal or an unusable id set only
    # applies to the incident revision that produced it.
    grouping_blocked_revision: int
    # Capability-scoped refusal: Backend cannot do GROUPING at all. Survives
    # every revision and is reported as a dependency gap.
    grouping_capability_blocked: bool

    # --- Resident question state (§2.4).
    pending_question_id: str | None
    answer_notes: list[str]
    reextraction: bool
    ask_prepare_failed: bool

    # --- Budget mirrors of the Backend session counters (§1.1). Backend stays
    # the source of truth; these exist so routing can be decided without a
    # database round trip inside every edge function. They are never reset.
    tool_calls_used: int
    ask_rounds_used: int
    ask_elapsed_seconds: int
    iterations: int

    # --- Action chosen by decide_action for the dispatch edge to read.
    next_action: str
    action_reason: str
    action_grouping_ticket_ids: list[str] | None
    action_question_text: str | None
    action_question_options: list[str] | None
    action_allow_free_text: bool
    invalid_action_notes: list[str]

    # --- Technical outcome, kept strictly apart from the business exits. A
    # tool/adapter/extraction failure sets this and produces no result.
    technical_failure: dict[str, object] | None
    # Capabilities the contract requires that this Backend cannot serve yet.
    dependency_gaps: list[str]

    # --- Outcome.
    exit_reason: str | None
    result: dict[str, object] | None  # AgentAnalysisResultV4.model_dump(mode="json")


BUDGET_MAX_TOOL_CALLS = 5
BUDGET_MAX_ASK_ROUNDS = 3
BUDGET_MAX_WAIT_SECONDS = 300
# Safety net for the tool loop itself. Hitting it is not a business limit, so it
# never fabricates LIMIT_REACHED on its own (see `graph._limit_actually_reached`).
MAX_LOOP_ITERATIONS = 12


def budget_exhausted(state: AgentStateV4) -> bool:
    """True when no further billable tool call may be made (§1.1)."""
    return (
        state.get("tool_calls_used", 0) >= BUDGET_MAX_TOOL_CALLS
        or state.get("ask_elapsed_seconds", 0) >= BUDGET_MAX_WAIT_SECONDS
        or state.get("iterations", 0) > MAX_LOOP_ITERATIONS
    )


def ask_budget_available(state: AgentStateV4) -> bool:
    """True when one more resident question is still allowed (§1.1)."""
    return (
        state.get("tool_calls_used", 0) < BUDGET_MAX_TOOL_CALLS
        and state.get("ask_rounds_used", 0) < BUDGET_MAX_ASK_ROUNDS
        and state.get("ask_elapsed_seconds", 0) < BUDGET_MAX_WAIT_SECONDS
    )


def has_usable_image(state: AgentStateV4) -> bool:
    return bool(state.get("image_urls")) and state.get("is_relevant") is True


def severity_established(state: AgentStateV4) -> bool:
    """§1.7.7 requires a severity on every exit except INSUFFICIENT_INPUT.

    Absence here is a real gap to resolve — by asking the resident — not
    something to paper over with a default of LOW.
    """
    return state.get("severity") in {"LOW", "MEDIUM", "HIGH"}


# ---------------------------------------------------------------------------
# Fingerprints. Each level nests inside the next.
# ---------------------------------------------------------------------------


def _hash(material: dict[str, object]) -> str:
    payload = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _search_material(state: AgentStateV4) -> dict[str, object]:
    """Exactly what decides which candidates a search can return (§2.2).

    Backend derives building/floor/location from the ticket itself, so on the
    Agent side the search scope is the Category set plus the asset identity.
    """
    image_categories = state.get("image_categories")
    return {
        "text_categories": sorted(state.get("text_categories") or []),
        "image_categories": sorted(image_categories) if image_categories is not None else None,
        "location_id": state.get("location_id"),
    }


def _incident_material(state: AgentStateV4) -> dict[str, object]:
    """What the incident *is*: the search scope plus the observed symptom."""
    return {
        **_search_material(state),
        "symptom_facts": sorted(state.get("symptom_facts") or []),
        "is_relevant": state.get("is_relevant"),
    }


def _judgement_material(state: AgentStateV4) -> dict[str, object]:
    """Everything a duplicate verdict reads (`nodes._duplicate_evidence`)."""
    return {
        **_incident_material(state),
        "description": (state.get("description") or "").strip(),
        "answer_notes": list(state.get("answer_notes") or []),
        "severity": state.get("severity"),
        "red_flag_text": bool(state.get("red_flag_text")),
        "red_flag_signal": state.get("red_flag_signal"),
        "image_count": len(state.get("image_urls") or []),
    }


def compute_fingerprints(state: AgentStateV4) -> dict[str, str]:
    return {
        "search_fingerprint": _hash(_search_material(state)),
        "incident_fingerprint": _hash(_incident_material(state)),
        "judgement_fingerprint": _hash(_judgement_material(state)),
    }


def advance_revisions(state: AgentStateV4) -> dict[str, object]:
    """Recompute the three fingerprints and bump only the levels that moved.

    Returns the state updates to apply. First pass establishes revision 0 for
    all three without counting as a change.
    """
    updates: dict[str, object] = {}
    fingerprints = compute_fingerprints(state)
    for level in ("search", "incident", "judgement"):
        key = f"{level}_fingerprint"
        revision_key = f"{level}_revision"
        previous = state.get(key)
        revision = state.get(revision_key, 0)
        if previous is None:
            revision = 0
        elif fingerprints[key] != previous:
            revision += 1
        updates[key] = fingerprints[key]
        updates[revision_key] = revision
    return updates


# ---------------------------------------------------------------------------
# Revision-aware predicates.
# ---------------------------------------------------------------------------


def needs_duplicate_search(state: AgentStateV4) -> bool:
    """True when the current search scope has never been searched."""
    return state.get("duplicate_searched_revision", NEVER_RAN) != state.get("search_revision", 0)


def duplicate_candidates_valid(state: AgentStateV4) -> bool:
    """Candidates only count for the search scope that produced them.

    A Category or asset change makes the previous result a list about a
    different question; it is never reused as evidence for the new one.
    """
    if not state.get("duplicate_candidates"):
        return False
    return state.get("duplicate_candidates_revision", NEVER_RAN) == state.get("search_revision", 0)


def needs_duplicate_judgement(state: AgentStateV4) -> bool:
    """True when valid candidates were last judged on older evidence."""
    if not duplicate_candidates_valid(state):
        return False
    return state.get("duplicate_judged_revision", NEVER_RAN) != state.get("judgement_revision", 0)


def grouping_blocked_now(state: AgentStateV4) -> bool:
    """Whether grouping is off the table for the *current* incident revision."""
    if state.get("grouping_capability_blocked"):
        return True
    return state.get("grouping_blocked_revision", NEVER_RAN) == state.get("incident_revision", 0)


def needs_grouping_search(state: AgentStateV4) -> bool:
    if grouping_blocked_now(state):
        return False
    return state.get("grouping_searched_revision", NEVER_RAN) != state.get("search_revision", 0)


def grouping_candidate_ids(state: AgentStateV4) -> set[str]:
    """Ticket ids the current search revision GROUPING lookup returned."""
    if state.get("grouping_candidates_revision", NEVER_RAN) != state.get("search_revision", 0):
        return set()
    return {str(item["ticket_id"]) for item in (state.get("grouping_candidates") or [])}


def grouping_result_valid(state: AgentStateV4) -> bool:
    """Whether an accepted proposal still describes the current incident.

    A resident answer that changed the Category or the nature of the problem
    retires the proposal: it was Backend answering a question that no longer
    applies (§1.4).
    """
    grouping = state.get("grouping")
    if not grouping:
        return False
    if state.get("grouping_result_revision", NEVER_RAN) != state.get("incident_revision", 0):
        return False
    related = {str(item) for item in (grouping.get("related_ticket_ids") or [])}
    return bool(related) and related <= grouping_candidate_ids(state)
