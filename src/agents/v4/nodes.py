"""Graph nodes for the Agent v4 analysis pipeline.

Each node closes over one SQLAlchemy `Session`, the Backend-owned
`AgentBackendService` (which enforces the 5 tool calls / 3 questions / 300
seconds budget and validates every tool request), and the v4 tool port.

The nodes decide *what* to do. Backend decides whether it is allowed and what
it means. Unlike V3, no node here writes the analysis result: v4 terminal nodes
build an `AgentAnalysisResultV4` and hand it back through the graph state, and
Backend `finalize_v4()` — which does not exist yet — is what will validate and
persist it.

Three rules run through the whole file:

* **A technical failure is never a business exit.** A tool that errors, or a
  Backend capability that is missing, sets `technical_failure` /
  `dependency_gaps` and stops. It never becomes DUPLICATE_UNCERTAIN,
  LIMIT_REACHED, INSUFFICIENT_INPUT or ANALYSIS_COMPLETE.
* **Nothing is invented on the model's behalf.** A missing severity, a missing
  question, a missing grouping id: the Agent asks again or takes the safe path.
  It never fills the blank itself just to satisfy a schema.
* **Evidence has revisions.** A resident answer that changes the facts
  invalidates the duplicate and grouping verdicts reached before it; an answer
  that changes nothing does not spend another lookup.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from langgraph.types import interrupt

from src.agents.v4.llm_client import (
    AnalysisLLMClientV4,
    ExtractionContractError,
    validate_action_decision,
)
from src.agents.v4.state import (
    BUDGET_MAX_ASK_ROUNDS,
    BUDGET_MAX_TOOL_CALLS,
    BUDGET_MAX_WAIT_SECONDS,
    AgentStateV4,
    advance_revisions,
    ask_budget_available,
    budget_exhausted,
    grouping_blocked_now,
    grouping_candidate_ids,
    grouping_result_valid,
    has_usable_image,
    needs_grouping_search,
    severity_established,
)
from src.agents.v4.tools import (
    GROUPING_SEARCH_GAP,
    AnalysisToolPortV4,
    BackendAnalysisToolAdapterV4,
    ToolBudgetExhaustedError,
    ToolExecutionError,
    ToolPurposeUnsupportedError,
    duplicate_search_request,
    grouping_search_request,
    supports,
)
from src.database.models.ai_agent_session import AIAgentQuestion
from src.models.agent_schemas import AgentSeveritySource
from src.models.agent_schemas_v4 import (
    AgentAnalysisResultV4,
    AgentExitReasonV4,
    AgentGroupingResultV4,
    AgentSearchPurpose,
    AgentTicketRelation,
    AgentToolUsageV4,
    AskResidentRequestV4,
    ProposeCaseGroupingRequestV4,
)
from src.models.api.errors import DomainError
from src.models.enums import Severity
from src.services.agent_backend_service import AgentBackendService

logger = logging.getLogger(__name__)

# Only these two Categories can spread physically through the building, so only
# they are eligible for case grouping (logic doc §7.2.3, business spec §4.3a).
GROUPING_CATEGORY_CODES = {"WATER_LEAK", "ELECTRICAL_SHORT"}

MAX_ACTION_ATTEMPTS = 2

def _map_names_to_ids(names: list[str] | None, catalog: list[dict[str, object]]) -> list[str] | None:
    """Map model-visible display names back onto catalog UUIDs.

    A name outside the pinned snapshot is dropped, never coerced: that is what
    stops an invented Category from becoming an invented UUID.
    """
    if names is None:
        return None
    by_name = {str(item["display_name"]).strip().lower(): str(item["category_id"]) for item in catalog}
    mapped: list[str] = []
    for name in names:
        category_id = by_name.get(name.strip().lower())
        if category_id is None:
            logger.warning("Agent v4 returned a Category outside the catalog snapshot: %r", name)
            continue
        if category_id not in mapped:
            mapped.append(category_id)
    return mapped


def _category_names(ids: list[str] | None, catalog: list[dict[str, object]]) -> list[str]:
    if not ids:
        return []
    by_id = {str(item["category_id"]): str(item["display_name"]) for item in catalog}
    return [by_id[item] for item in ids if item in by_id]


def is_input_insufficient(state: AgentStateV4) -> bool:
    """Logic doc §5: the report cannot be understood safely.

    An attached photo that has nothing to do with the building is enough on its
    own; unintelligible text only counts when no usable photo rescues it.
    """
    has_image = bool(state.get("image_urls"))
    if has_image and state.get("is_relevant") is False:
        return True
    return not state.get("text_understandable", True) and not has_usable_image(state)


def has_technical_failure(state: AgentStateV4) -> bool:
    return state.get("technical_failure") is not None


class AgentNodesV4:
    def __init__(
        self,
        db,
        llm: AnalysisLLMClientV4,
        tools: AnalysisToolPortV4 | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.backend = AgentBackendService(db)
        self.llm = llm
        self.tools = tools or BackendAnalysisToolAdapterV4(self.backend)
        self.clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # Budget mirrors and failure bookkeeping.
    # ------------------------------------------------------------------

    def _budget(self, state: AgentStateV4) -> dict[str, object]:
        """Mirror the Backend counters. They are read, never written or reset."""
        session = self.backend._session(UUID(state["session_id"]))
        return {
            "tool_calls_used": session.total_tool_calls,
            "ask_rounds_used": session.ask_resident_rounds,
            "ask_elapsed_seconds": session.ask_resident_elapsed_seconds,
        }

    def _tool_usage(self, session_id: str) -> AgentToolUsageV4:
        usage = self.backend._backend_tool_usage(self.backend._session(UUID(session_id)))
        return AgentToolUsageV4(**usage.model_dump())

    @staticmethod
    def _fail(stage: str, error: Exception) -> dict[str, object]:
        """Record a technical failure. No business exit follows from this."""
        logger.warning("Agent v4 technical failure at %s: %s", stage, error)
        return {
            "technical_failure": {
                "stage": stage,
                "error_type": type(error).__name__,
                "detail": str(error),
            }
        }

    @staticmethod
    def _note_gap(state: AgentStateV4, gap: str) -> list[str]:
        gaps = list(state.get("dependency_gaps") or [])
        if gap not in gaps:
            gaps.append(gap)
        return gaps

    def _grouping_eligible(self, state: AgentStateV4) -> bool:
        """Read Category codes from the pinned session snapshot.

        `code` is Backend-internal and deliberately absent from both the graph
        state and every prompt, so eligibility is resolved here rather than by
        matching display-name strings.
        """
        session = self.backend._session(UUID(state["session_id"]))
        codes = {
            str(item["category_id"]): str(item.get("code") or "")
            for item in (session.category_catalog_snapshot or [])
        }
        return any(codes.get(item) in GROUPING_CATEGORY_CODES for item in (state.get("text_categories") or []))

    # ------------------------------------------------------------------
    # 1. Input validation.
    # ------------------------------------------------------------------

    def validate_input(self, state: AgentStateV4) -> dict[str, object]:
        description = (state.get("description") or "").strip()
        has_image = bool(state.get("image_urls"))
        if not description and not has_image:
            # Nothing to analyse at all: no model call is worth making.
            return {
                "text_understandable": False,
                "text_categories": [],
                "image_categories": None,
                "red_flag_signal": None,
                "is_relevant": None,
                **self._budget(state),
            }
        return {"description": description, **self._budget(state)}

    # ------------------------------------------------------------------
    # 2. Independent extraction: text, then image, neither seeing the other.
    # ------------------------------------------------------------------

    def extract_text(self, state: AgentStateV4) -> dict[str, object]:
        description = (state.get("description") or "").strip()
        if not description:
            return {
                "text_categories": [],
                "red_flag_text": False,
                "text_understandable": False,
                "text_symptom_facts": [],
                "text_severity": None,
                "is_confident": False,
            }

        catalog = state["catalog"]
        try:
            result = self.llm.extract_text(
                description=description,
                catalog_names=[str(item["display_name"]) for item in catalog],
                context_notes=state.get("answer_notes") or [],
            )
        except ExtractionContractError as exc:
            # The model could not satisfy its own schema even after a repair
            # turn. Filling in the missing field here is exactly what must not
            # happen, so the run stops without a business exit.
            return {**self._fail("extract_text", exc), **self._budget(state)}

        return {
            "text_categories": _map_names_to_ids(result.text_categories, catalog) or [],
            "red_flag_text": result.red_flag_text,
            "text_understandable": result.text_understandable,
            "text_symptom_facts": [item.strip() for item in result.symptom_facts if item.strip()],
            # Stays None when the model could not establish it. No default.
            "text_severity": result.severity,
            "text_notes": result.notes,
            "severity_gap_note": result.severity_unknown_reason,
            "is_confident": result.is_confident,
        }

    def extract_image(self, state: AgentStateV4) -> dict[str, object]:
        image_urls = state.get("image_urls") or []
        if not image_urls:
            # Contract §1.7.6: with no photo the three image fields are null
            # together. They are not "false"; they are absent.
            return {
                "image_categories": None,
                "red_flag_signal": None,
                "is_relevant": None,
                "image_severity": None,
                "image_symptom_facts": [],
            }

        catalog = state["catalog"]
        try:
            result = self.llm.extract_image(
                image_urls=image_urls,
                catalog_names=[str(item["display_name"]) for item in catalog],
            )
        except ExtractionContractError as exc:
            return {**self._fail("extract_image", exc), **self._budget(state)}

        return {
            "image_categories": _map_names_to_ids(result.image_categories, catalog) or [],
            "red_flag_signal": result.red_flag_signal,
            "is_relevant": result.is_relevant,
            "image_symptom_facts": [item.strip() for item in result.symptom_facts if item.strip()],
            "image_severity": result.severity,
            "image_notes": result.notes,
        }

    def merge_extraction(self, state: AgentStateV4) -> dict[str, object]:
        """Combine the two independent extractions and revise the evidence.

        Categories stay separate. Only severity has a defined precedence (logic
        doc §9.5: a usable photo wins, otherwise the text) — and when the
        preferred source could not establish one, the other source is consulted
        rather than a value being invented.
        """
        image_first = has_usable_image(state)
        ordered = (
            (state.get("image_severity"), "IMAGE"),
            (state.get("text_severity"), "TEXT"),
        )
        if not image_first:
            ordered = tuple(reversed(ordered))

        severity: str | None = None
        severity_source: str | None = None
        for value, source in ordered:
            if value in {"LOW", "MEDIUM", "HIGH"}:
                severity, severity_source = value, source
                break

        evidence: list[str] = []
        if state.get("red_flag_text"):
            evidence.append(f"text: {state.get('text_notes') or 'dấu hiệu nguy hiểm trong mô tả'}")
        if state.get("red_flag_signal"):
            evidence.append(f"image: {state.get('image_notes') or 'dấu hiệu nguy hiểm nhìn thấy trong ảnh'}")

        notes = [note for note in (state.get("text_notes"), state.get("image_notes")) if note]
        symptom_facts = sorted(
            {*(state.get("text_symptom_facts") or []), *(state.get("image_symptom_facts") or [])}
        )
        updates: dict[str, object] = {
            "severity": severity,
            "severity_source": severity_source,
            "symptom_facts": symptom_facts,
            "is_confident": bool(state.get("is_confident")) and bool(state.get("text_categories")) and severity is not None,
            "confidence_notes": " | ".join(notes)[:500] or None,
            "red_flag_evidence": evidence,
        }
        if severity is not None:
            updates["severity_gap_note"] = None

        # Advance each revision level on its own, so a reworded answer does not
        # spend a lookup while a changed Category does.
        revised: AgentStateV4 = {**state, **updates}  # type: ignore[typeddict-item]
        revisions = advance_revisions(revised)
        for level in ("search", "incident", "judgement"):
            key = f"{level}_revision"
            if state.get(key) is not None and revisions[key] != state.get(key):
                logger.info("Agent v4 %s evidence changed; revision -> %s", level, revisions[key])
        updates.update(revisions)
        updates.update(self._budget(state))
        return updates

    # ------------------------------------------------------------------
    # 3. Duplicate detection (search purpose=DUPLICATE, then judge).
    # ------------------------------------------------------------------

    def _duplicate_evidence(self, state: AgentStateV4) -> dict[str, object]:
        """Everything the duplicate judgement is entitled to see (§1.5).

        Sanitized by construction: the graph state never holds a reporter name,
        phone, unit or the raw content of anybody else's ticket.
        """
        image_categories = state.get("image_categories")
        return {
            "description": state.get("description", ""),
            "answer_notes": list(state.get("answer_notes") or []),
            "symptom_facts": list(state.get("symptom_facts") or []),
            "text_category_names": _category_names(state.get("text_categories"), state["catalog"]),
            "image_category_names": (
                _category_names(image_categories, state["catalog"]) if image_categories is not None else None
            ),
            "severity": state.get("severity"),
            "severity_source": state.get("severity_source"),
            "red_flag_evidence": list(state.get("red_flag_evidence") or []),
            "location_id": state.get("location_id"),
            "location_label": state.get("location_label", ""),
        }

    def search_duplicates(self, state: AgentStateV4) -> dict[str, object]:
        revision = state.get("search_revision", 0)
        if not supports(self.tools, AgentSearchPurpose.DUPLICATE):
            # A declared capability gap, reported to the caller. Duplicate
            # detection is skipped; it is never faked from a GROUPING result.
            from src.agents.v4.tools import DUPLICATE_SEARCH_GAP

            return {
                "dependency_gaps": self._note_gap(state, DUPLICATE_SEARCH_GAP),
                "duplicate_searched_revision": revision,
                "duplicate_candidates": [],
                **self._budget(state),
            }

        category_ids = list(state.get("text_categories") or [])
        for item in state.get("image_categories") or []:
            if item not in category_ids:
                category_ids.append(item)
        if not category_ids:
            return {"duplicate_searched_revision": revision, **self._budget(state)}

        try:
            response = self.tools.search_related_tickets(
                duplicate_search_request(
                    session_id=UUID(state["session_id"]),
                    ticket_id=UUID(state["ticket_id"]),
                    category_ids=[UUID(item) for item in category_ids],
                )
            )
        except ToolPurposeUnsupportedError as exc:
            return {
                "dependency_gaps": self._note_gap(state, exc.detail),
                "duplicate_searched_revision": revision,
                "duplicate_candidates": [],
                **self._budget(state),
            }
        except ToolBudgetExhaustedError:
            # Business signal: the round simply stops here.
            return {"duplicate_searched_revision": revision, **self._budget(state)}
        except (ToolExecutionError, DomainError) as exc:
            return {**self._fail("search_duplicates", exc), **self._budget(state)}

        return {
            "duplicate_candidates": [item.model_dump(mode="json") for item in response.related_tickets],
            # Candidates belong to the search scope that produced them; a later
            # Category or asset change retires them rather than reusing them.
            "duplicate_candidates_revision": revision,
            "duplicate_searched_revision": revision,
            **self._budget(state),
        }

    def judge_duplicate(self, state: AgentStateV4) -> dict[str, object]:
        """Judge the candidates against the current judgement revision."""
        revision = state.get("judgement_revision", 0)
        candidates = state.get("duplicate_candidates") or []
        if not candidates:
            return {"duplicate_verdict": "DIFFERENT_INCIDENT", "duplicate_judged_revision": revision}

        judgement = self.llm.judge_duplicate(evidence=self._duplicate_evidence(state), candidates=candidates)

        verdict = judgement.verdict
        master_id = judgement.master_ticket_id
        reason = judgement.reason

        if verdict == "SAME_INCIDENT":
            chosen = next((item for item in candidates if str(item.get("ticket_id")) == str(master_id)), None)
            blocker = self._same_incident_blocker(chosen)
            if blocker is not None:
                # Contract §1.5 item 5 with assumption 2: without a distinct
                # asset identity Backend must not auto-link, so the Agent must
                # not claim certainty it cannot support. Coordinator review is
                # the specified outcome, not a silent link.
                logger.info("Downgrading SAME_INCIDENT to UNCERTAIN: %s", blocker)
                verdict = "UNCERTAIN"
                master_id = None
                reason = f"{reason} | {blocker}"[:500]

        return {
            "duplicate_verdict": verdict,
            "duplicate_master_ticket_id": master_id if verdict == "SAME_INCIDENT" else None,
            "duplicate_reason": reason,
            "duplicate_judged_revision": revision,
        }

    def judge_red_flag_relation(self, state: AgentStateV4) -> dict[str, object]:
        """Re-read candidates already in hand, with the new red-flag evidence.

        Reached only when a red flag appeared *after* a duplicate search had
        already run in this session. No tool is called and no budget is spent —
        a red flag stops all lookups (§6) — but the evidence has changed, so the
        earlier verdict cannot simply be reused. §1.5a needs the same certainty
        as an auto-link: same live asset, master inside this session candidate
        list. Anything less leaves `red_flag_relation` null.
        """
        result = self.judge_duplicate(state)
        if result.get("duplicate_verdict") != "SAME_INCIDENT":
            result["duplicate_master_ticket_id"] = None
        return result

    def _same_incident_blocker(self, candidate: dict[str, object] | None) -> str | None:
        """Reasons certainty about "same live incident" cannot be claimed."""
        if candidate is None:
            return "Ứng viên được chọn không nằm trong kết quả tra cứu của phiên này."
        if not candidate.get("location_id"):
            return "Không xác định được cùng chính xác một tài sản/vị trí chung nên chưa đủ căn cứ liên kết tự động."
        return None

    # ------------------------------------------------------------------
    # 4. Tool loop: grouping search, grouping proposal, ask resident.
    # ------------------------------------------------------------------

    def decide_action(self, state: AgentStateV4) -> dict[str, object]:
        budget = self._budget(state)
        merged: AgentStateV4 = {**state, **budget}  # type: ignore[typeddict-item]
        iterations = state.get("iterations", 0) + 1
        merged["iterations"] = iterations
        cleared = {
            "action_grouping_ticket_ids": None,
            "action_question_text": None,
            "action_question_options": None,
            "action_allow_free_text": False,
        }

        if budget_exhausted(merged):
            return {"next_action": "CONCLUDE", "iterations": iterations, **cleared, **budget}

        grouping_eligible = self._grouping_eligible(state)
        available: list[str] = []
        if grouping_eligible and needs_grouping_search(merged):
            available.append("SEARCH_GROUPING")
        if (
            grouping_eligible
            and grouping_candidate_ids(merged)
            and not grouping_result_valid(merged)
            and not grouping_blocked_now(merged)
        ):
            available.append("PROPOSE_GROUPING")
        if ask_budget_available(merged):
            available.append("ASK_RESIDENT")
        available.append("CONCLUDE")

        # Severity is required on every exit except INSUFFICIENT_INPUT (§1.7.7)
        # and must never be defaulted. While a question is still affordable,
        # resolving it comes before anything else.
        if not severity_established(merged) and "ASK_RESIDENT" in available:
            available = ["ASK_RESIDENT"]

        if available == ["CONCLUDE"]:
            return {"next_action": "CONCLUDE", "iterations": iterations, **cleared, **budget}

        allowed_ids = grouping_candidate_ids(merged)
        violations: list[str] = []
        decision = None
        retry_note: str | None = None
        for _ in range(MAX_ACTION_ATTEMPTS):
            candidate = self.llm.decide_next_action(
                description=state.get("description", ""),
                text_category_names=_category_names(state.get("text_categories"), state["catalog"]),
                image_category_names=(
                    _category_names(state.get("image_categories"), state["catalog"])
                    if state.get("image_categories") is not None
                    else None
                ),
                severity=state.get("severity"),
                severity_gap_note=state.get("severity_gap_note"),
                is_confident=bool(state.get("is_confident")),
                confidence_notes=state.get("confidence_notes"),
                available_actions=available,  # type: ignore[arg-type]
                grouping_candidates=state.get("grouping_candidates") or [],
                budget_note=(
                    f"đã dùng {merged.get('tool_calls_used', 0)}/{BUDGET_MAX_TOOL_CALLS} lần gọi công cụ, "
                    f"{merged.get('ask_rounds_used', 0)}/{BUDGET_MAX_ASK_ROUNDS} lượt hỏi, "
                    f"{merged.get('ask_elapsed_seconds', 0)}/{BUDGET_MAX_WAIT_SECONDS} giây chờ"
                ),
                retry_note=retry_note,
            )
            violation = validate_action_decision(
                candidate,
                available_actions=available,
                grouping_candidate_ids=allowed_ids,
            )
            if violation is None:
                decision = candidate
                break
            violations.append(violation)
            retry_note = violation

        if decision is None:
            # The model could not produce a well-formed action. Repairing it
            # here would mean inventing the very business data it failed to
            # supply, so the safe path is taken instead. Deciding an action is
            # not a billable tool call, so nothing was spent.
            logger.warning("Agent v4 action rejected %d time(s): %s", len(violations), violations)
            return {
                "next_action": "CONCLUDE",
                "action_reason": "Model action rejected by contract validation.",
                "invalid_action_notes": [*(state.get("invalid_action_notes") or []), *violations],
                "iterations": iterations,
                **cleared,
                **budget,
            }

        return {
            "next_action": decision.action,
            "action_reason": decision.reason,
            "action_grouping_ticket_ids": decision.grouping_related_ticket_ids,
            "action_question_text": decision.question_text,
            "action_question_options": decision.question_options,
            "action_allow_free_text": decision.allow_free_text_fallback,
            "invalid_action_notes": [*(state.get("invalid_action_notes") or []), *violations],
            "iterations": iterations,
            **budget,
        }

    def search_grouping(self, state: AgentStateV4) -> dict[str, object]:
        revision = state.get("search_revision", 0)
        incident_revision = state.get("incident_revision", 0)
        if not supports(self.tools, AgentSearchPurpose.GROUPING):
            # Capability-scoped: no revision will ever make this work, so it is
            # recorded as a dependency gap and stays blocked for the session.
            return {
                "dependency_gaps": self._note_gap(state, GROUPING_SEARCH_GAP),
                "grouping_capability_blocked": True,
                "grouping_searched_revision": revision,
                **self._budget(state),
            }

        category_ids = list(state.get("text_categories") or [])
        if not category_ids:
            return {
                "grouping_searched_revision": revision,
                "grouping_blocked_revision": incident_revision,
                **self._budget(state),
            }

        try:
            response = self.tools.search_related_tickets(
                grouping_search_request(
                    session_id=UUID(state["session_id"]),
                    ticket_id=UUID(state["ticket_id"]),
                    category_ids=[UUID(item) for item in category_ids],
                )
            )
        except ToolPurposeUnsupportedError as exc:
            return {
                "dependency_gaps": self._note_gap(state, exc.detail),
                "grouping_capability_blocked": True,
                "grouping_searched_revision": revision,
                **self._budget(state),
            }
        except ToolBudgetExhaustedError:
            return {"grouping_searched_revision": revision, **self._budget(state)}
        except (ToolExecutionError, DomainError) as exc:
            return {**self._fail("search_grouping", exc), **self._budget(state)}

        return {
            "grouping_candidates": [item.model_dump(mode="json") for item in response.related_tickets],
            "grouping_candidates_revision": revision,
            "grouping_searched_revision": revision,
            **self._budget(state),
        }

    def propose_grouping(self, state: AgentStateV4) -> dict[str, object]:
        """Propose exactly the tickets the model named — or nothing at all.

        `decide_action` has already rejected an empty or out-of-list id set, so
        reaching here with one means the state moved underneath us; the
        proposal is dropped rather than widened to "everything found".
        """
        incident_revision = state.get("incident_revision", 0)
        allowed = grouping_candidate_ids(state)
        requested = list(dict.fromkeys(state.get("action_grouping_ticket_ids") or []))
        if not requested or any(item not in allowed for item in requested):
            logger.warning("Dropping PROPOSE_GROUPING: ids %s are not the current GROUPING candidates.", requested)
            # Blocked for this incident revision only. If the resident later
            # changes what the problem is, grouping becomes available again.
            return {"grouping_blocked_revision": incident_revision, **self._budget(state)}

        reason = state.get("action_reason") or "Agent đề xuất gộp cụm sự cố lan rộng."
        try:
            response = self.tools.propose_case_grouping(
                ProposeCaseGroupingRequestV4(
                    session_id=UUID(state["session_id"]),
                    ticket_id=UUID(state["ticket_id"]),
                    related_ticket_ids=[UUID(item) for item in requested],
                    reason=reason[:300],
                )
            )
        except ToolBudgetExhaustedError:
            return {"grouping_blocked_revision": incident_revision, **self._budget(state)}
        except (ToolExecutionError, DomainError) as exc:
            return {**self._fail("propose_case_grouping", exc), **self._budget(state)}

        if not response.accepted:
            # A rejection is Backend answering the question, not a failure.
            # Re-proposing the same set at this revision would only burn budget.
            logger.info("propose_case_grouping not accepted: %s", response.rejected_reason)
            return {"grouping_blocked_revision": incident_revision, **self._budget(state)}

        # Note what is not copied: response.density. Backend recomputes Density
        # per distinct unit when it finalizes (§1.4).
        return {
            "grouping": {
                "grouped": True,
                "related_ticket_ids": [str(item) for item in response.related_ticket_ids],
                "reason": reason[:300],
            },
            # Stamped with the incident revision it describes. A later answer
            # that changes the Category or the nature of the problem retires it
            # instead of carrying a stale case into the payload.
            "grouping_result_revision": incident_revision,
            "grouping_blocked_revision": incident_revision,
            **self._budget(state),
        }

    def ask_prepare(self, state: AgentStateV4) -> dict[str, object]:
        """Ask the resident the model's question. Never a substitute question."""
        question_text = (state.get("action_question_text") or "").strip()
        if not question_text:
            # decide_action validates this; reaching here without one means the
            # action was rewritten mid-flight. Inventing a generic question
            # would spend a resident round on something nobody asked for.
            logger.warning("ASK_RESIDENT reached ask_prepare without question_text; skipping.")
            return {"ask_prepare_failed": True, **self._budget(state)}

        options = state.get("action_question_options")
        try:
            question_id = self.tools.ask_resident(
                AskResidentRequestV4(
                    session_id=UUID(state["session_id"]),
                    ticket_id=UUID(state["ticket_id"]),
                    question_type="MULTIPLE_CHOICE" if options else "FREE_TEXT",
                    # Length was validated before routing here; truncating
                    # would send the resident a question nobody wrote.
                    question_text=question_text,
                    options=options,
                    allow_free_text_fallback=bool(state.get("action_allow_free_text")) or not options,
                )
            )
        except ToolBudgetExhaustedError:
            logger.info("ask_resident dừng lại: đã hết ngân sách hỏi cư dân.")
            return {"ask_prepare_failed": True, **self._budget(state)}
        except (ToolExecutionError, DomainError) as exc:
            # A closed session or a malformed question is a technical fault, not
            # a budget limit. Reporting LIMIT_REACHED here would contradict the
            # session counters and be rejected by Backend, hanging the ticket.
            return {**self._fail("ask_resident", exc), **self._budget(state)}

        return {"pending_question_id": str(question_id), "ask_prepare_failed": False, **self._budget(state)}

    def ask_wait(self, state: AgentStateV4) -> dict[str, object]:
        interrupt({"question_id": state.get("pending_question_id"), "reason": "waiting_for_resident_answer"})
        return {}

    def ask_finalize(self, state: AgentStateV4) -> dict[str, object]:
        question_id = state.get("pending_question_id")
        question = self.db.get(AIAgentQuestion, UUID(question_id)) if question_id else None
        if question is None or question.status != "ANSWERED":
            return {**self._budget(state)}

        notes = list(state.get("answer_notes") or [])
        updates: dict[str, object] = {}
        if question.answer_type == "NEW_PHOTO":
            attachment = self.backend.attachments.get_latest_resident_supplement(UUID(state["ticket_id"]))
            if attachment is not None:
                try:
                    signed_url = self.backend.storage.create_signed_download_url(attachment.object_path)
                    updates["image_urls"] = [*(state.get("image_urls") or []), signed_url]
                    updates["image_paths"] = [*(state.get("image_paths") or []), attachment.object_path]
                except DomainError:
                    logger.warning("Unable to sign resident-supplement image URL.", exc_info=True)
            notes.append(f"Cư dân đã gửi ảnh bổ sung (vòng {question.round_number}).")
        else:
            notes.append(f"Trả lời của cư dân (vòng {question.round_number}): {question.answer_text}")

        updates["answer_notes"] = notes
        updates["reextraction"] = True
        updates["pending_question_id"] = None
        # Budget counters are mirrored, never reset: the 300 seconds and the
        # three rounds are for the whole session (§1.1).
        updates.update(self._budget(state))
        return updates

    # ------------------------------------------------------------------
    # 5. Terminal nodes. They build the payload; they do not persist it.
    # ------------------------------------------------------------------

    def _build_result(
        self,
        state: AgentStateV4,
        *,
        exit_reason: AgentExitReasonV4,
        duplicate: AgentTicketRelation | None = None,
        red_flag_relation: AgentTicketRelation | None = None,
        confidence_notes: str | None = None,
        is_confident: bool | None = None,
    ) -> AgentAnalysisResultV4:
        session = self.backend._session(UUID(state["session_id"]))
        usage = self._tool_usage(state["session_id"])
        notes = confidence_notes if confidence_notes is not None else state.get("confidence_notes")

        if exit_reason == AgentExitReasonV4.INSUFFICIENT_INPUT:
            # Nothing extracted may be reported here, so the payload carries the
            # absence honestly instead of a plausible-looking guess.
            has_image = bool(state.get("image_urls"))
            return AgentAnalysisResultV4(
                ticket_id=UUID(state["ticket_id"]),
                analysis_session_id=UUID(state["session_id"]),
                exit_reason=exit_reason,
                text_categories=None,
                red_flag_text=False,
                image_categories=[] if has_image else None,
                red_flag_signal=False if has_image else None,
                is_relevant=bool(state.get("is_relevant")) if has_image else None,
                severity=None,
                severity_source=None,
                is_confident=False,
                confidence_notes=notes,
                grouping=None,
                duplicate=None,
                red_flag_relation=None,
                tool_usage=usage,
                category_catalog_version=session.category_catalog_version,
                model_version=state["model_version"],
                analyzed_at=self.clock(),
            )

        severity = state.get("severity")
        if severity not in {"LOW", "MEDIUM", "HIGH"}:
            # Guard rather than default. Routing keeps this unreachable; if it
            # ever fires, a loud failure beats a fabricated severity reaching
            # Backend scoring.
            raise ValueError(f"{exit_reason.value} requires an established severity; routing should have prevented this.")

        image_categories = state.get("image_categories")
        # §1.4: a case proposal may only be reported when it is about the
        # current incident, its tickets came from the current GROUPING search,
        # the Category is still grouping-eligible, and there is no duplicate.
        grouping_payload: AgentGroupingResultV4 | None = None
        if duplicate is None and grouping_result_valid(state) and self._grouping_eligible(state):
            accepted = state["grouping"]
            grouping_payload = AgentGroupingResultV4(
                grouped=True,
                related_ticket_ids=[UUID(item) for item in accepted["related_ticket_ids"]],
                reason=str(accepted["reason"]),
            )
        return AgentAnalysisResultV4(
            ticket_id=UUID(state["ticket_id"]),
            analysis_session_id=UUID(state["session_id"]),
            exit_reason=exit_reason,
            text_categories=[UUID(item) for item in (state.get("text_categories") or [])],
            red_flag_text=bool(state.get("red_flag_text")),
            image_categories=([UUID(item) for item in image_categories] if image_categories is not None else None),
            red_flag_signal=state.get("red_flag_signal"),
            is_relevant=state.get("is_relevant"),
            severity=Severity(severity),
            severity_source=AgentSeveritySource(state.get("severity_source") or "TEXT"),
            is_confident=bool(state.get("is_confident")) if is_confident is None else is_confident,
            confidence_notes=notes,
            # A duplicate is never also a spreading case (§1.5 item 1).
            grouping=grouping_payload,
            duplicate=duplicate,
            red_flag_relation=red_flag_relation,
            tool_usage=usage,
            category_catalog_version=session.category_catalog_version,
            model_version=state["model_version"],
            analyzed_at=self.clock(),
        )

    @staticmethod
    def _emit(result: AgentAnalysisResultV4) -> dict[str, object]:
        return {"exit_reason": result.exit_reason.value, "result": result.model_dump(mode="json")}

    def abort_technical(self, state: AgentStateV4) -> dict[str, object]:
        """Terminal for technical faults. Produces no business exit at all."""
        failure = state.get("technical_failure") or {"stage": "unknown", "detail": "unspecified"}
        logger.error("Agent v4 aborting without a business exit: %s", failure)
        return {"exit_reason": None, "result": None}

    def exit_red_flag(self, state: AgentStateV4) -> dict[str, object]:
        # §1.5a: a red flag on a report about an incident that is already being
        # worked on links evidence to the master. It never closes the new ticket,
        # so `duplicate` stays null and only `red_flag_relation` is set.
        relation: AgentTicketRelation | None = None
        master_id = state.get("duplicate_master_ticket_id")
        if state.get("duplicate_verdict") == "SAME_INCIDENT" and master_id:
            relation = AgentTicketRelation(
                master_ticket_id=UUID(str(master_id)),
                reason=(state.get("duplicate_reason") or "Cùng sự cố đang hoạt động, phản ánh mới có dấu hiệu nguy hiểm.")[:500],
            )
        evidence = " | ".join(state.get("red_flag_evidence") or []) or None
        result = self._build_result(
            state,
            exit_reason=AgentExitReasonV4.RED_FLAG,
            red_flag_relation=relation,
            confidence_notes=evidence or state.get("confidence_notes"),
        )
        return self._emit(result)

    def exit_duplicate_existing(self, state: AgentStateV4) -> dict[str, object]:
        # Same fallback text for both fields. A previous version let
        # `confidence_notes` fall through to `state.get("duplicate_reason") or
        # None`, silently dropping the coordinator-facing note to null whenever
        # `duplicate_reason` was empty even though `duplicate.reason` a few
        # lines below had a real fallback string — the note box in the ticket
        # panel would then just not render for a duplicate exit.
        reason = (state.get("duplicate_reason") or "Cùng một sự cố đang được xử lý.")[:500]
        duplicate = AgentTicketRelation(master_ticket_id=UUID(str(state["duplicate_master_ticket_id"])), reason=reason)
        result = self._build_result(
            state,
            exit_reason=AgentExitReasonV4.DUPLICATE_EXISTING,
            duplicate=duplicate,
            confidence_notes=reason,
            is_confident=True,
        )
        return self._emit(result)

    def exit_duplicate_uncertain(self, state: AgentStateV4) -> dict[str, object]:
        reason = state.get("duplicate_reason") or "Có ứng viên liên quan nhưng chưa đủ chắc chắn để liên kết."
        result = self._build_result(
            state,
            exit_reason=AgentExitReasonV4.DUPLICATE_UNCERTAIN,
            confidence_notes=reason[:500],
            is_confident=False,
        )
        return self._emit(result)

    def exit_analysis_complete(self, state: AgentStateV4) -> dict[str, object]:
        # ANALYSIS_COMPLETE asserts only that extraction finished. It makes no
        # claim about the text-derived and image-derived Category agreeing —
        # Backend reconciles them (§1.7 item 7).
        result = self._build_result(state, exit_reason=AgentExitReasonV4.ANALYSIS_COMPLETE)
        return self._emit(result)

    def exit_limit(self, state: AgentStateV4) -> dict[str, object]:
        result = self._build_result(state, exit_reason=AgentExitReasonV4.LIMIT_REACHED, is_confident=False)
        return self._emit(result)

    def exit_insufficient(self, state: AgentStateV4) -> dict[str, object]:
        # `_route_conclude` reaches this exit from two different conditions
        # (§ graph.py `_route_conclude`): a missing severity, or
        # `is_input_insufficient` (an irrelevant image, or unintelligible text
        # with no usable photo to rescue it). The old fallback only covered the
        # first case, so a ticket that failed on relevance/understandability
        # while severity happened to already be established fell through to
        # `state.get("confidence_notes")` — which is commonly empty, since the
        # extraction prompt only asks the model to write notes when it is not
        # confident, and confidence tracks Category/severity, not relevance.
        # That left the coordinator no explanation at all for the exit.
        notes = state.get("confidence_notes")
        if not (notes or "").strip():
            if not severity_established(state):
                gap = state.get("severity_gap_note") or "không xác định được mức độ nghiêm trọng"
                notes = f"Không đủ căn cứ để kết luận: {gap}"[:500]
            elif bool(state.get("image_urls")) and state.get("is_relevant") is False:
                notes = "Ảnh đính kèm không cho thấy sự cố nào trong khu chung cư nên không dùng được để phân loại."
            else:
                notes = "Mô tả của cư dân không đủ rõ để hiểu vấn đề và không có ảnh khả dụng để bổ sung."
        result = self._build_result(state, exit_reason=AgentExitReasonV4.INSUFFICIENT_INPUT, confidence_notes=notes)
        return self._emit(result)


__all__ = ["AgentNodesV4", "has_technical_failure", "is_input_insufficient"]
