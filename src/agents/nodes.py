"""Graph nodes for the one ticket-analysis pipeline.

Each node closes over one SQLAlchemy `Session`, the Backend-owned
`AgentBackendService` (which enforces the 5 tool calls / 3 questions / 300
seconds budget and validates every request), and the model client.

The nodes decide *what* to do. Backend decides whether it is allowed and what
it means. Three rules run through the whole file:

* **A technical failure is never a business exit.** A model that cannot satisfy
  its own schema, a tool that errors: those set `technical_failure` and stop.
  They never become DUPLICATE_UNCERTAIN, LIMIT_REACHED, INSUFFICIENT_INPUT or
  ANALYSIS_COMPLETE.
* **Nothing is invented on the model's behalf.** A missing severity, a missing
  question, an unrecognised Category name: the Agent asks again or takes the
  safe path. It never fills the blank itself to satisfy a schema.
* **Evidence has a revision.** A resident answer that changes the Category, the
  location or the material facts invalidates the candidate lookup made before
  it; an answer that changes none of them does not spend another one.

Grouping is not here. It runs in the background after the resident already has
their answer -- see `service.run_case_grouping`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from langgraph.types import interrupt

from src.agents.llm_client import AgentLLMClient, ModelContractError
from src.agents.state import (
    AgentState,
    advance_evidence_revision,
    category_is_confirmed,
    classification_settled,
)
from src.database.models.ai_agent_session import AIAgentQuestion
from src.models.agent_schemas import (
    LOCATION_OPTIONS,
    AgentAnalysisResult,
    AgentExitReason,
    AgentQuestionKind,
    AgentSearchPurpose,
    AgentSeveritySource,
    AgentTicketRelation,
    AgentToolUsage,
    CandidateTicket,
    DuplicateVerdict,
)
from src.models.api.errors import AGENT_BUDGET_EXHAUSTED, DomainError
from src.models.enums import Severity
from src.services.agent_backend_service import AgentBackendService

logger = logging.getLogger(__name__)

#: Fixed answer sets. The model writes the question; Backend owns the options,
#: so an answer always maps back onto something the system can act on rather
#: than onto free prose it would have to interpret.
SEVERITY_OPTIONS: dict[str, str] = {
    "Nhẹ, chưa ảnh hưởng nhiều": Severity.LOW.value,
    "Vừa, gây bất tiện rõ rệt": Severity.MEDIUM.value,
    "Nặng, ảnh hưởng nghiêm trọng": Severity.HIGH.value,
}

# The two location answers come from the contract module: Backend has to
# enforce what each one implies when the answer arrives, so both ends read the
# same strings (`src.models.agent_schemas.LOCATION_OPTIONS`).

RECENT_COMPLETION_QUESTION = "Phản ánh tương tự vừa được xử lý xong. Trường hợp nào đúng với bạn?"
RECENT_NO_NEW_INFO = "Không có thông tin mới, tôi gửi lại đúng sự cố đó"
RECENT_RECURRED = "Sự cố tái phát sau khi đã xử lý"
RECENT_WORSE = "Sự cố nghiêm trọng hơn hoặc có thông tin mới"
RECENT_UNSURE = "Tôi không chắc"
RECENT_COMPLETION_OPTIONS = [RECENT_NO_NEW_INFO, RECENT_RECURRED, RECENT_WORSE, RECENT_UNSURE]


def _map_name_to_id(name: str | None, catalog: list[dict[str, object]]) -> str | None:
    """Map a model-visible display name back onto a catalog UUID.

    A name outside the pinned snapshot is dropped, never coerced: that is what
    stops an invented Category from becoming an invented UUID.
    """
    if not name:
        return None
    by_name = {str(item["display_name"]).strip().lower(): str(item["category_id"]) for item in catalog}
    category_id = by_name.get(name.strip().lower())
    if category_id is None:
        logger.warning("Agent returned a Category outside the catalog snapshot: %r", name)
    return category_id


def _category_name(category_id: str | None, catalog: list[dict[str, object]]) -> str:
    if not category_id:
        return ""
    for item in catalog:
        if str(item["category_id"]) == str(category_id):
            return str(item["display_name"])
    return ""


class AgentNodes:
    def __init__(
        self,
        db,
        llm: AgentLLMClient,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.backend = AgentBackendService(db)
        self.llm = llm
        self.clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # Budget mirrors and failure bookkeeping.
    # ------------------------------------------------------------------

    def _budget(self, state: AgentState) -> dict[str, object]:
        """Mirror the Backend counters. They are read, never written or reset."""
        session = self.backend._session(UUID(state["session_id"]))
        return {
            "tool_calls_used": session.total_tool_calls,
            "ask_rounds_used": session.ask_resident_rounds,
            "ask_elapsed_seconds": session.ask_resident_elapsed_seconds,
        }

    def _tool_usage(self, session_id: str) -> AgentToolUsage:
        return self.backend._backend_tool_usage(self.backend._session(UUID(session_id)))

    @staticmethod
    def _fail(stage: str, error: Exception) -> dict[str, object]:
        """Record a technical failure. No business exit follows from this."""
        logger.warning("Agent technical failure at %s: %s", stage, error)
        return {
            "technical_failure": {
                "stage": stage,
                "error_code": type(error).__name__,
                "detail": str(error),
            }
        }

    # ------------------------------------------------------------------
    # 1. One multimodal classification call.
    # ------------------------------------------------------------------

    def classify(self, state: AgentState) -> dict[str, object]:
        """The single model call that reads the whole evidence package.

        Description, photos, selected location and the full question/answer
        history go in together. Because one call sees everything, there is no
        text-only result and no image-only result to reconcile afterwards --
        the two `*_category_id` fields it reports are explanation, not inputs
        to a merge.

        A Category the resident already confirmed is passed in as fixed context
        and re-imposed on the way out. The model may still reassess severity,
        danger, its reasoning and whether another permitted question is needed;
        it may not change which problem the ticket is about.
        """
        description = (state.get("description") or "").strip()
        image_urls = state.get("image_urls") or []
        if not description and not image_urls:
            # Nothing to analyse at all; no model call is worth making.
            return {
                "understandable": False,
                "category_id": None,
                "severity": None,
                "red_flag": False,
                "image_relevant": None,
                "incident_facts": [],
                "ai_reason": "Phản ánh không có mô tả và không có ảnh.",
                "requested_question": None,
                **self._budget(state),
            }

        catalog = state["catalog"]
        confirmed_id = state.get("confirmed_category_id") if category_is_confirmed(state) else None
        confirmed_name = state.get("confirmed_category_name") or _category_name(confirmed_id, catalog)
        try:
            result = self.llm.classify(
                description=description,
                image_urls=image_urls,
                catalog_names=[str(item["display_name"]) for item in catalog],
                location_label=state.get("location_label", ""),
                floor_label=state.get("floor_label", ""),
                unit_code=state.get("unit_code"),
                conversation=state.get("conversation") or [],
                confirmed_category=confirmed_name or None,
            )
        except ModelContractError as exc:
            # The model could not satisfy its own schema even after a repair
            # turn. Filling in the missing field here is exactly what must not
            # happen, so the run stops without a business exit.
            return {**self._fail("classify", exc), **self._budget(state)}

        category_id = _map_name_to_id(result.category, catalog)
        if confirmed_id:
            # The resident settled this. Telling the model is not the same as
            # trusting it to comply, so the confirmed id is re-imposed here and
            # the disagreement -- if there was one -- is only ever recorded as
            # evidence in the two `*_category_id` fields.
            if category_id and category_id != confirmed_id:
                logger.info("Ignoring a Category the model changed after the resident confirmed one.")
            category_id = confirmed_id
        text_category_id = _map_name_to_id(result.text_category, catalog)
        image_category_id = _map_name_to_id(result.image_category, catalog) if image_urls else None
        question = self._requested_question(result, catalog, confirmed=bool(confirmed_id))

        updates: dict[str, object] = {
            "category_id": category_id,
            "text_category_id": text_category_id,
            "image_category_id": image_category_id,
            "severity": result.severity,
            "severity_source": ("IMAGE" if image_urls and result.image_relevant else "TEXT"),
            "red_flag": result.red_flag,
            "ai_reason": result.ai_reason,
            "understandable": result.understandable,
            "image_relevant": result.image_relevant if image_urls else None,
            "incident_facts": [item.strip() for item in result.incident_facts if item.strip()],
            "requested_question": question,
        }
        # The fingerprint is computed on the post-classification facts, so a
        # reworded answer that changed nothing does not retire the candidate
        # snapshot the previous round already paid for.
        merged: AgentState = {**state, **updates}  # type: ignore[typeddict-item]
        updates.update(advance_evidence_revision(merged))
        updates.update(self._budget(state))
        return updates

    def _requested_question(
        self,
        result,
        catalog: list[dict[str, object]],
        *,
        confirmed: bool = False,
    ) -> dict[str, object] | None:
        """Turn the model's question request into a concrete, answerable one.

        Backend owns the option lists. For a Category confirmation that means
        dropping any name the model invented and refusing the question outright
        if fewer than two real Categories survive -- a one-option "choice" is
        not a question, and inventing a second option would be putting words in
        the resident's mouth.

        `confirmed` closes the Category question for good once the resident has
        answered it. Asking again would be asking them to defend a decision
        they already made, and it is the same three-question budget either way.
        """
        kind = result.question_kind
        if kind == "NONE":
            return None
        text = (result.question_text or "").strip()

        if kind == "CATEGORY_CONFIRMATION":
            if confirmed:
                logger.info("Dropping CATEGORY_CONFIRMATION: the resident already chose a Category.")
                return None
            names: list[str] = []
            for name in result.category_options or []:
                if _map_name_to_id(name, catalog) and name.strip() not in names:
                    names.append(name.strip())
            if len(names) < 2:
                logger.warning("Dropping CATEGORY_CONFIRMATION: fewer than two catalog Categories survived.")
                return None
            return {"kind": AgentQuestionKind.CATEGORY_CONFIRMATION.value, "text": text, "options": names}

        if kind == "SEVERITY_CONFIRMATION":
            return {
                "kind": AgentQuestionKind.SEVERITY_CONFIRMATION.value,
                "text": text,
                "options": list(SEVERITY_OPTIONS),
            }

        if kind == "LOCATION_CONFIRMATION":
            return {
                "kind": AgentQuestionKind.LOCATION_CONFIRMATION.value,
                "text": text,
                "options": list(LOCATION_OPTIONS),
            }
        return None

    # ------------------------------------------------------------------
    # 2. Asking the resident.
    # ------------------------------------------------------------------

    def ask_prepare(self, state: AgentState) -> dict[str, object]:
        question = state.get("requested_question")
        if not question or not str(question.get("text") or "").strip():
            logger.warning("Reached ask_prepare without a usable question; skipping.")
            return {"ask_prepare_failed": True, **self._budget(state)}

        kind = str(question["kind"])
        options = list(question.get("options") or [])
        try:
            created = self.backend.create_question(
                UUID(state["session_id"]),
                ticket_id=UUID(state["ticket_id"]),
                question_kind=kind,
                question_type="MULTIPLE_CHOICE" if options else "FREE_TEXT",
                question_text=str(question["text"]),
                options=options or None,
                # A location is only ever re-picked from the fixed selector, and
                # a recurrence answer is one of four fixed statements. Letting
                # either be answered in prose would be inviting exactly the
                # free-text inference this design rules out.
                allow_free_text_fallback=kind
                not in {
                    AgentQuestionKind.LOCATION_CONFIRMATION.value,
                    AgentQuestionKind.RECENT_COMPLETION.value,
                },
            )
        except DomainError as exc:
            if exc.code == AGENT_BUDGET_EXHAUSTED:
                logger.info("ask_resident stopped: the question budget is spent.")
                return {"ask_prepare_failed": True, **self._budget(state)}
            # A closed session or a malformed question is a technical fault, not
            # a budget limit. Reporting LIMIT_REACHED here would contradict the
            # session counters and hang the ticket.
            return {**self._fail("ask_resident", exc), **self._budget(state)}

        return {
            "pending_question_id": str(created.id),
            "pending_question_kind": kind,
            "ask_prepare_failed": False,
            **self._budget(state),
        }

    def ask_wait(self, state: AgentState) -> dict[str, object]:
        interrupt({"question_id": state.get("pending_question_id"), "reason": "waiting_for_resident_answer"})
        return {}

    def ask_finalize(self, state: AgentState) -> dict[str, object]:
        """Fold the resident's answer back into the evidence package.

        Each kind of answer updates exactly the field it is about. Nothing is
        inferred from the prose: a Category answer is matched against the
        options that were offered, a severity answer against the fixed three,
        and a location answer was already applied to the ticket by
        `AgentQuestionService` before the graph woke up.
        """
        question_id = state.get("pending_question_id")
        question = self.db.get(AIAgentQuestion, UUID(question_id)) if question_id else None
        if question is None or question.status != "ANSWERED":
            return {**self._budget(state)}

        answer = (question.answer_text or "").strip()
        kind = question.question_kind
        conversation = [
            *(state.get("conversation") or []),
            {
                "kind": kind,
                "round": question.round_number,
                "question": question.question_text,
                "answer": answer or "(ảnh bổ sung)",
            },
        ]
        updates: dict[str, object] = {
            "conversation": conversation,
            "requested_question": None,
            "pending_question_id": None,
            "pending_question_kind": None,
        }

        if kind == AgentQuestionKind.CATEGORY_CONFIRMATION.value:
            # `AgentQuestionService` already wrote the choice onto the ticket
            # and recorded the id it resolved. Reading that id back -- rather
            # than re-matching the prose -- keeps the graph and the database
            # agreeing on which Category the resident picked.
            payload = question.answer_payload or {}
            chosen = payload.get("confirmed_category_id") or _map_name_to_id(answer, state["catalog"])
            if chosen:
                # This is the one place a final Category is set from something
                # other than the model, and it is a choice made from a list,
                # not an inference. From here on it is fixed: `classify` is
                # told about it and overrules anything that contradicts it.
                updates["category_id"] = str(chosen)
                updates["confirmed_category_id"] = str(chosen)
                updates["confirmed_category_name"] = str(
                    payload.get("confirmed_category_name") or _category_name(str(chosen), state["catalog"])
                )
        elif kind == AgentQuestionKind.SEVERITY_CONFIRMATION.value:
            severity = SEVERITY_OPTIONS.get(answer)
            if severity:
                updates["severity"] = severity
                updates["severity_source"] = "TEXT"
        elif kind == AgentQuestionKind.LOCATION_CONFIRMATION.value:
            payload = question.answer_payload or {}
            selected = payload.get("selected_location_id")
            if selected:
                updates["location_id"] = str(selected)
                updates["location_label"] = str(payload.get("selected_location_label") or "")
                # The location type decides the scoring bonus, so the P3 check
                # would score the old place without this.
                location = self.backend.catalog.get_location(UUID(str(selected)))
                updates["location_type_code"] = (
                    location.location_type.code if location and location.location_type else None
                )
        elif kind == AgentQuestionKind.RECENT_COMPLETION.value:
            updates["recent_completion_answer"] = answer

        if question.answer_type == "NEW_PHOTO":
            attachment = self.backend.attachments.get_latest_resident_supplement(UUID(state["ticket_id"]))
            if attachment is not None:
                try:
                    signed_url = self.backend.storage.create_signed_download_url(attachment.object_path)
                    updates["image_urls"] = [*(state.get("image_urls") or []), signed_url]
                    updates["image_paths"] = [*(state.get("image_paths") or []), attachment.object_path]
                except DomainError:
                    logger.warning("Unable to sign the resident-supplement image URL.", exc_info=True)

        updates["iterations"] = state.get("iterations", 0) + 1
        # Budget counters are mirrored, never reset: the 300 seconds and the
        # three rounds are for the whole session.
        updates.update(self._budget(state))
        return updates

    # ------------------------------------------------------------------
    # 3. Duplicate stage.
    # ------------------------------------------------------------------

    def search_duplicates(self, state: AgentState) -> dict[str, object]:
        """Fetch the candidate snapshot Backend built, or reuse the last one.

        Only reached once Category, severity and location are settled: the
        lookup keys on the exact Category and the exact location, so running it
        earlier would be looking for the wrong thing.
        """
        revision = state.get("evidence_revision", 0)
        category_id = state.get("category_id")
        if not category_id:
            return {"duplicate_searched_revision": revision, **self._budget(state)}

        try:
            response = self.backend.search_related_tickets(
                UUID(state["session_id"]),
                ticket_id=UUID(state["ticket_id"]),
                category_id=UUID(str(category_id)),
                purpose=AgentSearchPurpose.DUPLICATE.value,
            )
        except DomainError as exc:
            if exc.code == AGENT_BUDGET_EXHAUSTED:
                # A business signal: the round simply stops looking.
                return {"duplicate_searched_revision": revision, **self._budget(state)}
            return {**self._fail("search_duplicates", exc), **self._budget(state)}

        return {
            "duplicate_candidates": list(response.get("candidates") or []),
            # Candidates belong to the evidence revision that produced them; a
            # later Category or location change retires them.
            "duplicate_candidates_revision": revision,
            "duplicate_searched_revision": revision,
            **self._budget(state),
        }

    def judge_duplicate(self, state: AgentState) -> dict[str, object]:
        """Judge the snapshot. Skipped entirely when it is empty.

        An empty candidate list has exactly one possible answer, so spending a
        model call on it would be latency in exchange for nothing.
        """
        candidates = state.get("duplicate_candidates") or []
        if not candidates:
            return {"duplicate_verdict": "DIFFERENT_INCIDENT", "duplicate_reason": None}

        try:
            judgement = self.llm.judge_duplicate(
                evidence=self._duplicate_evidence(state),
                candidates=candidates,
            )
        except Exception as exc:  # noqa: BLE001 - a model fault is technical, never UNCERTAIN
            return {**self._fail("judge_duplicate", exc), **self._budget(state)}

        verdict = judgement.verdict
        master_id = judgement.master_ticket_id
        reason = judgement.reason

        if verdict == "SAME_INCIDENT":
            chosen = next((item for item in candidates if str(item.get("ticket_id")) == str(master_id)), None)
            if chosen is None:
                # The model named something outside the snapshot it was given.
                # Linking on that would be linking to a ticket nobody checked.
                logger.info("Downgrading SAME_INCIDENT to UNCERTAIN: master is outside the candidate snapshot.")
                return {
                    "duplicate_verdict": "UNCERTAIN",
                    "duplicate_master_ticket_id": None,
                    "duplicate_reason": (
                        f"{reason} | Ứng viên được chọn không nằm trong danh sách tra cứu của phiên này."
                    )[:500],
                }
            if chosen.get("recently_completed"):
                # The matching report was closed within the hour. Whether this
                # is the same incident or the problem coming back is something
                # only the resident knows, so it is asked rather than assumed.
                return {
                    "duplicate_verdict": None,
                    "duplicate_master_ticket_id": str(master_id),
                    "duplicate_reason": reason,
                    "recent_completion_master_id": str(master_id),
                }

        return {
            "duplicate_verdict": verdict,
            "duplicate_master_ticket_id": master_id if verdict == "SAME_INCIDENT" else None,
            "duplicate_reason": reason,
        }

    def ask_recent_completion(self, state: AgentState) -> dict[str, object]:
        """Queue the fixed recurrence question. Its wording is not the model's."""
        return {
            "requested_question": {
                "kind": AgentQuestionKind.RECENT_COMPLETION.value,
                "text": RECENT_COMPLETION_QUESTION,
                "options": list(RECENT_COMPLETION_OPTIONS),
            }
        }

    def settle_recent_completion(self, state: AgentState) -> dict[str, object]:
        """Turn the resident's recurrence answer into a duplicate verdict.

        Same incident only when they say there is nothing new. A recurrence, a
        worsening or new information makes this an independent ticket, and "not
        sure" is exactly what `DUPLICATE_UNCERTAIN` is for.
        """
        answer = (state.get("recent_completion_answer") or "").strip()
        master_id = state.get("recent_completion_master_id")
        base_reason = state.get("duplicate_reason") or "Phản ánh tương tự vừa được xử lý xong."

        if answer == RECENT_NO_NEW_INFO:
            return {
                "duplicate_verdict": "SAME_INCIDENT",
                "duplicate_master_ticket_id": master_id,
                "duplicate_reason": f"{base_reason} | Cư dân xác nhận không có thông tin mới."[:500],
                "recent_completion_master_id": None,
            }
        if answer in {RECENT_RECURRED, RECENT_WORSE}:
            return {
                "duplicate_verdict": "DIFFERENT_INCIDENT",
                "duplicate_master_ticket_id": None,
                "duplicate_reason": f"{base_reason} | Cư dân cho biết sự cố tái phát hoặc có thông tin mới."[:500],
                "recent_completion_master_id": None,
            }
        # "I am not sure", or no usable answer at all.
        return {
            "duplicate_verdict": "UNCERTAIN",
            "duplicate_master_ticket_id": None,
            "duplicate_reason": (
                f"{base_reason} | Cư dân không chắc đây là cùng sự cố hay sự cố tái phát."
            )[:500],
            "recent_completion_master_id": None,
        }

    def _duplicate_evidence(self, state: AgentState) -> dict[str, object]:
        """Everything the duplicate judgement is entitled to see.

        Sanitized by construction: the graph state never holds a reporter name,
        a phone number, an apartment or the raw content of anybody else's
        ticket.
        """
        return {
            "description": state.get("description", ""),
            "category_name": _category_name(state.get("category_id"), state["catalog"]),
            "severity": state.get("severity"),
            "location_id": state.get("location_id"),
            "location_label": state.get("location_label", ""),
            "floor_label": state.get("floor_label", ""),
            "incident_facts": list(state.get("incident_facts") or []),
            "conversation": list(state.get("conversation") or []),
        }

    # ------------------------------------------------------------------
    # 4. Terminal nodes. They build the payload; `service` persists it.
    # ------------------------------------------------------------------

    def _build_result(
        self,
        state: AgentState,
        *,
        exit_reason: AgentExitReason,
        duplicate: AgentTicketRelation | None = None,
    ) -> AgentAnalysisResult:
        session = self.backend._session(UUID(state["session_id"]))
        usage = self._tool_usage(state["session_id"])
        candidates = [CandidateTicket.model_validate(item) for item in (state.get("duplicate_candidates") or [])]
        verdict = state.get("duplicate_verdict")

        if exit_reason is AgentExitReason.INSUFFICIENT_INPUT:
            # Nothing extracted may be reported here, so the payload carries the
            # absence honestly instead of a plausible-looking guess.
            return AgentAnalysisResult(
                ticket_id=UUID(state["ticket_id"]),
                analysis_session_id=UUID(state["session_id"]),
                exit_reason=exit_reason,
                red_flag=False,
                ai_reason=self._insufficient_reason(state),
                location_id=UUID(state["location_id"]) if state.get("location_id") else None,
                tool_usage=usage,
                category_catalog_version=session.category_catalog_version,
                model_version=state["model_version"],
                analyzed_at=self.clock(),
            )

        severity = state.get("severity")
        severity_source = state.get("severity_source")
        return AgentAnalysisResult(
            ticket_id=UUID(state["ticket_id"]),
            analysis_session_id=UUID(state["session_id"]),
            exit_reason=exit_reason,
            category_id=UUID(str(state["category_id"])) if state.get("category_id") else None,
            text_category_id=UUID(str(state["text_category_id"])) if state.get("text_category_id") else None,
            image_category_id=UUID(str(state["image_category_id"])) if state.get("image_category_id") else None,
            severity=Severity(severity) if severity in {"LOW", "MEDIUM", "HIGH"} else None,
            severity_source=(
                AgentSeveritySource(severity_source) if severity in {"LOW", "MEDIUM", "HIGH"} and severity_source else None
            ),
            red_flag=bool(state.get("red_flag")),
            ai_reason=state.get("ai_reason"),
            location_id=UUID(state["location_id"]) if state.get("location_id") else None,
            duplicate=duplicate,
            duplicate_verdict=DuplicateVerdict(verdict) if verdict else None,
            duplicate_reason=state.get("duplicate_reason"),
            duplicate_candidates=candidates,
            tool_usage=usage,
            category_catalog_version=session.category_catalog_version,
            model_version=state["model_version"],
            analyzed_at=self.clock(),
        )

    @staticmethod
    def _insufficient_reason(state: AgentState) -> str:
        """Say which of the two ways the report failed, so a coordinator knows."""
        if bool(state.get("image_urls")) and state.get("image_relevant") is False:
            return "Ảnh đính kèm không cho thấy sự cố nào trong khu chung cư nên không dùng được để phân loại."
        return (
            state.get("ai_reason")
            or "Mô tả của cư dân không đủ rõ để hiểu vấn đề và không có ảnh khả dụng để bổ sung."
        )

    @staticmethod
    def _emit(result: AgentAnalysisResult) -> dict[str, object]:
        return {"exit_reason": result.exit_reason.value, "result": result.model_dump(mode="json")}

    def abort_technical(self, state: AgentState) -> dict[str, object]:
        """Terminal for technical faults. Produces no business exit at all."""
        failure = state.get("technical_failure") or {"stage": "unknown", "detail": "unspecified"}
        logger.error("Agent aborting without a business exit: %s", failure)
        return {"exit_reason": None, "result": None}

    def exit_red_flag(self, state: AgentState) -> dict[str, object]:
        """Danger. Also a P3, so `finalize` parks it at the human gate."""
        return self._emit(self._build_result(state, exit_reason=AgentExitReason.RED_FLAG))

    def exit_p3_review(self, state: AgentState) -> dict[str, object]:
        """The classification is finished and it scores P3.

        No duplicate lookup has run and none will from here: the payload is
        the classification and nothing else, and `finalize` hands the ticket
        to a coordinator.
        """
        return self._emit(self._build_result(state, exit_reason=AgentExitReason.P3_REVIEW_REQUIRED))

    def exit_duplicate_existing(self, state: AgentState) -> dict[str, object]:
        reason = (state.get("duplicate_reason") or "Cùng một sự cố đang được xử lý.")[:500]
        duplicate = AgentTicketRelation(
            master_ticket_id=UUID(str(state["duplicate_master_ticket_id"])),
            reason=reason,
        )
        return self._emit(
            self._build_result(state, exit_reason=AgentExitReason.DUPLICATE_EXISTING, duplicate=duplicate)
        )

    def exit_duplicate_uncertain(self, state: AgentState) -> dict[str, object]:
        return self._emit(self._build_result(state, exit_reason=AgentExitReason.DUPLICATE_UNCERTAIN))

    def exit_analysis_complete(self, state: AgentState) -> dict[str, object]:
        return self._emit(self._build_result(state, exit_reason=AgentExitReason.ANALYSIS_COMPLETE))

    def exit_limit(self, state: AgentState) -> dict[str, object]:
        return self._emit(self._build_result(state, exit_reason=AgentExitReason.LIMIT_REACHED))

    def exit_insufficient(self, state: AgentState) -> dict[str, object]:
        return self._emit(self._build_result(state, exit_reason=AgentExitReason.INSUFFICIENT_INPUT))


def duplicate_stage_ready(state: AgentState) -> bool:
    """Whether the duplicate lookup may run yet."""
    return classification_settled(state) and not state.get("requested_question")


__all__ = [
    "RECENT_COMPLETION_OPTIONS",
    "RECENT_COMPLETION_QUESTION",
    "SEVERITY_OPTIONS",
    "AgentNodes",
    "duplicate_stage_ready",
]
